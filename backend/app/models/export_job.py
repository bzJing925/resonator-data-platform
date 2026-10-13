"""全筛选查询导出任务元数据。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_text() -> str:
    return str(uuid4())


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid_text)
    request_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    request_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    executor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    source_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    progress_pct: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    query_spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    expected_sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("format IN ('csv','xlsx')", name="ck_export_job_format"),
        CheckConstraint(
            "status IN ('pending','running','success','failed','cancel_requested','cancelled')",
            name="ck_export_job_status",
        ),
        CheckConstraint("progress_pct BETWEEN 0 AND 100", name="ck_export_job_progress"),
        CheckConstraint("rows >= 0", name="ck_export_job_rows"),
        Index("idx_export_jobs_status_created", "status", "created_at"),
        Index("idx_export_jobs_expires", "expires_at"),
    )
