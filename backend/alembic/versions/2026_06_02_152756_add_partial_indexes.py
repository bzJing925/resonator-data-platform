"""add partial indexes for performance

Revision ID: 2026_06_02_152756
Revises: 2f38e7f18d1b
Create Date: 2026-06-02 15:27:56

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_06_02_152756"
down_revision: str | None = "2f38e7f18d1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Pass 数据 partial index（pf='Y' 是主查询路径，占比约 12%）
    op.create_index("idx_dev_pf_pass", "devices", ["pf"], postgresql_where=sa.text("pf = 'Y'"))

    # 2. 频率范围筛选 partial index（与 pf='Y' 交叉，散点图常用）
    op.create_index("idx_dev_fs_pass", "devices", ["fs_ghz"], postgresql_where=sa.text("pf = 'Y'"))

    # 3. fp_ghz 常规索引（箱型图/折线图的筛选列）
    op.create_index("idx_dev_fp", "devices", ["fp_ghz"], unique=False)

    # 4. x, y 复合索引（版图分布图按矩形范围扫描）
    op.create_index("idx_dev_xy", "devices", ["x", "y"], unique=False)

    # 5. original_filename 前缀索引（distinct 查询加速，取前 50 字符）
    op.execute("CREATE INDEX idx_dev_filename_prefix ON devices (LEFT(original_filename, 50))")


def downgrade() -> None:
    op.drop_index("idx_dev_filename_prefix", table_name="devices")
    op.drop_index("idx_dev_xy", table_name="devices")
    op.drop_index("idx_dev_fp", table_name="devices")
    op.drop_index("idx_dev_fs_pass", table_name="devices")
    op.drop_index("idx_dev_pf_pass", table_name="devices")
