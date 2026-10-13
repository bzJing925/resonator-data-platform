"""Fresh-session task admission, execution leases and atomic stage handoff.

No filesystem mutation occurs here. Callers persist input before dispatch and
must not hold an uncommitted transaction while invoking these owning services.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.models import Batch, Device, ProcessingRun, SyncRevision, UploadTask
from app.services.source_identity import assign_batch_identity
from app.services.task_state import transition_task


class RunConflictError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    def __reduce__(self):
        # Celery pickles exceptions before returning them across worker boundaries.
        # Keep RuntimeError's readable message while reconstructing both arguments.
        return type(self), (self.code, str(self))


@dataclass(frozen=True)
class RunTicket:
    run_id: str
    task_id: int
    batch_id: int
    lease_token: str


@contextmanager
def _locked_session(engine):
    with Session(engine) as db:
        if engine.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def _locked_batch(db: Session, batch_id: int) -> Batch:
    stmt = select(Batch).where(Batch.id == batch_id).with_for_update()
    batch = db.scalar(stmt.execution_options(populate_existing=True))
    if batch is None or batch.deleted_at is not None:
        raise RunConflictError("BATCH_UNAVAILABLE", "批次不存在或已删除")
    return batch


def create_run(engine, batch_id: int, kind: str, *, config: dict | None = None) -> RunTicket:
    with _locked_session(engine) as db:
        return create_run_in_session(db, batch_id, kind, config=config)


def create_run_in_session(
    db: Session,
    batch_id: int,
    kind: str,
    *,
    config: dict | None = None,
    existing_pending_task: UploadTask | None = None,
) -> RunTicket:
    """Compose admission with draft consumption in the caller's locked transaction."""
    batch = _locked_batch(db, batch_id)
    if existing_pending_task is not None:
        db.refresh(existing_pending_task)
        if (
            existing_pending_task.status != "pending"
            or existing_pending_task.cancelled_at is not None
        ):
            raise RunConflictError("TASK_ALREADY_STARTED", "历史任务已启动或已请求停止")
    old_task = db.get(UploadTask, batch.task_id) if batch.task_id else None
    if batch.active_run_id is not None or (
        old_task
        and old_task.status in {"pending", "running", "cancel_requested"}
        and old_task is not existing_pending_task
    ):
        raise RunConflictError("BATCH_BUSY", "该批次已有活动处理")
    if kind not in {"upload", "reextract", "redeembed", "recompute", "remap"}:
        raise ValueError("unknown processing kind")
    assign_batch_identity(db, batch)
    if batch.source not in {"server", "web"}:
        for device in db.scalars(
            select(Device).where(Device.batch_id == batch.id, Device.source_device_key.is_(None))
        ).yield_per(500):
            device.source_device_key = str(uuid4())
    db.flush()  # Identity registration/backfill precedes base revision capture.
    task = existing_pending_task or UploadTask(
        batch_no=batch.batch_no,
        kind=kind,
        progress_msg="排队中",
        stage={"redeembed": "deembed", "recompute": "metrics"}.get(kind, "extract"),
    )
    db.add(task)
    db.flush()
    from pathlib import Path

    from app.models import Mapping
    from app.services.upload_precheck import file_digest

    mapping = db.get(Mapping, batch.mapping_id)
    mapping_digest = (
        file_digest(Path(mapping.file_path))
        if mapping and Path(mapping.file_path).is_file()
        else "missing"
    )
    from app.services.processing_retry import (
        algorithm_digest,
        configuration_digest,
        directory_digest,
    )

    config = {
        "f_start_ghz": batch.f_start_ghz, "f_end_ghz": batch.f_end_ghz,
        "deembed": batch.deembedded, "deembed_method": batch.deembed_method,
        "process_type": batch.process_type, **(config or {}),
    }
    config["accepted_config_digest"] = configuration_digest(config)
    prepared = config.get("prepared_dir")
    if prepared and (Path(prepared) / "table-rows.json").is_file():
        config["prepared_rows_digest"] = file_digest(Path(prepared) / "table-rows.json")

    input_digest = config.get("precheck_report", {}).get("input_digest")
    raw_path = Path(batch.raw_zip_path) if batch.raw_zip_path else None
    if input_digest or kind in {"upload", "reextract"} and raw_path and raw_path.is_file():
        config["input_digest_kind"] = "archive"
        input_digest = input_digest or file_digest(raw_path)
    else:
        config["input_digest_kind"] = "directory"
        input_digest = directory_digest(batch.file_path)
    run = ProcessingRun(
        task_id=task.id,
        batch_id=batch.id,
        kind=kind,
        config_json={
            **(config or {}),
            "base_has_results": bool(
                db.scalar(select(Device.id).where(Device.batch_id == batch.id).limit(1))
            ),
        },
        input_digest=input_digest,
        algorithm_digest=algorithm_digest(),
        mapping_digest=(config or {}).get("precheck_report", {}).get("mapping_digest")
        or mapping_digest,
        base_result_revision=batch.result_revision,
        base_source_revision=db.scalar(
            select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id)
        ),
        lease_token=str(uuid4()),
    )
    db.add(run)
    db.flush()
    batch.task_id, batch.active_run_id = task.id, run.id
    from app.services.processing_report import seed_precheck_report, summarize_run

    seed_precheck_report(db, run)
    summarize_run(db, run.id)
    return RunTicket(run.id, task.id, batch.id, run.lease_token)


