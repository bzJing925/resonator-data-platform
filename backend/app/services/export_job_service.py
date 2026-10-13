"""Independent durable export ownership; terminal artifacts are never partial files."""
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, text, tuple_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Batch, DataEpoch, ExportJob
from app.schemas.result_manifest import ResultManifest
from app.services.api_errors import api_error
from app.services.batch_scope import active_batch_clause
from app.services.export_query import DEFAULT_EXPORT_FIELDS
from app.services.processing_run import _locked_session
from app.services.query_manifest import matching_sources, scope_version
from app.services.query_spec import _build_filters, query_spec
from app.services.task_state import can_transition

TERMINAL = {'success', 'failed', 'cancelled'}


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _job(db, job_id):
    try:
        UUID(job_id)
    except (ValueError, TypeError):
        raise api_error(404, 'EXPORT_NOT_FOUND', '导出不存在') from None
    job = db.scalar(select(ExportJob).where(ExportJob.id == job_id).with_for_update())
    if job is None:
        raise api_error(404, 'EXPORT_NOT_FOUND', '导出不存在')
    return job


def _live(job, token):
    return (job.lease_token == token and job.lease_expires_at is not None
            and utc(job.lease_expires_at) > datetime.now(UTC))


def serialize(job):
    return {'id': job.id, 'status': job.status, 'progress': {'pct': job.progress_pct},
        'progress_pct': job.progress_pct, 'rows': job.rows, 'manifest': job.manifest,
        'error': job.error, 'artifacts': [{**item,
            'download_url': f"/api/export/jobs/{job.id}/artifacts/{item['name']}"}
            for item in job.artifacts], 'expires_at': utc(job.expires_at).isoformat()}


def create_job(engine, request):
    spec = query_spec(request.query_spec.filters,
        request.query_spec.fields or DEFAULT_EXPORT_FIELDS, request.query_spec.order_by)
    body = {**request.model_dump(mode='json'), 'query_spec': spec.model_dump(mode='json')}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
    with _locked_session(engine) as db:
        if engine.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
                       {'key': 'export:' + request.request_key})
        old = db.scalar(select(ExportJob).where(ExportJob.request_key == request.request_key))
        if old is not None:
            if old.request_digest != digest:
                raise api_error(409, 'EXPORT_REQUEST_CONFLICT', '请求键已用于不同导出')
            return serialize(old), False, old.lease_token
        sources, _verified = matching_sources(db,
            [active_batch_clause(), *_build_filters(spec.filters)])
        version, scope_digest = scope_version(db)
        if version is None or any(ref is None or ref['source_revision'] is None
                                  for ref in sources.values()):
            raise api_error(409, 'SOURCE_NOT_READY', '来源初始化未完成，不能全量导出')
        current = {(ref['origin_id'], ref['source_batch_id']): ref for ref in sources.values()}
        for ref in request.expected_sources:
            if current.get((str(ref.origin_id), str(ref.source_batch_id))) != ref.model_dump(
                    mode='json'):
                raise api_error(409, 'SOURCE_CHANGED', '预期来源与筛选快照不一致')
        now = datetime.now(UTC)
        job = ExportJob(request_key=request.request_key, request_digest=digest,
            format=request.format, query_spec=spec.model_dump(mode='json'),
            expected_sources=list(current.values()), source_epoch=version[1],
            scope_digest=scope_digest, lease_token=str(uuid4()),
            lease_expires_at=now + timedelta(seconds=get_settings().EXPORT_LEASE_SECONDS),
            expires_at=now + timedelta(hours=get_settings().EXPORT_TTL_HOURS))
        db.add(job)
        db.flush()
        return serialize(job), True, job.lease_token


def get_job(engine, job_id):
    with Session(engine) as db:
        return serialize(_job(db, job_id))


def mark_dispatched(engine, job_id, token, executor_id):
    with _locked_session(engine) as db:
        job = _job(db, job_id)
        if job.lease_token == token and job.status == 'pending':
            job.executor_id = executor_id


def claim_job(engine, job_id, token, executor_id):
    with _locked_session(engine) as db:
        job = _job(db, job_id)
        if not _live(job, token):
            return None
        if job.status == 'cancel_requested':
            job.status, job.finished_at = 'cancelled', datetime.now(UTC)
            job.cancelled_at = job.finished_at
            return None
        if job.status != 'pending':
            return None
        job.status, job.executor_id = 'running', executor_id
        job.heartbeat_at = datetime.now(UTC)
        job.lease_expires_at = job.heartbeat_at + timedelta(
            seconds=get_settings().EXPORT_LEASE_SECONDS)
        return {'id': job.id, 'format': job.format, 'query_spec': job.query_spec,
                'expected_sources': job.expected_sources, 'source_epoch': job.source_epoch,
                'scope_digest': job.scope_digest}


def heartbeat(engine, job_id, token, rows=None, progress=None):
    with _locked_session(engine) as db:
        job = _job(db, job_id)
        if not _live(job, token) or job.status != 'running':
            return False
        job.heartbeat_at = datetime.now(UTC)
        job.lease_expires_at = job.heartbeat_at + timedelta(
            seconds=get_settings().EXPORT_LEASE_SECONDS)
        if rows is not None:
            job.rows = max(job.rows, rows)
        if progress is not None:
            job.progress_pct = min(99, max(job.progress_pct, progress))
        return True


