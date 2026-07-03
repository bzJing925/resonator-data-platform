"""桌面模式下在后台线程执行上传处理任务。"""

from __future__ import annotations

import logging
import traceback

from app.db import SessionLocal
from app.workers.compute_batch import compute_batch_task
from app.workers.extract_batch import extract_batch_task
from app.workers.local_queue import get_local_queue
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)


def _run_task(task_id: int, **kwargs) -> None:
    db = SessionLocal()
    try:
        publisher = ProgressPublisher(task_id)
        publisher.start(db, "本地处理开始")

        extract_result = extract_batch_task.apply(
            kwargs={"upload_task_id": task_id, **kwargs}
        ).get()

        compute_result = compute_batch_task.apply(args=[extract_result]).get()

        publisher.done(
            db,
            batch_id=compute_result.get("batch_id"),
            device_count=compute_result.get("device_count", 0),
        )
    except Exception as exc:
        logger.exception("本地任务 %s 失败", task_id)
        try:
            publisher = ProgressPublisher(task_id)
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
        logger.info("本地 worker 开始处理任务 %s", task.task_id)
        _run_task(
            task_id=task.task_id,
            zip_path=str(task.zip_path),
            batch_no=task.batch_no,
            mapping_id=task.mapping_id,
            f_start_ghz=task.f_start_ghz,
            f_end_ghz=task.f_end_ghz,
            deembed_enabled=task.deembed,
            deembed_method=task.deembed_method,
            process_type=task.process_type,
        )
    logger.info("本地 worker 退出")
