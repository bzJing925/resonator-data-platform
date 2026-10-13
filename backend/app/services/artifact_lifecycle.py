"""Persist intent before IO; batch locks serialize readers, restore and purge recovery."""
import json
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ArtifactLease,
    Batch,
    CleanupJob,
    Device,
    FileNode,
    ProcessingRun,
    StagedDeviceRow,
    SyncIdentity,
    SyncRevision,
    Tombstone,
    UploadTask,
)
from app.services.api_errors import api_error
from app.services.batch_lifecycle import _active_task
from app.services.processing_run import _locked_session


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _batch(db, batch_id):
    return db.scalar(select(Batch).where(Batch.id == batch_id).with_for_update()
                     .execution_options(populate_existing=True))


def acquire_lease(engine, batch_id, resource_path, purpose, owner_id, *, seconds=None):
    if purpose not in {'run', 'download', 'export'}:
        raise ValueError('unsupported artifact lease purpose')
    with _locked_session(engine) as db:
        batch = _batch(db, batch_id)
        if batch is None or batch.deleted_at is not None:
            raise api_error(410, "SOURCE_UNAVAILABLE", '批次不可读')
        blocked = db.scalar(select(CleanupJob.id).where(
            CleanupJob.batch_id == batch_id, CleanupJob.status.in_(['running', 'failed']),
            CleanupJob.attempts > 0,
        ).limit(1))
        if blocked:
            raise api_error(409, "SOURCE_STATE_CONFLICT", '文件清理中，请稍后重试')
        lease = ArtifactLease(id=str(uuid4()), batch_id=batch_id,
                              resource_path=("*" if resource_path == "*"
                                             else str(Path(resource_path).absolute())),
                              purpose=purpose, owner_id=owner_id,
                              expires_at=datetime.now(UTC) + timedelta(
                                  seconds=seconds or get_settings().ARTIFACT_LEASE_SECONDS))
        db.add(lease)
        return lease.id


def renew_lease(engine, lease_id, owner_id, *, seconds=None):
    with _locked_session(engine) as db:
        lease = db.get(ArtifactLease, lease_id)
        if lease is None or lease.owner_id != owner_id:
            return False
        _batch(db, lease.batch_id)
        if _utc(lease.expires_at) <= datetime.now(UTC):
            return False  # An expired reader cannot revive after a purge obtained the lock.
        lease.expires_at = datetime.now(UTC) + timedelta(
            seconds=seconds or get_settings().ARTIFACT_LEASE_SECONDS)
        return True


def release_lease(engine, lease_id, owner_id):
    with _locked_session(engine) as db:
        db.execute(delete(ArtifactLease).where(ArtifactLease.id == lease_id,
                                              ArtifactLease.owner_id == owner_id))


def _paths(db, batch, job):
    if job.resource_kind != 'batch':
        if not job.resource_path.startswith('{'):
            return [Path(job.resource_path)]
        intent = json.loads(job.resource_path)
        if (intent['origin_id'], intent['source_batch_id']) != (
            batch.origin_id, batch.source_batch_id):
            raise ValueError('PURGE_SOURCE_MISMATCH')
        return [Path(intent['path'])]
    intent = json.loads(job.resource_path)
    if (intent.get("origin_id") != batch.origin_id
            or intent.get("source_batch_id") != batch.source_batch_id):
        raise ValueError("PURGE_SOURCE_MISMATCH")
    paths = intent["paths"]
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError("PURGE_INVALID_PATH_INTENT")
    return [Path(path) for path in paths]


