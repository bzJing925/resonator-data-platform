"""Explicit source state; absence and partial catalogs never imply deletion."""
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    Batch,
    DataEpoch,
    LocalReplicaPreference,
    ReplicaBatch,
    SyncIdentity,
    Tombstone,
)
from app.schemas.replication_v3 import CatalogV3
from app.services import replica_service as v2
from app.services.api_errors import api_error
from app.services.processing_run import _locked_session
from app.services.source_identity import batch_source_ref, set_replica_revision


@contextmanager
def source_snapshot(engine):
    with engine.connect() as connection:
        if engine.dialect.name == 'postgresql':
            connection = connection.execution_options(isolation_level='REPEATABLE READ')
        with Session(connection) as db:
            if engine.dialect.name == 'sqlite':
                db.execute(text('BEGIN'))
            yield db


def catalog(db):
    with source_snapshot(db.bind) as source:
        result = v2.catalog(source)
        result.update(protocol=3, complete=True, epoch=source.get(DataEpoch, 1).epoch)
        for batch in result['batches']:
            if batch['source_ref'] is None:
                raise api_error(503, "SOURCE_NOT_READY", '来源身份未初始化，请完成启动升级')
            stored = source.get(Batch, batch['id'])
            batch['state'] = ('deleted' if stored is None or stored.deleted_at is not None
                              else 'active')
        result['tombstones'] = [
            {column.name: (getattr(item, column.name).isoformat()
                           if isinstance(getattr(item, column.name), datetime)
                           else getattr(item, column.name))
             for column in Tombstone.__table__.columns}
            for item in source.scalars(select(Tombstone)).all()]
        return CatalogV3.model_validate(result).model_dump(mode='json')


def resolve(db, origin, source_batch):
    batch = db.scalar(select(Batch).where(Batch.origin_id == str(origin),
                                          Batch.source_batch_id == str(source_batch)))
    if batch is None:
        raise api_error(404, "SOURCE_NOT_FOUND", '来源不存在')
    tombstone = db.get(Tombstone, (str(origin), str(source_batch)))
    preference = db.get(LocalReplicaPreference, (str(origin), str(source_batch)))
    return {'batch_id': batch.id, 'batch_no': batch.batch_no,
            'hidden': bool(preference and preference.hidden),
            'state': 'deleted' if batch.deleted_at is not None else 'active',
            'purged': bool(tombstone and tombstone.purged_at),
            'source_ref': batch_source_ref(db, batch)}


def set_preference(engine, source_ref, hidden):
    origin, source_batch = str(source_ref.origin_id), str(source_ref.source_batch_id)
    with _locked_session(engine) as db:
        if engine.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
                       {'key': 'aln-query-scope'})
        local = db.scalar(select(Batch).where(Batch.origin_id == origin,
                         Batch.source_batch_id == source_batch).with_for_update())
        if local is not None and db.get(ReplicaBatch, local.id) is None:
            raise api_error(409, "SOURCE_STATE_CONFLICT", '本地拥有的批次不能按副本隐藏')
        # Serialize unknown sources as well, without generating or changing source identity.
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
                       {'key': 'replica-preference:' + origin + source_batch})
        preference = db.get(LocalReplicaPreference, (origin, source_batch))
        if preference is None:
            preference = LocalReplicaPreference(origin_id=origin, source_batch_id=source_batch)
            db.add(preference)
        preference.hidden = hidden
    return {'source_ref': source_ref.model_dump(mode='json'), 'hidden': hidden}


