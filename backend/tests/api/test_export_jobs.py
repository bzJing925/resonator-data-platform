"""Durable admission, state gates and real artifact whitelist downloads."""
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ExportJob
from app.schemas.export_job import ExportCreate
from app.services import export_job_service as jobs
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def export_context(request, tmp_path, monkeypatch):
    from app.config import Settings
    from app.db import get_db
    from app.main import app
    settings = Settings(DATA_ROOT=tmp_path / 'data', ALN_DESKTOP_MODE=False, WATCH_ENABLED=False)
    settings.exports_dir.mkdir(parents=True)
    monkeypatch.setattr(jobs, 'get_settings', lambda: settings)
    deliveries = []
    monkeypatch.setattr('app.api.export_jobs.dispatch_export_job',
                        lambda *args: deliveries.append(args) or 'test-queue')
    with platform_database(request.param, tmp_path) as (db, _batch):
        previous = dict(app.dependency_overrides)
        def sessions():
            with Session(db.bind) as session:
                yield session
        app.dependency_overrides[get_db] = sessions
        try:
            # The real fixture schema owns startup; don't run a separate global DB lifespan.
            yield TestClient(app), db.bind, settings, deliveries
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)


def body(key='export-test'):
    return {'request_key': key, 'format': 'csv', 'expected_sources': [],
            'query_spec': {'filters': {'qs': {'gt': 1000}}, 'fields': ['id'], 'order_by': None}}


def test_idempotency_state_and_download_gates(export_context):
    client, engine, settings, deliveries = export_context
    response = client.post('/api/export/jobs', json=body())
    assert response.status_code == 202, response.text
    accepted = response.json()
    job_id = accepted['id']
    assert accepted['poll_url'] == f'/api/export/jobs/{job_id}'
    assert client.post('/api/export/jobs', json=body()).json()['id'] == job_id
    assert len(deliveries) == 1
    altered = body()
    altered['format'] = 'xlsx'
    assert client.post('/api/export/jobs', json=altered).status_code == 409
    assert client.get(f'/api/export/jobs/{job_id}').json()['status'] == 'pending'
    assert client.get(f'/api/export/jobs/{job_id}/artifacts/data.csv').status_code == 409
    with Session(engine) as db:
        token = db.get(ExportJob, job_id).lease_token
    assert jobs.claim_job(engine, job_id, token, 'test-executor')
    directory = settings.exports_dir / job_id
    directory.mkdir()
    data = directory / 'data.csv'
    data.write_text('id\n')
    from app.services.query_manifest import make_manifest
    from app.services.query_spec import query_spec
    manifest = make_manifest(query_spec(body()['query_spec']['filters'], ['id']),
                             {}, True, 0, 0).model_dump(mode='json')
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    artifacts = [{'name': p.name, 'bytes': p.stat().st_size} for p in directory.iterdir()]
    assert jobs.finish_job(engine, job_id, token, 'success', manifest=manifest, artifacts=artifacts)
    assert client.get(f'/api/export/jobs/{job_id}/artifacts/data.csv').text == 'id\n'
    assert client.get(f'/api/export/jobs/{job_id}/artifacts/unregistered.csv').status_code == 404
    assert client.post(f'/api/export/jobs/{job_id}/cancel').json()['status'] == 'success'
    with Session(engine) as db:
        db.get(ExportJob, job_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get(f'/api/export/jobs/{job_id}/artifacts/data.csv').status_code == 410


def test_cancel_recovery_and_stale_writer(export_context):
    client, engine, _settings, _deliveries = export_context
    job_id = client.post('/api/export/jobs', json=body()).json()['id']
    with Session(engine) as db:
        token = db.get(ExportJob, job_id).lease_token
    assert jobs.claim_job(engine, job_id, token, 'worker-1')
    assert client.post(f'/api/export/jobs/{job_id}/cancel').json()['status'] == 'cancel_requested'
    assert jobs.recover_exports(engine) == 0
    assert jobs.recover_exports(engine, stopped_executor_ids={'worker-1'}) == 1
    assert jobs.get_job(engine, job_id)['status'] == 'cancelled'
    assert not jobs.finish_job(engine, job_id, token, 'success',
                               manifest={'returned': 0}, artifacts=[])
    for state in ('pending', 'running'):
        request = ExportCreate.model_validate(body(state))
        result, _, token = jobs.create_job(engine, request)
        jobs.mark_dispatched(engine, result['id'], token, 'lost-' + state)
        if state == 'running':
            jobs.claim_job(engine, result['id'], token, 'lost-' + state)
        assert jobs.recover_exports(engine, stopped_executor_ids={'lost-' + state}) == 1
        assert jobs.get_job(engine, result['id'])['error']['code'] == 'EXPORT_INTERRUPTED'
        assert not jobs.claim_job(engine, result['id'], token, 'late')


def test_invalid_expected_sources_and_fields(export_context):
    client, _engine, _settings, _deliveries = export_context
    malformed = body()
    malformed['query_spec']['fields'] = ['DROP TABLE devices']
    assert client.post('/api/export/jobs', json=malformed).status_code == 400
    malformed = body()
    malformed['expected_sources'] = [{'origin_id': str(uuid4()), 'source_batch_id': str(uuid4()),
        'source_device_key': None, 'source_revision': 1}]
    assert client.post('/api/export/jobs', json=malformed).status_code == 409


def test_expired_lease_and_cancel_cannot_publish(export_context):
    client, engine, _settings, _deliveries = export_context
    job_id = client.post('/api/export/jobs', json=body()).json()['id']
    with Session(engine) as db:
        job = db.get(ExportJob, job_id)
        token = job.lease_token
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert jobs.claim_job(engine, job_id, token, 'late') is None
    assert not jobs.heartbeat(engine, job_id, token)
    assert client.post(f'/api/export/jobs/{job_id}/cancel').json()['status'] == 'cancel_requested'
    assert jobs.confirm_cancelled(engine, job_id, token)
    assert jobs.get_job(engine, job_id)['status'] == 'cancelled'
