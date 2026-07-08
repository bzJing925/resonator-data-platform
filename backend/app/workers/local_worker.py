"""桌面模式下在后台线程执行上传处理任务。"""

from __future__ import annotations

import logging
import traceback

from app.db import SessionLocal
from app.workers.cancel import TaskCancelledError
from app.workers.compute_batch import compute_batch_task
from app.workers.extract_batch import extract_batch_task
from app.workers.local_queue import LocalTask, get_local_queue
from app.workers.progress import ProgressPublisher
from app.workers.reprocess_batch import recompute_batch_task, redeembed_batch_task

logger = logging.getLogger(__name__)


def _run_upload_or_reextract(task: LocalTask) -> None:
    db = SessionLocal()
    try:
        publisher = ProgressPublisher(task.task_id)
        publisher.start(db, "本地处理开始")
        kwargs = {
            "upload_task_id": task.task_id,
            "zip_path": str(task.zip_path),
            "batch_no": task.batch_no,
            "mapping_id": task.mapping_id,
            "f_start_ghz": task.f_start_ghz,
            "f_end_ghz": task.f_end_ghz,
            "deembed_enabled": task.deembed,
            "deembed_method": task.deembed_method,
            "process_type": task.process_type,
        }
        extract_result = extract_batch_task.apply(kwargs=kwargs).get()
        compute_result = compute_batch_task.apply(args=[extract_result]).get()
        publisher.done(
            db,
            batch_id=compute_result.get("batch_id"),
            device_count=compute_result.get("device_count", 0),
        )
    except TaskCancelledError:
        logger.info("本地任务 %s 被取消", task.task_id)
        try:
            publisher = ProgressPublisher(task.task_id)
            publisher.cancel(db, "已取消")
        except Exception:
            pass
    except Exception as exc:
        logger.exception("本地任务 %s 失败", task.task_id)
        try:
            publisher = ProgressPublisher(task.task_id)
            publisher.fail(db, f"{exc}\n{traceback.format_exc()}")
        except Exception:
            pass
    finally:
        db.close()


def _run_redeembed(task: LocalTask) -> None:
    db = SessionLocal()
    try:
        try:
            redeembed_batch_task.apply(
                kwargs={"upload_task_id": task.task_id, "batch_no": task.batch_no}
            ).get()
        except TaskCancelledError:
            logger.info("本地重新去嵌任务 %s 被取消", task.task_id)
            try:
                publisher = ProgressPublisher(task.task_id)
                publisher.cancel(db, "已取消")
            except Exception:
                pass
        except Exception as exc:
            logger.exception("本地重新去嵌任务 %s 失败", task.task_id)
            try:
                publisher = ProgressPublisher(task.task_id)
                publisher.fail(db, f"{exc}\n{traceback.format_exc()}")
            except Exception:
                pass
    finally:
        db.close()


def _run_recompute(task: LocalTask) -> None:
    db = SessionLocal()
    try:
        try:
            recompute_batch_task.apply(
                kwargs={
                    "upload_task_id": task.task_id,
                    "batch_no": task.batch_no,
                    "metrics": task.metrics or [],
                }
            ).get()
        except TaskCancelledError:
            logger.info("本地重新计算任务 %s 被取消", task.task_id)
            try:
                publisher = ProgressPublisher(task.task_id)
                publisher.cancel(db, "已取消")
            except Exception:
                pass
        except Exception as exc:
            logger.exception("本地重新计算任务 %s 失败", task.task_id)
            try:
                publisher = ProgressPublisher(task.task_id)
                publisher.fail(db, f"{exc}\n{traceback.format_exc()}")
            except Exception:
                pass
    finally:
        db.close()


def local_worker_loop() -> None:
    queue = get_local_queue()
    logger.info("本地 worker 启动")
    while not queue.is_shutdown():
        task = queue.get(timeout=1.0)
        if task is None:
            continue
        if queue.is_cancelled(task.task_id):
            logger.info("本地任务 %s 已被取消，跳过", task.task_id)
            queue.clear_cancel(task.task_id)
            continue
        logger.info("本地 worker 开始处理任务 %s kind=%s", task.task_id, task.kind)
        if task.kind in ("upload", "reextract"):
            _run_upload_or_reextract(task)
        elif task.kind == "redeembed":
            _run_redeembed(task)
        elif task.kind == "recompute":
            _run_recompute(task)
        else:
            logger.error("未知本地任务类型: %s", task.kind)
        queue.clear_cancel(task.task_id)
    logger.info("本地 worker 退出")
