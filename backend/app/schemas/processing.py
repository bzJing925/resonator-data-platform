"""任务、执行阶段与处理事件的共享契约。"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ProcessingStatus = Literal[
    "pending",
    "running",
    "success",
    "failed",
    "cancel_requested",
    "cancelled",
]
ProcessingItemStatus = Literal["pending", "running", "success", "failed", "skipped", "cancelled"]
ProcessingStage = Literal["extract", "deembed", "metrics", "done", "failed"]
QualityStatus = Literal["unknown", "complete", "partial", "invalid"]


class ProcessingEvent(BaseModel):
    schema_version: Literal[1]
    task_id: int = Field(gt=0)
    run_id: UUID
    event_seq: int = Field(ge=1)
    status: ProcessingStatus
    stage: ProcessingStage
    progress: dict[str, Any]
    summary: dict[str, Any]
