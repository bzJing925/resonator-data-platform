"""回收站、删除保留与延迟清理元数据。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_text() -> str:
    return str(uuid4())


class CleanupJob(Base):
    __tablename__ = "cleanup_jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid_text)
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)
    resource_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','success','failed','cancelled')",
            name="ck_cleanup_job_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_cleanup_job_attempts"),
        Index("idx_cleanup_jobs_status_not_before", "status", "not_before"),
        Index("idx_cleanup_jobs_batch", "batch_id"),
    )


class Tombstone(Base):
    __tablename__ = "tombstones"

    origin_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_batch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restored_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("state IN ('active','deleted')", name="ck_tombstone_state"),
        CheckConstraint("revision >= 0", name="ck_tombstone_revision"),
        Index("idx_tombstones_state_revision", "state", "revision"),
    )


class LocalReplicaPreference(Base):
    __tablename__ = "local_replica_preferences"

    origin_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_batch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ArtifactLease(Base):
    __tablename__ = "artifact_leases"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid_text)
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False)
    resource_path: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        CheckConstraint("purpose IN ('run','download','export')", name="ck_artifact_lease_purpose"),
        Index("idx_artifact_leases_batch_expiry", "batch_id", "expires_at"),
    )
