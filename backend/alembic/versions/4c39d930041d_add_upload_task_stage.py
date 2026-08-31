"""add upload_task stage

Revision ID: 4c39d930041d
Revises: 70da8915313e
Create Date: 2026-06-16 17:41:11.889180

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '4c39d930041d'
down_revision: str | None = '70da8915313e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 先以可空列加入，避免已有行违反 NOT NULL
    op.add_column('upload_tasks', sa.Column('stage', sa.Text(), nullable=True))
    op.add_column('upload_tasks', sa.Column('stage_progress_pct', sa.SmallInteger(), nullable=True))

    # 根据已有状态回填合理的阶段值
    op.execute("""
        UPDATE upload_tasks
        SET stage = CASE
                WHEN status = 'success' THEN 'done'
                WHEN status = 'failed' THEN 'failed'
                ELSE 'extract'
            END,
            stage_progress_pct = CASE
                WHEN status = 'success' THEN 100
                ELSE 0
            END
    """)

    # 改为 NOT NULL 并加上约束（模型里已有 CheckConstraint，迁移里不显式加亦可）
    op.alter_column('upload_tasks', 'stage', nullable=False)
    op.alter_column('upload_tasks', 'stage_progress_pct', nullable=False)


def downgrade() -> None:
    op.drop_column('upload_tasks', 'stage_progress_pct')
    op.drop_column('upload_tasks', 'stage')
