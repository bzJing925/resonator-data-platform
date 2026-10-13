"""Persistent artifact reader leases without cascading audit ownership."""
import sqlalchemy as sa

from alembic import op

revision = 'cf37b3290061'
down_revision = 'bd03a1e9f052'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('artifact_leases',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('resource_path', sa.Text(), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('owner_id', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("purpose IN ('run','download','export')",
                           name='ck_artifact_lease_purpose'))
    op.create_index('idx_artifact_leases_batch_expiry', 'artifact_leases',
                    ['batch_id', 'expires_at'])


def downgrade():
    op.drop_table('artifact_leases')
