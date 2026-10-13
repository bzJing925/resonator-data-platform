"""Transactional query clock and materialized-statistics freshness marker."""

from sqlalchemy import BigInteger, CheckConstraint, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DataEpoch(Base):
    __tablename__ = "data_epoch"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    stats_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_data_epoch_singleton"),
        CheckConstraint("epoch >= 0", name="ck_data_epoch_nonnegative"),
    )
