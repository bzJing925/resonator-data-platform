"""服务器角色同步端点：供内网桌面端拉快照、推批次、按自然键取曲线。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.api.devices import build_sparam_response, resolve_device_sparam_path
from app.config import get_settings
from app.models import Batch, Device, Mapping, MappingEntry, UploadTask
from app.services.batch_scope import active_batch_clause, get_active_batch
from app.services.raw_download import raw_file_response
from app.services.snapshot_service import build_snapshot
from app.services.source_identity import SourceIdentityError
from app.services.upload_service import (
    MappingNotFoundError,
    TaskDispatchError,
    create_batch_and_dispatch,
)

router = APIRouter(prefix="/sync", tags=["sync"])


class SnapshotRequest(BaseModel):
    known_batch_nos: list[str] = Field(default_factory=list, max_length=100000)


class MappingEntryIn(BaseModel):
    mark: str
    description: str | None = None
    eg: float | None = None
    fl: float | None = None
    ag: float | None = None
    area_s11: float | None = None
    area_s22: float | None = None
    has_pf: bool = False


class MappingEnsureRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    entries: list[MappingEntryIn] = Field(default_factory=list)


# 数值列在 DB 里既有 Float（eg/fl/ag）也有 Integer（area_s11/area_s22）；
# 请求 JSON 一律按 float 解析。指纹比较前统一归一为 float，
# 否则同一份对照表第二次推送时 3 (int) vs 3.0 (float) 会被误判为内容不同。
_NUMERIC_ENTRY_KEYS = ("eg", "fl", "ag", "area_s11", "area_s22")


def _entries_fingerprint(entries: list[dict]) -> str:
    canonical_entries = []
    for entry in entries:
        canonical = dict(entry)
        for key in _NUMERIC_ENTRY_KEYS:
            if canonical.get(key) is not None:
                canonical[key] = float(canonical[key])
        canonical_entries.append(canonical)
    canonical = json.dumps(canonical_entries, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post("/snapshot")
def snapshot(body: SnapshotRequest, db: DbSession) -> dict[str, Any]:
    return build_snapshot(db, set(body.known_batch_nos))


def _persist_synced_mapping(mapping, entries):
    """Project the accepted entries into the existing algorithm's CSV format."""
    import csv

    settings = get_settings()
    settings.mappings_dir.mkdir(parents=True, exist_ok=True)
    path = settings.mappings_dir / f"sync-{uuid.uuid4().hex}.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        for entry in sorted(entries, key=lambda item: item["mark"]):
            tokens = [
                f"{name.upper()}{entry[name]}"
                for name in ("eg", "fl", "ag") if entry.get(name) is not None
            ]
            if entry.get("area_s11") is not None and entry.get("area_s22") is not None:
                tokens.append(f"{int(entry['area_s11'])}&{int(entry['area_s22'])}")
            if entry.get("has_pf"):
                tokens.append("PF")
            description = entry.get("description") or " ".join(tokens) or "unknown"
            writer.writerow([entry["mark"], description])
    mapping.file_path = str(path)


@router.post("/mapping")
def ensure_mapping(body: MappingEnsureRequest, db: DbSession) -> dict[str, Any]:
    return ensure_mapping_data(body, db)


def ensure_mapping_data(body, db, *, commit=True):
    name = body.name.strip()
    incoming = [entry.model_dump() for entry in body.entries]
    existing = db.scalar(select(Mapping).where(Mapping.name == name))
    if existing is not None:
        current = [
            {
                "mark": e.mark,
                "description": e.description,
                "eg": e.eg,
                "fl": e.fl,
                "ag": e.ag,
                "area_s11": e.area_s11,
                "area_s22": e.area_s22,
                "has_pf": e.has_pf,
            }
            for e in sorted(existing.entries, key=lambda x: x.mark)
        ]
        if _entries_fingerprint(current) != _entries_fingerprint(
            sorted(incoming, key=lambda x: x["mark"])
        ):
            raise HTTPException(status_code=409, detail=f"对照表 {name} 已存在且内容不同，请改名")
        if not Path(existing.file_path).is_file():
            _persist_synced_mapping(existing, incoming)
            db.commit() if commit else db.flush()
        return {"mapping_id": existing.id, "created": False}

    mapping = Mapping(name=name, file_path=f"sync://{name}", entry_count=len(incoming))
    db.add(mapping)
    db.flush()
    db.bulk_insert_mappings(
        MappingEntry,
        [{"mapping_id": mapping.id, "raw_tokens": None, **entry} for entry in incoming],
    )
    _persist_synced_mapping(mapping, incoming)
    db.commit() if commit else db.flush()
    return {"mapping_id": mapping.id, "created": True}


