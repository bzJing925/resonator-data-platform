"""upload_tasks 表（Celery 任务进度）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UploadTask(Base):
    __tablename__ = "upload_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_no: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    progress_pct: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    # stage 把整条管线拆成“解压 / 指标计算”两个阶段；stage_progress_pct 是当前阶段内进度。
    stage: Mapped[str] = mapped_column(Text, default="extract", nullable=False)
    stage_progress_pct: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    progress_msg: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    celery_task_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','success','failed')",
            name="ck_uptask_status",
        ),
        CheckConstraint(
            "stage IN ('extract','metrics','done','failed')",
            name="ck_uptask_stage",
        ),
        CheckConstraint(
            "progress_pct BETWEEN 0 AND 100",
            name="ck_uptask_progress",
        ),
        CheckConstraint(
            "stage_progress_pct BETWEEN 0 AND 100",
            name="ck_uptask_stage_progress",
        ),
        Index("idx_uptask_status_started", "status", "started_at"),
    )
