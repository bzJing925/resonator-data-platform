"""Persist independently captured sync expectations."""

import sqlalchemy as sa

from alembic import op

revision = "972be620abc1"
down_revision = "01fb348dec8e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sync_operations", sa.Column("expected", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("sync_operations", "expected")
