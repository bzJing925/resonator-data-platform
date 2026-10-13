"""Real two-database deletion and restoration without destructive IO."""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.main import app
from app.models import Device, SyncRevision, Tombstone, UploadTask
from app.models.process import ProcessAlnDepRun, ProcessIcpEtchRun, ProcessMoDepRun
from app.services.batch_lifecycle import restore, soft_delete
from app.services.source_identity import assign_batch_identity
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def trash_api(request, tmp_path, monkeypatch):
    with platform_database(request.param, tmp_path) as (db, batch):
        root = tmp_path / 'batch-files'
        root.mkdir()
        curve = root / 'retained.s1p'
        curve.write_text('# GHz S RI R 50\n1 0.1 0\n2 0.2 0\n')
        raw = tmp_path / 'original.zip'
        raw.write_bytes(b'original-input')
        batch.file_path, batch.raw_zip_path, batch.device_count = str(root), str(raw), 1
        assign_batch_identity(db, batch)
        device = Device(batch_id=batch.id, source_device_key=str(uuid4()),
                        original_filename='retained.s1p', s_param_path='retained.s1p',
                        s_param_port='S11', fs_ghz=1.2, fp_ghz=1.4, pf='Y')
        db.add(device)
        for model in (ProcessIcpEtchRun, ProcessMoDepRun, ProcessAlnDepRun):
            extra = ({'block_index': 1} if model is ProcessIcpEtchRun
                     else {'source_sheet': 'independent'} if model is ProcessAlnDepRun else {})
            db.add(model(run_no='independent', **extra))
        db.commit()
        engine = db.bind
        monkeypatch.setattr(app.state, "artifact_engine", engine, raising=False)
        settings = Settings(DATA_ROOT=tmp_path / 'data', WATCH_ENABLED=False,
                            ALN_DESKTOP_MODE=False)
        for module in ('app.api.export', 'app.services.file_tree_service'):
            monkeypatch.setattr(module + '.get_settings', lambda: settings)
        monkeypatch.setattr('app.api.query.get_redis_client', lambda: None)
        def override():
            with Session(engine) as session:
                yield session
        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = override
        client = TestClient(app)
        try:
            yield client, db, batch, device, curve, raw
        finally:
            client.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)


def test_delete_restore_identity_files_and_revision(trash_api):
    client, db, batch, device, curve, raw = trash_api
    name = batch.batch_no
    original = (batch.origin_id, batch.source_batch_id, device.source_device_key, device.id)
    rev = db.get(SyncRevision, batch.id).revision
    db.rollback()
    assert client.delete(f'/api/batches/{name}').status_code == 204
    db.expire_all()
    deleted_rev = db.get(SyncRevision, batch.id).revision
    assert deleted_rev > rev
    db.rollback()
    assert client.delete(f'/api/batches/{name}').status_code == 204
    db.expire_all()
    assert db.get(SyncRevision, batch.id).revision == deleted_rev
    db.rollback()
    assert client.get('/api/trash').json()['total'] == 1
    assert curve.is_file() and raw.read_bytes() == b'original-input'
    for model in (ProcessIcpEtchRun, ProcessMoDepRun, ProcessAlnDepRun):
        assert db.scalar(select(model.run_no)) == 'independent'
    db.rollback()
    response = client.post(f'/api/batches/{name}/restore')
    assert response.status_code == 200, response.text
    db.expire_all()
    assert batch.deleted_at is None and batch.purge_after is None
    assert db.get(SyncRevision, batch.id).revision > deleted_rev
    assert original == (batch.origin_id, batch.source_batch_id, device.source_device_key, device.id)
    assert db.get(Tombstone, original[:2]).state == 'active'
    assert client.get('/api/trash').json()['total'] == 0
    assert client.get('/api/batches').json()['total'] == 1


@pytest.mark.parametrize('status', ['pending', 'running', 'cancel_requested'])
def test_active_task_blocks_deletion(trash_api, status):
    client, db, batch, _device, curve, _raw = trash_api
    db.add(UploadTask(batch_no=batch.batch_no, status=status))
    db.commit()
    response = client.delete(f'/api/batches/{batch.batch_no}')
    assert response.status_code == 409, response.text
    db.expire_all()
    assert batch.deleted_at is None and curve.exists()


def test_replica_delete_and_purged_restore_rejected(trash_api):
    _client, db, batch, _device, _curve, _raw = trash_api
    name = batch.batch_no
    batch.source = 'server'
    db.commit()
    db.rollback()
    with pytest.raises(HTTPException) as exc:
        soft_delete(db.bind, name)
    assert exc.value.status_code == 409
    batch.source = 'local'
    db.commit()
    db.rollback()
    soft_delete(db.bind, name)
    db.expire_all()
    tombstone = db.get(Tombstone, (batch.origin_id, batch.source_batch_id))
    tombstone.purged_at = batch.deleted_at
    db.commit()
    db.rollback()
    with pytest.raises(HTTPException) as exc:
        restore(db.bind, name)
    assert exc.value.status_code == 410


def test_uuid_impact_read_and_explicit_replica_routes(trash_api):
    from app.models import ReplicaBatch
    from app.services.remote_access import route

    client, db, batch, _, _, _ = trash_api
    path = f'/api/sync/v3/sources/{batch.origin_id}/{batch.source_batch_id}'
    impact = client.get(path + '/deletion-impact')
    assert impact.status_code == 200
    assert impact.json()['source_ref']['source_batch_id'] == batch.source_batch_id
    db.add(ReplicaBatch(batch_id=batch.id, server_id='expected-server', remote_id=42))
    batch.source = 'server'
    db.commit()
    with pytest.raises(HTTPException) as caught:
        route(f'/api/batches/{batch.batch_no}', 'DELETE', {}, {}, db)
    assert caught.value.status_code == 409
    assert caught.value.detail['code'] == 'EXPLICIT_REMOTE_OPERATION_REQUIRED'
    for suffix, method in [('', 'DELETE'), ('/restore', 'POST'), ('/deletion-impact', 'GET')]:
        assert route(path + suffix, method, {}, {}, db) == (
            path + suffix, {}, None, 'expected-server'
        )
    assert route(f'/api/batches/{batch.batch_no}/deletion-impact', 'GET', {}, {}, db) is None
