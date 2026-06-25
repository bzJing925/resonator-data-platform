"""Pipeline batch Celery task skeleton.

边解压边计算流水线（streaming pipeline）的 Celery 任务入口。
当前为骨架实现：包含调度判断 should_use_pipeline 和任务签名，
实际流式处理逻辑待后续填充。
"""

from __future__ import annotations

import logging
import shutil
from typing import Any

from celery import Task
from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Batch, Device, Mapping
from app.workers.celery_app import celery_app
from app.workers.pipeline.extractor import zip_contains_calibration
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)


def should_use_pipeline(zip_path: str, deembed: bool) -> bool:
    """判断当前批次是否应使用 pipeline（边解压边计算）链路。

    规则：
    1. 若未启用 deembed，直接返回 False（pipeline 目前只服务于需要
       边解压边去嵌的场景）。
    2. 若 settings.PIPELINE_ENABLED 为 False，返回 False。
    3. 否则检查 zip 内是否包含 OPEN/SHORT 校准件；有则 True，无则 False。
    """
    if not deembed:
        return False
    settings = get_settings()
    if not settings.PIPELINE_ENABLED:
        return False
    return zip_contains_calibration(zip_path)


@celery_app.task(bind=True, name="aln.pipeline_batch")
def pipeline_batch_task(
    self: Task,
    upload_task_id: int,
    zip_path: str,
    batch_no: str,
    mapping_id: int,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed_method: str = "default",
    process_type: str = "AUTO",
) -> dict[str, Any]:
    """Pipeline batch task skeleton.

    参数与 legacy extract_batch / compute_batch 保持一致，
    方便上层 upload API 做统一调度。
    """
    publisher = ProgressPublisher(upload_task_id)
    settings = get_settings()
    db = SessionLocal()

    try:
        publisher.start(db, msg="开始 pipeline 处理…")

        batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        if batch is None:
            raise RuntimeError(f"batches 表无 batch_no={batch_no} 的预占行")

        mapping_row = db.get(Mapping, mapping_id)
        if mapping_row is None:
            raise RuntimeError(f"mappings 表无 id={mapping_id}")

        target_dir = settings.files_dir / batch_no
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # 清理该 batch 旧 devices，防止重复入库
        db.execute(delete(Device).where(Device.batch_id == batch.id))
        db.commit()

        # TODO: 接入 StreamingExtractor + DutProcessor 完成流式处理
        raise NotImplementedError("pipeline_batch_task 流式处理逻辑尚未实现")

    except Exception as exc:
        logger.exception("pipeline_batch_task fatal")
        try:
            db.rollback()
        except Exception:
            pass
        try:
            publisher.fail(db, error_msg=str(exc))
        except Exception:
            logger.exception("publisher.fail itself raised")
        raise
    finally:
        db.close()
