"""add task cancelled

Revision ID: 2026_07_06_150000
Revises: 2026_07_03_120000
Create Date: 2026-07-06 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_07_06_150000"
down_revision: str | None = "2026_07_03_120000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "upload_tasks",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_uptask_status", "upload_tasks", type_="check", if_exists=True)
    op.create_check_constraint(
        "ck_uptask_status",
        "upload_tasks",
        "status IN ('pending','running','success','failed','cancelled')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_uptask_status", "upload_tasks", type_="check", if_exists=True)
    op.create_check_constraint(
        "ck_uptask_status",
        "upload_tasks",
        "status IN ('pending','running','success','failed')",
    )
    op.drop_column("upload_tasks", "cancelled_at")
