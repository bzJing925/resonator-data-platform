"""add file_nodes virtual file tree

Revision ID: 2026_06_30_120000
Revises: 78bd1c93213e
Create Date: 2026-06-30 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2026_06_30_120000'
down_revision: str | None = '78bd1c93213e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'file_nodes',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('batch_id', sa.BigInteger(), nullable=False),
        sa.Column('parent_id', sa.BigInteger(), nullable=True),
        sa.Column('node_type', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('relpath', sa.Text(), nullable=True),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('source_zip', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['file_nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "node_type IN ('root','zip','folder','file')",
            name='ck_file_node_type',
        ),
    )
    op.create_index(
        'idx_file_nodes_batch_parent_order',
        'file_nodes',
        ['batch_id', 'parent_id', 'sort_order', 'is_deleted'],
    )
    op.create_index(
        'idx_file_nodes_batch_type',
        'file_nodes',
        ['batch_id', 'node_type'],
    )
    op.create_index(
        'idx_file_nodes_relpath',
        'file_nodes',
        ['batch_id', 'relpath'],
    )


def downgrade() -> None:
    op.drop_index('idx_file_nodes_relpath', table_name='file_nodes')
    op.drop_index('idx_file_nodes_batch_type', table_name='file_nodes')
    op.drop_index('idx_file_nodes_batch_parent_order', table_name='file_nodes')
    op.drop_table('file_nodes')
