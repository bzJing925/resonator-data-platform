"""Cancellation intent is durable; confirmation requires executor evidence."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, ProcessingRun, UploadTask
from app.services.processing_run import (
    RunConflictError,
    _locked_session,
    _owned_run,
    finish_run,
)
from app.services.task_state import transition_task


def request_cancellation(engine, task_id):
    with _locked_session(engine) as db:
        task = db.get(UploadTask, task_id)
        if task is None:
            raise RunConflictError("TASK_MISSING", "任务不存在")
        db.scalar(select(Batch).where(Batch.batch_no == task.batch_no).with_for_update())
        db.refresh(task)
        run = db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == task_id))
        prior_status = task.status
        if run is not None:
            run, _batch = (
                _owned_run(db, run.id, run.lease_token)
                if run.status not in {"success", "failed", "cancelled"}
                else (run, None)
            )
        if task.status in {"success", "failed"}:
            raise RunConflictError("TASK_FINISHED", "任务已结束，无法取消")
        if task.status != "cancelled" and task.status != "cancel_requested":
            if run:
                if (
                    transition_task(
                        db, task.id, run.id, run.lease_token, run.status, "cancel_requested"
                    )
                    != "changed"
                ):
                    raise RunConflictError("CANCEL_CONFLICT", "任务状态已变化")
            else:
                task.status = "cancel_requested"  # Historical task has no run to synchronize.
            task.cancelled_at = datetime.now(UTC)
            task.progress_msg = "已请求停止，等待执行器确认"
        return {
            "task_id": task.id,
            "run_id": run.id if run else None,
            "lease_token": run.lease_token if run else None,
            "status": task.status,
            "prior_status": prior_status,
            "dispatch_phase": run.dispatch_phase if run else None,
            "executor_ids": dict(run.executor_ids) if run else {},
            "celery_task_id": task.celery_task_id,
        }


def confirm_stopped(engine, task_id, *, run_id=None, lease_token=None):
    """Called only after queue removal, quiescent delivery, or a joined owned process."""
    if run_id:
        try:
            finish_run(engine, run_id, lease_token, "cancelled")
            from app.workers.progress import ProgressPublisher

            try:
                ProgressPublisher(task_id, run_id=run_id, lease_token=lease_token)._publish(
                    {"task_id": task_id, "event": "cancelled", "status": "cancelled"}
                )
            except Exception:
                import logging

                logging.getLogger(__name__).exception("停止已确认，通知发送失败")
            return True
        except RunConflictError:
            return False
    with _locked_session(engine) as db:
        task = db.get(UploadTask, task_id)
        if task is None or task.status not in {"cancel_requested", "cancelled"}:
            return False
        task.status, task.stage = "cancelled", "failed"
        task.finished_at = task.finished_at or datetime.now(UTC)
        task.progress_msg = "已确认停止，输入和活动结果保留"
        return True


def acknowledge_cancelled_delivery(engine, run_id, lease_token):
    with Session(engine) as db:
        run = db.get(ProcessingRun, run_id)
        eligible = (
            run is not None
            and run.lease_token == lease_token
            and run.status == "cancel_requested"
            and run.dispatch_phase in {"queued", "extract_handoff"}
        )
        task_id = run.task_id if run else None
    if eligible:
        return confirm_stopped(engine, task_id, run_id=run_id, lease_token=lease_token)
    return False


def record_stop_unconfirmed(engine, task_id):
    from app.services.processing_report import record_issue

    with Session(engine) as db:
        task = db.get(UploadTask, task_id)
        if task is None or task.status != "cancel_requested":
            return
        task.error_msg = "STOP_NOT_CONFIRMED"
        task.progress_msg = "停止尚未确认，输入与暂存文件保留"
        run = db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == task_id))
        if run:
            run.summary_json = {**run.summary_json, "stop_error": "STOP_NOT_CONFIRMED"}
            record_issue(db, run.id, "STOP_NOT_CONFIRMED", "执行器停止尚未确认，文件保留")
        db.commit()
