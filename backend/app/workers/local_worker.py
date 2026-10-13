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
from app.workers.local_queue import LocalExportTask, LocalPrecheckTask, LocalTask, get_local_queue
from app.workers.progress import ProgressPublisher
from app.workers.reprocess_batch import recompute_batch_task, redeembed_batch_task

logger = logging.getLogger(__name__)


def _run_upload_or_reextract(task: LocalTask) -> None:
    db = SessionLocal()
    try:
        publisher = ProgressPublisher(task.task_id, run_id=task.run_id)
        if task.run_id is None:
            publisher.start(db, "本地处理开始")
        kwargs = {
            "upload_task_id": task.task_id,
            "run_id": task.run_id,
            "lease_token": task.lease_token,
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
        if task.run_id is None:
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
            extracted = redeembed_batch_task.apply(
                kwargs={
                    "upload_task_id": task.task_id,
                    "batch_no": task.batch_no,
                    "run_id": task.run_id,
                    "lease_token": task.lease_token,
                }
            ).get()
            if task.run_id is not None:
                compute_batch_task.apply(args=[extracted]).get()
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
                    "run_id": task.run_id,
                    "lease_token": task.lease_token,
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


def _run_processing(task: LocalTask) -> None:
    """子进程入口：按 kind 分发到执行函数。

    必须是模块级函数（spawn 模式下子进程按「模块.函数名」重新导入定位）。
    remap 功能落地后在此补充分支。
    """
    if task.kind in ("upload", "reextract"):
        _run_upload_or_reextract(task)
    elif task.kind == "redeembed":
        _run_redeembed(task)
    elif task.kind == "table_import":
        from app.workers.precheck import import_prechecked_table_task

        import_prechecked_table_task.apply(args=[task.task_id, task.run_id, task.lease_token]).get(
            propagate=True
        )
    elif task.kind == "recompute":
        _run_recompute(task)
    else:
        logger.error("未知本地任务类型: %s", task.kind)


def _run_precheck(task: LocalPrecheckTask) -> None:
    from app.workers.precheck import precheck_upload_task
    precheck_upload_task.apply(args=[task.draft_id]).get(propagate=True)


def _run_export(task: LocalExportTask) -> None:
    from app.workers.export_job import export_job_task
    export_job_task.apply(args=[task.job_id, task.lease_token]).get(propagate=True)


LOCAL_HANDLERS = {LocalTask: _run_processing, LocalPrecheckTask: _run_precheck,
                  LocalExportTask: _run_export}


def _task_subprocess_target(task: LocalTask | LocalPrecheckTask | LocalExportTask) -> None:
    handler = LOCAL_HANDLERS.get(type(task))
    if handler is None:
        raise ValueError('未知本地任务类型')
    handler(task)


def _mark_task_cancelled(task_id: int | str, process_pid=None) -> None:
    """The caller has joined its owned process; retain every input/result directory."""
    from sqlalchemy import select

    from app.models import ProcessingRun, UploadDraft
    from app.services.processing_cancel import confirm_stopped

    with SessionLocal() as db:
        if isinstance(task_id, str):
            from app.models import ExportJob
            from app.services.export_job_service import confirm_cancelled
            job = db.get(ExportJob, task_id)
            if job is not None:
                engine, token = db.bind, job.lease_token
                db.rollback()
                confirm_cancelled(engine, task_id, token)
                return
            draft = db.get(UploadDraft, task_id)
            if draft and draft.status == "preparing":
                draft.status = "expired"
                draft.report = {**draft.report, "state": "stopped", "progress_pct": 100}
                db.commit()
            return
        run = db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == task_id))
        if run and process_pid is not None and run.dispatch_phase not in {"queued", "finished"}:
            if f"local-{process_pid}" not in run.executor_ids.values():
                return
        engine = db.get_bind()
        run_id, token = (run.id, run.lease_token) if run else (None, None)
    confirm_stopped(engine, task_id, run_id=run_id, lease_token=token)


