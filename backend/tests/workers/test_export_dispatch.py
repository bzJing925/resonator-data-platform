"""Typed local queue and Celery delivery share durable ownership, not UploadTask."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExportJob, UploadTask
from app.schemas.export_job import ExportCreate
from app.services import export_job_service as jobs
from app.workers import dispatch
from app.workers.export_job import execute_export
from app.workers.local_queue import LocalExportTask, LocalPrecheckTask, LocalTask, LocalTaskQueue
from app.workers.local_worker import LOCAL_HANDLERS
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def exports(request, tmp_path, monkeypatch):
    from app.config import Settings
    settings = Settings(DATA_ROOT=tmp_path / 'data', ALN_DESKTOP_MODE=False)
    monkeypatch.setattr(jobs, 'get_settings', lambda: settings)
    with platform_database(request.param, tmp_path) as (db, _batch):
        yield db.bind, settings


def request():
    return ExportCreate.model_validate({'request_key': 'same', 'format': 'csv',
        'query_spec': {'filters': {}, 'fields': ['id'], 'order_by': None}, 'expected_sources': []})


def test_concurrent_same_key_one_admission(exports):
    engine, _settings = exports
    barrier = Barrier(2)
    def admit(_):
        barrier.wait(timeout=5)
        return jobs.create_job(engine, request())
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(admit, range(2)))
    assert sum(result[1] for result in results) == 1
    assert results[0][0]['id'] == results[1][0]['id']
    with Session(engine) as db:
        assert len(db.scalars(select(ExportJob)).all()) == 1
        assert db.scalar(select(UploadTask)) is None


def test_native_and_celery_dispatch(exports, monkeypatch):
    engine, settings = exports
    result, _, token = jobs.create_job(engine, request())
    monkeypatch.setattr('app.db.engine', engine)
    monkeypatch.setattr(dispatch, 'get_settings', lambda: settings)
    queue = LocalTaskQueue()
    monkeypatch.setattr('app.workers.local_queue.get_local_queue', lambda: queue)
    settings.ALN_DESKTOP_MODE = True
    dispatch.dispatch_export_job(result['id'], token)
    task = queue.get(timeout=0)
    assert isinstance(task, LocalExportTask) and task.job_id == result['id']
    assert set(LOCAL_HANDLERS) == {LocalTask, LocalPrecheckTask, LocalExportTask}
    settings.ALN_DESKTOP_MODE = False
    calls = []
    class Receipt:
        id = 'broker-receipt'
    monkeypatch.setattr('app.workers.export_job.export_job_task.apply_async',
        lambda **kwargs: calls.append(kwargs) or Receipt())
    assert dispatch.dispatch_export_job(result['id'], token) == 'broker-receipt'
    assert calls[0]['args'] == [result['id'], token] and calls[0]['task_id']


def test_claim_cancel_failure_and_duplicate_delivery(exports):
    engine, _settings = exports
    result, _, token = jobs.create_job(engine, request())
    job_id = result['id']
    assert jobs.claim_job(engine, job_id, 'wrong', 'wrong') is None
    def broken_writer(*args):
        raise OSError('fixture output failure')
    assert execute_export(engine, job_id, token, 'worker', broken_writer)['published'] is False
    assert jobs.get_job(engine, job_id)['status'] == 'failed'
    assert not execute_export(engine, job_id, token, 'worker', broken_writer)['claimed']
    other = request().model_copy(update={'request_key': 'cancel'})
    result, _, token = jobs.create_job(engine, other)
    jobs.request_cancel(engine, result['id'])
    assert jobs.claim_job(engine, result['id'], token, 'worker') is None
    assert jobs.get_job(engine, result['id'])['status'] == 'cancelled'
