"""devices 表（核心，~50 万行起）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.batch import Batch


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), nullable=False
    )

    # 来源元信息
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    mark: Mapped[str | None] = mapped_column(Text)
    wafer: Mapped[int | None] = mapped_column(SmallInteger)
    folder_name: Mapped[str | None] = mapped_column(Text)
    coord: Mapped[str | None] = mapped_column(Text)
    x: Mapped[int | None] = mapped_column(SmallInteger)
    y: Mapped[int | None] = mapped_column(SmallInteger)

    # 类别 / 工艺
    eg: Mapped[float | None]
    fl: Mapped[float | None]
    ag: Mapped[float | None]
    pf: Mapped[str | None] = mapped_column(CHAR(1))
    area_n: Mapped[int | None] = mapped_column(Integer)
    area_um2: Mapped[int | None] = mapped_column(Integer)

    # 主峰
    fs_ghz: Mapped[float | None]
    fp_ghz: Mapped[float | None]
    zs_ohm: Mapped[float | None]
    zp_ohm: Mapped[float | None]
    qs: Mapped[float | None]
    qp: Mapped[float | None]
    qs_bodeq: Mapped[float | None]
    qp_bodeq: Mapped[float | None]
    dbqs: Mapped[float | None]
    dbqp: Mapped[float | None]
    bodeq_fitted: Mapped[float | None]
    bodeq_smooth: Mapped[float | None]
    bodeq_raw: Mapped[float | None]
    fbode_ghz: Mapped[float | None]
    k2eff_pct: Mapped[float | None]

    # 中间峰
    fp2_ghz: Mapped[float | None]
    fs2_ghz: Mapped[float | None]
    zp2_ohm: Mapped[float | None]
    zs2_ohm: Mapped[float | None]

    deembedded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    s_param_path: Mapped[str | None] = mapped_column(Text)
    s_param_port: Mapped[str] = mapped_column(Text, default="S11", nullable=False)

    batch: Mapped[Batch] = relationship(back_populates="devices")

    __table_args__ = (
        UniqueConstraint(
            "batch_id", "s_param_path", "s_param_port", name="uq_device_batch_sparam_port"
        ),
        CheckConstraint("pf IN ('Y','N')", name="ck_device_pf"),
        Index("idx_dev_batch_wafer", "batch_id", "wafer"),
        Index("idx_dev_eg_fl_ag", "eg", "fl", "ag"),
        Index("idx_dev_coord", "coord"),
        Index("idx_dev_mark", "mark"),
        Index("idx_dev_pf", "pf"),
        # Partial indexes: 只索引 Pass 数据，体积小、命中率高
        Index("idx_dev_pf_pass", "pf", postgresql_where=text("pf = 'Y'")),
        Index("idx_dev_fs_pass", "fs_ghz", postgresql_where=text("pf = 'Y'")),
        Index("idx_dev_fp", "fp_ghz"),
        Index("idx_dev_xy", "x", "y"),
    )
