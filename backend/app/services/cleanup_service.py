"""Compatibility entry restricted to the leased, retained purge service."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch
from app.services.artifact_lifecycle import purge_due


def delete_batch_and_files(db: Session, batch_no: str) -> bool:
    """Internal purge only. Active batches and unexpired retention are never removed."""
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        return False
    engine = db.bind
    db.rollback()
    outcome = purge_due(engine)
    return outcome['success'] > 0
