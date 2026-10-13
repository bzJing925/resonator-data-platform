"""持久处理运行、条目问题与暂存结果。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_text() -> str:
    return str(uuid4())


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid_text)
    task_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("upload_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    # Keep the numeric batch reference for audit even after physical batch removal.
    batch_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    retry_of_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="SET NULL"), nullable=True
    )
    base_result_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    base_source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    mapping_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    algorithm_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    quality_status: Mapped[str] = mapped_column(Text, default="unknown", nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_phase: Mapped[str] = mapped_column(Text, default="queued", nullable=False)
    executor_ids: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    published_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','success','failed','cancel_requested','cancelled')",
            name="ck_processing_run_status",
        ),
        CheckConstraint(
            "quality_status IN ('unknown','complete','partial','invalid')",
            name="ck_processing_run_quality_status",
        ),
        CheckConstraint(
            "dispatch_phase IN ('queued','extract','extract_handoff',"
            "'compute','publish','finished')",
            name="ck_processing_run_dispatch_phase",
        ),
        CheckConstraint(
            "kind IN ('upload','reextract','redeembed','recompute','remap')",
            name="ck_processing_run_kind",
        ),
        CheckConstraint("attempt >= 1", name="ck_processing_run_attempt"),
        CheckConstraint("published_count >= 0", name="ck_processing_run_published_count"),
        Index("idx_processing_runs_status_heartbeat", "status", "heartbeat_at"),
        Index("idx_processing_runs_batch_status", "batch_id", "status"),
    )


class ProcessingItem(Base):
    __tablename__ = "processing_items"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid_text)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="RESTRICT"), nullable=False
    )
    source_device_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_member: Mapped[str] = mapped_column(Text, nullable=False)
    port: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Non-null normalized key makes NULL ports unique on SQLite and PostgreSQL alike.
    port_key: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    unit_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "unit_kind IN ('file','device','table_row')", name="ck_processing_item_unit_kind"
        ),
        CheckConstraint(
            "status IN ('pending','running','success','failed','skipped','cancelled')",
            name="ck_processing_item_status",
        ),
        CheckConstraint("attempt >= 1", name="ck_processing_item_attempt"),
        CheckConstraint(
            "(port IS NULL AND port_key = '') OR (port IS NOT NULL AND port_key = port)",
            name="ck_processing_item_port_key",
        ),
        UniqueConstraint(
            "run_id", "unit_kind", "source_member", "port_key", name="uq_processing_item_input"
        ),
        Index("idx_processing_items_run_status", "run_id", "status"),
    )


class QualityIssue(Base):
    __tablename__ = "quality_issues"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid_text)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="RESTRICT"), nullable=False
    )
    item_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_items.id", ondelete="SET NULL"), nullable=True
    )
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("severity IN ('error','warning','info')", name="ck_quality_issue_severity"),
        Index("idx_quality_issues_run_severity", "run_id", "severity"),
    )


class StagedDeviceRow(Base):
    __tablename__ = "staged_device_rows"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="CASCADE"), primary_key=True
    )
    source_device_key: Mapped[str] = mapped_column(Text, primary_key=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_staged_device_rows_digest", "payload_digest"),)
