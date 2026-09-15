"""桌面端同步编排：下行拉快照、上行推原始 snp 并清理本地文件。

推送对象仅限本地上传且文件仍在本地的批次（快照导入批次的数据本来就在
服务器上）。所有失败都可安全重试，服务器端幂等兜底。
"""

from __future__ import annotations

import gzip
import logging
import shutil
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Batch, Mapping
from app.services.server_client import ServerClient, ServerError
from app.services.snapshot_service import decode_devices, import_snapshot

log = logging.getLogger("aln")

# 轮询服务器任务状态的节奏与上限
POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 3600.0

# extract_batch/deembed 管线落在批次目录顶层的中间产物（s2p 拆分件、
# 校准拆分件与去嵌结果，见 workers/extract_batch.py 与 core/deembed.py）。
# 推送只传原始文件，服务器入库时会自行重建；打包必须跳过，
# 否则服务器会把拆分件当独立 DUT 重复入库。
_PIPELINE_TEMP_DIRS = frozenset(
    {"S11_raw", "S22_raw", "cal_S11", "cal_S22", "S11_de", "S22_de"}
)


def _files_root() -> Path:
    return get_settings().files_dir


def _zip_staging_dir() -> Path:
    path = get_settings().data_root / "sync-staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_unsynced_batches(db: Session) -> list[Batch]:
    """本地上传（http/watch）、入库完成、未推送且文件仍在本地的批次。"""
    batches = db.scalars(
        select(Batch).where(
            Batch.synced_at.is_(None),
            Batch.device_count > 0,
            Batch.source.in_(("http", "watch")),
        ).order_by(Batch.uploaded_at)
    ).all()
    return [b for b in batches if (_files_root() / b.batch_no).is_dir()]


def pack_batch_zip(batch: Batch, out_path: Path) -> Path:
    """把 files/<batch_no>/ 打成服务器 ingest 可吃的 zip（.gz 解压回 .s1p）。

    跳过顶层管线中间目录（_PIPELINE_TEMP_DIRS）与点开头杂项文件
    （.DS_Store 等）。任何异常都先清理半截 zip 再重抛；
    损坏的 .gz 转成带文件名的 ServerError。
    """
    src = _files_root() / batch.batch_no
    if not src.is_dir():
        raise ServerError(f"本地批次目录不存在: {src}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(src.rglob("*")):
                if not file.is_file() or file.name.startswith("."):
                    continue
                rel = file.relative_to(src)
                if rel.parts[0] in _PIPELINE_TEMP_DIRS:
                    continue
                arcname = f"{batch.batch_no}/{rel.as_posix()}"
                if file.suffix == ".gz":
                    try:
                        with (
                            zf.open(arcname[: -len(".gz")], "w") as zout,
                            gzip.open(file, "rb") as fh,
                        ):
                            shutil.copyfileobj(fh, zout)
                    except gzip.BadGzipFile as exc:
                        raise ServerError(f"gzip 文件损坏: {file}") from exc
                else:
                    zf.write(file, arcname=arcname)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return out_path


def _mapping_entries_payload(mapping: Mapping) -> list[dict]:
    return [
        {
            "mark": e.mark, "description": e.description, "eg": e.eg, "fl": e.fl,
            "ag": e.ag, "area_s11": e.area_s11, "area_s22": e.area_s22, "has_pf": e.has_pf,
        }
        for e in mapping.entries
    ]


def ensure_mapping_on_server(db: Session, client: ServerClient, batch: Batch) -> None:
    mapping = db.get(Mapping, batch.mapping_id)
    if mapping is None:
        raise ServerError("批次关联的对照表不存在")
    try:
        client.ensure_mapping(mapping.name, _mapping_entries_payload(mapping))
    except ServerError as exc:
        if "409" in str(exc) or "内容不同" in str(exc):
            raise ServerError(f"服务器上对照表 {mapping.name} 内容不同，请改名后重试") from exc
        raise


def wait_task(client: ServerClient, task_id: int) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        task = client.task_status(task_id)
        if task["status"] == "success":
            return task
        if task["status"] in {"failed", "cancelled"}:
            raise ServerError(f"服务器入库失败: {task.get('error_msg') or task['status']}")
        time.sleep(POLL_INTERVAL_S)
    raise ServerError(f"等待服务器入库超时: task_id={task_id}")


def finalize_synced_batch(db: Session, client: ServerClient, batch_no: str) -> dict[str, Any]:
    """校验服务器 device_count 与本地一致后，打 synced_at 并删除本地原始文件。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise ServerError(f"本机没有批次 {batch_no}")
    remote_count = int(client.get_batch(batch_no)["device_count"])
    if remote_count != batch.device_count:
        raise ServerError(
            f"疑似同名不同数据：服务器 {remote_count} 条 vs 本地 {batch.device_count} 条，"
            "请给本地批次改名后重推，本地文件未删除"
        )
    # 先提交同步状态再删文件：删除失败只留残留文件（记日志），
    # 重试走"已同步"早退分支，不会重复推送。
    batch.synced_at = datetime.now(UTC)
    db.commit()
    files_dir = _files_root() / batch_no
    if files_dir.is_dir():
        try:
            shutil.rmtree(files_dir)
        except OSError:
            log.exception("批次 %s 已同步，但删除本地文件失败", batch_no)
    return {"status": "synced", "device_count": remote_count}


def push_batch_to_server(db: Session, client: ServerClient, batch_no: str) -> dict[str, Any]:
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise ServerError(f"本机没有批次 {batch_no}")
    if batch.synced_at is not None:
        return {"status": "synced", "device_count": batch.device_count, "note": "此前已同步"}

    ensure_mapping_on_server(db, client, batch)
    zip_path = pack_batch_zip(batch, _zip_staging_dir() / f"{batch_no}.zip")
    try:
        meta = {
            "batch_no": batch_no,
            "mapping_name": batch.mapping.name,
            "deembed": "true" if batch.deembedded else "false",
            "deembed_method": batch.deembed_method,
            "process_type": batch.process_type,
            "uploaded_by": client.config.username,
        }
        if batch.f_start_ghz is not None:
            meta["f_start_ghz"] = str(batch.f_start_ghz)
        if batch.f_end_ghz is not None:
            meta["f_end_ghz"] = str(batch.f_end_ghz)
        result = client.push_batch(zip_path, meta)
    finally:
        zip_path.unlink(missing_ok=True)

    if result["status"] == "accepted":
        wait_task(client, int(result["task_id"]))
    return finalize_synced_batch(db, client, batch_no)


def pull_from_server(db: Session, client: ServerClient) -> dict[str, int]:
    known = set(db.scalars(select(Batch.batch_no)).all())
    payload = client.fetch_snapshot(known)
    return import_snapshot(
        db,
        payload["batches"],
        payload["mappings"],
        decode_devices(payload["devices_arrow_gz_b64"]),
        source="server",
    )
