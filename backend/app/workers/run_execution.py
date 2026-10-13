"""Shared lease entry point for Celery and the native subprocess executor."""

import inspect
import logging
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from app.config import get_settings
from app.services.processing_run import (
    RunConflictError,
    RunTicket,
    adopt_pending_task,
    claim_run,
    finish_run,
    handoff_run,
    heartbeat_run,
)

current_run: ContextVar[RunTicket | None] = ContextVar("current_processing_run", default=None)
logger = logging.getLogger(__name__)


def leased_stage(phase):
    def decorate(fn):
        @wraps(fn)
        def execute(*args, **kwargs):
            from app.db import engine

            binding = inspect.signature(fn).bind(*args, **kwargs)
            bound = binding.arguments
            payload = bound.get("extract_result") or bound
            run_id, token = payload.get("run_id"), payload.get("lease_token")
            if run_id is None:
                task_id = payload.get("upload_task_id") or payload.get("task_id")
                if task_id is None:
                    raise RunConflictError("RUN_LEASE_MISSING", "缺少任务与处理租约")
                ticket = adopt_pending_task(
                    engine,
                    task_id,
                    config={k: v for k, v in payload.items() if k not in {"self", "all_files"}},
                )
                run_id, token = ticket.run_id, ticket.lease_token
                if "extract_result" in bound:
                    bound["extract_result"] = {**payload, "run_id": run_id, "lease_token": token}
                else:
                    bound["run_id"], bound["lease_token"] = run_id, token
                args, kwargs = binding.args, binding.kwargs
            if token is None:
                raise RunConflictError("RUN_LEASE_MISSING", "缺少处理租约")
            from app.services.processing_cancel import acknowledge_cancelled_delivery

            if acknowledge_cancelled_delivery(engine, run_id, token):
                return {"run_id": run_id, "status": "cancelled"}
            task = bound.get("self")
            executor_id = (
                f"local-{os.getpid()}"
                if getattr(get_settings(), "is_desktop", False)
                else getattr(getattr(task, "request", None), "id", None) or f"local-{os.getpid()}"
            )
            ticket = claim_run(engine, run_id, token, phase, executor_id=executor_id)
            try:
                with run_heartbeat(engine, ticket):
                    result = fn(*args, **kwargs)
                if phase == "extract":
                    handoff_run(engine, ticket.run_id, ticket.lease_token)
                    return {**result, "run_id": ticket.run_id, "lease_token": ticket.lease_token}
                finish_run(engine, ticket.run_id, ticket.lease_token, "success")
                return result
            except Exception:
                try:
                    from sqlalchemy.orm import Session

                    from app.models import ProcessingRun

                    with Session(engine) as db:
                        run = db.get(ProcessingRun, ticket.run_id)
                        cancelling = run is not None and run.status == "cancel_requested"
                    finish_run(
                        engine,
                        ticket.run_id,
                        ticket.lease_token,
                        "cancelled" if cancelling else "failed",
                        error_code=None if cancelling else "PROCESSING_FAILED",
                    )
                except RunConflictError:
                    pass  # A late delivery cannot overwrite the current lease or cancellation.
                raise

        return execute

    return decorate


@contextmanager
def run_heartbeat(engine, ticket):
    """Keep the execution lease alive without committing the writer's transaction."""
    from sqlalchemy.orm import Session

    from app.models import ProcessingRun
    from app.services.artifact_lifecycle import acquire_lease, release_lease, renew_lease

    with Session(engine) as db:
        run = db.get(ProcessingRun, ticket.run_id)
        batch_id = run.batch_id
    artifact_lease = acquire_lease(engine, batch_id, "*", "run", ticket.run_id)
    context_token = current_run.set(ticket)
    stop = threading.Event()

    def beat():
        while not stop.wait(get_settings().WORKER_HEARTBEAT_SECONDS):
            try:
                if not renew_lease(engine, artifact_lease, ticket.run_id):
                    logger.error("处理文件租约已过期 run=%s", ticket.run_id)
                    return
                if not heartbeat_run(engine, ticket.run_id, ticket.lease_token):
                    return
            except Exception:
                logger.exception("处理心跳失败 run=%s", ticket.run_id)

    heartbeat = threading.Thread(target=beat, daemon=True, name="aln-run-heartbeat")
    heartbeat.start()
    try:
        yield
    finally:
        stop.set()
        heartbeat.join(timeout=1.0)
        current_run.reset(context_token)
        try:
            release_lease(engine, artifact_lease, ticket.run_id)
        except Exception:
            logger.exception("处理文件租约释放失败 run=%s", ticket.run_id)
