"""Server v3 protocol coexists with v2; local preferences never send remote mutations."""
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.api.replication import TableIn
from app.config import get_settings
from app.models import Batch, SyncIdentity
from app.schemas.replication_v3 import (
    ReplicaPreferenceRequest,
    SourceOperation,
    TableReconfirmRequest,
)
from app.schemas.source import SourceRef
from app.services import batch_lifecycle, replica_service
from app.services import replication_v3 as svc
from app.services.api_errors import api_error

router = APIRouter(tags=['replication-v3'])


def ensure_server(db):
    settings = get_settings()
    if settings.is_desktop and not settings.ALN_SERVER_MODE:
        raise api_error(409, "SOURCE_STATE_CONFLICT", '请连接源服务器')
    if db.get(SyncIdentity, 'server') is None:
        raise api_error(503, "SOURCE_NOT_READY", '来源身份未初始化')


@router.get('/sync/v3/catalog')
def catalog(db: DbSession):
    ensure_server(db)
    return svc.catalog(db)


@router.get('/sync/v3/batches/{batch_id}')
def export_batch(batch_id: int, db: DbSession):
    ensure_server(db)
    with svc.source_snapshot(db.bind) as source:
        payload = replica_service.export_batch(source, batch_id)
        payload['protocol'] = 3
        return payload


@router.post('/sync/v3/tables')
def table(body: TableIn, db: DbSession):
    ensure_server(db)
    target = body.payload.get('target')
    if target:
        if not isinstance(target, dict):
            raise api_error(422, 'SOURCE_INPUT_INVALID', '关联目标格式无效')
        reference = target.get('source_ref')
        if reference is not None and not isinstance(reference, dict):
            raise api_error(422, 'SOURCE_INPUT_INVALID', '关联来源格式无效')
        if not reference or reference.get('source_revision') != target.get('revision'):
            raise api_error(409, "SOURCE_STATE_CONFLICT",
                            '关联来源缺少原始UUID/版本，请重新核对并提交')
        try:
            reference = SourceRef.model_validate(reference).model_dump(mode='json')
        except ValidationError as exc:
            raise api_error(422, 'SOURCE_INPUT_INVALID', '关联来源格式无效') from exc
        body.payload['target']['source_ref'] = reference
        local = svc.resolve(db, reference.get('origin_id'), reference.get('source_batch_id'))
        if local['batch_no'] != target.get('batch_no'):
            raise api_error(409, "SOURCE_STATE_CONFLICT", '关联来源名字已变，请重新核对')
    try:
        payload = replica_service.accept_table(db, body.key, body.payload)
    except (KeyError, TypeError, ValueError) as exc:
        db.rollback()
        raise api_error(422, 'TABLE_INPUT_INVALID', '表格同步数据无效') from exc
    except IntegrityError as exc:
        db.rollback()
        raise api_error(409, 'TABLE_SOURCE_CONFLICT', '来源或数据约束冲突，未覆盖已有批次') from exc
    with svc.source_snapshot(db.bind) as source:
        payload = replica_service.export_batch(source, payload['remote_id'])
        payload['protocol'] = 3
        return payload


@router.get('/sources/batches/{origin_id}/{source_batch_id}/resolve')
def resolve(origin_id: UUID, source_batch_id: UUID, db: DbSession):
    return svc.resolve(db, origin_id, source_batch_id)


@router.post('/replica-preferences')
def preference(body: ReplicaPreferenceRequest, db: DbSession):
    db.rollback()
    return svc.set_preference(db.bind, body.source_ref, body.hidden)


def _owned_name(db, origin, source_batch):
    found = svc.resolve(db, origin, source_batch)
    batch = db.get(Batch, found['batch_id'])
    if batch.source in {'server', 'web'}:
        raise api_error(409, "SOURCE_STATE_CONFLICT", '该来源仅是副本，请连接拥有此批次的服务器')
    return found['batch_no']


@router.get('/sync/v3/sources/{origin_id}/{source_batch_id}/deletion-impact')
def source_deletion_impact(origin_id: UUID, source_batch_id: UUID, db: DbSession):
    ensure_server(db)
    name = _owned_name(db, origin_id, source_batch_id)
    from sqlalchemy import select

    batch = db.scalar(select(Batch).where(Batch.batch_no == name))
    return batch_lifecycle.deletion_impact(db, batch)


@router.delete('/sync/v3/sources/{origin_id}/{source_batch_id}')
def delete_source(origin_id: UUID, source_batch_id: UUID, body: SourceOperation, db: DbSession):
    ensure_server(db)
    name = _owned_name(db, origin_id, source_batch_id)
    db.rollback()
    return batch_lifecycle.soft_delete(db.bind, name, expected_revision=body.expected_revision,
                                     expected_source=(str(origin_id), str(source_batch_id)))


@router.post('/sync/v3/sources/{origin_id}/{source_batch_id}/restore')
def restore_source(origin_id: UUID, source_batch_id: UUID, body: SourceOperation, db: DbSession):
    ensure_server(db)
    name = _owned_name(db, origin_id, source_batch_id)
    db.rollback()
    return batch_lifecycle.restore(db.bind, name, expected_revision=body.expected_revision,
                                     expected_source=(str(origin_id), str(source_batch_id)))


@router.post('/desktop-sync/table-targets/{batch_id}/reconfirm')
def reconfirm_table(batch_id: int, body: TableReconfirmRequest, db: DbSession):
    db.rollback()
    return svc.reconfirm_table_target(db.bind, batch_id, body.source_ref, body.request_key)


@router.get('/replica-preferences')
def preferences(db: DbSession):
    from sqlalchemy import select

    from app.models import LocalReplicaPreference
    items = []
    for preference in db.scalars(select(LocalReplicaPreference)).all():
        try:
            source = svc.resolve(db, preference.origin_id, preference.source_batch_id)
        except HTTPException:
            source = {'batch_id': None, 'batch_no': None, 'source_ref': {
                'origin_id': preference.origin_id, 'source_batch_id': preference.source_batch_id,
                'source_device_key': None, 'source_revision': None}}
        items.append({**source, 'hidden': preference.hidden})
    return {'items': items}
