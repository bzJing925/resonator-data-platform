"""一次性清理脚本。

1. 删除已处理批次保留的原始 zip（根据 KEEP_RAW_ZIP=false）。
2. 删除 uploads 目录中未被任何 batch 引用的孤儿 zip。
3. 删除 files 目录中无对应 batch 的残留解压目录。
4. 删除无关联 batch 的失败/孤儿 upload_tasks。

运行方式（从 backend 目录）：
    python scripts/cleanup_duplicates.py
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Batch, UploadTask

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cleanup")


def main() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        # 1. 删除已处理批次保留的 raw zip
        if not settings.KEEP_RAW_ZIP:
            batches = db.scalars(select(Batch).where(Batch.raw_zip_path.is_not(None))).all()
            for batch in batches:
                p = Path(batch.raw_zip_path)
                if p.exists():
                    try:
                        p.unlink()
                        logger.info("删除原 zip: %s", p)
                    except Exception:
                        logger.exception("删除失败: %s", p)
                batch.raw_zip_path = None
            db.commit()

        # 2. 删除 uploads 中的孤儿 zip
        referenced = {
            Path(b.raw_zip_path) for b in db.scalars(select(Batch)).all() if b.raw_zip_path
        }
        for p in settings.uploads_dir.rglob("*.zip"):
            if p not in referenced:
                try:
                    p.unlink()
                    logger.info("删除孤儿 zip: %s", p)
                except Exception:
                    logger.exception("删除失败: %s", p)

        # 3. 删除 files 中的残留目录
        referenced_dirs = {
            Path(b.file_path) for b in db.scalars(select(Batch)).all() if b.file_path
        }
        if settings.files_dir.exists():
            for p in settings.files_dir.iterdir():
                if p.is_dir() and p not in referenced_dirs:
                    try:
                        shutil.rmtree(p)
                        logger.info("删除残留目录: %s", p)
                    except Exception:
                        logger.exception("删除失败: %s", p)

        # 4. 删除无关联 batch 的 upload_tasks
        active_batch_nos = {b.batch_no for b in db.scalars(select(Batch)).all()}
        orphan_tasks = db.scalars(
            select(UploadTask).where(UploadTask.batch_no.not_in(active_batch_nos))
        ).all()
        for task in orphan_tasks:
            logger.info("删除孤儿 upload_task id=%s batch_no=%s", task.id, task.batch_no)
            db.delete(task)
        db.commit()

        logger.info("清理完成")
    finally:
        db.close()


if __name__ == "__main__":
    main()
