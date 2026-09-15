"""add batch synced_at

Revision ID: 2026_09_15_120000
Revises: 2026_07_20_120000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2026_09_15_120000"
down_revision: str = "2026_07_20_120000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("batches", "synced_at")