def validate_catalog(data):
    parsed = CatalogV3.model_validate(data)
    if any(data.get(key) for key in ('next_cursor', 'cursor', 'has_more', 'partial')):
        raise ValueError('目录仍有分页或不完整')
    keys = [(str(item.source_ref.origin_id), str(item.source_ref.source_batch_id))
            for item in parsed.batches]
    if len(keys) != len(set(keys)) or len({item.id for item in parsed.batches}) != len(keys):
        raise ValueError('catalog来源重复')
    sources = {(str(item.source_ref.origin_id), str(item.source_ref.source_batch_id)): item
               for item in parsed.batches}
    tombstones = [(str(item.origin_id), str(item.source_batch_id)) for item in parsed.tombstones]
    if len(tombstones) != len(set(tombstones)):
        raise ValueError('tombstone来源重复')
    for tombstone in parsed.tombstones:
        advertised = sources.get((str(tombstone.origin_id), str(tombstone.source_batch_id)))
        if advertised is not None and (advertised.revision < tombstone.revision or (
            advertised.revision == tombstone.revision and advertised.state != tombstone.state)):
            raise ValueError('目录与tombstone冲突')
    return parsed


def apply_catalog(db, data, fetch):
    # Validate the whole response before a single source mutation.
    parsed = validate_catalog(data)
    sid = str(parsed.server_id)
    keys = [(str(item.source_ref.origin_id), str(item.source_ref.source_batch_id))
            for item in parsed.batches]
    if len(keys) != len(set(keys)) or len({item.id for item in parsed.batches}) != len(keys):
        raise ValueError('catalog来源重复')
    upstream = db.get(SyncIdentity, 'upstream')
    if upstream is not None and upstream.value != sid:
        raise ValueError('服务器身份改变，未修改本地副本')
    if upstream is None:
        db.add(SyncIdentity(key='upstream', value=sid))
        db.commit()
    result = {'batches_added': 0, 'batches_updated': 0, 'batches_deleted': 0,
              'devices_added': 0, 'mappings_added': 0, 'errors': []}
    for remote in parsed.batches:
        origin = str(remote.source_ref.origin_id)
        source_batch = str(remote.source_ref.source_batch_id)
        preference = db.get(LocalReplicaPreference, (origin, source_batch))
        batch = db.scalar(select(Batch).where(Batch.origin_id == origin,
                                             Batch.source_batch_id == source_batch))
        replica = db.get(ReplicaBatch, batch.id) if batch is not None else None
        uploaded_owned = (batch is not None and replica is None
                          and batch.source in {'http', 'watch'} and batch.synced_at is not None)
        if (batch is not None and not uploaded_owned
                and (replica is None or replica.server_id != sid)):
            result['errors'].append(f'{remote.batch_no}:本地拥有来源，不作为副本覆盖')
            continue
        if preference is not None and preference.hidden:
            continue
        if remote.state == 'deleted':
            if replica is None:
                continue
            if batch is not None and (replica.revision < remote.revision
                                     or (replica.revision == remote.revision
                                         and batch.deleted_at is None)):
                if batch.deleted_at is None:
                    result['batches_deleted'] += 1
                if batch.deleted_at is None:
                    batch.deleted_at = datetime.now(UTC)
                replica.revision = remote.revision
                db.flush()
                set_replica_revision(db, batch.id, remote.source_ref.source_revision)
                db.commit()
            continue
        if not remote.ready or (replica is not None and replica.revision >= remote.revision
                                and batch.deleted_at is None):
            continue
        payload = fetch(remote.id)
        if (payload.get('protocol') != 3 or payload.get('server_id') != sid
                or payload.get('source_ref') != remote.source_ref.model_dump(mode='json')
                or payload.get('revision') != remote.revision
                or payload.get('batch', {}).get('origin_id') != origin
                or payload.get('batch', {}).get('source_batch_id') != source_batch):
            raise ValueError('来源在读取期间发生变化，请重新同步')
        try:
            stats = v2.import_batch(db, payload, existing=batch)
            local = db.scalar(select(Batch).where(Batch.origin_id == origin,
                                                   Batch.source_batch_id == source_batch))
            local.deleted_at, local.purge_after = None, None
            db.flush()
            set_replica_revision(db, local.id, remote.source_ref.source_revision)
            db.commit()
            from app.services import sync_status
            db.rollback()
            operation = sync_status.begin(db.bind, payload['source_ref'], 'server', payload,
                request_key=f"pull:{sid}:{source_batch}:{remote.revision}")
            if not sync_status.confirm_readback(db.bind, operation, payload):
                raise ValueError('来源读回不能确认')
            for key, count in stats.items():
                result[key] += count
        except ValueError as exc:
            db.rollback()
            result['errors'].append(f'{remote.batch_no}:{exc}')
    # Older cleaned stores may retain only a tombstone, with no batch catalog entry.
    known = {(str(item.source_ref.origin_id), str(item.source_ref.source_batch_id)): item
             for item in parsed.batches}
    for tombstone in parsed.tombstones:
        origin, source_batch = str(tombstone.origin_id), str(tombstone.source_batch_id)
        advertised = known.get((origin, source_batch))
        if advertised is not None:
            continue
        if tombstone.state != 'deleted':
            continue  # Restoring rows requires an available, validated payload.
        local = db.scalar(select(Batch).where(Batch.origin_id == origin,
                                             Batch.source_batch_id == source_batch))
        replica = db.get(ReplicaBatch, local.id) if local is not None else None
        if (local is None or replica is None or replica.server_id != sid
                or replica.revision > tombstone.revision):
            continue
        if local.deleted_at is None:
            local.deleted_at = tombstone.deleted_at or datetime.now(UTC)
            result['batches_deleted'] += 1
        replica.revision = tombstone.revision
        db.flush()
        set_replica_revision(db, local.id, tombstone.revision)
        db.commit()
    return result


