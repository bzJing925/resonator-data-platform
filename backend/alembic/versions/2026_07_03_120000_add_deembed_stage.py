"""add deembed stage

Revision ID: 2026_07_03_120000
Revises: 2026_06_30_120000
Create Date: 2026-07-03 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_07_03_120000"
down_revision: str | None = "2026_06_30_120000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_uptask_stage", "upload_tasks", type_="check", if_exists=True)
    op.create_check_constraint(
        "ck_uptask_stage",
        "upload_tasks",
        "stage IN ('extract','deembed','metrics','done','failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_uptask_stage", "upload_tasks", type_="check", if_exists=True)
    op.create_check_constraint(
        "ck_uptask_stage",
        "upload_tasks",
        "stage IN ('extract','metrics','done','failed')",
    )
