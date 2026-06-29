"""add materialized views for batch/wafer stats

Revision ID: 2026_06_02_153000
Revises: 2026_06_02_152756
Create Date: 2026-06-02 15:30:00

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2026_06_02_153000'
down_revision: str | None = '2026_06_02_152756'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 批次统计物化视图：用于 BatchDetail 概览和 Dashboard
    # 避免每次查询时对 devices 表做全量聚合
    op.execute("""
        CREATE MATERIALIZED VIEW mv_batch_stats AS
        SELECT
            b.id AS batch_id,
            b.batch_no,
            d.wafer,
            COUNT(*) AS total_count,
            COUNT(*) FILTER (WHERE d.pf = 'Y') AS pass_count,
            ROUND(AVG(d.fs_ghz)::numeric, 6) AS avg_fs_ghz,
            ROUND(
                (PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY d.fs_ghz
                ))::numeric, 6
            ) AS median_fs_ghz,
            ROUND(AVG(d.qs)::numeric, 3) AS avg_qs,
            ROUND(AVG(d.k2eff_pct)::numeric, 4) AS avg_k2eff_pct,
            ROUND(MIN(d.fs_ghz)::numeric, 6) AS min_fs_ghz,
            ROUND(MAX(d.fs_ghz)::numeric, 6) AS max_fs_ghz
        FROM devices d
        JOIN batches b ON b.id = d.batch_id
        GROUP BY b.id, b.batch_no, d.wafer
        WITH NO DATA;
    """)

    # 为物化视图创建索引，加速按 batch_id 查询
    op.execute("""
        CREATE UNIQUE INDEX idx_mv_batch_stats_pk
        ON mv_batch_stats (batch_id, wafer);
    """)

    # 初始填充
    op.execute("REFRESH MATERIALIZED VIEW mv_batch_stats;")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_batch_stats;")