@router.post("/push-batch")
def push_batch(
    db: DbSession,
    file: Annotated[UploadFile, File(...)],
    batch_no: Annotated[str, Form(min_length=1, max_length=300)],
    mapping_name: Annotated[str, Form(min_length=1, max_length=200)],
    f_start_ghz: Annotated[float | None, Form()] = None,
    f_end_ghz: Annotated[float | None, Form()] = None,
    deembed: Annotated[bool, Form()] = False,
    deembed_method: Annotated[str, Form()] = "default",
    process_type: Annotated[str, Form()] = "AUTO",
    uploaded_by: Annotated[str, Form()] = "anonymous",
    source_ref: Annotated[str | None, Form()] = None,
    source_inputs: Annotated[UploadFile | str | None, File()] = None,
) -> dict[str, Any]:
    # batch_no 会拼进 files_dir 路径并用于目录清理，先做正向路径校验。
    # 服务器可能跑 POSIX 或 Windows：POSIX 的 Path 认不出 Windows 的反斜杠
    # 分隔符与盘符相对路径（"a\\b"、"C:" 在 POSIX 下 name 是其自身），
    # 需同时按 Windows 语义校验；strip(". ") 负责挡住 "." / ".." 以及
    # Windows 下会被归一掉的尾点空格名（". "、" ."）。
    if (
        Path(batch_no).name != batch_no
        or PureWindowsPath(batch_no).name != batch_no
        or not batch_no.strip(". ")
    ):
        raise HTTPException(status_code=400, detail="批次名含有非法路径字符")
    if process_type not in ("AUTO", "S1P", "S2P", "BOTH"):
        raise HTTPException(
            status_code=400, detail="process_type 必须是 AUTO / S1P / S2P / BOTH 之一"
        )
    valid_methods = {"default", "original", "gsg100", "vz", "basic"}
    if deembed and deembed_method not in valid_methods:
        raise HTTPException(
            status_code=400,
            detail=f"去嵌方法必须是 {', '.join(sorted(valid_methods))} 之一",
        )

    try:
        transferred_ref = json.loads(source_ref) if source_ref is not None else None
        if hasattr(source_inputs, 'file'):
            transferred_inputs = json.load(source_inputs.file)
        else:
            transferred_inputs = json.loads(source_inputs) if source_inputs is not None else None
    except ValueError as exc:
        raise HTTPException(422, {"code": "SOURCE_TRANSFER_INVALID", "message": "来源数据格式无效",
                                  "retryable": False, "context": {}}) from exc
    mapping = db.scalar(select(Mapping).where(Mapping.name == mapping_name.strip()))
    if mapping is None:
        raise HTTPException(status_code=422, detail=f"对照表 {mapping_name} 不存在，先推送对照表")

    existing = None
    if isinstance(transferred_ref, dict):
        existing = db.scalar(select(Batch).where(
            Batch.origin_id == transferred_ref.get('origin_id'),
            Batch.source_batch_id == transferred_ref.get('source_batch_id')))
    if existing is None:
        existing = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if existing is not None:
        if transferred_ref is not None and (
            not isinstance(transferred_ref, dict)
            or transferred_ref.get('origin_id') != existing.origin_id
            or transferred_ref.get('source_batch_id') != existing.source_batch_id
        ):
            raise HTTPException(409, {"code": "SYNC_SOURCE_CONFLICT",
                "message": "同名批次来源不同，未覆盖", "retryable": False, "context": {}})
        get_active_batch(db, existing.batch_no)
        if transferred_ref is not None:
            from app.services.source_transfer import verify_existing_transfer
            verify_existing_transfer(db, existing, transferred_ref, transferred_inputs)
        task = db.get(UploadTask, existing.task_id) if existing.task_id else None
        if task is not None and task.status in {"pending", "running", "cancel_requested"}:
            raise HTTPException(status_code=409, detail="服务器上该批次任务进行中，稍后重试")
        if task is not None and task.status == "success":
            return {
                "status": "already_synced",
                "task_id": task.id,
                "device_count": existing.device_count,
            }
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SYNC_BATCH_CONFLICT",
                "message": "同名批次已存在，失败记录与输入保留，请重试原任务或选择新批次名",
                "retryable": False,
                "context": {"task_id": task.id if task else None},
            },
        )

    settings = get_settings()
    month_dir = settings.uploads_dir / datetime.now(UTC).strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    saved_path = month_dir / f"{uuid.uuid4().hex}.zip"
    max_bytes = settings.UPLOAD_MAX_GB * 1024**3
    written = 0
    try:
        with saved_path.open("wb") as out:
            while chunk := file.file.read(8 * 1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail="文件超过上限")
                out.write(chunk)
    except Exception:
        saved_path.unlink(missing_ok=True)
        raise

    # Admission can commit before an error is returned; retain the saved input.
    try:
        task = create_batch_and_dispatch(
            db,
            zip_path=saved_path,
            batch_no=batch_no,
            mapping_id=mapping.id,
            source="sync",
            source_ref=transferred_ref,
            source_inputs=transferred_inputs,
            f_start_ghz=f_start_ghz,
            f_end_ghz=f_end_ghz,
            deembed=deembed,
            deembed_method=deembed_method,
            process_type=process_type,
            uploaded_by=uploaded_by,
        )
        if task is None:
            raise HTTPException(status_code=500, detail="创建批次失败")
    except HTTPException:
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, {"code": "SYNC_SOURCE_CONFLICT",
            "message": "来源身份或批次已被登记，未覆盖已有数据",
            "retryable": False, "context": {}}) from exc
    except SourceIdentityError as exc:
        db.rollback()
        raise HTTPException(409, {"code": exc.code, "message": str(exc),
                                  "retryable": False, "context": {}}) from exc
    except MappingNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TaskDispatchError as exc:
        raise HTTPException(status_code=503, detail=f"任务队列不可用: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建批次失败: {exc!s}") from exc
    if task.batch_no != batch_no:
        # Admission is durable and the executor may already be running.
        # Retain its task and input; the caller must resolve the name conflict.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SYNC_BATCH_CONFLICT",
                "message": "并发推送产生批次名冲突，已接受任务与输入保留",
                "retryable": False,
                "context": {"task_id": task.id, "batch_no": task.batch_no},
            },
        )
    return {"status": "accepted", "task_id": task.id, "device_count": None}


