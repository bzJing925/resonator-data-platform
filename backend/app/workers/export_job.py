"""Both executors claim the same durable export lease before invoking the writer."""
import logging
import os
import threading

from app.config import get_settings
from app.services import export_job_service as jobs
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def execute_export(engine, job_id, token, executor_id, writer):
    ticket = jobs.claim_job(engine, job_id, token, executor_id)
    if ticket is None:
        return {'id': job_id, 'claimed': False}
    stop, lost = threading.Event(), threading.Event()
    artifacts = []
    published = False

    def beat():
        while not stop.wait(get_settings().WORKER_HEARTBEAT_SECONDS):
            try:
                if not jobs.heartbeat(engine, job_id, token):
                    lost.set()
                    return
            except Exception:
                lost.set()
                logger.exception('导出心跳失败 id=%s', job_id)
                return

    thread = threading.Thread(target=beat, daemon=True, name='aln-export-heartbeat')
    thread.start()
    try:
        manifest, artifacts = writer(engine, ticket, token, lost)
        published = jobs.finish_job(engine, job_id, token, 'success',
                                   manifest=manifest, artifacts=artifacts)
        published = published and jobs.get_job(engine, job_id)['status'] == 'success'
        return {'id': job_id, 'published': published}
    except Exception as exc:
        code = getattr(exc, 'code', 'EXPORT_FAILED')
        jobs.finish_job(engine, job_id, token, 'failed', error={
            'code': code, 'message': '导出未完成', 'retryable': False, 'context': {}})
        logger.exception('导出失败 id=%s code=%s', job_id, code)
        return {'id': job_id, 'published': False, 'error_code': code}
    finally:
        stop.set()
        thread.join(timeout=1)
        if not published:
            from pathlib import Path
            for artifact in artifacts:
                if Path(artifact['name']).name == artifact['name']:
                    (get_settings().exports_dir / job_id / artifact['name']).unlink(missing_ok=True)


@celery_app.task(bind=True, name='app.workers.export_job.export_job_task')
def export_job_task(self, job_id, lease_token):
    from app.db import engine

    def writer(*args):
        from app.services.export_writer import write_export
        return write_export(*args)

    owner = f'local-{os.getpid()}' if get_settings().is_desktop else self.request.id
    return execute_export(engine, job_id, lease_token, owner, writer)
