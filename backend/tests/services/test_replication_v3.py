"""Real PostgreSQL/SQLite source state, identity and atomic table receipts."""
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, Device, LocalReplicaPreference, ReplicaBatch
from app.schemas.source import SourceRef
from app.services import replica_service as v2
from app.services import replication_v3 as v3
from app.services.batch_lifecycle import restore, soft_delete
from app.services.source_identity import assign_batch_identity
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def replicas(request, tmp_path):
    with platform_database(request.param, tmp_path) as (server, batch):
        with platform_database('sqlite', tmp_path) as (desktop, local):
            assign_batch_identity(server, batch)
            batch.device_count = 1
            server.add(Device(batch_id=batch.id, original_filename='one.s1p',
                              source_device_key=str(uuid4()), fs_ghz=4, s_param_port='S11'))
            server.commit()
            local.batch_no = 'local-owned'
            assign_batch_identity(desktop, local)
            desktop.commit()
            yield server, batch, desktop, local


def fetch(server):
    def get(batch_id):
        with v3.source_snapshot(server.bind) as source:
            payload = v2.export_batch(source, batch_id)
            payload['protocol'] = 3
            return payload
    return get


def test_updates_delete_restore_and_owned_names(replicas):
    server, batch, desktop, owned = replicas
    data = v3.catalog(server)
    assert data['complete'] is True and data['protocol'] == 3
    assert v3.apply_catalog(desktop, data, fetch(server))['batches_added'] == 1
    replica = desktop.scalar(select(Batch).where(Batch.source == 'server'))
    device = desktop.scalar(select(Device).where(Device.batch_id == replica.id))
    old_id, old_uuid, bid, name = device.id, device.source_device_key, batch.id, batch.batch_no
    server.scalar(select(Device)).fs_ghz = 5
    server.commit()
    assert v3.apply_catalog(desktop, v3.catalog(server), fetch(server))['batches_updated'] == 1
    desktop.expire_all()
    assert device.fs_ghz == 5 and device.id == old_id
    server.rollback()
    soft_delete(server.bind, name)
    deleted = v3.catalog(server)
    assert deleted['tombstones'][0]['state'] == 'deleted'
    assert v2.catalog(server)['batches'][0]['ready'] is False
    assert v3.apply_catalog(desktop, deleted, fetch(server))['batches_deleted'] == 1
    desktop.expire_all()
    assert replica.deleted_at is not None and owned.deleted_at is None
    assert device.id == old_id and device.source_device_key == old_uuid
    server.rollback()
    restore(server.bind, name)
    assert v3.apply_catalog(desktop, v3.catalog(server), fetch(server))['batches_updated'] == 1
    desktop.expire_all()
    assert replica.deleted_at is None and device.id == old_id
    assert v3.resolve(desktop, batch.origin_id, batch.source_batch_id)['batch_id'] == replica.id
    assert v3.resolve(server, batch.origin_id, batch.source_batch_id)['batch_id'] == bid


@pytest.mark.parametrize('mutation', ['partial', 'protocol', 'identity', 'payload', 'missing'])
def test_bad_catalog_and_payload_never_delete_owned_data(replicas, mutation):
    server, batch, desktop, owned = replicas
    data = v3.catalog(server)
    v3.apply_catalog(desktop, data, fetch(server))
    broken = deepcopy(data)
    callback = fetch(server)
    if mutation == 'partial':
        broken['complete'] = False
    elif mutation == 'protocol':
        broken['protocol'] = 2
    elif mutation == 'identity':
        broken['server_id'] = str(uuid4())
    elif mutation == 'payload':
        server.scalar(select(Device)).fs_ghz = 7
        server.commit()
        broken = v3.catalog(server)
        def callback(batch_id):
            payload = fetch(server)(batch_id)
            payload['batch']['source_batch_id'] = str(uuid4())
            return payload
    else:
        broken['batches'] = []  # A complete but absent entry is still not a deletion instruction.
        assert v3.apply_catalog(desktop, broken, callback)['batches_deleted'] == 0
        return
    with pytest.raises((ValidationError, ValueError)):
        v3.apply_catalog(desktop, broken, callback)
    desktop.expire_all()
    assert owned.deleted_at is None
    assert desktop.scalar(select(Batch).where(Batch.source == 'server')).deleted_at is None