def _batch_path_intent(db, batch):
    runs = db.scalars(select(ProcessingRun).where(ProcessingRun.batch_id == batch.id)).all()
    old_jobs = db.scalars(select(CleanupJob).where(
        CleanupJob.batch_id == batch.id, CleanupJob.resource_kind != "batch",
        CleanupJob.status != "success")).all()
    old_paths = [str(path) for job in old_jobs for path in _paths(db, batch, job)]
    paths = [batch.file_path, batch.raw_zip_path, *(run.stage_dir for run in runs), *old_paths]
    physical = [path for path in paths if path and not (
        batch.source == "table" and str(path).startswith(("table://", "table-sync://")))]
    return json.dumps({"origin_id": batch.origin_id, "source_batch_id": batch.source_batch_id,
        "paths": list(dict.fromkeys(str(Path(path).absolute()) for path in physical))})


def _safe_paths(db, batch, job):
    if (batch.source in {'server', 'web'} or (job.resource_kind != 'batch'
            and not job.resource_path.startswith('{')
            and batch.origin_id != db.get(SyncIdentity, 'server').value)):
        raise ValueError('PURGE_SOURCE_MISMATCH')
    settings = get_settings()
    roots = [settings.files_dir.absolute(), settings.uploads_dir.absolute()]
    checked = []
    for path in _paths(db, batch, job):
        absolute = path.absolute()
        root = next((root for root in roots if absolute != root and absolute.is_relative_to(root)),
                    None)
        if root is None or not absolute.resolve().is_relative_to(root.resolve()):
            raise ValueError('PURGE_PATH_ESCAPE')
        # Never follow a symlink, including one whose target happens to be under the root.
        if any(part.is_symlink() for part in [absolute, *absolute.parents] if part != root):
            raise ValueError('PURGE_PATH_SYMLINK')
        others = db.scalars(select(Batch).where(Batch.id != batch.id)).all()
        for other in others:
            for reference in (other.file_path, other.raw_zip_path):
                if reference and (Path(reference).absolute().is_relative_to(absolute)
                                  or absolute.is_relative_to(Path(reference).absolute())):
                    raise ValueError("PURGE_SHARED_RESOURCE")
        checked.append(absolute)
    return checked


def _has_lease(db, batch_id, paths, now):
    leases = db.scalars(select(ArtifactLease).where(
        ArtifactLease.batch_id == batch_id, ArtifactLease.expires_at > now)).all()
    return any(lease.resource_path == '*' or any(
        Path(lease.resource_path) == path or Path(lease.resource_path).is_relative_to(path)
        or path.is_relative_to(Path(lease.resource_path)) for path in paths) for lease in leases)


