"""Owning transactions for persistent, single-consumption upload drafts."""

import hashlib
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import PRECHECK_STAGE_RANGES, get_settings
from app.models import Batch, Mapping, ProcessingRun, ReplicaBatch, UploadDraft, UploadTask
from app.services.processing_run import (
    _locked_session,
    create_run_in_session,
    record_dispatch_failure,
)
from app.services.source_identity import (
    assign_batch_identity,
    batch_source_ref,
    register_prechecked_sources,
)
from app.services.upload_precheck import (
    build_precheck_report,
    calculate_check_digest,
    file_digest,
    validate_upload_options,
)
from app.workers.dispatch import ConfirmedDispatchError, dispatch_batch_task


class DraftError(ValueError):
    def __init__(self, code, message, status=409, context=None):
        self.code, self.status = code, status
        self.context = context or {}
        super().__init__(message)


def _draft(db, draft_id):
    draft = db.scalar(select(UploadDraft).where(UploadDraft.id == draft_id).with_for_update())
    if draft is None:
        raise DraftError("DRAFT_NOT_FOUND", "草稿不存在", 404)
    return draft


def _expired(draft):
    expiry = draft.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry <= datetime.now(UTC) or draft.status == "expired"


def _target_revision(db, batch):
    replica = db.get(ReplicaBatch, batch.id)
    return {
        "batch_id": batch.id,
        "result_revision": batch.result_revision,
        "source_ref": batch_source_ref(db, batch),
        "replica_revision": replica.revision if replica else None,
    }


