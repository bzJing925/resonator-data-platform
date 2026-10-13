"""来源批次内输入成员到永久器件身份的登记。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_text() -> str:
    return str(uuid4())


class SourceInput(Base):
    __tablename__ = "source_inputs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid_text)
    # No cascading batch FK: this identity map remains useful after trash cleanup.
    batch_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=False
    )
    member_key: Mapped[str] = mapped_column(Text, nullable=False)
    port: Mapped[str | None] = mapped_column(Text, nullable=True)
    port_key: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    source_device_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    input_digest: Mapped[str] = mapped_column(Text, nullable=False)
    current_relpath: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "(port IS NULL AND port_key = '') OR (port IS NOT NULL AND port_key = port)",
            name="ck_source_input_port_key",
        ),
        UniqueConstraint("batch_id", "member_key", "port_key", name="uq_source_input_member"),
        Index("idx_source_inputs_batch", "batch_id"),
        Index("idx_source_inputs_relpath", "batch_id", "current_relpath"),
    )
