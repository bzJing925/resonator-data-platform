"""add kind to upload_tasks."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "2026_07_06_160000"
down_revision = "2026_07_06_150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_tasks",
        sa.Column("kind", sa.Text(), server_default="upload", nullable=False),
    )
    op.create_check_constraint(
        "ck_uptask_kind",
        "upload_tasks",
        sa.text("kind IN ('upload','reextract','redeembed','recompute')"),
    )


def downgrade() -> None:
    op.drop_constraint("ck_uptask_kind", "upload_tasks", type_="check")
    op.drop_column("upload_tasks", "kind")
