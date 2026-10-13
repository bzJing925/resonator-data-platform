"""Persist PostgreSQL origin metadata and transactional business revision clocks.

Trigger SQL is frozen in this revision and does not depend on future app models.
"""

from uuid import uuid4

from alembic import op

revision = "bd03a1e9f052"
down_revision = "4b38220ae4eb"
branch_labels = None
depends_on = None

_TABLE_SQL = [
    """

CREATE TABLE replica_batches (
        batch_id SERIAL NOT NULL,
        server_id TEXT NOT NULL,
        remote_id INTEGER NOT NULL,
        revision INTEGER NOT NULL,
        device_ids JSON NOT NULL,
        PRIMARY KEY (batch_id)
)

    """,
    """

CREATE TABLE sync_identity (
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (key)
)

    """,
    """

CREATE TABLE sync_revisions (
        batch_id SERIAL NOT NULL,
        revision INTEGER NOT NULL,
        PRIMARY KEY (batch_id)
)

    """,
    """

CREATE TABLE table_receipts (
        key TEXT NOT NULL,
        digest TEXT NOT NULL,
        batch_id INTEGER NOT NULL,
        PRIMARY KEY (key)
)

    """,
    """

CREATE TABLE data_epoch (
        id SERIAL NOT NULL,
        epoch BIGINT DEFAULT '0' NOT NULL,
        stats_epoch BIGINT,
        PRIMARY KEY (id),
        CONSTRAINT ck_data_epoch_singleton CHECK (id = 1),
        CONSTRAINT ck_data_epoch_nonnegative CHECK (epoch >= 0)
)

    """,
]
_TRIGGER_SQL = [
    """

CREATE OR REPLACE FUNCTION aln_track_data_revision() RETURNS trigger AS $$
DECLARE
    old_row jsonb;
    new_row jsonb;
    ids bigint[] := ARRAY[]::bigint[];
BEGIN
    old_row := CASE WHEN TG_OP = 'INSERT' THEN '{}'::jsonb ELSE to_jsonb(OLD) END;
    new_row := CASE WHEN TG_OP = 'DELETE' THEN '{}'::jsonb ELSE to_jsonb(NEW) END;
    UPDATE data_epoch SET epoch = epoch + 1 WHERE id = 1;
    IF TG_TABLE_NAME = 'batches' THEN
        ids := ARRAY[(old_row->>'id')::bigint, (new_row->>'id')::bigint];
    ELSIF TG_TABLE_NAME = 'devices' THEN
        ids := ARRAY[(old_row->>'batch_id')::bigint, (new_row->>'batch_id')::bigint];
    ELSIF TG_TABLE_NAME IN ('mappings','mapping_entries') THEN
        SELECT array_agg(id) INTO ids FROM batches WHERE mapping_id IN (
            (old_row->>CASE WHEN TG_TABLE_NAME='mappings' THEN 'id' ELSE 'mapping_id'
END)::bigint,
            (new_row->>CASE WHEN TG_TABLE_NAME='mappings' THEN 'id' ELSE 'mapping_id'
END)::bigint
        );
    END IF;
    INSERT INTO sync_revisions(batch_id,revision)
        SELECT batch_id,1 FROM (SELECT DISTINCT unnest(ids) AS batch_id) AS changed
        WHERE batch_id IS NOT NULL
        ON CONFLICT(batch_id) DO UPDATE SET revision=sync_revisions.revision+1;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
    """,
    """
DROP TRIGGER IF EXISTS "sync_batches_INSERT" ON batches
    """,
    """
CREATE TRIGGER "sync_batches_INSERT" AFTER INSERT ON batches FOR EACH ROW EXECUTE FUNCTION
aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_batches_UPDATE" ON batches
    """,
    """
CREATE TRIGGER "sync_batches_UPDATE" AFTER UPDATE ON batches FOR EACH ROW WHEN
(to_jsonb(OLD.batch_no) IS DISTINCT FROM to_jsonb(NEW.batch_no) OR to_jsonb(OLD.mapping_id) IS
DISTINCT FROM to_jsonb(NEW.mapping_id) OR to_jsonb(OLD.f_start_ghz) IS DISTINCT FROM
to_jsonb(NEW.f_start_ghz) OR to_jsonb(OLD.f_end_ghz) IS DISTINCT FROM to_jsonb(NEW.f_end_ghz) OR
to_jsonb(OLD.deembedded) IS DISTINCT FROM to_jsonb(NEW.deembedded) OR
to_jsonb(OLD.deembed_method) IS DISTINCT FROM to_jsonb(NEW.deembed_method) OR
to_jsonb(OLD.process_type) IS DISTINCT FROM to_jsonb(NEW.process_type) OR
to_jsonb(OLD.file_path) IS DISTINCT FROM to_jsonb(NEW.file_path) OR to_jsonb(OLD.device_count)
IS DISTINCT FROM to_jsonb(NEW.device_count) OR to_jsonb(OLD.result_revision) IS DISTINCT FROM
to_jsonb(NEW.result_revision) OR to_jsonb(OLD.deleted_at) IS DISTINCT FROM
to_jsonb(NEW.deleted_at) OR to_jsonb(OLD.origin_id) IS DISTINCT FROM to_jsonb(NEW.origin_id) OR
to_jsonb(OLD.source_batch_id) IS DISTINCT FROM to_jsonb(NEW.source_batch_id)) EXECUTE FUNCTION
aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_batches_DELETE" ON batches
    """,
    """
CREATE TRIGGER "sync_batches_DELETE" AFTER DELETE ON batches FOR EACH ROW EXECUTE FUNCTION
aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_devices_INSERT" ON devices
    """,
    """
CREATE TRIGGER "sync_devices_INSERT" AFTER INSERT ON devices FOR EACH ROW EXECUTE FUNCTION
aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_devices_UPDATE" ON devices
    """,
    """
CREATE TRIGGER "sync_devices_UPDATE" AFTER UPDATE ON devices FOR EACH ROW WHEN
(to_jsonb(OLD.batch_id) IS DISTINCT FROM to_jsonb(NEW.batch_id) OR
to_jsonb(OLD.original_filename) IS DISTINCT FROM to_jsonb(NEW.original_filename) OR
to_jsonb(OLD.display_name) IS DISTINCT FROM to_jsonb(NEW.display_name) OR to_jsonb(OLD.mark) IS
DISTINCT FROM to_jsonb(NEW.mark) OR to_jsonb(OLD.wafer) IS DISTINCT FROM to_jsonb(NEW.wafer) OR
to_jsonb(OLD.folder_name) IS DISTINCT FROM to_jsonb(NEW.folder_name) OR to_jsonb(OLD.coord) IS
DISTINCT FROM to_jsonb(NEW.coord) OR to_jsonb(OLD.x) IS DISTINCT FROM to_jsonb(NEW.x) OR
to_jsonb(OLD.y) IS DISTINCT FROM to_jsonb(NEW.y) OR to_jsonb(OLD.eg) IS DISTINCT FROM
to_jsonb(NEW.eg) OR to_jsonb(OLD.fl) IS DISTINCT FROM to_jsonb(NEW.fl) OR to_jsonb(OLD.ag) IS
DISTINCT FROM to_jsonb(NEW.ag) OR to_jsonb(OLD.pf) IS DISTINCT FROM to_jsonb(NEW.pf) OR
to_jsonb(OLD.area_n) IS DISTINCT FROM to_jsonb(NEW.area_n) OR to_jsonb(OLD.area_um2) IS DISTINCT
FROM to_jsonb(NEW.area_um2) OR to_jsonb(OLD.fs_ghz) IS DISTINCT FROM to_jsonb(NEW.fs_ghz) OR
to_jsonb(OLD.fp_ghz) IS DISTINCT FROM to_jsonb(NEW.fp_ghz) OR to_jsonb(OLD.zs_ohm) IS DISTINCT
FROM to_jsonb(NEW.zs_ohm) OR to_jsonb(OLD.zp_ohm) IS DISTINCT FROM to_jsonb(NEW.zp_ohm) OR
to_jsonb(OLD.qs) IS DISTINCT FROM to_jsonb(NEW.qs) OR to_jsonb(OLD.qp) IS DISTINCT FROM
to_jsonb(NEW.qp) OR to_jsonb(OLD.qs_bodeq) IS DISTINCT FROM to_jsonb(NEW.qs_bodeq) OR
to_jsonb(OLD.qp_bodeq) IS DISTINCT FROM to_jsonb(NEW.qp_bodeq) OR to_jsonb(OLD.dbqs) IS DISTINCT
FROM to_jsonb(NEW.dbqs) OR to_jsonb(OLD.dbqp) IS DISTINCT FROM to_jsonb(NEW.dbqp) OR
to_jsonb(OLD.bodeq_fitted) IS DISTINCT FROM to_jsonb(NEW.bodeq_fitted) OR
to_jsonb(OLD.bodeq_smooth) IS DISTINCT FROM to_jsonb(NEW.bodeq_smooth) OR
to_jsonb(OLD.bodeq_raw) IS DISTINCT FROM to_jsonb(NEW.bodeq_raw) OR to_jsonb(OLD.fbode_ghz) IS
DISTINCT FROM to_jsonb(NEW.fbode_ghz) OR to_jsonb(OLD.k2eff_pct) IS DISTINCT FROM
to_jsonb(NEW.k2eff_pct) OR to_jsonb(OLD.fp2_ghz) IS DISTINCT FROM to_jsonb(NEW.fp2_ghz) OR
to_jsonb(OLD.fs2_ghz) IS DISTINCT FROM to_jsonb(NEW.fs2_ghz) OR to_jsonb(OLD.zp2_ohm) IS
DISTINCT FROM to_jsonb(NEW.zp2_ohm) OR to_jsonb(OLD.zs2_ohm) IS DISTINCT FROM
to_jsonb(NEW.zs2_ohm) OR to_jsonb(OLD.deembedded) IS DISTINCT FROM to_jsonb(NEW.deembedded) OR
to_jsonb(OLD.s_param_path) IS DISTINCT FROM to_jsonb(NEW.s_param_path) OR
to_jsonb(OLD.s_param_port) IS DISTINCT FROM to_jsonb(NEW.s_param_port) OR
to_jsonb(OLD.source_device_key) IS DISTINCT FROM to_jsonb(NEW.source_device_key)) EXECUTE
FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_devices_DELETE" ON devices
    """,
    """
CREATE TRIGGER "sync_devices_DELETE" AFTER DELETE ON devices FOR EACH ROW EXECUTE FUNCTION
aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_mappings_INSERT" ON mappings
    """,
    """
CREATE TRIGGER "sync_mappings_INSERT" AFTER INSERT ON mappings FOR EACH ROW EXECUTE FUNCTION
aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_mappings_UPDATE" ON mappings
    """,
    """
CREATE TRIGGER "sync_mappings_UPDATE" AFTER UPDATE ON mappings FOR EACH ROW WHEN
(to_jsonb(OLD.name) IS DISTINCT FROM to_jsonb(NEW.name) OR to_jsonb(OLD.file_path) IS DISTINCT
FROM to_jsonb(NEW.file_path) OR to_jsonb(OLD.entry_count) IS DISTINCT FROM
to_jsonb(NEW.entry_count)) EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_mappings_DELETE" ON mappings
    """,
    """
CREATE TRIGGER "sync_mappings_DELETE" AFTER DELETE ON mappings FOR EACH ROW EXECUTE FUNCTION
aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_mapping_entries_INSERT" ON mapping_entries
    """,
    """
CREATE TRIGGER "sync_mapping_entries_INSERT" AFTER INSERT ON mapping_entries FOR EACH ROW
EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_mapping_entries_UPDATE" ON mapping_entries
    """,
    """
CREATE TRIGGER "sync_mapping_entries_UPDATE" AFTER UPDATE ON mapping_entries FOR EACH ROW WHEN
(to_jsonb(OLD.mapping_id) IS DISTINCT FROM to_jsonb(NEW.mapping_id) OR to_jsonb(OLD.mark) IS
DISTINCT FROM to_jsonb(NEW.mark) OR to_jsonb(OLD.description) IS DISTINCT FROM
to_jsonb(NEW.description) OR to_jsonb(OLD.eg) IS DISTINCT FROM to_jsonb(NEW.eg) OR
to_jsonb(OLD.fl) IS DISTINCT FROM to_jsonb(NEW.fl) OR to_jsonb(OLD.ag) IS DISTINCT FROM
to_jsonb(NEW.ag) OR to_jsonb(OLD.area_s11) IS DISTINCT FROM to_jsonb(NEW.area_s11) OR
to_jsonb(OLD.area_s22) IS DISTINCT FROM to_jsonb(NEW.area_s22) OR to_jsonb(OLD.has_pf) IS
DISTINCT FROM to_jsonb(NEW.has_pf) OR to_jsonb(OLD.raw_tokens) IS DISTINCT FROM
to_jsonb(NEW.raw_tokens)) EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_mapping_entries_DELETE" ON mapping_entries
    """,
    """
CREATE TRIGGER "sync_mapping_entries_DELETE" AFTER DELETE ON mapping_entries FOR EACH ROW
EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_process_icp_etch_runs_INSERT" ON process_icp_etch_runs
    """,
    """
CREATE TRIGGER "sync_process_icp_etch_runs_INSERT" AFTER INSERT ON process_icp_etch_runs FOR
EACH ROW EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_process_icp_etch_runs_UPDATE" ON process_icp_etch_runs
    """,
    """
CREATE TRIGGER "sync_process_icp_etch_runs_UPDATE" AFTER UPDATE ON process_icp_etch_runs FOR
EACH ROW WHEN (to_jsonb(OLD.block_index) IS DISTINCT FROM to_jsonb(NEW.block_index) OR
to_jsonb(OLD.run_no) IS DISTINCT FROM to_jsonb(NEW.run_no) OR to_jsonb(OLD.etch_time_sec) IS
DISTINCT FROM to_jsonb(NEW.etch_time_sec) OR to_jsonb(OLD.etch_rate_nm_min) IS DISTINCT FROM
to_jsonb(NEW.etch_rate_nm_min) OR to_jsonb(OLD.thk_a) IS DISTINCT FROM to_jsonb(NEW.thk_a) OR
to_jsonb(OLD.wiw_1sig_pct) IS DISTINCT FROM to_jsonb(NEW.wiw_1sig_pct) OR
to_jsonb(OLD.wtw_1sig_pct) IS DISTINCT FROM to_jsonb(NEW.wtw_1sig_pct) OR to_jsonb(OLD.comments)
IS DISTINCT FROM to_jsonb(NEW.comments)) EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_process_icp_etch_runs_DELETE" ON process_icp_etch_runs
    """,
    """
CREATE TRIGGER "sync_process_icp_etch_runs_DELETE" AFTER DELETE ON process_icp_etch_runs FOR
EACH ROW EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_process_mo_dep_runs_INSERT" ON process_mo_dep_runs
    """,
    """
CREATE TRIGGER "sync_process_mo_dep_runs_INSERT" AFTER INSERT ON process_mo_dep_runs FOR EACH
ROW EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_process_mo_dep_runs_UPDATE" ON process_mo_dep_runs
    """,
    """
CREATE TRIGGER "sync_process_mo_dep_runs_UPDATE" AFTER UPDATE ON process_mo_dep_runs FOR EACH
ROW WHEN (to_jsonb(OLD.run_no) IS DISTINCT FROM to_jsonb(NEW.run_no) OR
to_jsonb(OLD.group_label) IS DISTINCT FROM to_jsonb(NEW.group_label) OR
to_jsonb(OLD.ar_flow_sccm) IS DISTINCT FROM to_jsonb(NEW.ar_flow_sccm) OR
to_jsonb(OLD.dc_power_kw) IS DISTINCT FROM to_jsonb(NEW.dc_power_kw) OR
to_jsonb(OLD.dep_time_sec) IS DISTINCT FROM to_jsonb(NEW.dep_time_sec) OR
to_jsonb(OLD.dep_rate_nm_min) IS DISTINCT FROM to_jsonb(NEW.dep_rate_nm_min) OR
to_jsonb(OLD.thk_nm) IS DISTINCT FROM to_jsonb(NEW.thk_nm) OR to_jsonb(OLD.wiw_1sig_pct) IS
DISTINCT FROM to_jsonb(NEW.wiw_1sig_pct) OR to_jsonb(OLD.wtw_1sig_pct) IS DISTINCT FROM
to_jsonb(NEW.wtw_1sig_pct) OR to_jsonb(OLD.stress_avg_mpa) IS DISTINCT FROM
to_jsonb(NEW.stress_avg_mpa) OR to_jsonb(OLD.stress_wiw_range_mpa) IS DISTINCT FROM
to_jsonb(NEW.stress_wiw_range_mpa) OR to_jsonb(OLD.stress_wtw_range_mpa) IS DISTINCT FROM
to_jsonb(NEW.stress_wtw_range_mpa) OR to_jsonb(OLD.rs_ohm_sq) IS DISTINCT FROM
to_jsonb(NEW.rs_ohm_sq) OR to_jsonb(OLD.resistivity_uohm_cm) IS DISTINCT FROM
to_jsonb(NEW.resistivity_uohm_cm) OR to_jsonb(OLD.rs_uni_pct) IS DISTINCT FROM
to_jsonb(NEW.rs_uni_pct) OR to_jsonb(OLD.roughness_ra_nm) IS DISTINCT FROM
to_jsonb(NEW.roughness_ra_nm) OR to_jsonb(OLD.xrd_rc_fwhm_deg) IS DISTINCT FROM
to_jsonb(NEW.xrd_rc_fwhm_deg) OR to_jsonb(OLD.comments) IS DISTINCT FROM to_jsonb(NEW.comments))
EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_process_mo_dep_runs_DELETE" ON process_mo_dep_runs
    """,
    """
CREATE TRIGGER "sync_process_mo_dep_runs_DELETE" AFTER DELETE ON process_mo_dep_runs FOR EACH
ROW EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_process_aln_dep_runs_INSERT" ON process_aln_dep_runs
    """,
    """
CREATE TRIGGER "sync_process_aln_dep_runs_INSERT" AFTER INSERT ON process_aln_dep_runs FOR EACH
ROW EXECUTE FUNCTION aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_process_aln_dep_runs_UPDATE" ON process_aln_dep_runs
    """,
    """
CREATE TRIGGER "sync_process_aln_dep_runs_UPDATE" AFTER UPDATE ON process_aln_dep_runs FOR EACH
ROW WHEN (to_jsonb(OLD.source_sheet) IS DISTINCT FROM to_jsonb(NEW.source_sheet) OR
to_jsonb(OLD.recipe_id) IS DISTINCT FROM to_jsonb(NEW.recipe_id) OR to_jsonb(OLD.run_no) IS
DISTINCT FROM to_jsonb(NEW.run_no) OR to_jsonb(OLD.group_label) IS DISTINCT FROM
to_jsonb(NEW.group_label) OR to_jsonb(OLD.n2_flow_sccm) IS DISTINCT FROM
to_jsonb(NEW.n2_flow_sccm) OR to_jsonb(OLD.ar_flow_sccm) IS DISTINCT FROM
to_jsonb(NEW.ar_flow_sccm) OR to_jsonb(OLD.dc_power_kw) IS DISTINCT FROM
to_jsonb(NEW.dc_power_kw) OR to_jsonb(OLD.dep_time_sec) IS DISTINCT FROM
to_jsonb(NEW.dep_time_sec) OR to_jsonb(OLD.dep_rate_nm_min) IS DISTINCT FROM
to_jsonb(NEW.dep_rate_nm_min) OR to_jsonb(OLD.thk_nm) IS DISTINCT FROM to_jsonb(NEW.thk_nm) OR
to_jsonb(OLD.wiw_1sig_pct) IS DISTINCT FROM to_jsonb(NEW.wiw_1sig_pct) OR
to_jsonb(OLD.wtw_1sig_pct) IS DISTINCT FROM to_jsonb(NEW.wtw_1sig_pct) OR
to_jsonb(OLD.stress_avg_mpa) IS DISTINCT FROM to_jsonb(NEW.stress_avg_mpa) OR
to_jsonb(OLD.stress_wiw_range_mpa) IS DISTINCT FROM to_jsonb(NEW.stress_wiw_range_mpa) OR
to_jsonb(OLD.stress_wtw_range_mpa) IS DISTINCT FROM to_jsonb(NEW.stress_wtw_range_mpa) OR
to_jsonb(OLD.refractive_index) IS DISTINCT FROM to_jsonb(NEW.refractive_index) OR
to_jsonb(OLD.roughness_ra_nm) IS DISTINCT FROM to_jsonb(NEW.roughness_ra_nm) OR
to_jsonb(OLD.aog_per_um2) IS DISTINCT FROM to_jsonb(NEW.aog_per_um2) OR
to_jsonb(OLD.xrd_rc_fwhm_deg) IS DISTINCT FROM to_jsonb(NEW.xrd_rc_fwhm_deg) OR
to_jsonb(OLD.comments) IS DISTINCT FROM to_jsonb(NEW.comments)) EXECUTE FUNCTION
aln_track_data_revision()
    """,
    """
DROP TRIGGER IF EXISTS "sync_process_aln_dep_runs_DELETE" ON process_aln_dep_runs
    """,
    """
CREATE TRIGGER "sync_process_aln_dep_runs_DELETE" AFTER DELETE ON process_aln_dep_runs FOR EACH
ROW EXECUTE FUNCTION aln_track_data_revision()
    """,
]
_TABLES = (
    "batches",
    "devices",
    "mappings",
    "mapping_entries",
    "process_icp_etch_runs",
    "process_mo_dep_runs",
    "process_aln_dep_runs",
)


def upgrade() -> None:
    for statement in _TABLE_SQL:
        op.execute(statement)
    conn = op.get_bind()
    conn.exec_driver_sql(
        "INSERT INTO sync_identity(key,value) VALUES ('server',%s)", (str(uuid4()),)
    )
    op.execute("INSERT INTO data_epoch(id,epoch) VALUES (1,0)")
    op.execute(
        "INSERT INTO sync_revisions(batch_id,revision) "
        "SELECT id,1 FROM batches WHERE source NOT IN ('server','web')"
    )
    for statement in _TRIGGER_SQL:
        op.execute(statement)


def downgrade() -> None:
    for table in _TABLES:
        for operation in ("INSERT", "UPDATE", "DELETE"):
            op.execute(f'DROP TRIGGER IF EXISTS "sync_{table}_{operation}" ON {table}')
    op.execute("DROP FUNCTION IF EXISTS aln_track_data_revision()")
    for table in (
        "data_epoch",
        "table_receipts",
        "sync_revisions",
        "sync_identity",
        "replica_batches",
    ):
        op.drop_table(table)