def test_hidden_preference_blocks_materialization_and_read_scope(replicas):
    from app.services.batch_scope import active_batch_clause, get_active_batch

    server, batch, desktop, owned = replicas
    reference = SourceRef.model_validate(v3.catalog(server)['batches'][0]['source_ref'])
    desktop.rollback()
    v3.set_preference(desktop.bind, reference, True)
    assert v3.apply_catalog(desktop, v3.catalog(server), fetch(server))['batches_added'] == 0
    assert desktop.scalar(select(ReplicaBatch)) is None
    desktop.rollback()
    v3.set_preference(desktop.bind, reference, False)
    assert v3.apply_catalog(desktop, v3.catalog(server), fetch(server))['batches_added'] == 1
    replica = desktop.scalar(select(ReplicaBatch))
    replica_id = replica.batch_id
    desktop.rollback()
    v3.set_preference(desktop.bind, reference, True)
    assert desktop.scalar(select(Batch.id).where(active_batch_clause())) == owned.id
    with pytest.raises(HTTPException) as exc:
        get_active_batch(desktop, batch_id=replica_id)
    assert exc.value.status_code == 410
    assert desktop.get(LocalReplicaPreference, (batch.origin_id, batch.source_batch_id)).hidden
    owned_ref = SourceRef.model_validate(v3.resolve(desktop, owned.origin_id,
                                                   owned.source_batch_id)['source_ref'])
    desktop.rollback()
    with pytest.raises(HTTPException) as exc:
        v3.set_preference(desktop.bind, owned_ref, True)
    assert exc.value.status_code == 409


def test_pg_sqlite_table_receipt_revision_conflict(replicas):
    server, batch, desktop, _owned = replicas
    data = v3.catalog(server)
    reference = data['batches'][0]['source_ref']
    payload = fetch(server)(batch.id)
    payload = {key: payload[key] for key in ('batch', 'mapping', 'devices')}
    payload['target'] = {'batch_no': batch.batch_no, 'revision': reference['source_revision'],
                         'source_ref': reference}
    original_uuid = server.scalar(select(Device)).source_device_key
    payload['devices'][0]['fs_ghz'] = 6
    payload['devices'][0]['source_device_key'] = str(uuid4())
    server.rollback()
    result = v2.accept_table(server, 'receipt:key', payload)
    assert result['devices'][0]['fs_ghz'] == 6
    assert result['devices'][0]['source_device_key'] == original_uuid
    assert v2.accept_table(server, 'receipt:key', payload)['remote_id'] == batch.id
    changed = deepcopy(payload)
    changed['devices'][0]['fs_ghz'] = 8
    with pytest.raises(HTTPException) as exc:
        v2.accept_table(server, 'receipt:key', changed)
    assert exc.value.status_code == 409
    server.rollback()
    with pytest.raises(HTTPException) as exc:
        v2.accept_table(server, 'other-key', payload)
    assert exc.value.status_code == 409
    server.rollback()
    assert server.scalar(select(Device.fs_ghz)) == 6


def test_pagination_tombstone_only_and_idempotent_delete(replicas):
    server, batch, desktop, owned = replicas
    v3.apply_catalog(desktop, v3.catalog(server), fetch(server))
    server.rollback()
    soft_delete(server.bind, batch.batch_no)
    data = v3.catalog(server)
    partial = deepcopy(data)
    partial['next_cursor'] = 'next-page'
    with pytest.raises(ValueError, match='分页'):
        v3.apply_catalog(desktop, partial, fetch(server))
    assert desktop.scalar(select(Batch).where(Batch.source == 'server')).deleted_at is None
    data['batches'] = []
    assert v3.apply_catalog(desktop, data, fetch(server))['batches_deleted'] == 1
    local = desktop.scalar(select(Batch).where(Batch.source == 'server'))
    deleted_at = local.deleted_at
    assert v3.apply_catalog(desktop, data, fetch(server))['batches_deleted'] == 0
    desktop.expire_all()
    assert local.deleted_at == deleted_at and owned.deleted_at is None