def request_cancel(engine, job_id):
    with _locked_session(engine) as db:
        job = _job(db, job_id)
        if job.status in {'pending', 'running'}:
            job.status = 'cancel_requested'
        return serialize(job)


def finish_job(engine, job_id, token, status, *, manifest=None, artifacts=None, error=None):
    with _locked_session(engine) as db:
        job = _job(db, job_id)
        if not _live(job, token):
            return False
        if job.status in TERMINAL:
            return job.status == status
        if job.status == 'cancel_requested':
            status, manifest, artifacts, error = 'cancelled', None, [], None
        if not can_transition(job.status, status):
            return False
        if status == 'success':
            from app.services.query_snapshot import ExportWriteError
            if engine.dialect.name == 'postgresql':
                db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
                           {'key': 'aln-query-scope'})
            source_ids = list(db.scalars(select(Batch.id).where(
                tuple_(Batch.origin_id, Batch.source_batch_id).in_([
                    (ref['origin_id'], ref['source_batch_id']) for ref in job.expected_sources]))
                .order_by(Batch.id).with_for_update()))
            db.scalar(select(DataEpoch).where(DataEpoch.id == 1).with_for_update())
            version, preferences = scope_version(db)
            sources, _verified = matching_sources(db,
                [active_batch_clause(), *_build_filters(job.query_spec['filters'])])
            actual = sorted((ref for ref in sources.values() if ref),
                key=lambda ref: (ref['origin_id'], ref['source_batch_id']))
            expected = sorted(job.expected_sources,
                key=lambda ref: (ref['origin_id'], ref['source_batch_id']))
            if (version is None or version[1] != job.source_epoch
                    or preferences != job.scope_digest or actual != expected
                    or len(source_ids) != len(expected)):
                raise ExportWriteError('SOURCE_CHANGED')
            if manifest is None or not artifacts:
                raise ValueError('成功导出需要完整manifest和文件')
            checked = ResultManifest.model_validate(manifest)
            json.dumps(manifest, allow_nan=False)
            directory = get_settings().exports_dir / job.id
            for artifact in artifacts:
                name = artifact['name']
                if Path(name).name != name or name.endswith('.part'):
                    raise ValueError('产物名不安全')
                path = directory / name
                if (not path.is_file() or path.is_symlink()
                        or path.stat().st_size != artifact['bytes']):
                    raise ValueError('产物尚未完整落盘')
            job.manifest, job.artifacts = manifest, artifacts
            job.rows = checked.returned
            job.progress_pct = 100
        job.status, job.error, job.finished_at = status, error, datetime.now(UTC)
        if status == 'cancelled':
            job.cancelled_at = job.finished_at
        return True


def artifact_path(engine, job_id, name):
    with Session(engine) as db:
        job = _job(db, job_id)
        if job.status != 'success':
            raise api_error(409, 'EXPORT_NOT_READY', '导出尚未成功')
        if utc(job.expires_at) <= datetime.now(UTC):
            raise api_error(410, 'EXPORT_EXPIRED', '导出已过期')
        if name not in {item['name'] for item in job.artifacts} or Path(name).name != name:
            raise api_error(404, 'EXPORT_ARTIFACT_NOT_FOUND', '产物不存在')
        path = get_settings().exports_dir / job.id / name
        if not path.is_file() or path.is_symlink():
            raise api_error(410, 'EXPORT_ARTIFACT_MISSING', '产物已不存在')
        return path


def recover_exports(engine, *, stopped_executor_ids=frozenset()):
    """A native dead PID is evidence; an API restart alone is not Celery evidence."""
    recovered = 0
    with _locked_session(engine) as db:
        for job in db.scalars(select(ExportJob).where(
                ExportJob.status.in_(('pending', 'running', 'cancel_requested')))
                .with_for_update()):
            stopped = job.executor_id in stopped_executor_ids if job.executor_id else False
            if job.executor_id and job.executor_id.startswith(('local-', 'native-queue-')):
                pid = job.executor_id.rsplit('-', 1)[-1]
                if pid.isdigit():
                    try:
                        os.kill(int(pid), 0)
                    except ProcessLookupError:
                        stopped = True
                    except PermissionError:
                        pass
            if not stopped:
                continue
            job.lease_token = str(uuid4())
            if job.status == 'pending':
                job.status = 'running'
                db.flush()  # Preserve the shared C02 graph, even for lost queue ownership.
            if job.status == 'cancel_requested':
                job.status, job.cancelled_at = 'cancelled', datetime.now(UTC)
            else:
                job.status = 'failed'
                job.error = {'code': 'EXPORT_INTERRUPTED', 'message': '旧执行器已停止，导出中断',
                             'retryable': False, 'context': {}}
            job.finished_at = datetime.now(UTC)
            recovered += 1
    return recovered


def confirm_cancelled(engine, job_id, token):
    """Call only after queue removal, owned process join, or cooperative writer exit."""
    with _locked_session(engine) as db:
        job = _job(db, job_id)
        if job.lease_token != token or job.status != 'cancel_requested':
            return False
        job.status, job.finished_at = 'cancelled', datetime.now(UTC)
        job.cancelled_at = job.finished_at
        job.lease_token = str(uuid4())
        return True
