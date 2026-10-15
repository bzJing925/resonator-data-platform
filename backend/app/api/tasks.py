"""任务接口：详情 / 列表 / SSE 流 / 取消。"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from app.api.deps import DbSession
from app.config import get_settings
from app.models import Batch, ProcessingItem, ProcessingRun, QualityIssue, UploadTask
from app.schemas.task import TaskDetail, TaskListItem
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
    run = db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == task_id))
    if run:
        data.run_id, data.quality_status, data.summary = (
            run.id,
            run.quality_status,
            run.summary_json,
        )
    return data


def _report_run(db, task_id):
    if db.get(UploadTask, task_id) is None:
        raise HTTPException(404, detail="任务不存在")
    return db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == task_id))


@router.get("/{task_id}/retry-options")
def get_retry_options(task_id: int, db: DbSession, offset: int = 0, limit: int = 100):
    from app.services.processing_retry import retry_items_query

    run = _report_run(db, task_id)
    revision = None if run is None else run.summary_json.get(
        "published_source_revision", run.base_source_revision
    )
    parent = db.get(ProcessingRun, run.retry_of_run_id) if run and run.retry_of_run_id else None
    offset, limit = max(0, offset), max(1, min(limit, 500))
    rows = []
    total = 0
    if run and revision is not None and run.status in {"success", "failed", "cancelled"}:
        base = retry_items_query(run, require_identity=True)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(
            base.order_by(ProcessingItem.id).offset(offset).limit(limit + 1)
        ).all()
    return {
        "task_id": task_id,
        "run_id": run.id if run else None,
        "expected_source_revision": revision,
        "retry_of_task_id": parent.task_id if parent else None,
        "total": total,
        "items": [{
            "id": item.id, "unit_kind": item.unit_kind,
            "source_member": item.source_member, "port": item.port,
            "status": item.status, "error_code": item.error_code,
        } for item in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/{task_id}/report")
def get_task_report(task_id: int, db: DbSession, offset: int = 0, limit: int = 100):
    run = _report_run(db, task_id)
    if run is None:
        return {
            "run_id": None,
            "quality_status": "unknown",
            "summary": {},
            "issues": [],
            "next_offset": None,
        }
    offset, limit = max(0, offset), max(1, min(limit, 500))
    rows = db.scalars(
        select(QualityIssue)
        .where(QualityIssue.run_id == run.id)
        .order_by(QualityIssue.created_at, QualityIssue.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return {
        "run_id": run.id,
        "quality_status": run.quality_status,
        "summary": run.summary_json,
        "issues": [
            {
                "id": r.id,
                "item_id": r.item_id,
                "severity": r.severity,
                "code": r.code,
                "message": r.message,
                "details": r.details_json,
            }
            for r in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/{task_id}/issues.csv")
def download_task_issues(task_id: int, db: DbSession):
    run = _report_run(db, task_id)
    run_id = run.id if run else None
    engine = db.get_bind()

    def chunks():
        from sqlalchemy.orm import Session

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "item_id", "severity", "code", "message", "details"])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        with Session(engine) as reader:
            rows = reader.scalars(
                select(QualityIssue)
                .where(QualityIssue.run_id == run_id)
                .order_by(QualityIssue.created_at, QualityIssue.id)
                .execution_options(yield_per=1000)
            )
            for row in rows:
                # Neutralize spreadsheet formula injection in exported untrusted text.
                values = [
                    row.id,
                    row.item_id or "",
                    row.severity,
                    row.code,
                    row.message,
                    json.dumps(row.details_json, ensure_ascii=False),
                ]
                writer.writerow(
                    ["'" + v if v.startswith(("=", "+", "-", "@")) else v for v in values]
                )
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)

    return StreamingResponse(
        chunks(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="task-{task_id}-issues.csv"'},
    )


@router.post("/{task_id}/cancel", response_model=TaskDetail)
def cancel_task(task_id: int, db: DbSession) -> TaskDetail:
    from app.services.processing_cancel import confirm_stopped, request_cancellation
    from app.services.processing_run import RunConflictError

    engine = db.get_bind()
    db.rollback()
    try:
        ticket = request_cancellation(engine, task_id)
    except RunConflictError as exc:
        raise HTTPException(
            404 if exc.code == "TASK_MISSING" else 409,
            detail={"code": exc.code, "message": str(exc), "retryable": False, "context": {}},
        ) from exc
    if ticket["status"] != "cancelled":
        try:
            if get_settings().is_desktop:
                removed = get_local_queue().request_cancel(task_id)
                if removed and (
                    ticket["dispatch_phase"] == "queued"
                    or (ticket["run_id"] is None and ticket["prior_status"] == "pending")
                ):
                    confirm_stopped(
                        engine, task_id, run_id=ticket["run_id"], lease_token=ticket["lease_token"]
                    )
            else:
                ids = {
                    value
                    for value in ticket["executor_ids"].values()
                    if isinstance(value, str) and not value.startswith("local-")
                }
                if ticket["celery_task_id"]:
                    ids.add(ticket["celery_task_id"])
                for executor_id in ids:
                    celery_app.control.revoke(executor_id, terminate=False)
        except Exception:
            from app.services.processing_cancel import record_stop_unconfirmed

            logger.exception("任务 %s 的停止请求投递失败", task_id)
            record_stop_unconfirmed(engine, task_id)
    db.expire_all()
    return get_task(task_id, db)


def _task_snapshot(db, task):
    run = db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == task.id).with_for_update())
    payload = {
        "task_id": task.id,
        "status": task.status,
        "stage": task.stage,
        "progress_pct": task.progress_pct,
        "stage_progress_pct": task.stage_progress_pct,
        "progress_msg": task.progress_msg,
        "batch_no": task.batch_no,
        "error_msg": task.error_msg,
        "progress": {
            "pct": task.progress_pct,
            "stage_pct": task.stage_progress_pct,
            "message": task.progress_msg,
        },
        "summary": {},
    }
    if run:
        sequence = int(run.summary_json.get("event_seq", 0)) + 1
        run.summary_json = {**run.summary_json, "event_seq": sequence}
        payload.update(
            schema_version=1,
            run_id=run.id,
            event_seq=sequence,
            status=run.status,
            summary=run.summary_json,
        )
        db.commit()
    return payload


def _snapshot_event(task_id):
    from app.db import SessionLocal

    with SessionLocal() as db:
        task = db.get(UploadTask, task_id)
        if task is None:
            return {"event": "error", "data": json.dumps({"error_msg": "任务不存在"})}, True
        payload = _task_snapshot(db, task)
    terminal = payload["status"] in {"success", "failed", "cancelled"}
    event = ("done" if payload["status"] == "success" else "error") if terminal else "progress"
    return {"event": event, "data": json.dumps(payload)}, terminal


async def _stream_task_events(task_id: int) -> AsyncIterator[dict]:
    settings = get_settings()
    if settings.is_desktop:
        async for item in _stream_task_polling(task_id):
            yield item
        return
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    try:
        try:
            await pubsub.subscribe(f"task:{task_id}")
        except Exception:
            async for item in _stream_task_polling(task_id):
                yield item
            return
        snapshot, terminal = _snapshot_event(task_id)
        yield snapshot
        if terminal:
            return
        last_sequence = json.loads(snapshot["data"]).get("event_seq", 0)
        started = time.monotonic()
        while time.monotonic() - started < _STREAM_MAX_SECONDS:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0), timeout=20.0
                )
            except TimeoutError:
                message = None
            except Exception:
                async for item in _stream_task_polling(task_id):
                    yield item
                return
            if message is None:
                snapshot, terminal = _snapshot_event(task_id)
                last_sequence = json.loads(snapshot["data"]).get("event_seq", last_sequence)
                yield snapshot
                if terminal:
                    return
                continue
            raw = message.get("data")
            if not isinstance(raw, str):
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            sequence = payload.get("event_seq")
            if sequence is not None and sequence <= last_sequence:
                continue
            if sequence is not None:
                last_sequence = sequence
            status = payload.get("status")
            terminal = status in {"success", "failed", "cancelled"}
            event = ("done" if status == "success" else "error") if terminal else "progress"
            yield {"event": event, "data": json.dumps(payload)}
            if terminal:
                return
        yield {
            "event": "stream_closed",
            "data": json.dumps({"code": "STREAM_TIMEOUT", "retryable": True}),
        }
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            await pubsub.aclose()
        with contextlib.suppress(Exception):
            await r.aclose()


async def _stream_task_polling(task_id: int) -> AsyncIterator[dict]:
    started = time.monotonic()
    while time.monotonic() - started < _STREAM_MAX_SECONDS:
        snapshot, terminal = _snapshot_event(task_id)
        yield snapshot
        if terminal:
            return
        await asyncio.sleep(1.0)
    yield {
        "event": "stream_closed",
        "data": json.dumps({"code": "STREAM_TIMEOUT", "retryable": True}),
    }


@router.get("/{task_id}/stream")
async def stream_task(task_id: int) -> EventSourceResponse:
    return EventSourceResponse(_stream_task_events(task_id))


class RetryRequest(BaseModel):
    request_key: str
    mode: Literal["failed"]
    item_ids: list[str] | None = None
    expected_source_revision: int = Field(ge=0)


@router.post("/{task_id}/retry", status_code=202)
def retry_task(task_id: int, body: RetryRequest, db: DbSession):
    from app.services.processing_retry import create_retry_run
    from app.services.processing_run import RunConflictError, record_dispatch_failure
    from app.workers.dispatch import (
        ConfirmedDispatchError,
        dispatch_batch_task,
        dispatch_prechecked_table,
        dispatch_reprocess_task,
    )

    engine = db.bind
    db.rollback()
    try:
        ticket = create_retry_run(
            engine, task_id, request_key=body.request_key,
            expected_source_revision=body.expected_source_revision, item_ids=body.item_ids,
        )
    except (RunConflictError, ValueError) as exc:
        code = getattr(exc, "code", "RETRY_REQUEST_INVALID")
        status = 404 if code == "RUN_MISSING" else 422 if code in {
            "RETRY_ITEMS_INVALID", "RETRY_INPUT_MISSING", "RETRY_SOURCE_UNKNOWN",
            "RETRY_REQUEST_INVALID",
        } else 409
        raise HTTPException(status, detail={
            "code": code, "message": str(exc), "retryable": False, "context": {},
        }) from exc
    run = db.get(ProcessingRun, ticket.run_id)
    # Admission is already committed. Only the first acceptance may dispatch;
    # replayed requests return the same task without a second queue delivery.
    from app.services.processing_run import _locked_session

    db.rollback()
    with _locked_session(engine) as delivery:
        owned = delivery.scalar(select(ProcessingRun).where(
            ProcessingRun.id == ticket.run_id,
        ).with_for_update())
        already = owned.config_json.get("retry_delivery_started", False)
        if not already:
            owned.config_json = {**owned.config_json, "retry_delivery_started": True}
        batch_id = owned.batch_id
    if not already:
        batch = db.get(Batch, batch_id)
        config = run.config_json
        try:
            if "table_stats" in config.get("precheck_report", {}):
                delivery_id = dispatch_prechecked_table(
                    ticket.task_id, ticket.run_id, ticket.lease_token
                )
            elif run.kind == "upload":
                delivery_id = dispatch_batch_task(
                    ticket.task_id, Path(batch.raw_zip_path), batch.batch_no, batch.mapping_id,
                    run_id=ticket.run_id, lease_token=ticket.lease_token,
                    **{key: config[key] for key in (
                        "f_start_ghz", "f_end_ghz", "deembed", "deembed_method", "process_type"
                    ) if key in config},
                )
            else:
                delivery_id = dispatch_reprocess_task(
                    ticket.task_id, batch.batch_no, batch.mapping_id, run.kind,
                    zip_path=Path(batch.raw_zip_path) if batch.raw_zip_path else None,
                    metrics=config.get("metrics"), run_id=ticket.run_id,
                    lease_token=ticket.lease_token,
                    **{key: config[key] for key in (
                        "f_start_ghz", "f_end_ghz", "deembed", "deembed_method", "process_type"
                    ) if key in config},
                )
            if delivery_id is None:
                raise ConfirmedDispatchError("队列未接受重试任务")
        except Exception as exc:
            db.rollback()
            record_dispatch_failure(
                engine, ticket, confirmed=isinstance(exc, ConfirmedDispatchError)
            )
            raise HTTPException(503, detail={
                "code": "RETRY_DISPATCH_FAILED", "message": "重试投递异常，任务与输入保留",
                "retryable": True, "context": {"task_id": ticket.task_id},
            }) from exc
    db.expire_all()
    task = db.get(UploadTask, ticket.task_id)
    return {
        "task_id": str(ticket.task_id), "run_id": ticket.run_id,
        "batch_no": task.batch_no, "status": task.status,
        "stream_url": f"/api/tasks/{ticket.task_id}/stream",
    }
