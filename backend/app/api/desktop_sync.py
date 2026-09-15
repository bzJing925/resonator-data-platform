"""桌面角色同步端点：前端调用，驱动 server_client/desktop_sync_service。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import DbSession
from app.config import get_settings
from app.services import desktop_sync_service, server_client
from app.services.server_client import ServerClient, ServerConfig, ServerError

router = APIRouter(prefix="/desktop-sync", tags=["desktop-sync"])


class ConfigIn(BaseModel):
    server_url: str = Field(min_length=1, max_length=500)
    username: str = Field(default="anonymous", max_length=100)


def _load_client() -> ServerClient:
    config = server_client.load_config()
    if config is None:
        raise HTTPException(status_code=400, detail="尚未配置服务器地址")
    return ServerClient(config)


@router.get("/status")
def status(db: DbSession) -> dict[str, Any]:
    settings = get_settings()
    config = server_client.load_config()
    reachable = False
    if settings.is_desktop and config is not None:
        try:
            with ServerClient(config) as client:
                reachable = client.reachable()
        except Exception:  # noqa: BLE001 — 可达性探测不允许抛出
            reachable = False
    unsynced = (
        [b.batch_no for b in desktop_sync_service.list_unsynced_batches(db)]
        if settings.is_desktop
        else []
    )
    return {
        "enabled": settings.is_desktop,
        "configured": config is not None,
        "server_url": config.server_url if config else None,
        "username": config.username if config else None,
        "reachable": reachable,
        "unsynced_batches": unsynced,
    }


@router.get("/config")
def get_config() -> dict[str, str | None]:
    config = server_client.load_config()
    if config is None:
        return {"server_url": None, "username": None}
    return {"server_url": config.server_url, "username": config.username}


@router.put("/config")
def put_config(body: ConfigIn) -> dict[str, str]:
    config = ServerConfig(server_url=body.server_url, username=body.username)
    server_client.save_config(config)
    return {"server_url": config.server_url, "username": config.username}


@router.post("/pull")
def pull(db: DbSession) -> dict[str, int]:
    try:
        with _load_client() as client:
            return desktop_sync_service.pull_from_server(db, client)
    except ServerError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/push/{batch_no:path}")
def push(batch_no: str, db: DbSession) -> dict[str, Any]:
    try:
        with _load_client() as client:
            return desktop_sync_service.push_batch_to_server(db, client, batch_no)
    except ServerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
