"""两阶段上传检查草稿。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_text() -> str:
    return str(uuid4())


class UploadDraft(Base):
    __tablename__ = "upload_drafts"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid_text)
    group_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    input_digest: Mapped[str] = mapped_column(Text, nullable=False)
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    upload_options: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    report: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_tasks.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('preparing','ready','failed','consumed','expired')",
            name="ck_upload_draft_status",
        ),
        Index("idx_upload_drafts_group", "group_id"),
        Index("idx_upload_drafts_status_expires", "status", "expires_at"),
    )
