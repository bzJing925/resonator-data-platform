"""Explicit access to retained deleted batches."""
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.models import Batch
from app.services.batch_lifecycle import deletion_impact

router = APIRouter(prefix="/trash", tags=["trash"])


@router.get("")
def list_trash(db: DbSession, page: Annotated[int, Query(ge=1)] = 1,
               size: Annotated[int, Query(ge=1, le=200)] = 20):
    scope = Batch.deleted_at.is_not(None)
    total = db.scalar(select(func.count()).select_from(Batch).where(scope)) or 0
    batches = db.scalars(select(Batch).where(scope).order_by(Batch.deleted_at.desc())
                        .offset((page - 1) * size).limit(size)).all()
    return {"total": total, "page": page, "size": size,
            "items": [deletion_impact(db, batch) for batch in batches]}
