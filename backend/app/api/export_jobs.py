"""Durable full-query export routes, separate from legacy graph-limited exports."""
from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import DbSession
from app.schemas.export_job import ExportCreate
from app.services import export_job_service as jobs
from app.services.api_errors import api_error
from app.workers.dispatch import dispatch_export_job

router = APIRouter(prefix='/export/jobs', tags=['export-jobs'])


@router.post('', status_code=202)
def create(body: ExportCreate, db: DbSession):
    engine = db.bind
    db.rollback()
    result, created, token = jobs.create_job(engine, body)
    if created:
        try:
            dispatch_export_job(result['id'], token)
        except Exception as exc:
            # A transport timeout cannot prove the broker rejected delivery.
            raise api_error(503, 'EXPORT_DISPATCH_UNKNOWN', '投递状态未确认，可用相同请求键查询',
                            context={'id': result['id']}) from exc
    return {'id': result['id'], 'status': result['status'],
            'poll_url': f"/api/export/jobs/{result['id']}"}


@router.get('/{job_id}')
def get(job_id: str, db: DbSession):
    return jobs.get_job(db.bind, job_id)


@router.post('/{job_id}/cancel')
def cancel(job_id: str, db: DbSession):
    db.rollback()
    result = jobs.request_cancel(db.bind, job_id)
    from app.config import get_settings
    if result['status'] == 'cancel_requested' and get_settings().is_desktop:
        from app.workers.local_queue import get_local_queue
        get_local_queue().request_cancel(job_id)
    return result


@router.get('/{job_id}/artifacts/{name}')
def download(job_id: str, name: str, db: DbSession):
    path = jobs.artifact_path(db.bind, job_id, name)
    return FileResponse(path, filename=name)
