"""Export request fingerprints and independent execution ownership."""
import sqlalchemy as sa

from alembic import op

revision = '01fb348dec8e'
down_revision = 'cf37b3290061'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('request_digest', 'lease_token', 'executor_id', 'scope_digest'):
        op.add_column('export_jobs', sa.Column(name, sa.Text(), nullable=True))
    for name in ('heartbeat_at', 'lease_expires_at'):
        op.add_column('export_jobs', sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column('export_jobs', sa.Column('source_epoch', sa.Integer(), nullable=True))


def downgrade():
    for name in ('request_digest', 'lease_token', 'executor_id', 'scope_digest', 'heartbeat_at',
                 'lease_expires_at', 'source_epoch'):
        op.drop_column('export_jobs', name)
