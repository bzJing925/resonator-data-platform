"""桌面版网页数据同步 API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import DbSession
from app.services.web_sync_service import WebSyncError, pull_from_web, push_batch_to_web

router = APIRouter(prefix="/web-sync", tags=["web-sync"])


class PushRequest(BaseModel):
    token: str = Field(min_length=1, max_length=500)


@router.post("/pull")
def pull_web_data(db: DbSession) -> dict[str, int]:
    try:
        return pull_from_web(db)
    except WebSyncError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/push/{batch_no:path}")
def push_web_data(batch_no: str, body: PushRequest, db: DbSession) -> dict[str, str | int]:
    try:
        return push_batch_to_web(db, batch_no, body.token)
    except WebSyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
