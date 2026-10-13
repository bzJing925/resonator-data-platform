"""One explicit scope for Batch-associated reads; independent Process tables are unaffected."""

from sqlalchemy import exists, select

from app.models import Batch, Device, LocalReplicaPreference
from app.services.api_errors import api_error


def active_batch_clause(model=Batch):
    hidden = exists(select(LocalReplicaPreference.origin_id).where(
        LocalReplicaPreference.origin_id == model.origin_id,
        LocalReplicaPreference.source_batch_id == model.source_batch_id,
        LocalReplicaPreference.hidden.is_(True)))
    return model.deleted_at.is_(None) & ~hidden


def get_active_batch(db, batch_no=None, *, batch_id=None):
    predicate = Batch.id == batch_id if batch_id is not None else Batch.batch_no == batch_no
    batch = db.scalar(select(Batch).where(predicate).execution_options(populate_existing=True))
    if batch is None:
        raise api_error(404, "SOURCE_NOT_FOUND", "批次不存在")
    if batch.deleted_at is not None:
        raise api_error(410, "SOURCE_UNAVAILABLE", "批次已删除，请在回收站恢复")
    preference = (db.get(LocalReplicaPreference, (batch.origin_id, batch.source_batch_id))
                  if batch.origin_id and batch.source_batch_id else None)
    if preference is not None and preference.hidden:
        raise api_error(410, "SOURCE_UNAVAILABLE", "本地副本已隐藏，请在设置中取消隐藏")
    return batch


def get_active_device(db, device_id):
    device = db.get(Device, device_id)
    if device is None:
        raise api_error(404, "SOURCE_NOT_FOUND", "器件不存在")
    get_active_batch(db, batch_id=device.batch_id)
    return device