def reconfirm_table_target(engine, batch_id, source_ref, request_key):
    import json

    origin, source_batch = str(source_ref.origin_id), str(source_ref.source_batch_id)
    if source_ref.source_revision is None or source_ref.source_device_key is not None:
        raise api_error(422, "SOURCE_INPUT_INVALID", '必须明确批次来源UUID和复核版本')
    with _locked_session(engine) as db:
        batch = db.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
        if (batch is None or batch.source != 'table' or batch.deleted_at is not None
                or db.get(ReplicaBatch, batch_id) is not None or not v2.ready(db, batch)):
            raise api_error(409, "SOURCE_STATE_CONFLICT", '本地待提交表格不可复核')
        original = db.get(SyncIdentity, f'table-target:{batch_id}')
        if original is None:
            raise api_error(409, "SOURCE_STATE_CONFLICT", '没有原始关联目标')
        target = db.scalar(select(Batch).where(Batch.origin_id == origin,
            Batch.source_batch_id == source_batch).with_for_update())
        replica = db.get(ReplicaBatch, target.id) if target is not None else None
        if (target is None or replica is None or target.deleted_at is not None
                or replica.revision != source_ref.source_revision):
            raise api_error(409, "SOURCE_STATE_CONFLICT", '请先同步并重新核对来源版本')
        old = json.loads(original.value)
        if old.get('batch_no') != target.batch_no:
            raise api_error(409, "SOURCE_STATE_CONFLICT", '目标已变，请重新导入，不替换原关联')
        payload = v2.export_batch(db, batch_id)
        payload = {key: payload[key] for key in ('batch', 'mapping', 'devices')}
        from app.services.desktop_sync_service import mapping_server_name
        payload['mapping']['name'] = mapping_server_name(batch.mapping)
        proof = {'batch_id': batch_id, 'request_key': request_key,
                 'original_target': old, 'reviewed_digest': v2.table_digest(payload),
                 'target': {'batch_no': target.batch_no, 'revision': replica.revision,
                            'source_ref': source_ref.model_dump(mode='json')}}
        key = 'table-reconfirm:' + request_key
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': key})
        existing = db.get(SyncIdentity, key)
        value = json.dumps(proof, sort_keys=True)
        if existing is not None and existing.value != value:
            raise api_error(409, "SOURCE_STATE_CONFLICT", '复核请求键已绑定其他内容')
        if existing is None:
            db.add(SyncIdentity(key=key, value=value))
        pointer = db.get(SyncIdentity, f'table-submission:{batch_id}')
        if pointer is None:
            db.add(SyncIdentity(key=f'table-submission:{batch_id}', value=value))
        else:
            pointer.value = value
    return proof
