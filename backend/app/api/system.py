"""系统接口：/health /stats。"""

from __future__ import annotations

import shutil

from fastapi import APIRouter
from redis import Redis
from sqlalchemy import func, select, text

from app.api.deps import DbSession
from app.config import get_settings
from app.models import Batch, Device, Mapping, UploadTask

router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: DbSession) -> dict:
    settings = get_settings()
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc!s}"

    redis_status = "ok"
    if settings.is_desktop:
        redis_status = "skipped"
    else:
        try:
            # 用 context manager 关闭连接 — /health 通常被监控按秒级轮询，
            # 没有 close 会让每次调用都新开一个 pool，逐渐耗光 redis 连接数。
            with Redis.from_url(settings.REDIS_URL, decode_responses=True) as r:
                r.ping()
        except Exception as exc:
            redis_status = f"error: {exc!s}"

    disk_free_gb: float | None = None
    disk_total_gb: float | None = None
    try:
        usage = shutil.disk_usage(str(settings.DATA_ROOT))
        disk_free_gb = round(usage.free / (1024**3), 2)
        disk_total_gb = round(usage.total / (1024**3), 2)
    except Exception:
        pass

    overall = "ok" if db_status == "ok" and redis_status in ("ok", "skipped") else "degraded"
    return {
        "status": overall,
        "db": db_status,
        "redis": redis_status,
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
    }


@router.get("/stats")
def stats(db: DbSession) -> dict:
    settings = get_settings()

    batches = db.scalar(select(func.count()).select_from(Batch)) or 0
    devices = db.scalar(select(func.count()).select_from(Device)) or 0
    mappings = db.scalar(select(func.count()).select_from(Mapping)) or 0
    tasks_pending = (
        db.scalar(
            select(func.count()).select_from(UploadTask).where(UploadTask.status == "pending")
        )
        or 0
    )
    tasks_running = (
        db.scalar(
            select(func.count()).select_from(UploadTask).where(UploadTask.status == "running")
        )
        or 0
    )

    disk_free_gb: float | None = None
    disk_used_gb: float | None = None
    try:
        usage = shutil.disk_usage(str(settings.DATA_ROOT))
        disk_free_gb = round(usage.free / (1024**3), 2)
        disk_used_gb = round(usage.used / (1024**3), 2)
    except Exception:
        pass

    return {
        "batches": batches,
        "devices": devices,
        "mappings": mappings,
        "disk_used_gb": disk_used_gb,
        "disk_free_gb": disk_free_gb,
        "tasks_pending": tasks_pending,
        "tasks_running": tasks_running,
    }
