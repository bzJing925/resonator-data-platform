"""上传业务逻辑：HTTP 上传与目录监听共用。

抽离后，create_upload 只负责接收流并保存到本地 zip；
本模块负责创建 UploadTask + Batch 并投递 Celery chain。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from celery import chain
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Batch, Mapping, UploadTask
from app.workers.compute_batch import compute_batch_task
from app.workers.extract_batch import extract_batch_task
from app.workers.pipeline_batch import pipeline_batch_task, should_use_pipeline

logger = logging.getLogger(__name__)


class MappingNotFoundError(Exception):
    """请求的对照表 ID 不存在。"""


class TaskDispatchError(Exception):
    """Celery 任务投递失败（如 broker 不可达）。"""


def _dispatch_chain(
    task: UploadTask,
    zip_path: Path,
    batch_no: str,
    mapping_id: int,
    *,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
) -> str:
    """根据批次特征选择 pipeline 或 legacy 链路，投递 Celery 任务。"""
    if should_use_pipeline(zip_path, deembed):
        result = pipeline_batch_task.apply_async(
            kwargs={
                "upload_task_id": task.id,
                "zip_path": str(zip_path),
                "batch_no": batch_no,
                "mapping_id": mapping_id,
                "f_start_ghz": f_start_ghz,
                "f_end_ghz": f_end_ghz,
                "deembed_method": deembed_method if deembed else "default",
                "process_type": process_type,
            }
        )
    else:
        result = chain(
            extract_batch_task.s(
                upload_task_id=task.id,
                zip_path=str(zip_path),
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=f_start_ghz,
                f_end_ghz=f_end_ghz,
                deembed_enabled=bool(deembed),
                deembed_method=deembed_method if deembed else "default",
                process_type=process_type,
            ),
            compute_batch_task.s(),
        ).apply_async()
    return result.id


def create_batch_and_dispatch(
    db: Session,
    zip_path: Path,
    batch_no: str,
    mapping_id: int,
    *,
    source: Literal["http", "watch"] = "http",
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
    uploaded_by: str = "anonymous",
) -> UploadTask | None:
    """创建 UploadTask + Batch 并投递处理链。

    若 batch_no 已存在，返回 None（调用方应视为重复并跳过/报错）。
    """
    existing = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if existing is not None:
        logger.warning("批次 %s 已存在，跳过重复 %s", batch_no, source)
        return None

    mapping = db.get(Mapping, mapping_id)
    if mapping is None:
        raise MappingNotFoundError(f"对照表 {mapping_id} 不存在")

    task = UploadTask(
        batch_no=batch_no,
        status="pending",
        progress_pct=0,
        progress_msg="排队中",
    )
    db.add(task)
    db.flush()

    batch = Batch(
        batch_no=batch_no,
        mapping_id=mapping_id,
        f_start_ghz=f_start_ghz,
        f_end_ghz=f_end_ghz,
        deembedded=bool(deembed),
        deembed_method=deembed_method if deembed else "default",
        process_type=process_type,
        file_path=str(zip_path),
        raw_zip_path=str(zip_path),
        source=source,
        device_count=0,
        task_id=task.id,
        uploaded_by=uploaded_by,
    )
    db.add(batch)
    db.commit()
    db.refresh(task)

    try:
        celery_task_id = _dispatch_chain(
            task,
            zip_path,
            batch_no,
            mapping_id,
            f_start_ghz=f_start_ghz,
            f_end_ghz=f_end_ghz,
            deembed=deembed,
            deembed_method=deembed_method,
            process_type=process_type,
        )
    except Exception as exc:
        task.status = "failed"
        task.error_msg = f"任务投递失败: {exc!s}"
        task.finished_at = datetime.now(UTC)
        db.commit()
        raise TaskDispatchError(f"Celery 任务投递失败: {exc!s}") from exc

    if celery_task_id:
        task.celery_task_id = celery_task_id
        db.commit()

    return task


def ensure_default_mapping(db: Session) -> int | None:
    """返回第一个 mapping 的 id；无 mapping 返回 None。"""
    mapping = db.scalar(select(Mapping).order_by(Mapping.id).limit(1))
    return mapping.id if mapping else None


def settings() -> Settings:
    return get_settings()
