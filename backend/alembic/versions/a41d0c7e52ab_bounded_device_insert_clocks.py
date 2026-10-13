"""Aggregate device INSERT clock deltas without changing atomic revisions."""

from alembic import op

revision = "a41d0c7e52ab"
down_revision = "972be620abc1"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""CREATE OR REPLACE FUNCTION aln_track_device_insert() RETURNS trigger AS $$
    DECLARE inserted_count bigint;
    BEGIN
        SELECT count(*) INTO inserted_count FROM aln_inserted_devices;
        IF inserted_count = 0 THEN RETURN NULL; END IF;
        UPDATE data_epoch SET epoch = epoch + inserted_count WHERE id = 1;
        INSERT INTO sync_revisions(batch_id,revision)
            SELECT batch_id,count(*) FROM aln_inserted_devices GROUP BY batch_id
            ON CONFLICT(batch_id) DO UPDATE
            SET revision=sync_revisions.revision+excluded.revision;
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql""")
    op.execute('DROP TRIGGER IF EXISTS "sync_devices_INSERT" ON devices')
    op.execute('CREATE TRIGGER "sync_devices_INSERT" AFTER INSERT ON devices '
               'REFERENCING NEW TABLE AS aln_inserted_devices FOR EACH STATEMENT '
               'EXECUTE FUNCTION aln_track_device_insert()')


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute('DROP TRIGGER IF EXISTS "sync_devices_INSERT" ON devices')
    op.execute('CREATE TRIGGER "sync_devices_INSERT" AFTER INSERT ON devices '
               'FOR EACH ROW EXECUTE FUNCTION aln_track_data_revision()')
    op.execute('DROP FUNCTION IF EXISTS aln_track_device_insert()')
