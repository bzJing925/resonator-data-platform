"""任务接口：详情 / 列表 / SSE 流 / 取消。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.api.deps import DbSession
from app.config import get_settings
from app.db import get_db
from app.models import Batch, UploadTask
from app.schemas.task import TaskDetail, TaskListItem
from app.services.cleanup_service import delete_batch_and_files
from app.workers.celery_app import celery_app
from app.workers.local_queue import get_local_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

# SSE 流最长持续时间。worker 崩溃 / Redis 消息丢失时，避免连接永远 yield ping
# 占住 pubsub 与 fd；超时后 yield error 让客户端要么放弃要么重连查 /tasks/{id}。
_STREAM_MAX_SECONDS = 3600


@router.get("", response_model=list[TaskListItem])
def list_tasks(db: DbSession, limit: int = 50) -> list[TaskListItem]:
    limit = max(1, min(limit, 200))
    stmt = select(UploadTask).order_by(UploadTask.started_at.desc()).limit(limit)
    rows = db.scalars(stmt).all()
    return [TaskListItem.model_validate(r) for r in rows]


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: int, db: DbSession) -> TaskDetail:
    task = db.get(UploadTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    batch = db.scalar(select(Batch).where(Batch.batch_no == task.batch_no))
    raw_zip_deleted = True
    if batch and batch.raw_zip_path and Path(batch.raw_zip_path).exists():
        raw_zip_deleted = False
    data = TaskDetail.model_validate(task)
    data.raw_zip_deleted = raw_zip_deleted
    return data


@router.post("/{task_id}/cancel", response_model=TaskDetail)
def cancel_task(task_id: int, db: DbSession) -> TaskDetail:
    task = db.get(UploadTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    if task.status == "cancelled":
        data = TaskDetail.model_validate(task)
        data.raw_zip_deleted = True
        return data
    if task.status not in ("pending", "running"):
        raise HTTPException(status_code=409, detail="任务已结束，无法取消")

    task.cancelled_at = datetime.now(UTC)
    db.commit()

    settings = get_settings()
    cleanup_ok = True
    revoke_ok = True
    files_deleted = False
    try:
        if settings.is_desktop:
            get_local_queue().request_cancel(task_id)
        elif task.celery_task_id:
            celery_app.control.revoke(
                task.celery_task_id, terminate=task.status == "running"
            )
    except Exception:
        logger.exception("取消任务 %s 时撤销 worker 失败", task_id)
        revoke_ok = False

    try:
        # 只有原始上传任务才删除批次与文件；重处理任务取消时不应误删已有数据
        if task.batch_no and task.kind == "upload":
            delete_batch_and_files(db, task.batch_no)
            files_deleted = True
    except Exception:
        logger.exception("取消任务 %s 时清理文件失败", task_id)
        cleanup_ok = False

    task.status = "cancelled"
    task.stage = "failed"
    task.stage_progress_pct = 0
    task.progress_pct = 0
    if not revoke_ok and not cleanup_ok:
        task.error_msg = "取消成功，但撤销 worker 和文件清理均失败"
        task.progress_msg = "已取消，但撤销 worker 和文件清理均失败"
    elif not revoke_ok:
        task.error_msg = "取消成功，但撤销 worker 失败"
        task.progress_msg = "已取消，但撤销 worker 失败"
    elif not cleanup_ok:
        task.error_msg = "取消成功，但文件清理失败"
        task.progress_msg = "已取消，但文件清理失败"
    else:
        task.error_msg = None
        task.progress_msg = "已取消并清理文件"
    task.finished_at = datetime.now(UTC)
    db.commit()

    data = TaskDetail.model_validate(task)
    data.raw_zip_deleted = files_deleted
    return data


async def _stream_task_events(task_id: int) -> AsyncIterator[dict]:
    settings = get_settings()
    if settings.is_desktop:
        async for item in _stream_task_polling(task_id):
            yield item
        return

    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    channel = f"task:{task_id}"
    await pubsub.subscribe(channel)

    try:
        with next(get_db()) as db:
            task = db.get(UploadTask, task_id)
            if task is None:
                yield {
                    "event": "error",
                    "data": json.dumps({"error_msg": f"任务 {task_id} 不存在"}),
                }
                return

            yield {
                "event": "progress",
                "data": json.dumps(
                    {
                        "progress_pct": task.progress_pct,
                        "progress_msg": task.progress_msg,
                        "status": task.status,
                    }
                ),
            }

            if task.status in ("success", "failed", "cancelled"):
                event = "done" if task.status == "success" else "error"
                payload: dict = {
                    "status": task.status,
                    "batch_no": task.batch_no,
                }
                if task.error_msg:
                    payload["error_msg"] = task.error_msg
                yield {"event": event, "data": json.dumps(payload)}
                return

        start_ts = time.monotonic()
        while True:
            if time.monotonic() - start_ts > _STREAM_MAX_SECONDS:
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"error_msg": f"流超时 {_STREAM_MAX_SECONDS}s，请重新拉取任务状态"}
                    ),
                }
                return
            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0),
                    timeout=20.0,
                )
            except TimeoutError:
                yield {"event": "ping", "data": "{}"}
                continue

            if msg is None:
                yield {"event": "ping", "data": "{}"}
                continue

            data = msg.get("data")
            if not isinstance(data, str):
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue

            event_name = payload.get("event") or "progress"
            status_val = payload.get("status")
            if status_val == "success":
                event_name = "done"
            elif status_val == "failed":
                event_name = "error"

            yield {"event": event_name, "data": json.dumps(payload)}

            if event_name in ("done", "error"):
                return
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
        except Exception:
            pass
        try:
            await r.close()
        except Exception:
            pass


async def _stream_task_polling(task_id: int) -> AsyncIterator[dict]:
    start_ts = time.monotonic()

    while True:
        with next(get_db()) as db:
            task = db.get(UploadTask, task_id)
            if task is None:
                yield {
                    "event": "error",
                    "data": json.dumps({"error_msg": f"任务 {task_id} 不存在"}),
                }
                return

            yield {
                "event": "progress",
                "data": json.dumps(
                    {
                        "progress_pct": task.progress_pct,
                        "progress_msg": task.progress_msg,
                        "status": task.status,
                        "stage": task.stage,
                        "stage_progress_pct": task.stage_progress_pct,
                    }
                ),
            }

            if task.status == "success":
                yield {
                    "event": "done",
                    "data": json.dumps({"status": "success", "batch_no": task.batch_no}),
                }
                return
            if task.status in ("failed", "cancelled"):
                yield {
                    "event": "error",
                    "data": json.dumps({"status": task.status, "error_msg": task.error_msg}),
                }
                return

        if time.monotonic() - start_ts > _STREAM_MAX_SECONDS:
            yield {
                "event": "error",
                "data": json.dumps(
                    {"error_msg": f"流超时 {_STREAM_MAX_SECONDS}s，请重新拉取任务状态"}
                ),
            }
            return

        await asyncio.sleep(1.0)


@router.get("/{task_id}/stream")
async def stream_task(task_id: int) -> EventSourceResponse:
    return EventSourceResponse(_stream_task_events(task_id))
