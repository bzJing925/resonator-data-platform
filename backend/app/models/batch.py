"""batches 表。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.file_node import FileNode
    from app.models.mapping import Mapping


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_no: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    mapping_id: Mapped[int] = mapped_column(
        ForeignKey("mappings.id", ondelete="RESTRICT"), nullable=False
    )
    f_start_ghz: Mapped[float | None]
    f_end_ghz: Mapped[float | None]
    deembedded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deembed_method: Mapped[str] = mapped_column(Text, default="default", nullable=False)
    process_type: Mapped[str] = mapped_column(Text, default="AUTO", nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    raw_zip_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, default="http", nullable=False)
    device_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    uploaded_by: Mapped[str] = mapped_column(Text, default="anonymous", nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("upload_tasks.id", ondelete="SET NULL"))

    mapping: Mapped[Mapping] = relationship(back_populates="batches")

    @property
    def raw_zip_deleted(self) -> bool:
        return self.raw_zip_path is None

    devices: Mapped[list[Device]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )
    file_nodes: Mapped[list[FileNode]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "process_type IN ('AUTO','S1P','S2P','BOTH')",
            name="ck_batch_proc_type",
        ),
        Index("idx_batches_uploaded_at", "uploaded_at"),
        Index("idx_batches_mapping", "mapping_id"),
    )
