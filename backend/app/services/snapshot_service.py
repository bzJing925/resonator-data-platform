"""批次快照的导出与导入（传输无关）。

服务器角色用 build_snapshot 导出差集快照；桌面/网页角色用
import_snapshot 增量导入。GitHub 网页版同步与内网服务器同步共用本模块。
"""

from __future__ import annotations

import base64
import gzip
from collections import defaultdict
from collections.abc import Iterable

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, Device, Mapping, MappingEntry

_DEVICE_COLUMNS = {column.name for column in Device.__table__.columns}
_SHARED_DEVICE_COLUMNS = sorted(_DEVICE_COLUMNS - {"id", "batch_id", "s_param_path"})


def _nullable(value):
    # PyArrow 的 to_pylist 通常已转换为 None；这里也兼容 pandas/numpy 的 NaN。
    try:
        return None if value != value else value
    except Exception:  # noqa: BLE001
        return value


def _mapping_payload(mapping: Mapping) -> dict:
    return {
        "id": mapping.id,
        "name": mapping.name,
        "entries": [
            {
                "mark": entry.mark,
                "description": entry.description,
                "eg": entry.eg,
                "fl": entry.fl,
                "ag": entry.ag,
                "area_s11": entry.area_s11,
                "area_s22": entry.area_s22,
                "has_pf": entry.has_pf,
            }
            for entry in mapping.entries
        ],
    }


def build_snapshot(db: Session, known_batch_nos: set[str]) -> dict:
    """导出 known_batch_nos 之外的全部批次，返回可 JSON 序列化的 dict。"""
    if known_batch_nos:
        batches = db.scalars(
            select(Batch).where(Batch.batch_no.notin_(known_batch_nos))
        ).all()
    else:
        batches = db.scalars(select(Batch)).all()
    mappings_payload: dict[int, dict] = {}
    batches_payload: list[dict] = []
    devices_payload: list[dict] = []
    for batch in batches:
        batches_payload.append(
            {
                "batch_no": batch.batch_no,
                "mapping_id": batch.mapping_id,
                "f_start_ghz": batch.f_start_ghz,
                "f_end_ghz": batch.f_end_ghz,
                "deembedded": batch.deembedded,
                "deembed_method": batch.deembed_method,
                "process_type": batch.process_type,
                "source": batch.source,
                "device_count": batch.device_count,
                "uploaded_by": batch.uploaded_by,
            }
        )
        if batch.mapping_id not in mappings_payload and batch.mapping is not None:
            mappings_payload[batch.mapping_id] = _mapping_payload(batch.mapping)
        for device in batch.devices:
            row = {column: getattr(device, column) for column in _SHARED_DEVICE_COLUMNS}
            row["batch_no"] = batch.batch_no
            devices_payload.append(row)

    arrow_b64 = ""
    if devices_payload:
        table = pa.Table.from_pylist(devices_payload)
        sink = pa.BufferOutputStream()
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
        arrow_b64 = base64.b64encode(gzip.compress(sink.getvalue().to_pybytes())).decode("ascii")

    return {
        "batches": batches_payload,
        "mappings": list(mappings_payload.values()),
        "devices_arrow_gz_b64": arrow_b64,
    }


def decode_devices(payload_b64: str) -> list[dict]:
    """build_snapshot 的逆操作；客户端拉取后调用。"""
    if not payload_b64:
        return []
    raw = gzip.decompress(base64.b64decode(payload_b64))
    return pa.ipc.open_file(pa.py_buffer(raw)).read_all().to_pylist()