def test_concurrent_target_revision_is_atomic_on_both_databases(replicas):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.models import TableReceipt

    server, batch, _desktop, _owned = replicas
    reference = v3.catalog(server)['batches'][0]['source_ref']
    exported = fetch(server)(batch.id)
    payload = {key: exported[key] for key in ('batch', 'mapping', 'devices')}
    payload['target'] = {'batch_no': batch.batch_no, 'revision': reference['source_revision'],
                         'source_ref': reference}
    engine = server.bind
    server.rollback()
    barrier = Barrier(2)
    def push(index):
        body = deepcopy(payload)
        body['devices'][0]['fs_ghz'] = 10 + index
        with Session(engine) as session:
            barrier.wait(timeout=5)
            try:
                v2.accept_table(session, f'concurrent:{index}', body)
                return 200
            except HTTPException as exc:
                session.rollback()
                return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(push, (0, 1))) == [200, 409]
    with Session(engine) as session:
        assert len(session.scalars(select(TableReceipt)).all()) == 1
        assert session.scalar(select(Device.fs_ghz)) in {10, 11}


def test_explicit_table_reconfirm_keeps_original_base_and_uses_new_key(replicas):
    import json

    from app.models import SyncIdentity

    server, batch, desktop, owned = replicas
    v3.apply_catalog(desktop, v3.catalog(server), fetch(server))
    replica_batch = desktop.scalar(select(Batch).where(Batch.source == 'server'))
    original = v3.resolve(desktop, replica_batch.origin_id,
                          replica_batch.source_batch_id)['source_ref']
    owned.source, owned.device_count = 'table', 1
    desktop.add(Device(batch_id=owned.id, original_filename='one.s1p',
                       source_device_key=str(uuid4()), s_param_port='S11', fs_ghz=8))
    old_target = {'batch_no': replica_batch.batch_no, 'revision': original['source_revision'],
                  'source_ref': original}
    desktop.add(SyncIdentity(key=f'table-target:{owned.id}', value=json.dumps(old_target)))
    desktop.commit()
    server.scalar(select(Device)).fs_ghz = 7
    server.commit()
    v3.apply_catalog(desktop, v3.catalog(server), fetch(server))
    current = SourceRef.model_validate(v3.resolve(desktop, replica_batch.origin_id,
                                                  replica_batch.source_batch_id)['source_ref'])
    bid, engine = owned.id, desktop.bind
    desktop.rollback()
    proof = v3.reconfirm_table_target(engine, bid, current, 'explicit-review')
    assert proof['target']['revision'] == current.source_revision
    assert proof['original_target']['revision'] == original['source_revision']
    assert v3.reconfirm_table_target(engine, bid, current, 'explicit-review') == proof
    desktop.expire_all()
    assert json.loads(desktop.get(SyncIdentity, f'table-target:{bid}').value) == old_target
    desktop.scalar(select(Device).where(Device.batch_id == bid)).fs_ghz = 9
    desktop.commit()
    desktop.rollback()
    with pytest.raises(HTTPException) as exc:
        v3.reconfirm_table_target(engine, bid, current, 'explicit-review')
    assert exc.value.status_code == 409


def test_concurrent_same_receipt_standalone_import(replicas):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.models import TableReceipt

    server, batch, _desktop, _owned = replicas
    exported = fetch(server)(batch.id)
    payload = {key: exported[key] for key in ('batch', 'mapping', 'devices')}
    payload['batch']['batch_no'] = 'new-table'
    payload['batch'].pop('origin_id')
    payload['batch'].pop('source_batch_id')
    payload['devices'][0]['source_device_key'] = str(uuid4())
    payload['mapping']['name'] = 'new-table-map'
    engine, barrier = server.bind, Barrier(2)
    server.rollback()
    def push(_index):
        with Session(engine) as session:
            barrier.wait(timeout=5)
            return v2.accept_table(session, 'same-new-table', deepcopy(payload))['remote_id']
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(push, (0, 1)))
    assert results[0] == results[1]
    with Session(engine) as session:
        assert len(session.scalars(select(TableReceipt)).all()) == 1
        assert len(session.scalars(select(Batch)).all()) == 2
