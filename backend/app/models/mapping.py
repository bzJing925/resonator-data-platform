"""mappings + mapping_entries 表。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.batch import Batch


class Mapping(Base):
    __tablename__ = "mappings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    file_path: Mapped[str] = mapped_column(Text)
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entries: Mapped[list[MappingEntry]] = relationship(
        back_populates="mapping", cascade="all, delete-orphan"
    )
    batches: Mapped[list[Batch]] = relationship(back_populates="mapping")


class MappingEntry(Base):
    __tablename__ = "mapping_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mapping_id: Mapped[int] = mapped_column(
        ForeignKey("mappings.id", ondelete="CASCADE"), nullable=False
    )
    mark: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    eg: Mapped[float | None]
    fl: Mapped[float | None]
    ag: Mapped[float | None]
    area_s11: Mapped[int | None]
    area_s22: Mapped[int | None]
    has_pf: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_tokens: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    mapping: Mapped[Mapping] = relationship(back_populates="entries")

    __table_args__ = (UniqueConstraint("mapping_id", "mark", name="uq_mentry_mapping_mark"),)