def adopt_pending_task(engine, task_id, *, config=None):
    """Attach an untouched legacy admission to a run; never reset an existing attempt."""
    with _locked_session(engine) as db:
        task = db.get(UploadTask, task_id)
        if task is None:
            raise RunConflictError("TASK_MISSING", "任务不存在")
        existing = db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == task_id))
        if existing:
            return RunTicket(existing.id, task.id, existing.batch_id, existing.lease_token)
        if task.status != "pending" or task.cancelled_at is not None:
            raise RunConflictError("TASK_ALREADY_STARTED", "历史任务已启动或已结束")
        batch = db.scalar(select(Batch).where(Batch.batch_no == task.batch_no))
        if batch is None:
            raise RunConflictError("BATCH_MISSING", "批次不存在")
        return create_run_in_session(
            db, batch.id, task.kind, config=config, existing_pending_task=task
        )


def _owned_run(db: Session, run_id: str, token: str) -> tuple[ProcessingRun, Batch]:
    # Batch lock is always acquired before run lock, including stage handoff.
    batch_id = db.scalar(select(ProcessingRun.batch_id).where(ProcessingRun.id == run_id))
    if batch_id is None:
        raise RunConflictError("RUN_MISSING", "处理记录不存在")
    batch = _locked_batch(db, batch_id)
    run = db.scalar(
        select(ProcessingRun)
        .where(ProcessingRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if batch.active_run_id != run_id or run.lease_token != token:
        raise RunConflictError("RUN_LEASE_LOST", "处理租约已失效")
    return run, batch


def claim_run(
    engine, run_id: str, token: str, phase: str, *, executor_id: str | None = None
) -> RunTicket:
    if phase not in {"extract", "compute"}:
        raise ValueError("unknown execution phase")
    with _locked_session(engine) as db:
        run, batch = _owned_run(db, run_id, token)
        if run.status == "pending" and run.dispatch_phase == "queued":
            if transition_task(db, run.task_id, run.id, token, "pending", "running") != "changed":
                raise RunConflictError("RUN_CLAIM_CONFLICT", "任务状态已变化")
        elif not (
            phase == "compute"
            and run.status == "running"
            and run.dispatch_phase == "extract_handoff"
        ):
            raise RunConflictError("RUN_ALREADY_CLAIMED", "当前阶段已领取或已结束")
        run.lease_token = str(uuid4())
        run.dispatch_phase = phase
        run.heartbeat_at = datetime.now(UTC)
        if executor_id is not None:
            run.executor_ids = {**run.executor_ids, phase: executor_id}
        db.flush()
        return RunTicket(run.id, run.task_id, batch.id, run.lease_token)


def assert_run_writable(db: Session, run_id: str, token: str) -> None:
    valid = db.scalar(
        select(ProcessingRun.id)
        .join(Batch, Batch.id == ProcessingRun.batch_id)
        .join(UploadTask, UploadTask.id == ProcessingRun.task_id)
        .where(
            ProcessingRun.id == run_id,
            ProcessingRun.lease_token == token,
            ProcessingRun.status == "running",
            UploadTask.status == "running",
            Batch.active_run_id == run_id,
            Batch.deleted_at.is_(None),
        )
    )
    if valid is None:
        raise RunConflictError("RUN_LEASE_LOST", "处理租约已失效或任务已请求停止")


def fence_run_writes(db: Session, run_id: str, token: str) -> None:
    """Hold the run write lock until the chunk commits; recheck the lease atomically."""
    active = (
        select(Batch.id)
        .where(
            Batch.id == ProcessingRun.batch_id,
            Batch.active_run_id == ProcessingRun.id,
            Batch.deleted_at.is_(None),
            UploadTask.id == ProcessingRun.task_id,
            UploadTask.status == "running",
        )
        .exists()
    )
    result = db.execute(
        update(ProcessingRun)
        .where(
            ProcessingRun.id == run_id,
            ProcessingRun.lease_token == token,
            ProcessingRun.status == "running",
            active,
        )
        .values(heartbeat_at=ProcessingRun.heartbeat_at)
    )
    if result.rowcount != 1:
        raise RunConflictError("RUN_LEASE_LOST", "写入时处理租约已失效或已请求停止")


def heartbeat_run(engine, run_id: str, token: str) -> bool:
    with Session(engine) as db:
        valid = (
            select(Batch.id)
            .where(
                Batch.id == ProcessingRun.batch_id,
                Batch.active_run_id == run_id,
                Batch.deleted_at.is_(None),
            )
            .exists()
        )
        result = db.execute(
            update(ProcessingRun)
            .where(
                ProcessingRun.id == run_id,
                ProcessingRun.lease_token == token,
                ProcessingRun.status == "running",
                valid,
            )
            .values(heartbeat_at=datetime.now(UTC))
        )
        db.commit()
        return result.rowcount == 1


def handoff_run(engine, run_id: str, token: str) -> None:
    with _locked_session(engine) as db:
        run, _batch = _owned_run(db, run_id, token)
        if run.status != "running" or run.dispatch_phase != "extract":
            raise RunConflictError("RUN_HANDOFF_CONFLICT", "当前阶段不能交接")
        run.dispatch_phase = "extract_handoff"


def finish_run(
    engine,
    run_id: str,
    token: str,
    status: str,
    *,
    error_code: str | None = None,
    confirmed_dispatch_failure: bool = False,
) -> None:
    if status not in {"success", "failed", "cancelled"}:
        raise ValueError("finish_run requires a terminal status")
    with _locked_session(engine) as db:
        existing = db.get(ProcessingRun, run_id)
        if existing is not None and existing.status == status and existing.lease_token == token:
            task = db.get(UploadTask, existing.task_id)
            if task is not None and task.status == status:
                return
        run, batch = _owned_run(db, run_id, token)
        dispatch_failed = confirmed_dispatch_failure and error_code == "DISPATCH_FAILED"
        if confirmed_dispatch_failure and (
            not dispatch_failed
            or status != "failed"
            or run.status != "pending"
            or run.dispatch_phase != "queued"
        ):
            raise RunConflictError(
                "DISPATCH_FAILURE_UNCONFIRMED", "不能结束已领取或未确认失败的投递"
            )
        result = transition_task(
            db,
            run.task_id,
            run.id,
            token,
            run.status,
            status,
            confirmed_dispatch_failure=dispatch_failed,
        )
        if result == "conflict":
            raise RunConflictError("RUN_FINISH_CONFLICT", "任务状态不允许结束")
        finished = datetime.now(UTC)
        from app.services.processing_report import summarize_run

        if status in {"failed", "cancelled"}:
            from app.models import ProcessingItem
            from app.services.processing_report import record_issue

            for item in db.scalars(
                select(ProcessingItem).where(
                    ProcessingItem.run_id == run.id,
                    ProcessingItem.status.in_(("pending", "running")),
                )
            ):
                item.status = "cancelled" if status == "cancelled" else "failed"
                item.error_code = error_code or "PROCESSING_INTERRUPTED"
                record_issue(
                    db,
                    run.id,
                    item.error_code,
                    "处理未完成，输入保留供检查",
                    item_id=item.id,
                    details={"source_member": item.source_member},
                )
        summarize_run(db, run.id)
        run.finished_at = finished
        run.dispatch_phase = "finished"
        if error_code:
            run.summary_json = {**run.summary_json, "error_code": error_code}
        task = db.get(UploadTask, run.task_id)
        task.finished_at, task.stage = finished, "done" if status == "success" else "failed"
        if status == "success":
            task.progress_pct = task.stage_progress_pct = 100
        if status == "cancelled":
            task.progress_msg = "已确认停止，输入和活动结果保留"
            run.summary_json = {**run.summary_json, "stop_confirmed": True}
        task.error_msg = error_code
        batch.active_run_id = None


def record_dispatch_failure(engine, ticket: RunTicket, *, confirmed: bool) -> None:
    if confirmed:
        finish_run(
            engine,
            ticket.run_id,
            ticket.lease_token,
            "failed",
            error_code="DISPATCH_FAILED",
            confirmed_dispatch_failure=True,
        )
        return
    with _locked_session(engine) as db:
        run = db.get(ProcessingRun, ticket.run_id)
        if run is None or run.lease_token != ticket.lease_token:
            return  # An uncertain publisher outcome cannot overwrite a claimed delivery.
        if run.status != "pending" or run.dispatch_phase != "queued":
            return
        run.summary_json = {**run.summary_json, "error_code": "DISPATCH_OUTCOME_UNKNOWN"}
        db.get(UploadTask, run.task_id).error_msg = "投递结果不确定；保留输入与任务记录"
