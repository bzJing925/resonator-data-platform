"""工艺参数表：ICP 刻蚀 / Mo 生长 / AlN 生长。

独立数据域（暂不关联 devices/batches），来源：副本SICM_AlN_DT.xlsx，
由 scripts/import_process_excel.py 一次性导入。
详见 docs/superpowers/specs/2026-07-20-process-params-design.md。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Float, Integer, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ProcessIcpEtchRun(Base, TimestampMixin):
    """ICP 刻蚀工艺记录（SPM2 ICP Etch sheet，3 个表头块合并）。"""

    __tablename__ = "process_icp_etch_runs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    block_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    run_no: Mapped[str] = mapped_column(Text, nullable=False)
    etch_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    etch_rate_nm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    thk_a: Mapped[float | None] = mapped_column(Float, nullable=True)  # 埃
    wiw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wtw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessMoDepRun(Base, TimestampMixin):
    """Mo 底电极生长工艺记录（SPM3 Mo sheet）。"""

    __tablename__ = "process_mo_dep_runs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    run_no: Mapped[str] = mapped_column(Text, nullable=False)
    group_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    ar_flow_sccm: Mapped[float | None] = mapped_column(Float, nullable=True)
    dc_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    dep_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    dep_rate_nm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    thk_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    wiw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wtw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_avg_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_wiw_range_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_wtw_range_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    rs_ohm_sq: Mapped[float | None] = mapped_column(Float, nullable=True)
    resistivity_uohm_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    rs_uni_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    roughness_ra_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    xrd_rc_fwhm_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessAlnDepRun(Base, TimestampMixin):
    """AlN/AlScN 压电层生长工艺记录（SPM4 AlScN(30AT) + AlSc 两 sheet 合并）。"""

    __tablename__ = "process_aln_dep_runs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    source_sheet: Mapped[str] = mapped_column(Text, nullable=False)
    recipe_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_no: Mapped[str] = mapped_column(Text, nullable=False)
    group_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    n2_flow_sccm: Mapped[float | None] = mapped_column(Float, nullable=True)
    ar_flow_sccm: Mapped[float | None] = mapped_column(Float, nullable=True)
    dc_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    dep_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    dep_rate_nm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    thk_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    wiw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wtw_1sig_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_avg_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_wiw_range_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_wtw_range_mpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    refractive_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    roughness_ra_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    aog_per_um2: Mapped[float | None] = mapped_column(Float, nullable=True)
    xrd_rc_fwhm_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
