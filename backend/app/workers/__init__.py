"""Celery 任务入口。

启动：celery -A app.workers worker --loglevel=info
"""

from __future__ import annotations

# worker 启动时注册任务
from app.workers import (
    compute_batch,  # noqa: E402,F401
    extract_batch,  # noqa: E402,F401
    pipeline_batch,  # noqa: E402,F401
    process_batch,  # noqa: E402,F401
)
from app.workers.celery_app import celery_app
