"""allow batch process_type auto

Revision ID: 86e423b88f70
Revises: 4c39d930041d
Create Date: 2026-06-17 11:07:32.990445

"""
from typing import Sequence, Union

from alembic import op


revision: str = '86e423b88f70'
down_revision: Union[str, None] = '4c39d930041d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('ck_batch_proc_type', 'batches', type_='check')
    op.create_check_constraint(
        'ck_batch_proc_type',
        'batches',
        "process_type IN ('AUTO','S1P','S2P','BOTH')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_batch_proc_type', 'batches', type_='check')
    op.create_check_constraint(
        'ck_batch_proc_type',
        'batches',
        "process_type IN ('S1P','S2P','BOTH')",
    )
