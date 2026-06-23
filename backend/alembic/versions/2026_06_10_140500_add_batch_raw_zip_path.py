"""add raw_zip_path to batches

Revision ID: 2026_06_10_140500
Revises: 2026_06_02_153000
Create Date: 2026-06-10 14:05:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2026_06_10_140500'
down_revision: Union[str, None] = '2026_06_02_153000'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('batches', sa.Column('raw_zip_path', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('batches', 'raw_zip_path')
