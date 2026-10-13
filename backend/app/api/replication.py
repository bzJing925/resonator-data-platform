"""Native server replication protocol v2."""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import get_settings
from app.models import SyncIdentity
from app.services import replica_service as svc

router = APIRouter(prefix="/sync/v2", tags=["replication"])


def ensure_initialized(db):
    if not get_settings().ALN_SERVER_MODE:
        raise HTTPException(409, "目标不是原生服务器，请填写服务器地址")
    if db.bind.dialect.name != "sqlite":
        raise HTTPException(409, "此同步协议需要原生 SQLite 服务器")
    # Native startup initializes these before accepting requests.
    if db.scalar(select(SyncIdentity).limit(1)) is None:
        raise HTTPException(503, "服务器同步元数据未初始化，请重启服务器")


@router.get("/catalog")
def catalog(db: DbSession):
    ensure_initialized(db)
    return svc.catalog(db)


@router.get("/batches/{batch_id}")
def batch(batch_id: int, db: DbSession):
    ensure_initialized(db)
    return svc.export_batch(db, batch_id)


class TableIn(BaseModel):
    key: str = Field(min_length=1, max_length=150)
    payload: dict[str, Any]


@router.post("/tables")
def table(body: TableIn, db: DbSession):
    ensure_initialized(db)
    try:
        return svc.accept_table(db, body.key, body.payload)
    except (KeyError, TypeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, f"表格同步数据无效: {exc}") from exc
