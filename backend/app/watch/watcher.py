"""监听 data/watch/ 目录，自动处理放入的 .zip 文件。

使用 watchfiles 异步 API，在 FastAPI lifespan 内启动。
为避免文件还在拷贝中，触发后等待 1 秒并检查文件大小稳定再处理。
处理时先把 zip 移入 uploads 目录，再复用 HTTP 上传同一套创建批次逻辑。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import watchfiles

from app.config import get_settings
from app.db import SessionLocal
from app.services.upload_service import create_batch_and_dispatch, ensure_default_mapping

logger = logging.getLogger(__name__)


def _is_zip(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".zip"


async def _wait_stable(path: Path, stable_seconds: float = 1.0, timeout: float = 30.0) -> bool:
    """等待文件大小稳定，返回是否成功。"""
    last_size = -1
    total_wait = 0.0
    while total_wait < timeout:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last_size and size > 0:
            await asyncio.sleep(stable_seconds)
            try:
                if path.stat().st_size == size:
                    return True
            except FileNotFoundError:
                return False
        last_size = size
        await asyncio.sleep(0.5)
        total_wait += 0.5
    return False


async def _process_zip(watch_zip: Path) -> None:
    settings = get_settings()
    if not await _wait_stable(watch_zip):
        logger.warning("文件未稳定或已消失: %s", watch_zip)
        return

    batch_no = watch_zip.stem
    db = SessionLocal()
    try:
        mapping_id = ensure_default_mapping(db)
        if mapping_id is None:
            logger.error("数据库中无对照表，无法自动处理 %s", watch_zip.name)
            return

        # 把 zip 从 watch 目录移入 uploads，避免 watch 重复扫描或提前删除
        month_dir = settings.uploads_dir / datetime.now(UTC).strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)
        saved_name = f"watch_{uuid.uuid4().hex}.zip"
        saved_path = month_dir / saved_name

        try:
            watch_zip.rename(saved_path)
        except Exception:
            logger.exception("移动 watch zip 失败: %s", watch_zip)
            return

        task = create_batch_and_dispatch(
            db,
            zip_path=saved_path,
            batch_no=batch_no,
            mapping_id=mapping_id,
            source="watch",
            process_type="AUTO",
        )
        if task is None:
            logger.info("批次 %s 已存在，跳过 %s", batch_no, watch_zip.name)
            # 重复文件：删除从 watch 移出来的副本
            saved_path.unlink(missing_ok=True)
            return

        logger.info("已自动创建批次 %s，任务 id=%s", batch_no, task.id)
    finally:
        db.close()


async def _scan_existing(watch_dir: Path) -> None:
    """启动时扫描已存在的 zip，避免漏掉服务停止期间放入的文件。"""
    for path in sorted(watch_dir.glob("*.zip")):
        await _process_zip(path)


async def watch_uploads() -> None:
    """长期运行的目录监听协程。"""
    settings = get_settings()
    if not settings.WATCH_ENABLED:
        return

    watch_dir = settings.watch_dir
    watch_dir.mkdir(parents=True, exist_ok=True)
    logger.info("启动目录监听: %s", watch_dir)

    # 先处理已存在的文件
    await _scan_existing(watch_dir)

    async for changes in watchfiles.awatch(watch_dir):
        for change, raw_path in changes:
            path = Path(raw_path)
            if change == watchfiles.Change.deleted:
                continue
            if not _is_zip(path):
                continue
            # 异步处理，避免阻塞 watch loop
            asyncio.create_task(_process_zip(path))