def _remove(path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _due(db, batch, job, now):
    if job.resource_kind == "raw_zip":
        latest = db.scalar(select(UploadTask).where(UploadTask.batch_no == batch.batch_no)
                           .order_by(UploadTask.id.desc()).limit(1))
        if latest is not None and latest.status in {"failed", "cancelled"}:
            return False
    if _utc(job.not_before) > now or _active_task_for_job(batch):
        return False
    return job.resource_kind != 'batch' or (
        batch.deleted_at is not None and batch.purge_after is not None
        and _utc(batch.purge_after) <= now)


def _active_task_for_job(batch):
    return batch.active_run_id is not None


def purge_due(engine, *, now=None, limit=100):
    now = now or datetime.now(UTC)
    # New intents use a Batch placeholder so identity/history/name reservation survive cleanup.
    with _locked_session(engine) as db:
        batches = db.scalars(select(Batch).where(
            Batch.deleted_at.is_not(None), Batch.purge_after <= now).with_for_update()).all()
        for batch in batches:
            tombstone = db.get(Tombstone, (batch.origin_id, batch.source_batch_id))
            if tombstone is not None and tombstone.purged_at is None and not db.scalar(
                select(CleanupJob.id).where(CleanupJob.batch_id == batch.id,
                    CleanupJob.resource_kind == 'batch',
                    CleanupJob.status != 'cancelled').limit(1)):
                db.add(CleanupJob(batch_id=batch.id, resource_kind='batch',
                                  resource_path=_batch_path_intent(db, batch),
                                  not_before=batch.purge_after))
    with Session(engine) as db:
        ids = db.scalars(select(CleanupJob.id).where(
            CleanupJob.status.in_(['pending', 'running', 'failed']),
            CleanupJob.not_before <= now).order_by(CleanupJob.created_at).limit(limit)).all()
    result = {'success': 0, 'failed': 0, 'deferred': 0}
    for job_id in ids:
        try:
            # Intent commits before deleting anything. The following transaction keeps the
            # same batch lock across IO; a crashed attempt can safely resume after rollback.
            with _locked_session(engine) as db:
                job = db.get(CleanupJob, job_id)
                batch = _batch(db, job.batch_id)
                if batch is None or job.status == 'success':
                    continue
                paths = _safe_paths(db, batch, job)
                if (not _due(db, batch, job, now) or _active_task(db, batch)
                        or _has_lease(db, batch.id, paths, now)):
                    result['deferred'] += 1
                    continue
                if job.resource_kind == 'old_result' and any(
                    Path(batch.file_path).absolute().is_relative_to(path) for path in paths):
                    raise ValueError('PURGE_ACTIVE_RESULT')
                job.status, job.lease_token = 'running', str(uuid4())
                job.attempts += 1
            with _locked_session(engine) as db:
                job = db.get(CleanupJob, job_id)
                batch = _batch(db, job.batch_id)
                if job.status == 'success':
                    continue
                paths = _safe_paths(db, batch, job)
                if (not _due(db, batch, job, now) or _active_task(db, batch)
                        or _has_lease(db, batch.id, paths, now)):
                    job.status = 'pending'
                    result['deferred'] += 1
                    continue
                for path in paths:
                    _remove(path)
                if job.resource_kind == 'batch':
                    db.execute(delete(FileNode).where(FileNode.batch_id == batch.id))
                    db.execute(delete(Device).where(Device.batch_id == batch.id))
                    run_ids = select(ProcessingRun.id).where(ProcessingRun.batch_id == batch.id)
                    db.execute(delete(StagedDeviceRow).where(StagedDeviceRow.run_id.in_(run_ids)))
                    batch.file_path, batch.raw_zip_path = '', None
                    db.flush()
                    tombstone = db.get(Tombstone, (batch.origin_id, batch.source_batch_id))
                    tombstone.purged_at = now
                    tombstone.revision = db.get(SyncRevision, batch.id).revision
                elif (job.resource_kind == 'raw_zip' and batch.raw_zip_path
                      and Path(batch.raw_zip_path).absolute() == paths[0]):
                    batch.raw_zip_path = None
                job.status, job.error_code, job.finished_at = 'success', None, now
            result['success'] += 1
        except Exception as exc:
            with _locked_session(engine) as db:
                job = db.get(CleanupJob, job_id)
                _batch(db, job.batch_id)
                if job.status != 'success':
                    job.status, job.error_code = 'failed', str(exc)[:300]
            result['failed'] += 1
    return result


def cleanup_path_intent(batch, path):
    return json.dumps({'origin_id': batch.origin_id, 'source_batch_id': batch.source_batch_id,
                       'path': str(Path(path).absolute())})


@contextmanager
def leased_artifacts(engine, batch_id, purpose, owner_id):
    """Long synchronous transfers renew a fresh-session lease while holding source files."""
    import logging
    from threading import Event, Thread

    lease = acquire_lease(engine, batch_id, '*', purpose, owner_id)
    stop = Event()
    def heartbeat():
        while not stop.wait(max(1, get_settings().ARTIFACT_LEASE_SECONDS / 3)):
            try:
                if not renew_lease(engine, lease, owner_id):
                    return
            except Exception:
                logging.getLogger(__name__).exception('文件租约续期失败 owner=%s', owner_id)
    thread = Thread(target=heartbeat, daemon=True, name='aln-artifact-heartbeat')
    thread.start()
    try:
        yield lease
    finally:
        stop.set()
        thread.join(timeout=1)
        release_lease(engine, lease, owner_id)
