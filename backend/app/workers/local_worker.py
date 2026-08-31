"""桌面模式本地 worker：后台线程派发，独立子进程执行 CPU 密集任务。

重计算/提取是 numpy/scipy 密集负载，若与 FastAPI 同进程跑在线程里会长时间
占满 GIL，导致 /api/health 超时、SSE 断开、界面卡死。因此每个任务放进
multiprocessing 子进程，worker 线程只做 join 轮询与取消监控。
"""

from __future__ import annotations

import logging
import multiprocessing
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


def _task_subprocess_target(task: LocalTask) -> None:
    """子进程入口：按 kind 分发到执行函数。

    必须是模块级函数（spawn 模式下子进程按「模块.函数名」重新导入定位）。
    remap 功能落地后在此补充分支。
    """
    if task.kind in ("upload", "reextract"):
        _run_upload_or_reextract(task)
    elif task.kind == "redeembed":
        _run_redeembed(task)
    elif task.kind == "recompute":
        _run_recompute(task)
    else:
        logger.error("未知本地任务类型: %s", task.kind)


def _mark_task_cancelled(task_id: int) -> None:
    """子进程被 terminate 后来不及自写状态，由 worker 线程补写 cancelled。"""
    db = SessionLocal()
    try:
        ProgressPublisher(task_id).cancel(db, "已取消")
    except Exception:
        logger.exception("补写任务 %s 取消状态失败", task_id)
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
        process = multiprocessing.Process(
            target=_task_subprocess_target, args=(task,), daemon=True
        )
        process.start()
        while process.is_alive():
            process.join(timeout=1.0)
            if queue.is_cancelled(task.task_id):
                logger.info(
                    "本地任务 %s 被取消，终止子进程 pid=%s", task.task_id, process.pid
                )
                process.terminate()
                process.join(timeout=5.0)
                _mark_task_cancelled(task.task_id)
                break
            if queue.is_shutdown():
                logger.info("本地 worker 关闭中，终止任务 %s 子进程", task.task_id)
                process.terminate()
                process.join(timeout=5.0)
                break
        queue.clear_cancel(task.task_id)
        if queue.is_shutdown():
            break
    logger.info("本地 worker 退出")
