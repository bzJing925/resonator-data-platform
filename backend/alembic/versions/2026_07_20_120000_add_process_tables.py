"""add process parameter tables (icp_etch / mo_dep / aln_dep)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "2026_07_20_120000"
down_revision = "2026_07_06_160000"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "process_icp_etch_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("block_index", sa.SmallInteger(), nullable=False),
        sa.Column("run_no", sa.Text(), nullable=False),
        sa.Column("etch_time_sec", sa.Float(), nullable=True),
        sa.Column("etch_rate_nm_min", sa.Float(), nullable=True),
        sa.Column("thk_a", sa.Float(), nullable=True),
        sa.Column("wiw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("wtw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "process_mo_dep_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_no", sa.Text(), nullable=False),
        sa.Column("group_label", sa.Text(), nullable=True),
        sa.Column("ar_flow_sccm", sa.Float(), nullable=True),
        sa.Column("dc_power_kw", sa.Float(), nullable=True),
        sa.Column("dep_time_sec", sa.Float(), nullable=True),
        sa.Column("dep_rate_nm_min", sa.Float(), nullable=True),
        sa.Column("thk_nm", sa.Float(), nullable=True),
        sa.Column("wiw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("wtw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("stress_avg_mpa", sa.Float(), nullable=True),
        sa.Column("stress_wiw_range_mpa", sa.Float(), nullable=True),
        sa.Column("stress_wtw_range_mpa", sa.Float(), nullable=True),
        sa.Column("rs_ohm_sq", sa.Float(), nullable=True),
        sa.Column("resistivity_uohm_cm", sa.Float(), nullable=True),
        sa.Column("rs_uni_pct", sa.Float(), nullable=True),
        sa.Column("roughness_ra_nm", sa.Float(), nullable=True),
        sa.Column("xrd_rc_fwhm_deg", sa.Float(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "process_aln_dep_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_sheet", sa.Text(), nullable=False),
        sa.Column("recipe_id", sa.Text(), nullable=True),
        sa.Column("run_no", sa.Text(), nullable=False),
        sa.Column("group_label", sa.Text(), nullable=True),
        sa.Column("n2_flow_sccm", sa.Float(), nullable=True),
        sa.Column("ar_flow_sccm", sa.Float(), nullable=True),
        sa.Column("dc_power_kw", sa.Float(), nullable=True),
        sa.Column("dep_time_sec", sa.Float(), nullable=True),
        sa.Column("dep_rate_nm_min", sa.Float(), nullable=True),
        sa.Column("thk_nm", sa.Float(), nullable=True),
        sa.Column("wiw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("wtw_1sig_pct", sa.Float(), nullable=True),
        sa.Column("stress_avg_mpa", sa.Float(), nullable=True),
        sa.Column("stress_wiw_range_mpa", sa.Float(), nullable=True),
        sa.Column("stress_wtw_range_mpa", sa.Float(), nullable=True),
        sa.Column("refractive_index", sa.Float(), nullable=True),
        sa.Column("roughness_ra_nm", sa.Float(), nullable=True),
        sa.Column("aog_per_um2", sa.Float(), nullable=True),
        sa.Column("xrd_rc_fwhm_deg", sa.Float(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("process_aln_dep_runs")
    op.drop_table("process_mo_dep_runs")
    op.drop_table("process_icp_etch_runs")