def import_snapshot(
    db: Session,
    batches: list[dict],
    mappings: list[dict],
    devices: Iterable[dict],
    *,
    source: str,
) -> dict[str, int]:
    """增量导入快照。同名本机批次/对照表优先，不覆盖本地工作。"""
    local_mappings = {row.name: row for row in db.scalars(select(Mapping)).all()}
    mapping_ids: dict[int, int] = {}
    added_mappings = 0
    for remote in mappings:
        name = str(remote.get("name") or "").strip()
        if not name:
            continue
        mapping = local_mappings.get(name)
        if mapping is None:
            mapping = Mapping(name=name, file_path=f"{source}://{name}", entry_count=0)
            db.add(mapping)
            db.flush()
            entries = remote.get("entries") or []
            db.bulk_insert_mappings(
                MappingEntry,
                [
                    {
                        "mapping_id": mapping.id,
                        "mark": str(entry.get("mark") or ""),
                        "description": _nullable(entry.get("description")),
                        "eg": _nullable(entry.get("eg")),
                        "fl": _nullable(entry.get("fl")),
                        "ag": _nullable(entry.get("ag")),
                        "area_s11": _nullable(entry.get("area_s11")),
                        "area_s22": _nullable(entry.get("area_s22")),
                        "has_pf": bool(entry.get("has_pf")),
                        "raw_tokens": None,
                    }
                    for entry in entries
                    if entry.get("mark") is not None
                ],
            )
            mapping.entry_count = len(entries)
            local_mappings[name] = mapping
            added_mappings += 1
        if remote.get("id") is not None:
            mapping_ids[int(remote["id"])] = mapping.id

    local_batch_nos = set(db.scalars(select(Batch.batch_no)).all())
    batch_ids: dict[str, int] = {}
    added_batches = 0
    for remote in batches:
        batch_no = str(remote.get("batch_no") or "").strip()
        if not batch_no or batch_no in local_batch_nos:
            continue
        mapping_id = mapping_ids.get(int(remote.get("mapping_id") or 0))
        if mapping_id is None:
            mapping = local_mappings.get(str(remote.get("mapping_name") or ""))
            if mapping is None:
                raise ValueError(f"批次 {batch_no} 引用了快照中不存在的对照表")
            mapping_id = mapping.id
        batch = Batch(
            batch_no=batch_no,
            mapping_id=mapping_id,
            f_start_ghz=_nullable(remote.get("f_start_ghz")),
            f_end_ghz=_nullable(remote.get("f_end_ghz")),
            deembedded=bool(remote.get("deembedded")),
            deembed_method=str(remote.get("deembed_method") or "default"),
            process_type=str(remote.get("process_type") or "AUTO"),
            file_path=f"{source}://{batch_no}",
            raw_zip_path=None,
            source=source,
            device_count=int(remote.get("device_count") or 0),
            uploaded_by=str(remote.get("uploaded_by") or f"{source}-sync"),
        )
        db.add(batch)
        db.flush()
        batch_ids[batch_no] = batch.id
        added_batches += 1

    rows_by_batch: dict[str, list[dict]] = defaultdict(list)
    for index, remote in enumerate(devices):
        batch_id = batch_ids.get(str(remote.get("batch_no") or ""))
        if batch_id is None:
            continue
        values = {
            key: _nullable(value)
            for key, value in remote.items()
            if key in _DEVICE_COLUMNS and key not in {"id", "batch_id", "s_param_path"}
        }
        values["batch_id"] = batch_id
        values["original_filename"] = str(values.get("original_filename") or f"{source}-{index}")
        values["s_param_port"] = "S22" if values.get("s_param_port") == "S22" else "S11"
        if values.get("pf") is not None:
            values["pf"] = "Y" if str(values["pf"]).upper() in {"Y", "1", "TRUE"} else "N"
        values["deembedded"] = bool(values.get("deembedded"))
        rows_by_batch[str(remote.get("batch_no"))].append(values)
    for rows in rows_by_batch.values():
        db.bulk_insert_mappings(Device, rows)
    db.commit()
    return {
        "mappings_added": added_mappings,
        "batches_added": added_batches,
        "devices_added": sum(len(rows) for rows in rows_by_batch.values()),
    }
