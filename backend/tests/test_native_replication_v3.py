"""Two real native SQLite HTTP processes exercise v3 state and local hiding."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Batch, Device, Mapping
from app.services.source_identity import assign_batch_identity
from tests.test_native_pair import pair  # noqa: F401

pytestmark = pytest.mark.integration


def test_native_v3_delete_restore_hide_and_version_conflict(pair):  # noqa: F811
    (server, desktop), (server_dir, desktop_dir) = pair
    engine = create_engine(f'sqlite:///{server_dir / "aln-data.db"}')
    directory = server_dir / 'files' / 'v3-owned'
    directory.mkdir()
    (directory / 'one.s1p').write_text('# GHz S RI R 50\n1 0.1 0\n2 0.2 0\n')
    mapping_file = server_dir / 'mappings' / 'v3.csv'
    mapping_file.write_text('A1,EG1 FL1 700&5500\n')
    with Session(engine) as db:
        mapping = Mapping(name='v3-map', file_path=str(mapping_file), entry_count=0)
        db.add(mapping)
        db.flush()
        batch = Batch(batch_no='v3-owned', mapping_id=mapping.id,
                      file_path=str(directory), source='http', device_count=1)
        db.add(batch)
        db.flush()
        assign_batch_identity(db, batch)
        from uuid import uuid4
        db.add(Device(batch_id=batch.id, original_filename='one.s1p',
                      source_device_key=str(uuid4()), s_param_path='one.s1p',
                      s_param_port='S11', fs_ghz=1.2))
        db.commit()
    response = server.get('/api/sync/v3/catalog')
    assert response.status_code == 200, response.text
    catalog = response.json()
    reference = catalog['batches'][0]['source_ref']
    source_path = f"/api/sync/v3/sources/{reference['origin_id']}/{reference['source_batch_id']}"
    assert desktop.put('/api/desktop-sync/config', json={
        'server_url': str(server.base_url), 'username': 'native-v3-test'}).status_code == 200
    server_caps = server.get('/api/capabilities').json()
    desktop_caps = desktop.get('/api/capabilities').json()
    assert server_caps['mode'] == 'server' and server_caps['database'] == 'sqlite'
    assert desktop_caps['mode'] == 'desktop' and desktop_caps['database'] == 'sqlite'
    assert server_caps['features']['upload_precheck']
    assert desktop_caps['features']['remote_delete']
    pulled = desktop.post('/api/desktop-sync/synchronize')
    assert pulled.status_code == 200, pulled.text
    assert pulled.json()['batches_added'] == 1 and pulled.json()['errors'] == []
    query = {'fields': ['id', 'source_device_key', 'fs_ghz'], 'sampling': 'none'}
    row = desktop.post('/api/query/devices', json=query).json()['rows'][0]
    original_id, original_uuid = row['id'], row['source_device_key']
    assert desktop.get(f'/api/devices/{original_id}/download-s1p').status_code == 200
    with Session(engine) as db:
        db.scalar(select(Device)).fs_ghz = 1.3
        db.commit()
    stale = server.request('DELETE', source_path,
                           json={'expected_revision': reference['source_revision']})
    assert stale.status_code == 409, stale.text
    reference = server.get('/api/sync/v3/catalog').json()['batches'][0]['source_ref']
    deleted = server.request('DELETE', source_path,
                             json={'expected_revision': reference['source_revision']})
    assert deleted.status_code == 200, deleted.text
    assert (directory / 'one.s1p').exists()
    assert desktop.post('/api/desktop-sync/synchronize').json()['batches_deleted'] == 1
    assert desktop.post('/api/query/devices', json=query).json()['total'] == 0
    assert desktop.get(f'/api/devices/{original_id}/download-s1p').status_code == 410
    reference = server.get('/api/sync/v3/catalog').json()['batches'][0]['source_ref']
    restored = server.post(source_path + '/restore',
                           json={'expected_revision': reference['source_revision']})
    assert restored.status_code == 200, restored.text
    synced = desktop.post('/api/desktop-sync/synchronize')
    assert synced.status_code == 200 and synced.json()['errors'] == [], synced.text
    row = desktop.post('/api/query/devices', json=query).json()['rows'][0]
    assert (row['id'], row['source_device_key']) == (original_id, original_uuid)
    reference = server.get('/api/sync/v3/catalog').json()['batches'][0]['source_ref']
    assert desktop.post('/api/replica-preferences', json={
        'source_ref': reference, 'hidden': True}).status_code == 200
    preferences = desktop.get('/api/replica-preferences')
    assert preferences.status_code == 200, preferences.text
    assert preferences.json()['items'][0]['hidden'] is True
    assert desktop.post('/api/desktop-sync/synchronize').json()['errors'] == []
    assert desktop.post('/api/query/devices', json=query).json()['total'] == 0
    assert server.get('/api/batches/v3-owned').status_code == 200
    assert desktop.post('/api/replica-preferences', json={
        'source_ref': reference, 'hidden': False}).status_code == 200
    assert desktop.post('/api/query/devices', json=query).json()['total'] == 1
    assert desktop.get(f"/api/sources/batches/{reference['origin_id']}/"
                       f"{reference['source_batch_id']}/resolve").json()['batch_no'] == 'v3-owned'
    remote = server.get('/api/sync/v3/catalog').json()['batches'][0]
    exported = server.get(f"/api/sync/v3/batches/{remote['id']}").json()
    payload = {key: exported[key] for key in ('batch', 'mapping', 'devices')}
    payload['target'] = {'batch_no': 'v3-owned', 'revision': remote['revision'],
                         'source_ref': remote['source_ref']}
    payload['devices'][0]['fs_ghz'] = 1.5
    receipt = server.post('/api/sync/v3/tables',
                          json={'key': 'native-v3-receipt', 'payload': payload})
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()['devices'][0]['source_device_key'] == original_uuid
    repeated = server.post('/api/sync/v3/tables',
                            json={'key': 'native-v3-receipt', 'payload': payload})
    assert repeated.status_code == 200, repeated.text
    stale = server.post('/api/sync/v3/tables',
                         json={'key': 'new-stale-key', 'payload': payload})
    assert stale.status_code == 409, stale.text
    assert desktop.post('/api/desktop-sync/synchronize').json()['errors'] == []
    row = desktop.post('/api/query/devices', json=query).json()['rows'][0]
    assert row['fs_ghz'] == 1.5 and row['source_device_key'] == original_uuid
    engine.dispose()


def test_native_raw_upload_keeps_origin_batch_device_uuid(pair, tmp_path):  # noqa: F811
    import time

    from tests.fixtures.platform_cases import _algorithm_samples, _write_archive

    (server, desktop), (_server_dir, desktop_dir) = pair
    samples = _algorithm_samples(tmp_path)
    archive = _write_archive(tmp_path / 'v3-source.zip',
                              [('17_E6-1_X0Y0.s1p', samples['dut_s1p'])])
    mapping = desktop.post('/api/mappings', data={'name': 'v3-source-map'},
                           files={'file': ('mapping.csv', b'E6-1,EG0 FL0 700&5500\n')})
    assert mapping.status_code == 201, mapping.text
    accepted = desktop.post('/api/uploads', data={'mapping_id': mapping.json()['id'],
                            'f_start_ghz': 1, 'f_end_ghz': 5},
                            files={'file': ('v3-source.zip', archive.read_bytes())})
    assert accepted.status_code == 202, accepted.text
    task_id = accepted.json()['task_id']
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        task = desktop.get(f'/api/tasks/{task_id}').json()
        if task['status'] in {'success', 'failed', 'cancelled'}:
            break
        time.sleep(0.1)
    assert task['status'] == 'success', task
    engine = create_engine(f'sqlite:///{desktop_dir / "aln-data.db"}')
    with Session(engine) as db:
        batch = db.scalar(select(Batch))
        device = db.scalar(select(Device))
        identity = batch.origin_id, batch.source_batch_id, device.source_device_key, device.id
    assert desktop.put('/api/desktop-sync/config', json={
        'server_url': str(server.base_url), 'username': 'v3-source-test'}).status_code == 200
    synchronized = desktop.post('/api/desktop-sync/synchronize', timeout=120)
    assert synchronized.status_code == 200, synchronized.text
    assert synchronized.json()['errors'] == [], synchronized.text
    assert synchronized.json()['raw_pushed'] == 1
    statuses = desktop.get('/api/batches/v3-source/sync-status')
    assert statuses.status_code == 200, statuses.text
    assert statuses.json()['server']['state'] == 'confirmed'
    source = server.get('/api/sync/v3/catalog').json()['batches'][0]
    payload = server.get(f"/api/sync/v3/batches/{source['id']}").json()
    assert (payload['source_ref']['origin_id'], payload['source_ref']['source_batch_id'],
            payload['devices'][0]['source_device_key']) == identity[:3]
    with Session(engine) as db:
        batch = db.scalar(select(Batch))
        device = db.scalar(select(Device))
        actual = batch.origin_id, batch.source_batch_id, device.source_device_key, device.id
        assert actual == identity
        assert batch.source == 'server' and batch.raw_zip_path is not None
    assert desktop.post('/api/desktop-sync/synchronize').json()['raw_pushed'] == 0
    export = server.post('/api/export/jobs', json={'request_key': 'native-v3-export',
        'format': 'csv', 'query_spec': {'filters': {}, 'fields': ['id', 'fs_ghz'],
                                      'order_by': None},
        'expected_sources': [source['source_ref']]})
    assert export.status_code == 202, export.text
    poll = export.json()['poll_url']
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = server.get(poll).json()
        if job['status'] in {'success', 'failed', 'cancelled'}:
            break
        time.sleep(0.1)
    assert job['status'] == 'success' and job['manifest']['returned'] == 1, job
    download = server.get(poll + '/artifacts/data.csv')
    assert download.status_code == 200 and len(download.text.splitlines()) == 2, download.text
    endpoint = {'source': source['source_ref'], 'dataset_id': None,
                'query_spec': {'filters': {}, 'fields': [], 'order_by': None}}
    compared = server.post('/api/analysis/compare', json={'quality_rule': None, 'comparison': {
        'scope': 'source', 'a': endpoint, 'b': endpoint, 'metrics': ['qs'],
        'group_fields': ['eg', 's_param_port'], 'common_frequency_ghz': None,
        'condition_policy': 'require_equal', 'wafer_metric': None, 'unified_color_scale': True}})
    assert compared.status_code == 200, compared.text
    assert compared.json()['cohorts']['a']['manifest']['returned'] == 1
    assert compared.json()['deltas'][0]['mean_delta'] == 0
    engine.dispose()
