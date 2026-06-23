"""任务接口：详情 / 列表 / SSE 流。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.api.deps import DbSession
from app.config import get_settings
from app.db import get_db
from app.models import UploadTask
from app.schemas.task import TaskDetail, TaskListItem

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
    return TaskDetail.model_validate(task)


async def _stream_task_events(task_id: int) -> AsyncIterator[dict]:
    settings = get_settings()
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

            if task.status in ("success", "failed"):
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


@router.get("/{task_id}/stream")
async def stream_task(task_id: int) -> EventSourceResponse:
    return EventSourceResponse(_stream_task_events(task_id))
