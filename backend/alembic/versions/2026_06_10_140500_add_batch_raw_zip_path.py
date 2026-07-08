"""add raw_zip_path to batches

Revision ID: 2026_06_10_140500
Revises: 2026_06_02_153000
Create Date: 2026-06-10 14:05:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_06_10_140500"
down_revision: str | None = "2026_06_02_153000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("raw_zip_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("batches", "raw_zip_path")
