"""统一清理批次及其上传的物理文件。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Batch

logger = logging.getLogger(__name__)


def delete_batch_and_files(db: Session, batch_no: str) -> bool:
    """删除 batch（级联删 devices/file_nodes）及其上传文件。

    返回是否实际删除了 batch。
    """
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        return False

    settings = get_settings()
    files_dir = settings.files_dir / batch_no
    raw_zip = Path(batch.raw_zip_path) if batch.raw_zip_path else None

    db.delete(batch)
    db.commit()

    if files_dir.exists():
        try:
            shutil.rmtree(files_dir)
        except Exception:
            logger.exception("删除解压目录失败: %s", files_dir)
    if raw_zip and raw_zip.exists():
        try:
            raw_zip.unlink()
        except Exception:
            logger.exception("删除原 zip 失败: %s", raw_zip)

    return True