def _find_device(db, batch_no: str, filename: str, port: str) -> Device:
    get_active_batch(db, batch_no)
    devices = db.scalars(
        select(Device)
        .join(Batch, Device.batch_id == Batch.id)
        .where(
            Batch.batch_no == batch_no, active_batch_clause(),
            Device.original_filename == filename,
            Device.s_param_port == port.upper(),
        )
    ).all()
    if not devices:
        raise HTTPException(status_code=404, detail=f"器件不存在: {batch_no}/{filename}/{port}")
    if len(devices) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                f"批次 {batch_no} 中文件 {filename} 存在多份（zip 内不同子目录），"
                "请使用更精确的文件名"
            ),
        )
    return devices[0]


@router.get("/sparam")
def sparam_by_natural_key(
    db: DbSession,
    batch_no: Annotated[str, Query()],
    filename: Annotated[str, Query()],
    port: Annotated[str, Query()] = "S11",
    param: Annotated[str, Query()] = "s11_db",
) -> dict[str, Any]:
    device = _find_device(db, batch_no, filename, port)
    return build_sparam_response(device, device.id, param)


@router.get("/download")
def download_by_natural_key(
    db: DbSession,
    batch_no: Annotated[str, Query()],
    filename: Annotated[str, Query()],
    port: Annotated[str, Query()] = "S11",
) -> FileResponse:
    device = _find_device(db, batch_no, filename, port)
    if not device.s_param_path:
        raise HTTPException(status_code=404, detail="该器件没有 S 参数文件路径")
    path = resolve_device_sparam_path(device)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"S 参数文件不存在: {path}")
    return raw_file_response(path)
