"""Thirty-day soft deletion with fresh locked transactions and durable tombstones."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Batch, CleanupJob, ProcessingRun, SyncRevision, Tombstone, UploadTask
from app.services.api_errors import api_error
from app.services.processing_run import _locked_session
from app.services.source_identity import assign_batch_identity, batch_source_ref

logger = logging.getLogger(__name__)
_ACTIVE = ("pending", "running", "cancel_requested")


def _batch(db, name):
    batch = db.scalar(select(Batch).where(Batch.batch_no == name).with_for_update())
    if batch is None:
        raise api_error(404, "SOURCE_NOT_FOUND", "批次不存在")
    return batch


def _active_task(db, batch):
    run = db.scalar(select(ProcessingRun).where(
        ProcessingRun.batch_id == batch.id, ProcessingRun.status.in_(_ACTIVE),
    ))
    task = db.scalar(select(UploadTask).where(
        UploadTask.batch_no == batch.batch_no, UploadTask.status.in_(_ACTIVE),
    ))
    return run.task_id if run else task.id if task else None


def deletion_impact(db, batch):
    return {
        "batch_no": batch.batch_no, "source_ref": batch_source_ref(db, batch),
        "device_count": batch.device_count, "scope": batch.source,
        "raw_input_available": bool(batch.raw_zip_path and Path(batch.raw_zip_path).is_file()),
        "active_task_id": _active_task(db, batch),
        "purge_after": (batch.purge_after or datetime.now(UTC) + timedelta(days=30)).isoformat(),
    }


def _tombstone(db, batch, *, state, now):
    db.flush()  # The business trigger advances the source revision before recording it.
    revision = db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
    if revision is None:
        raise api_error(409, "SOURCE_STATE_CONFLICT", "来源修订不可核验")
    tombstone = db.get(Tombstone, (batch.origin_id, batch.source_batch_id))
    if tombstone is None:
        tombstone = Tombstone(origin_id=batch.origin_id, source_batch_id=batch.source_batch_id,
                              revision=revision, state=state)
        db.add(tombstone)
    tombstone.state, tombstone.revision = state, revision
    if state == "deleted":
        tombstone.deleted_at, tombstone.deleted_revision = now, revision
    else:
        tombstone.restored_at, tombstone.restored_revision = now, revision
    return tombstone


def soft_delete(engine, batch_no, *, expected_revision=None, expected_source=None):
    with _locked_session(engine) as db:
        batch = _batch(db, batch_no)
        if (expected_source is not None
                and (batch.origin_id, batch.source_batch_id) != expected_source):
            raise api_error(409, "SOURCE_STATE_CONFLICT", "来源身份已变化，未操作其他批次")
        if expected_revision is not None:
            revision = db.scalar(select(SyncRevision.revision).where(
                SyncRevision.batch_id == batch.id))
            if revision != expected_revision:
                raise HTTPException(409, {"code": "SOURCE_REVISION_CONFLICT",
                    "message": "源版本已变化，请重新核对", "retryable": False,
                    "context": {"expected_revision": expected_revision, "revision": revision}})
        if batch.source in {"server", "web"}:
            raise api_error(409, "SOURCE_STATE_CONFLICT", "副本请使用本地隐藏；远端删除须独立操作")
        if batch.deleted_at is not None:
            return deletion_impact(db, batch)
        if _active_task(db, batch) is not None or batch.active_run_id is not None:
            raise api_error(409, "SOURCE_STATE_CONFLICT", "处理尚未确认结束，不能删除")
        assign_batch_identity(db, batch)
        now = datetime.now(UTC)
        batch.deleted_at, batch.purge_after = now, now + timedelta(days=30)
        _tombstone(db, batch, state="deleted", now=now)
        impact = deletion_impact(db, batch)
    logger.info("batch_soft_delete source=%s impact=%s", impact["source_ref"], impact)
    return impact


def restore(engine, batch_no, *, expected_revision=None, expected_source=None):
    with _locked_session(engine) as db:
        batch = _batch(db, batch_no)
        if (expected_source is not None
                and (batch.origin_id, batch.source_batch_id) != expected_source):
            raise api_error(409, "SOURCE_STATE_CONFLICT", "来源身份已变化，未操作其他批次")
        if expected_revision is not None:
            revision = db.scalar(select(SyncRevision.revision).where(
                SyncRevision.batch_id == batch.id))
            if revision != expected_revision:
                raise HTTPException(409, {"code": "SOURCE_REVISION_CONFLICT",
                    "message": "源版本已变化，请重新核对", "retryable": False,
                    "context": {"expected_revision": expected_revision, "revision": revision}})
        tombstone = db.get(Tombstone, (batch.origin_id, batch.source_batch_id))
        if tombstone is not None and tombstone.purged_at is not None:
            raise api_error(410, "SOURCE_UNAVAILABLE", "批次已物理清理，无法恢复")
        if batch.deleted_at is None:
            return {"batch_no": batch.batch_no, "source_ref": batch_source_ref(db, batch)}
        # Reserved names are globally unique. Keep an explicit guard for legacy stores.
        collision = db.scalar(select(Batch.id).where(
            Batch.batch_no == batch.batch_no, Batch.id != batch.id,
        ).limit(1))
        if collision is not None:
            raise HTTPException(409, {"code": "RESTORE_NAME_CONFLICT",
                                      "message": "名称已占用，请选择新批次名再恢复",
                                      "retryable": False, "context": {}})
        # A running purge holds the same batch lock. Do not restore its partial files.
        job = db.scalar(select(CleanupJob.id).where(
            CleanupJob.batch_id == batch.id, CleanupJob.resource_kind == "batch",
            CleanupJob.status.in_(["running", "failed"]), CleanupJob.attempts > 0,
        ).limit(1))
        if job is not None:
            raise api_error(409, "SOURCE_STATE_CONFLICT", "正在物理清理，暂不能恢复")
        for pending in db.scalars(select(CleanupJob).where(
            CleanupJob.batch_id == batch.id, CleanupJob.resource_kind == "batch",
            CleanupJob.status.in_(["pending", "failed"]), CleanupJob.attempts == 0,
        )):
            pending.status = "cancelled"
        batch.deleted_at, batch.purge_after = None, None
        _tombstone(db, batch, state="active", now=datetime.now(UTC))
        result = {"batch_no": batch.batch_no, "source_ref": batch_source_ref(db, batch)}
    logger.info("batch_restore source=%s", result["source_ref"])
    return result