def local_worker_loop() -> None:
    queue = get_local_queue()
    logger.info("本地 worker 启动")
    while not queue.is_shutdown():
        task = queue.get(timeout=1.0)
        if task is None:
            continue
        if queue.is_cancelled(task.task_id):
            logger.info("本地任务 %s 已被取消，跳过", task.task_id)
            _mark_task_cancelled(task.task_id)
            queue.clear_cancel(task.task_id)
            continue
        logger.info("本地 worker 开始处理任务 %s kind=%s", task.task_id, task.kind)
        process = multiprocessing.Process(target=_task_subprocess_target, args=(task,), daemon=True)
        process.start()
        import time

        from app.config import get_settings
        from app.services.processing_cancel import record_stop_unconfirmed

        cancellation_started = None
        terminated = False
        while process.is_alive():
            process.join(timeout=0.25)
            if queue.is_cancelled(task.task_id) or queue.is_shutdown():
                if cancellation_started is None:
                    cancellation_started = time.monotonic()
                if (
                    process.is_alive()
                    and not terminated
                    and time.monotonic() - cancellation_started
                    >= get_settings().CANCEL_GRACE_SECONDS
                ):
                    process.terminate()
                    terminated = True
                    process.join(timeout=1.0)
                    if process.is_alive() and isinstance(task.task_id, int):
                        with SessionLocal() as db:
                            record_stop_unconfirmed(db.get_bind(), task.task_id)
        # A timed-out join never reaches this confirmation path.
        if queue.is_cancelled(task.task_id):
            _mark_task_cancelled(task.task_id, process_pid=process.pid)
        queue.clear_cancel(task.task_id)
        if queue.is_shutdown():
            break
    logger.info("本地 worker 退出")


def recover_interrupted_tasks() -> int:
    """Recover only native writers whose recorded owned processes are proven dead."""
    import os

    from sqlalchemy import select

    from app.config import get_settings
    from app.models import ProcessingRun, UploadTask
    from app.services.processing_cancel import confirm_stopped
    from app.services.processing_run import RunConflictError, finish_run

    if not get_settings().is_desktop:
        return 0  # An API restart never proves a Celery worker has stopped.

    def alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    with SessionLocal() as db:
        engine = db.get_bind()
        recoverable = []
        runs = db.scalars(
            select(ProcessingRun).where(
                ProcessingRun.status.in_(("pending", "running", "cancel_requested"))
            )
        )
        for run in runs:
            pids = {
                int(value[6:])
                for value in run.executor_ids.values()
                if isinstance(value, str) and value.startswith("local-") and value[6:].isdigit()
            }
            if pids and not any(alive(pid) for pid in pids):
                recoverable.append((run.id, run.task_id, run.lease_token, run.status))
        # Historical entries lacking ownership evidence remain explicitly unknown.
        tasks = db.scalars(
            select(UploadTask).where(
                UploadTask.status.in_(("pending", "running", "cancel_requested")),
                ~select(ProcessingRun.id).where(ProcessingRun.task_id == UploadTask.id).exists(),
            )
        )
        for task in tasks:
            task.error_msg = (
                "STOP_NOT_CONFIRMED"
                if task.status == "cancel_requested"
                else "PROCESS_STATE_UNKNOWN"
            )
            task.progress_msg = "启动恢复：执行器状态尚未确认，输入与结果保留"
        db.commit()
    recovered = 0
    for run_id, task_id, token, status in recoverable:
        try:
            if status == "cancel_requested":
                recovered += int(confirm_stopped(engine, task_id, run_id=run_id, lease_token=token))
            elif status == "running":
                finish_run(engine, run_id, token, "failed", error_code="PROCESS_INTERRUPTED")
                recovered += 1
            # A queued delivery is not rejected merely because its old owner exited.
        except RunConflictError:
            continue
    return recovered