def prepare_draft(
    engine, path: Path, original_name: str, options: dict, request_key: str, group_id: str
) -> tuple[str, bool]:
    try:
        UUID(group_id)
    except ValueError as exc:
        raise DraftError("INVALID_GROUP_ID", "组标识必须为UUID", 400) from exc
    if not request_key or len(request_key) > 128:
        raise DraftError("INVALID_REQUEST_KEY", "请求标识无效", 400)
    normalized = validate_upload_options(options)
    digest = file_digest(path)
    with _locked_session(engine) as db:
        if engine.dialect.name == "postgresql":
            key = int.from_bytes(hashlib.sha256(request_key.encode()).digest()[:8], signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        previous = db.scalar(
            select(UploadDraft).where(UploadDraft.request_key == request_key).with_for_update()
        )
        if previous:
            if (
                previous.input_digest != digest
                or previous.upload_options != normalized
                or previous.group_id != group_id
            ):
                raise DraftError("REQUEST_KEY_CONFLICT", "请求标识对应的输入或选项不同")
            return previous.id, False
        mapping = db.get(Mapping, normalized.get("mapping_id"))
        if mapping is None:
            raise DraftError("MAPPING_NOT_FOUND", "对照表不存在", 404)
        draft = UploadDraft(
            group_id=group_id,
            request_key=request_key,
            status="preparing",
            storage_path=str(path),
            input_digest=digest,
            original_name=Path(original_name).name,
            upload_options=normalized,
            expires_at=datetime.now(UTC) + timedelta(hours=get_settings().UPLOAD_DRAFT_TTL_HOURS),
            report={"precheck_state": "queued", "prepare_progress": 0},
        )
        db.add(draft)
        db.flush()
        return draft.id, True


def run_precheck(engine, draft_id: str) -> None:
    token = str(uuid4())
    with _locked_session(engine) as db:
        draft = _draft(db, draft_id)
        if draft.status != "preparing" or draft.report.get("precheck_state") != "queued":
            return
        if _expired(draft):
            draft.status = "expired"
            return
        mapping = db.get(Mapping, draft.upload_options["mapping_id"])
        if mapping is None:
            draft.status = "failed"
            draft.report = {"issues": [{"code": "MAPPING_NOT_FOUND", "severity": "fatal"}]}
            return
        storage, mapping_path = Path(draft.storage_path), Path(mapping.file_path)
        options = dict(draft.upload_options)
        draft.report = {"precheck_state": "running", "prepare_progress": 1, "lease_token": token}
        target = (
            options.get("target_batch")
            if storage.suffix.lower() in {".xlsx", ".xls", ".csv"}
            else None
        )
        target_batch = db.scalar(select(Batch).where(Batch.batch_no == target)) if target else None
        target_revision = None
        if target_batch:
            target_revision = _target_revision(db, target_batch)
        if target and (
            target_batch is None
            or target_batch.source != "server"
            or db.get(ReplicaBatch, target_batch.id) is None
        ):
            draft.status = "failed"
            draft.report = {
                "fatal_count": 1,
                "issues": [
                    {
                        "code": "TARGET_VERSION_CONFLICT",
                        "severity": "fatal",
                        "message": "关联目标不是有效服务器副本",
                    }
                ],
            }
            return
    directory = storage.parent / f"inspection-{token}"
    settings = get_settings()
    last_check = last_write = float("-inf")
    last_stage = None

    def checkpoint():
        nonlocal last_check
        now = time.monotonic()
        if now - last_check < settings.PRECHECK_CHECKPOINT_INTERVAL_SECONDS:
            return False
        with Session(engine) as observer:
            state = observer.get(UploadDraft, draft_id)
            if (
                state is None
                or state.status != "preparing"
                or state.report.get("lease_token") != token
                or state.report.get("cancel_requested")
            ):
                raise InterruptedError("precheck stop requested")
        last_check = now
        return False

    def progress(stage, done, total, unit):
        nonlocal last_write, last_stage
        checkpoint()
        now = time.monotonic()
        if (
            stage == last_stage and done != total
            and now - last_write < settings.PRECHECK_PROGRESS_INTERVAL_SECONDS
        ):
            return
        start, end = PRECHECK_STAGE_RANGES[stage]
        fraction = min(1, max(0, done / total)) if total else 0
        with _locked_session(engine) as db:
            state = _draft(db, draft_id)
            if (
                state.status != "preparing" or state.report.get("lease_token") != token
                or state.report.get("cancel_requested")
            ):
                raise InterruptedError("precheck stop requested")
            state.report = {
                **state.report,
                "prepare_progress": max(
                    state.report.get("prepare_progress", 1), int(start + (end - start) * fraction)
                ),
                "prepare_stage": stage, "prepare_completed": done,
                "prepare_total": total, "prepare_unit": unit,
                "prepare_updated_at": datetime.now(UTC).isoformat(),
            }
        last_write, last_stage = now, stage

    try:
        report = build_precheck_report(
            storage,
            directory,
            options,
            mapping_path,
            target_revision=target_revision,
            checkpoint=checkpoint,
            progress_callback=progress,
        )
    except Exception:
        report = {
            "fatal_count": 1,
            "issues": [
                {
                    "code": "PRECHECK_FAILED",
                    "severity": "fatal",
                    "message": "预检查执行失败",
                    "relpath": None,
                }
            ],
            "check_digest": None,
        }
    cancelled = False
    with _locked_session(engine) as db:
        draft = _draft(db, draft_id)
        if draft.status != "preparing" or draft.report.get("lease_token") != token:
            return
        cancelled = draft.report.get("cancel_requested", False)
        if cancelled:
            draft.status = "expired"
            draft.report = {"precheck_state": "stopped", "prepare_progress": 100}
        else:
            draft.status = "ready" if not report.get("fatal_count") else "failed"
            draft.report = {
                **report,
                "precheck_state": "finished",
                "prepare_progress": 100,
                "prepared_dir": str(directory),
                "lease_token": token,
            }
    if cancelled:
        shutil.rmtree(directory, ignore_errors=True)
        storage.unlink(missing_ok=True)


def draft_public(draft, db=None):
    report = {
        key: value
        for key, value in draft.report.items()
        if key not in {"prepared_dir", "lease_token"}
    }
    task = (
        db.get(UploadTask, draft.consumed_task_id)
        if db is not None and draft.consumed_task_id
        else None
    )
    return {
        "draft_id": draft.id,
        "group_id": draft.group_id,
        "status": draft.status,
        "prepare_progress": draft.report.get("prepare_progress", 0),
        "check_digest": draft.report.get("check_digest"),
        "report": report,
        "suggested_batch_no": Path(draft.original_name).stem,
        "expires_at": draft.expires_at,
        "consumed_task_id": draft.consumed_task_id,
        "consumed_task": {
            "task_id": str(task.id),
            "batch_no": task.batch_no,
            "status": task.status,
            "stage": task.stage,
            "progress_pct": task.progress_pct,
        }
        if task
        else None,
    }


def commit_draft(engine, draft_id, request_key, check_digest, warning_codes):
    with _locked_session(engine) as db:
        draft = _draft(db, draft_id)
        if draft.request_key != request_key:
            raise DraftError("REQUEST_KEY_CONFLICT", "请求标识不匹配")
        if draft.status == "consumed":
            task = db.get(UploadTask, draft.consumed_task_id)
            run = db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == task.id))
            return {
                "task_id": str(task.id),
                "batch_no": task.batch_no,
                "run_id": run.id,
                "status": task.status,
                "stream_url": f"/api/tasks/{task.id}/stream",
            }
        if _expired(draft):
            raise DraftError("DRAFT_EXPIRED", "草稿已过期", 410)
        if draft.status != "ready" or draft.report.get("fatal_count"):
            raise DraftError("DRAFT_NOT_READY", "草稿尚未通过预检查", 422)
        options = draft.upload_options
        mapping = db.get(Mapping, options["mapping_id"])
        if mapping is None:
            raise DraftError("PRECHECK_STALE", "对照表已变化")
        storage = Path(draft.storage_path)
        mapping_path = Path(mapping.file_path)
        if not storage.is_file() or not mapping_path.is_file():
            raise DraftError("PRECHECK_STALE", "输入或对照表文件已变化")
        revision = draft.report.get("target_revision")
        if revision:
            target = db.scalar(
                select(Batch).where(Batch.id == revision["batch_id"]).with_for_update()
            )
            if (
                target is None
                or target.deleted_at is not None
                or _target_revision(db, target) != revision
            ):
                raise DraftError("PRECHECK_STALE", "关联目标版本已变化")
        current = calculate_check_digest(
            file_digest(storage), options, file_digest(mapping_path), revision
        )
        if check_digest != draft.report.get("check_digest") or current != check_digest:
            raise DraftError("PRECHECK_STALE", "输入、选项或对照表已变化")
        if not set(draft.report.get("warning_codes", [])) <= set(warning_codes):
            raise DraftError("WARNINGS_NOT_ACKNOWLEDGED", "需确认全部警告", 422)
        base = Path(draft.original_name).stem
        batch_no = base
        while db.scalar(select(Batch.id).where(Batch.batch_no == batch_no)) is not None:
            batch_no = f"{base}-{uuid4().hex[:8]}"
        batch = Batch(
            batch_no=batch_no,
            mapping_id=mapping.id,
            source="http",
            file_path=draft.report["prepared_dir"],
            raw_zip_path=str(storage),
            f_start_ghz=options["f_start_ghz"],
            f_end_ghz=options["f_end_ghz"],
            deembedded=False,
            deembed_method=options["deembed_method"],
            process_type=options["process_type"],
        )
        assign_batch_identity(db, batch)
        db.add(batch)
        db.flush()
        if revision:
            import json

            from app.models import SyncIdentity

            db.add(
                SyncIdentity(
                    key=f"table-target:{batch.id}",
                    value=json.dumps(
                        {"batch_no": target.batch_no, "revision": revision["replica_revision"],
                         "source_ref": batch_source_ref(db, target)}
                    ),
                )
            )
        register_prechecked_sources(db, batch, draft.report)
        ticket = create_run_in_session(
            db,
            batch.id,
            "upload",
            config={
                **options,
                "prepared_dir": draft.report["prepared_dir"],
                "draft_id": draft.id,
                "precheck_report": draft_public(draft)["report"],
                "prepared_file_names": [
                    i["relpath"] for i in draft.report.get("items", []) if i["status"] == "success"
                ],
            },
        )
        draft.status, draft.consumed_task_id = "consumed", ticket.task_id
        mapping_id = mapping.id
    try:
        if storage.suffix.lower() in {".xlsx", ".xls", ".csv"}:
            from app.workers.dispatch import dispatch_prechecked_table

            delivered = dispatch_prechecked_table(ticket.task_id, ticket.run_id, ticket.lease_token)
        else:
            delivered = dispatch_batch_task(
                ticket.task_id,
                storage,
                batch_no,
                mapping_id,
                run_id=ticket.run_id,
                lease_token=ticket.lease_token,
                f_start_ghz=options["f_start_ghz"],
                f_end_ghz=options["f_end_ghz"],
                deembed=options["deembed"],
                deembed_method=options["deembed_method"],
                process_type=options["process_type"],
            )
        if delivered is None:
            raise ConfirmedDispatchError("not enqueued")
    except Exception as exc:
        record_dispatch_failure(engine, ticket, confirmed=isinstance(exc, ConfirmedDispatchError))
        with Session(engine) as observer:
            task = observer.get(UploadTask, ticket.task_id)
            raise DraftError(
                "DISPATCH_FAILED"
                if isinstance(exc, ConfirmedDispatchError)
                else "DISPATCH_OUTCOME_UNKNOWN",
                "任务投递失败，输入与尝试记录已保留",
                503,
                {
                    "task_id": str(task.id),
                    "run_id": ticket.run_id,
                    "batch_no": batch_no,
                    "status": task.status,
                },
            ) from exc
    with Session(engine) as db:
        task = db.get(UploadTask, ticket.task_id)
        return {
            "task_id": str(task.id),
            "batch_no": batch_no,
            "run_id": ticket.run_id,
            "status": task.status,
            "stream_url": f"/api/tasks/{task.id}/stream",
        }


