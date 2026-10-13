"""Celery app 定义（不导入具体任务模块，方便后端二进制复用）。"""

from __future__ import annotations

import os

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "aln",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    beat_schedule={"artifact-cleanup": {
        "task": "app.workers.maintenance.purge_artifacts",
        "schedule": settings.CLEANUP_INTERVAL_SECONDS,
    }},
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # CELERY_TASK_ALWAYS_EAGER=1 时任务在请求进程内联执行（端到端测试免 Redis/worker）
    task_always_eager=os.environ.get("CELERY_TASK_ALWAYS_EAGER") == "1",
)
