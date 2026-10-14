"""devices.matched: 未命中对照表的器件入库但不参与分析

- devices 增加 matched 列；存量数据在旧规则下全部命中对照表，默认 TRUE 正确。
- mv_batch_stats 重建为只聚合 matched=TRUE 的器件，使批次统计口径与分析一致。

Revision ID: b7e2f41c9d30
Revises: a41d0c7e52ab
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2f41c9d30"
down_revision: str | None = "a41d0c7e52ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MV_SELECT = """
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
        {where}
        GROUP BY b.id, b.batch_no, d.wafer
        WITH NO DATA;
"""


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("matched", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute("DROP MATERIALIZED VIEW mv_batch_stats")
    op.execute("CREATE MATERIALIZED VIEW mv_batch_stats AS" + _MV_SELECT.format(
        where="WHERE d.matched = TRUE"))
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_batch_stats_pk ON mv_batch_stats (batch_id, wafer)"
    )
    op.execute("REFRESH MATERIALIZED VIEW mv_batch_stats")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW mv_batch_stats")
    op.execute("CREATE MATERIALIZED VIEW mv_batch_stats AS" + _MV_SELECT.format(where=""))
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_batch_stats_pk ON mv_batch_stats (batch_id, wafer)"
    )
    op.execute("REFRESH MATERIALIZED VIEW mv_batch_stats")
    op.drop_column("devices", "matched")
