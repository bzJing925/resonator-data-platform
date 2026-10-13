"""进度发布工具：同时更新 upload_tasks 行 + Redis pub/sub。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Batch, ProcessingRun, UploadTask
from app.services.processing_run import RunConflictError, assert_run_writable, finish_run


class ProgressPublisher:
    """封装 Redis pub/sub + DB 更新两件事。"""

    def __init__(
        self, task_id: int, *, run_id: str | None = None, lease_token: str | None = None
    ) -> None:
        from app.workers.run_execution import current_run

        self.task_id = task_id
        context = current_run.get()
        if context is not None and context.task_id == task_id:
            run_id, lease_token = context.run_id, context.lease_token
        self.run_id, self.lease_token = run_id, lease_token
        self.channel = f"task:{task_id}"
        settings = get_settings()
        if not settings.is_desktop:
            self._redis: Redis | None = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        else:
            self._redis = None

    def _publish(self, payload: dict[str, Any]) -> None:
        if self.run_id is not None:
            from app.db import SessionLocal

            with SessionLocal() as db:
                run = db.scalar(
                    select(ProcessingRun)
                    .where(
                        ProcessingRun.id == self.run_id,
                        ProcessingRun.lease_token == self.lease_token,
                    )
                    .with_for_update()
                )
                if run is None:
                    return
                sequence = int(run.summary_json.get("event_seq", 0)) + 1
                run.summary_json = {**run.summary_json, "event_seq": sequence}
                task = db.get(UploadTask, self.task_id)
                payload = {
                    **payload,
                    "schema_version": 1,
                    "run_id": run.id,
                    "event_seq": sequence,
                    "status": run.status,
                    "stage": task.stage,
                    "progress": {
                        "pct": task.progress_pct,
                        "stage_pct": task.stage_progress_pct,
                        "message": task.progress_msg,
                    },
                    "summary": run.summary_json,
                }
                db.commit()
        try:
            self._redis.publish(self.channel, json.dumps(payload))
        except Exception:
            # Redis 故障不应影响主流程
            pass

    def _is_cancelled(self, db: Session) -> bool:
        if self.run_id is not None:
            try:
                assert_run_writable(db, self.run_id, self.lease_token)
                return False
            except RunConflictError:
                return True
        if db.scalar(select(ProcessingRun.id).where(ProcessingRun.task_id == self.task_id)):
            return True  # Unleased legacy publishers cannot overwrite a new run.
        task = db.get(UploadTask, self.task_id)
        return task is not None and task.cancelled_at is not None

    def _task_clause(self):
        clause = UploadTask.id == self.task_id
        if self.run_id is not None:
            valid = (
                select(ProcessingRun.id)
                .join(Batch, Batch.id == ProcessingRun.batch_id)
                .where(
                    ProcessingRun.id == self.run_id,
                    ProcessingRun.task_id == self.task_id,
                    ProcessingRun.lease_token == self.lease_token,
                    ProcessingRun.status == "running",
                    Batch.active_run_id == self.run_id,
                    Batch.deleted_at.is_(None),
                )
                .exists()
            )
            clause = clause & (UploadTask.status == "running") & valid
        return clause

    def _finish(self, db: Session, status: str, payload: dict[str, Any]) -> None:
        if self.lease_token is None or self._is_cancelled(db):
            return
        db.commit()
        try:
            finish_run(
                db.bind,
                self.run_id,
                self.lease_token,
                status,
                error_code="PROCESSING_FAILED" if status == "failed" else None,
            )
        except RunConflictError:
            return
        self._publish(payload)

    def published(self, db: Session, *, batch_id: int, device_count: int) -> None:
        """Notify the already committed publication; never transition status here."""
        run = db.get(ProcessingRun, self.run_id, populate_existing=True)
        if run and run.status == "success" and run.lease_token == self.lease_token:
            self._publish(
                {
                    "event": "done",
                    "task_id": self.task_id,
                    "batch_id": batch_id,
                    "device_count": device_count,
                    "status": run.status,
                }
            )

    def update(self, db: Session, progress_pct: int, progress_msg: str) -> None:
        if self._is_cancelled(db):
            return
        progress_pct = max(0, min(100, int(progress_pct)))
        db.execute(
            update(UploadTask)
            .where(self._task_clause())
            .values(progress_pct=progress_pct, progress_msg=progress_msg, status="running")
        )
        db.commit()
        self._publish(
            {
                "task_id": self.task_id,
                "status": "running",
                "progress_pct": progress_pct,
                "progress_msg": progress_msg,
            }
        )

    def stage_update(
        self,
        db: Session,
        stage: str,
        stage_progress_pct: int,
        progress_pct: int | None = None,
        progress_msg: str | None = None,
    ) -> None:
        """更新当前阶段进度，并可同步更新总体进度。"""
        if self._is_cancelled(db):
            return
        stage_progress_pct = max(0, min(100, int(stage_progress_pct)))
        values: dict[str, Any] = {
            "stage": stage,
            "stage_progress_pct": stage_progress_pct,
            "status": "running",
        }
        if progress_pct is not None:
            values["progress_pct"] = max(0, min(100, int(progress_pct)))
        if progress_msg is not None:
            values["progress_msg"] = progress_msg
        db.execute(update(UploadTask).where(self._task_clause()).values(**values))
        db.commit()
        payload = {
            "task_id": self.task_id,
            "status": "running",
            "stage": stage,
            "stage_progress_pct": stage_progress_pct,
            **values,
        }
        # 只把客户端需要的状态发出去
        payload.pop("status", None)
        self._publish(payload)

    def done(self, db: Session, batch_id: int, device_count: int) -> None:
        if self.run_id is not None:
            self._finish(
                db, "success", {"event": "done", "batch_id": batch_id, "device_count": device_count}
            )
            return
        if self._is_cancelled(db):
            return
        db.execute(
            update(UploadTask)
            .where(self._task_clause())
            .values(
                status="success",
                stage="done",
                stage_progress_pct=100,
                progress_pct=100,
                progress_msg=f"完成，共入库 {device_count} 行",
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()
        self._publish(
            {
                "task_id": self.task_id,
                "status": "success",
                "stage": "done",
                "stage_progress_pct": 100,
                "progress_pct": 100,
                "progress_msg": f"完成，共入库 {device_count} 行",
                "batch_id": batch_id,
                "device_count": device_count,
                "event": "done",
            }
        )

    def fail(self, db: Session, error_msg: str) -> None:
        if self.run_id is not None:
            self._finish(db, "failed", {"event": "failed", "error_msg": "处理失败，请查看问题报告"})
            return
        if self._is_cancelled(db):
            return
        db.execute(
            update(UploadTask)
            .where(self._task_clause())
            .values(
                status="failed",
                stage="failed",
                stage_progress_pct=0,
                error_msg=error_msg,
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()
        self._publish(
            {
                "task_id": self.task_id,
                "status": "failed",
                "stage": "failed",
                "stage_progress_pct": 0,
                "error_msg": error_msg,
                "event": "failed",
            }
        )

    def cancel(self, db: Session, error_msg: str = "已取消") -> None:
        if self.run_id is not None or db.scalar(
            select(ProcessingRun.id).where(ProcessingRun.task_id == self.task_id)
        ):
            return  # B10 owns confirmed cancellation for leased runs.
        db.execute(
            update(UploadTask)
            .where(self._task_clause())
            .values(
                status="cancelled",
                stage="failed",
                stage_progress_pct=0,
                progress_pct=0,
                progress_msg=error_msg,
                cancelled_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()
        self._publish(
            {
                "task_id": self.task_id,
                "status": "cancelled",
                "stage": "failed",
                "stage_progress_pct": 0,
                "progress_msg": error_msg,
                "event": "error",
            }
        )

    def start(self, db: Session, msg: str = "任务开始") -> None:
        if self._is_cancelled(db):
            return
        db.execute(
            update(UploadTask)
            .where(self._task_clause())
            .values(
                status="running",
                stage="extract",
                stage_progress_pct=0,
                progress_pct=0,
                progress_msg=msg,
            )
        )
        db.commit()
        self._publish(
            {
                "task_id": self.task_id,
                "status": "running",
                "stage": "extract",
                "stage_progress_pct": 0,
                "progress_pct": 0,
                "progress_msg": msg,
                "event": "start",
            }
        )
