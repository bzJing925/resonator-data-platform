"""跨端同步请求、回执与明确确认状态。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_text() -> str:
    return str(uuid4())


class SyncOperation(Base):
    __tablename__ = "sync_operations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid_text)
    origin_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_batch_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_device_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    request_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expected: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("target IN ('server','web')", name="ck_sync_operation_target"),
        CheckConstraint(
            "state IN ('local_only','pending','syncing','confirmed','conflict','failed','unknown')",
            name="ck_sync_operation_state",
        ),
        CheckConstraint(
            "source_revision IS NULL OR source_revision >= 0", name="ck_sync_operation_revision"
        ),
        Index("idx_sync_operations_source_target", "origin_id", "source_batch_id", "target"),
    )
