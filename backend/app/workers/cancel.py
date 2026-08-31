"""任务取消检测。"""

from __future__ import annotations

from app.config import get_settings
from app.db import SessionLocal
from app.models import UploadTask


class TaskCancelledError(Exception):
    """任务已被请求取消。"""


def is_task_cancelled(task_id: int) -> bool:
    """检测任务是否已请求取消。"""
    settings = get_settings()
    if settings.is_desktop:
        from app.workers.local_queue import get_local_queue

        return get_local_queue().is_cancelled(task_id)

    # server 模式直接查数据库（Celery revoke 会终止进程，这里作为兜底）
    try:
        with SessionLocal() as db:
            task = db.get(UploadTask, task_id)
            return task is not None and task.cancelled_at is not None
    except Exception:
        return False


def raise_if_cancelled(task_id: int) -> None:
    if is_task_cancelled(task_id):
        raise TaskCancelledError()
