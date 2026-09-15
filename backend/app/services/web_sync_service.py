"""桌面版与静态网页版之间的数据同步。

网页版的 ``main`` 分支是共享数据的唯一来源：桌面端将分享包写入
``uploads/``，仓库工作流负责校验、合并和发布；反向同步只读取已经发布的
``webdata`` 快照。因此本机不会因网络失败而留下半导入的数据。
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, Device, Mapping
from app.share_pack import write_pack

WEB_SITE = "https://bzjing925.github.io/aln-data-web"
WEB_REPO = "bzJing925/aln-data-web"
_DEVICE_COLUMNS = {column.name for column in Device.__table__.columns}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class WebSyncError(RuntimeError):
    """网页快照或 GitHub 写入不可用。"""


def _request_json(
    url: str, *, method: str = "GET", token: str | None = None, body: dict | None = None
):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ALN-Data-Desktop"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        request = Request(url, data=payload, headers=headers, method=method)
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise WebSyncError(f"网页服务返回 {exc.code}: {detail}") from exc
    except (URLError, OSError, json.JSONDecodeError) as exc:
        raise WebSyncError(f"无法连接网页服务: {exc}") from exc


def _request_bytes(url: str) -> bytes:
    try:
        request = Request(url, headers={"User-Agent": "ALN-Data-Desktop"})
        with urlopen(request, timeout=120) as response:
            return response.read()
    except (HTTPError, URLError, OSError) as exc:
        raise WebSyncError(f"无法下载网页版快照: {exc}") from exc


def fetch_web_snapshot() -> tuple[list[dict], list[dict], list[dict]]:
    """下载已发布快照，返回 batches、mappings、devices 三个 Python 对象。"""
    batches = _request_json(f"{WEB_SITE}/webdata/batches.json")
    mappings = _request_json(f"{WEB_SITE}/webdata/mappings.json")
    arrow = gzip.decompress(_request_bytes(f"{WEB_SITE}/webdata/devices.arrow.gz"))
    devices = pa.ipc.open_file(pa.py_buffer(arrow)).read_all().to_pylist()
    if not isinstance(batches, list) or not isinstance(mappings, list):
        raise WebSyncError("网页版快照格式不正确")
    return batches, mappings, devices


def import_web_snapshot(
    db: Session, batches: list[dict], mappings: list[dict], devices: Iterable[dict]
) -> dict[str, int]:
    """增量导入网页快照（薄封装，逻辑在 snapshot_service）。"""
    from app.services.snapshot_service import import_snapshot

    try:
        return import_snapshot(db, batches, mappings, devices, source="web")
    except ValueError as exc:
        raise WebSyncError(str(exc)) from exc


def pull_from_web(db: Session) -> dict[str, int]:
    batches, mappings, devices = fetch_web_snapshot()
    return import_web_snapshot(db, batches, mappings, devices)


def _mapping_json(mapping: Mapping) -> dict:
    return {
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


def push_batch_to_web(db: Session, batch_no: str, token: str) -> dict[str, str | int]:
    """把本机已完成批次生成分享包并直接写入网站数据仓 main 分支。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise WebSyncError(f"本机没有批次 {batch_no}")
    if batch.device_count <= 0:
        raise WebSyncError("批次尚未完成入库，完成后再同步")
    mapping = db.get(Mapping, batch.mapping_id)
    if mapping is None:
        raise WebSyncError("批次关联的对照表不存在")
    device_rows = db.execute(select(Device).where(Device.batch_id == batch.id)).scalars().all()
    if not device_rows:
        raise WebSyncError("批次没有可同步的数据点")
    shared_columns = _DEVICE_COLUMNS - {"id", "batch_id", "s_param_path"}
    rows = [
        {column: getattr(device, column) for column in shared_columns}
        for device in device_rows
    ]
    fd, temporary = tempfile.mkstemp(prefix="aln-share-", suffix=".zip")
    os.close(fd)
    pack_path = Path(temporary)
    try:
        write_pack(
            rows,
            {
                "batch_no": batch.batch_no,
                "mapping_name": mapping.name,
                "f_start_ghz": batch.f_start_ghz,
                "f_end_ghz": batch.f_end_ghz,
                "deembedded": batch.deembedded,
                "deembed_method": batch.deembed_method,
                "process_type": batch.process_type,
                "source": batch.source,
            },
            _mapping_json(mapping),
            [],
            pack_path,
        )
        safe = _SAFE_NAME.sub("_", batch.batch_no).strip("._") or "batch"
        remote_path = f"uploads/{safe}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.zip"
        result = _request_json(
            f"https://api.github.com/repos/{WEB_REPO}/contents/{remote_path}",
            method="PUT",
            token=token.strip(),
            body={
                "message": f"data: queue share pack {batch.batch_no}",
                "content": base64.b64encode(pack_path.read_bytes()).decode("ascii"),
                "branch": "main",
            },
        )
        return {
            "batch_no": batch.batch_no,
            "device_count": len(rows),
            "commit_url": str((result.get("commit") or {}).get("html_url") or ""),
            "message": "已写入网页版数据仓，网站将在 CI 合并并发布后更新（通常数分钟）。",
        }
    finally:
        pack_path.unlink(missing_ok=True)
