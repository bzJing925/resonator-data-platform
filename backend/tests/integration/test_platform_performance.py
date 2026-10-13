"""Fixed-size real export RSS and million-row atomic publication SQL evidence."""

import csv
import hashlib
import io
import json
import subprocess
import threading
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, insert, select
from sqlalchemy.orm import Session

from app.models import Batch, Device, Mapping, StagedDeviceRow
from app.services.processing_run import claim_run, create_run
from app.services.result_publish import publish_run
from app.services.source_identity import assign_batch_identity
from tests.integration.test_platform_flow import _until, live_platform  # noqa: F401

pytestmark = pytest.mark.integration


def _rss(pid):
    result = subprocess.run(['ps', '-o', 'rss=', '-p', str(pid)], capture_output=True,
                            text=True, check=True)
    return int(result.stdout.strip()) * 1024


def test_actual_export_200001_rss_and_million_publish_sql(live_platform):  # noqa: F811
    client, engine, root, kind = live_platform
    mapping_path = root / 'perf-map.csv'
    mapping_path.write_text('E6-1,EG0 FL0 700&5500\n')
    with Session(engine) as db:
        mapping = Mapping(name='perf-map', file_path=str(mapping_path), entry_count=1)
        db.add(mapping)
        db.flush()
        batch = Batch(batch_no='perf-export', mapping_id=mapping.id, file_path=str(root))
        assign_batch_identity(db, batch)
        db.add(batch)
        db.flush()
        batch_id, mapping_id = batch.id, mapping.id
        for start in range(0, 200001, 2000):
            db.execute(insert(Device), [{'batch_id': batch_id,
                'source_device_key': str(UUID(int=i + 1)), 'original_filename': f'{i}.s1p',
                'qs': float(i)} for i in range(start, min(200001, start + 2000))])
            db.commit()
    pid = json.loads((root / 'processes.json').read_text())['executor_pid']
    baseline = _rss(pid)
    samples = [baseline]
    stop = threading.Event()

    def sample():
        while not stop.wait(0.05):
            samples.append(_rss(pid))

    sampler = threading.Thread(target=sample)
    sampler.start()
    started = time.perf_counter()
    try:
        accepted = client.post('/api/export/jobs', json={'request_key': str(uuid4()),
            'format': 'csv', 'query_spec': {'filters': {'batch_no': 'perf-export'},
            'fields': ['id', 'qs'], 'order_by': 'id'}, 'expected_sources': []})
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()['id']
        job = _until(lambda: client.get(f'/api/export/jobs/{job_id}').json(),
                     lambda j: j['status'] in {'success', 'failed'}, timeout=120)
        assert job['status'] == 'success' and job['rows'] == 200001, job
        elapsed = time.perf_counter() - started
        samples.append(_rss(pid))
    finally:
        stop.set()
        sampler.join(timeout=10)
    csv_response = client.get(f'/api/export/jobs/{job_id}/artifacts/data.csv')
    assert csv_response.status_code == 200
    seen = {int(row['id']) for row in csv.DictReader(io.StringIO(
        csv_response.text.lstrip('\ufeff')))}
    with Session(engine) as db:
        expected = set(db.scalars(select(Device.id).where(Device.batch_id == batch_id)))
    assert len(seen) == 200001 and seen == expected
    export_evidence = {'baseline_rss_bytes': baseline, 'peak_rss_bytes': max(samples),
                       'sampling_ms': 50, 'samples': len(samples), 'seconds': elapsed,
                       'rows': len(seen), 'budget_bytes': 256 * 1024**2}
    (root / 'export-performance.json').write_text(json.dumps(export_evidence, indent=2))
    assert len(samples) > 2
    assert max(samples) <= baseline + 256 * 1024**2, export_evidence

    with Session(engine) as db:
        batch = Batch(batch_no='perf-publish', mapping_id=mapping_id, file_path=str(root))
        assign_batch_identity(db, batch)
        db.add(batch)
        db.commit()
        publish_batch_id = batch.id
    ticket = create_run(engine, publish_batch_id, 'upload', config={})
    ticket = claim_run(engine, ticket.run_id, ticket.lease_token, 'compute')
    # Fixture staging outside publication; no mock or change to the guarded transaction.
    with Session(engine) as db:
        for start in range(0, 1000000, 2000):
            values = []
            for i in range(start, start + 2000):
                payload = {'source_device_key': str(UUID(int=1000001 + i)),
                           'original_filename': f'{i}.s1p', 'qs': float(i)}
                encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
                values.append({'run_id': ticket.run_id,
                    'source_device_key': payload['source_device_key'], 'payload_json': payload,
                    'payload_digest': hashlib.sha256(encoded.encode()).hexdigest()})
            db.execute(insert(StagedDeviceRow), values)
            db.commit()
    sql = {'calls': 0, 'executemany_calls': 0}

    def statement(_conn, _cursor, _statement, _parameters, _context, executemany):
        sql['calls'] += 1
        sql['executemany_calls'] += int(executemany)

    event.listen(engine, 'before_cursor_execute', statement)
    started = time.perf_counter()
    try:
        result = publish_run(engine, ticket.run_id, ticket.lease_token)
    finally:
        elapsed = time.perf_counter() - started
        event.remove(engine, 'before_cursor_execute', statement)
    assert result['published_count'] == 1000000, result
    with Session(engine) as db:
        assert db.scalar(select(func.count(Device.id)).where(
            Device.batch_id == publish_batch_id)) == 1000000
        assert db.get(Batch, publish_batch_id).result_revision == 1
    evidence = {'executor': kind, 'rows': 1000000, 'publication_seconds': elapsed, **sql}
    (root / 'publication-performance.json').write_text(json.dumps(evidence, indent=2))
    print('PLATFORM_BACKEND_PERF', json.dumps({'export': export_evidence, 'publish': evidence}))
