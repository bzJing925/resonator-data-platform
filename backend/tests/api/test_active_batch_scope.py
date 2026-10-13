"""Deleted rows disappear across HTTP reads, exports and source snapshots."""
import csv
import io

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Batch, Device
from app.services.batch_stats_service import get_batch_stats
from app.services.remote_access import route
from app.services.replica_service import catalog, export_batch
from app.services.snapshot_service import build_snapshot
from tests.api.test_batch_trash import trash_api  # noqa: F401, F811


def test_all_read_scopes_and_restore(trash_api):  # noqa: F811
    client, db, batch, device, curve, raw = trash_api
    name, bid, did = batch.batch_no, batch.id, device.id
    req = {'fields': ['id', 'batch_no', 'fs_ghz'], 'sampling': 'none'}
    assert client.post('/api/query/devices', json=req).json()['total'] == 1
    db.rollback()
    assert client.delete(f'/api/batches/{name}').status_code == 204
    for path in (f'/api/batches/{name}', f'/api/batches/{name}/devices',
                 f'/api/batches/{name}/download-zip', f'/api/devices/{did}/sparam',
                 f'/api/devices/{did}/bodeq', f'/api/devices/{did}/sparam-sparse',
                 f'/api/devices/{did}/download-s1p', f'/api/files?batch_no={name}',
                 f'/api/files/tree?batch_no={name}',
                 f'/api/files/curve?batch_no={name}&relpath=retained.s1p'):
        response = client.get(path)
        assert response.status_code == 410, (path, response.text)
    assert client.get('/api/batches').json()['total'] == 0
    assert client.get('/api/stats').json()['devices'] == 0
    assert client.post('/api/query/devices', json=req).json()['total'] == 0
    aggregate = client.post('/api/query/aggregate', json={
        'group_by': ['batch_no'], 'metrics': [{'field': 'fs_ghz', 'agg': ['count', 'avg']}]})
    assert aggregate.status_code == 200, aggregate.text
    assert aggregate.json()['groups'] == []
    assert client.get('/api/query/distinct?field=batch_no').json()['values'] == []
    assert client.get('/api/query/distinct?field=fs_ghz').json()['values'] == []
    response = client.post('/api/export/csv', json=req)
    assert list(csv.DictReader(io.StringIO(response.text))) == []
    response = client.post('/api/export/xlsx', json=req)
    assert response.status_code == 200 and response.json()['rows'] == 0
    db.expire_all()
    assert build_snapshot(db, set())['batches'] == []
    assert catalog(db)['batches'][0]['ready'] is False
    for call in (lambda: export_batch(db, bid),
                 lambda: get_batch_stats(db, bid, 1),
                 lambda: route(f'/api/devices/{did}/sparam', 'GET', {}, {}, db)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 410
    assert db.scalar(select(Device.id)) == did and db.scalar(select(Batch.id)) == bid
    assert curve.exists() and raw.exists()
    db.rollback()
    assert client.post(f'/api/batches/{name}/restore').status_code == 200
    assert client.post('/api/query/devices', json=req).json()['total'] == 1