def recheck_draft(engine, draft_id: str, updates: dict):
    with _locked_session(engine) as db:
        draft = _draft(db, draft_id)
        if _expired(draft):
            raise DraftError("DRAFT_EXPIRED", "草稿已过期", 410)
        if draft.status in {"consumed", "preparing"}:
            raise DraftError("DRAFT_BUSY", "草稿正在处理或已消费")
        draft.upload_options = validate_upload_options({**draft.upload_options, **updates})
        draft.status = "preparing"
        draft.report = {"precheck_state": "queued", "prepare_progress": 0}


def record_precheck_dispatch_failure(engine, draft_id: str, *, confirmed: bool):
    with _locked_session(engine) as db:
        draft = _draft(db, draft_id)
        if draft.status != "preparing" or draft.report.get("precheck_state") != "queued":
            return
        draft.report = {
            **draft.report,
            "dispatch_error": {
                "code": "PRECHECK_DISPATCH_FAILED" if confirmed else "DISPATCH_OUTCOME_UNKNOWN",
                "message": "预检查投递失败，输入已保留",
            },
        }
        if confirmed:
            draft.status = "failed"


def delete_draft(engine, draft_id: str):
    storage = directory = None
    with _locked_session(engine) as db:
        draft = _draft(db, draft_id)
        if draft.status == "consumed":
            raise DraftError("DRAFT_CONSUMED", "已消费草稿保留追溯记录")
        if draft.report.get("precheck_state") == "running":
            draft.report = {**draft.report, "cancel_requested": True}
            return {"draft_id": draft.id, "status": "preparing", "stop_confirmed": False}
        storage = Path(draft.storage_path)
        directory = draft.report.get("prepared_dir")
        draft.status = "expired"
        draft.report = {**draft.report, "precheck_state": "stopped"}
    if directory:
        shutil.rmtree(directory, ignore_errors=True)
    storage.unlink(missing_ok=True)
    return {"draft_id": draft_id, "status": "expired", "stop_confirmed": True}
