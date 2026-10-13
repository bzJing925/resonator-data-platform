"""Real HTTP, broker and executors; all databases and artifacts are disposable.

ALN_TEST_POSTGRES_URL must identify a test database. Each PG run uses its own
schema and Redis process. No eager execution or dispatch replacement is used.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models import Base, Device, ProcessingRun, UploadTask
from app.services.replica_service import init_replication
from tests.fixtures.platform_cases import _algorithm_samples, _write_archive

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _until(read, predicate, timeout=90):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = read()
        if predicate(latest):
            return latest
        time.sleep(0.05)  # Poll an observed state, never schedule a race with sleep.
    pytest.fail(f'Expected observed state within {timeout}s; last={latest!r}')


@pytest.fixture(params=['sqlite', 'postgresql'])
def live_platform(request, tmp_path):
    if 'integration' not in (request.config.getoption('-m') or ''):
        pytest.skip('Run explicitly with -m integration; real executor acceptance required')
    kind = request.param
    root = tmp_path / kind
    root.mkdir()
    processes, logs = [], []
    admin = engine = client = None
    schema = f'aln_flow_{uuid4().hex}'
    env = {**os.environ, 'ALN_DESKTOP_MODE': str(kind == 'sqlite').lower(),
           'ALN_SERVER_MODE': 'true', 'ALN_DESKTOP_DIR': str(root), 'DATA_ROOT': str(root),
           'DOTENV_PATH': str(root / 'none.env'), 'DEBUG': 'false',
           'WATCH_ENABLED': 'false', 'KEEP_RAW_ZIP': 'true', 'PIPELINE_WORKERS': '1',
           'CELERY_TASK_ALWAYS_EAGER': '0', 'WORKER_HEARTBEAT_SECONDS': '1'}

    def start(args, name):
        log = (root / f'{name}.log').open('w')
        logs.append(log)
        proc = subprocess.Popen(args, cwd=BACKEND, env=env, stdout=log, stderr=log)
        processes.append(proc)
        return proc

    try:
        if kind == 'postgresql':
            configured = os.environ.get('ALN_TEST_POSTGRES_URL')
            assert configured, 'ALN_TEST_POSTGRES_URL missing: PG acceptance unverified'
            redis = shutil.which('redis-server')
            assert redis, 'redis-server missing: real broker acceptance unverified'
            url = make_url(configured)
            admin = create_engine(url)
            with admin.begin() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            url = url.update_query_dict({'options': f'-csearch_path={schema}'})
            env['DATABASE_URL'] = url.render_as_string(hide_password=False)
            engine = create_engine(url)
            Base.metadata.create_all(engine)
            init_replication(engine)
            redis_port = _port()
            env['REDIS_URL'] = f'redis://127.0.0.1:{redis_port}/0'
            start([redis, '--bind', '127.0.0.1', '--port', str(redis_port),
                   '--dir', str(root), '--save', '', '--appendonly', 'no'], 'redis')
            import redis as redis_client

            broker = redis_client.Redis.from_url(env['REDIS_URL'])

            def ping():
                try:
                    return broker.ping()
                except redis_client.RedisError:
                    return False

            _until(ping, bool, 15)
            broker.close()
            start([sys.executable, '-m', 'celery', '-A', 'app.workers', 'worker',
                   '--pool=solo', '--concurrency=1', '--loglevel=info'], 'celery')
        port = _port()
        api = start([sys.executable, '-m', 'uvicorn', 'app.main:app',
                     '--host', '127.0.0.1', '--port', str(port)], 'api')
        client = httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=120)

        def health():
            assert api.poll() is None, (root / 'api.log').read_text()
            try:
                return client.get('/api/health').status_code == 200
            except httpx.HTTPError:
                return False

        _until(health, bool, 30)
        if kind == 'sqlite':
            engine = create_engine(f'sqlite:///{root / "aln-data.db"}')
        assert client.get('/api/capabilities').json()['database'] == kind
        (root / 'processes.json').write_text(json.dumps({
            'executor_pid': processes[-1].pid if kind == 'sqlite' else processes[1].pid}))
        yield client, engine, root, kind
    finally:
        if client:
            client.close()
        for proc in reversed(processes):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        for log in logs:
            log.close()
        if engine:
            engine.dispose()
        if admin:
            with admin.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.dispose()


def _prepare(client, mapping_id, archive):
    key, group = str(uuid4()), str(uuid4())
    response = client.post('/api/uploads/prepare',
                           files={'file': (archive.name, archive.read_bytes())},
                           data={'mapping_id': mapping_id, 'request_key': key, 'group_id': group,
                                 'f_start_ghz': 1, 'f_end_ghz': 5})
    assert response.status_code == 202, response.text
    draft_id = response.json()['draft_id']
    draft = _until(lambda: client.get(f'/api/uploads/drafts/{draft_id}').json(),
                   lambda d: d['status'] in {'ready', 'failed'})
    assert draft['status'] == 'ready', draft
    return draft_id, {'request_key': key, 'check_digest': draft['check_digest'],
                      'acknowledged_warning_codes': draft['report'].get('warning_codes', [])}


def _finished(client, task_id):
    return _until(lambda: client.get(f'/api/tasks/{task_id}').json(),
                  lambda t: t['status'] in {'success', 'failed', 'cancelled'}, 120)


def test_real_prepare_commit_partial_retry_cancel_publish_export_restore(live_platform, tmp_path):
    client, engine, root, kind = live_platform
    samples = _algorithm_samples(tmp_path)
    # Valid Touchstone with no resonance: precheck accepts, actual extraction fails.
    constant = ('# GHz S RI R 50\n' + ''.join(
        f'{1 + i / 50} 0.1 0\n' for i in range(201))).encode()
    archive = _write_archive(tmp_path / 'partial.zip', [
        ('17_E6-1_X0Y0.s1p', samples['dut_s1p']), ('18_E6-2_X1Y0.s1p', constant)])
    response = client.post('/api/mappings', data={'name': 'flow-map'}, files={'file': (
        'map.csv', b'E6-1,EG0 FL0 700&5500\nE6-2,EG0 FL0 700&5500\n')})
    assert response.status_code == 201, response.text
    mapping_id = response.json()['id']
    draft_id, body = _prepare(client, mapping_id, archive)
    # Both actual HTTP connections enter together; a draft has one durable task/run.
    barrier = Barrier(2)

    def commit():
        with httpx.Client(base_url=client.base_url, trust_env=False, timeout=120) as caller:
            barrier.wait(timeout=10)
            return caller.post(f'/api/uploads/drafts/{draft_id}/commit', json=body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: commit(), range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    first, second = [r.json() for r in responses]
    assert first['task_id'] == second['task_id'] and first['run_id'] == second['run_id']
    task_id, batch_no = int(first['task_id']), first['batch_no']
    task = _finished(client, task_id)
    assert task['status'] == 'success', task
    report = client.get(f'/api/tasks/{task_id}/report').json()
    assert report['quality_status'] == 'partial', report
    assert report['summary']['device_counts']['success'] == 1, report
    assert report['summary']['device_counts']['failed'] == 1, report
    with Session(engine) as db:
        assert db.scalar(select(func.count(ProcessingRun.id))) == 1
        run = db.get(ProcessingRun, first['run_id'])
        executor = run.executor_ids
        assert executor and any((str(v).startswith('local-')) == (kind == 'sqlite')
                                for v in executor.values()), executor
        assert db.get(UploadTask, task_id).status == 'success'
    options = client.get(f'/api/tasks/{task_id}/retry-options').json()
    assert len(options['items']) == 1, options
    retry_body = {'request_key': str(uuid4()), 'mode': 'failed',
                  'item_ids': [options['items'][0]['id']],
                  'expected_source_revision': options['expected_source_revision']}
    retried = client.post(f'/api/tasks/{task_id}/retry', json=retry_body)
    assert retried.status_code == 202, retried.text
    replay = client.post(f'/api/tasks/{task_id}/retry', json=retry_body)
    assert replay.status_code == 202 and replay.json()['task_id'] == retried.json()['task_id']
    retry_task = _finished(client, retried.json()['task_id'])
    # Retry republishes retained successes; immutable bad input remains a failed item.
    assert retry_task['status'] == 'success', retry_task
    retry_report = client.get(f"/api/tasks/{retried.json()['task_id']}/report").json()
    assert retry_report['quality_status'] == 'partial', retry_report
    assert retry_report['summary']['device_counts']['failed'] == 1, retry_report
    assert client.get(f'/api/tasks/{task_id}/report').json() == report
    retry_options = client.get(f"/api/tasks/{retried.json()['task_id']}/retry-options").json()
    assert retry_options['retry_of_task_id'] == task_id

    query = {'filters': {'batch_no': batch_no}, 'fields': ['id', 'qs', 'batch_no'],
             'order_by': None, 'sampling': 'none'}
    result = client.post('/api/query/devices', json=query)
    assert result.status_code == 200, result.text
    result = result.json()
    assert result['manifest']['matched_total'] == result['manifest']['returned'] == 1
    original = result['rows'][0]
    refs = result['manifest']['source_refs']
    exported = client.post('/api/export/jobs', json={'request_key': str(uuid4()), 'format': 'csv',
                           'query_spec': {k: query[k] for k in ('filters', 'fields', 'order_by')},
                           'expected_sources': refs})
    assert exported.status_code == 202, exported.text
    job_id = exported.json()['id']
    job = _until(lambda: client.get(f'/api/export/jobs/{job_id}').json(),
                 lambda j: j['status'] in {'success', 'failed', 'cancelled'})
    assert job['status'] == 'success' and job['rows'] == 1, job
    data = client.get(f'/api/export/jobs/{job_id}/artifacts/data.csv')
    assert data.status_code == 200
    rows = list(csv.DictReader(io.StringIO(data.text.lstrip('\ufeff'))))
    assert len(rows) == 1 and int(rows[0]['id']) == original['id']
    ref = refs[0]
    source = f"/api/sync/v3/sources/{ref['origin_id']}/{ref['source_batch_id']}"
    conflict = client.request('DELETE', source, json={
        'expected_revision': ref['source_revision'] - 1})
    assert conflict.status_code == 409, conflict.text
    deleted = client.request('DELETE', source,
                             json={'expected_revision': ref['source_revision']})
    assert deleted.status_code == 200, deleted.text
    assert client.post('/api/query/devices', json=query).json()['manifest']['returned'] == 0
    catalog = client.get('/api/sync/v3/catalog').json()['batches']
    current = next(b['source_ref'] for b in catalog if b['batch_no'] == batch_no)
    restored = client.post(source + '/restore',
                           json={'expected_revision': current['source_revision']})
    assert restored.status_code == 200, restored.text
    restored_rows = client.post('/api/query/devices', json=query).json()['rows']
    assert len(restored_rows) == 1 and restored_rows[0]['id'] == original['id']
    assert restored_rows[0]['_source_ref']['source_device_key'] == (
        original['_source_ref']['source_device_key'])

    # Enough real extraction work to observe running before requesting cancellation.
    many = _write_archive(tmp_path / 'cancel.zip', [
        (f'{i}_E6-1_X{i}Y0.s1p', samples['dut_s1p']) for i in range(250)])
    cancel_draft, cancel_body = _prepare(client, mapping_id, many)
    accepted = client.post(f'/api/uploads/drafts/{cancel_draft}/commit', json=cancel_body)
    assert accepted.status_code == 200, accepted.text
    cancel_id = accepted.json()['task_id']
    observed = _until(lambda: client.get(f'/api/tasks/{cancel_id}').json(),
                      lambda t: t['status'] != 'pending')
    assert observed['status'] == 'running', observed
    stopped = client.post(f'/api/tasks/{cancel_id}/cancel')
    assert stopped.status_code == 200, stopped.text
    terminal = _finished(client, cancel_id)
    assert terminal['status'] == 'cancelled', terminal
    with Session(engine) as db:
        run = db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == int(cancel_id)))
        assert run.status == 'cancelled' and run.finished_at is not None
        assert db.scalar(select(func.count(Device.id)).where(Device.batch_id == run.batch_id)) == 0
    evidence = {'executor': kind, 'partial_report': report, 'retry': retry_task,
                'cancel': terminal, 'export_rows': len(rows), 'restored_id': original['id']}
    (root / 'acceptance.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
