-- Frozen SQLite v4 schema from the pre-v5 models and Alembic head.
CREATE TABLE mappings (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    entry_count INTEGER NOT NULL DEFAULT 0,
    uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE mapping_entries (
    id INTEGER PRIMARY KEY,
    mapping_id INTEGER NOT NULL REFERENCES mappings(id) ON DELETE CASCADE,
    mark TEXT NOT NULL,
    description TEXT,
    eg FLOAT,
    fl FLOAT,
    ag FLOAT,
    area_s11 INTEGER,
    area_s22 INTEGER,
    has_pf BOOLEAN NOT NULL DEFAULT 0,
    raw_tokens JSON,
    CONSTRAINT uq_mentry_mapping_mark UNIQUE (mapping_id, mark)
);

CREATE TABLE upload_tasks (
    id INTEGER NOT NULL PRIMARY KEY,
    batch_no TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress_pct SMALLINT NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'extract',
    stage_progress_pct SMALLINT NOT NULL DEFAULT 0,
    progress_msg TEXT,
    error_msg TEXT,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    cancelled_at DATETIME,
    celery_task_id TEXT,
    kind TEXT NOT NULL DEFAULT 'upload',
    CONSTRAINT ck_uptask_status
        CHECK (status IN ('pending','running','success','failed','cancelled')),
    CONSTRAINT ck_uptask_stage
        CHECK (stage IN ('extract','deembed','metrics','done','failed')),
    CONSTRAINT ck_uptask_kind
        CHECK (kind IN ('upload','reextract','redeembed','recompute','remap')),
    CONSTRAINT ck_uptask_progress CHECK (progress_pct BETWEEN 0 AND 100),
    CONSTRAINT ck_uptask_stage_progress CHECK (stage_progress_pct BETWEEN 0 AND 100)
);

CREATE INDEX idx_uptask_status_started ON upload_tasks(status, started_at);

CREATE TABLE batches (
    id INTEGER PRIMARY KEY,
    batch_no TEXT NOT NULL UNIQUE,
    mapping_id INTEGER NOT NULL REFERENCES mappings(id) ON DELETE RESTRICT,
    f_start_ghz FLOAT,
    f_end_ghz FLOAT,
    deembedded BOOLEAN NOT NULL DEFAULT 0,
    deembed_method TEXT NOT NULL DEFAULT 'default',
    process_type TEXT NOT NULL DEFAULT 'AUTO',
    file_path TEXT NOT NULL,
    raw_zip_path TEXT,
    source TEXT NOT NULL DEFAULT 'http',
    device_count INTEGER NOT NULL DEFAULT 0,
    uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    uploaded_by TEXT NOT NULL DEFAULT 'anonymous',
    synced_at DATETIME,
    task_id INTEGER REFERENCES upload_tasks(id) ON DELETE SET NULL,
    CONSTRAINT ck_batch_proc_type CHECK (process_type IN ('AUTO','S1P','S2P','BOTH'))
);

CREATE INDEX idx_batches_uploaded_at ON batches(uploaded_at);
CREATE INDEX idx_batches_mapping ON batches(mapping_id);

CREATE TABLE devices (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    display_name TEXT,
    mark TEXT,
    wafer SMALLINT,
    folder_name TEXT,
    coord TEXT,
    x SMALLINT,
    y SMALLINT,
    eg FLOAT,
    fl FLOAT,
    ag FLOAT,
    pf CHAR(1),
    area_n INTEGER,
    area_um2 INTEGER,
    fs_ghz FLOAT,
    fp_ghz FLOAT,
    zs_ohm FLOAT,
    zp_ohm FLOAT,
    qs FLOAT,
    qp FLOAT,
    qs_bodeq FLOAT,
    qp_bodeq FLOAT,
    dbqs FLOAT,
    dbqp FLOAT,
    bodeq_fitted FLOAT,
    bodeq_smooth FLOAT,
    bodeq_raw FLOAT,
    fbode_ghz FLOAT,
    k2eff_pct FLOAT,
    fp2_ghz FLOAT,
    fs2_ghz FLOAT,
    zp2_ohm FLOAT,
    zs2_ohm FLOAT,
    deembedded BOOLEAN NOT NULL DEFAULT 0,
    s_param_path TEXT,
    s_param_port TEXT NOT NULL DEFAULT 'S11',
    CONSTRAINT uq_device_batch_sparam_port UNIQUE (batch_id, s_param_path, s_param_port),
    CONSTRAINT ck_device_pf CHECK (pf IN ('Y','N'))
);

CREATE INDEX idx_dev_batch_wafer ON devices(batch_id, wafer);
CREATE INDEX idx_dev_eg_fl_ag ON devices(eg, fl, ag);
CREATE INDEX idx_dev_coord ON devices(coord);
CREATE INDEX idx_dev_mark ON devices(mark);
CREATE INDEX idx_dev_pf ON devices(pf);
CREATE INDEX idx_dev_pf_pass ON devices(pf) WHERE pf = 'Y';
CREATE INDEX idx_dev_fs_pass ON devices(fs_ghz) WHERE pf = 'Y';
CREATE INDEX idx_dev_fp ON devices(fp_ghz);
CREATE INDEX idx_dev_xy ON devices(x, y);

CREATE TABLE file_nodes (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES file_nodes(id) ON DELETE CASCADE,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    relpath TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_deleted BOOLEAN NOT NULL DEFAULT 0,
    source_zip TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_file_node_type CHECK (node_type IN ('root','zip','folder','file'))
);

CREATE INDEX idx_file_nodes_batch_parent_order
    ON file_nodes(batch_id, parent_id, sort_order, is_deleted);
CREATE INDEX idx_file_nodes_batch_type ON file_nodes(batch_id, node_type);
CREATE INDEX idx_file_nodes_relpath ON file_nodes(batch_id, relpath);

CREATE TABLE replica_batches (
    batch_id INTEGER PRIMARY KEY,
    server_id TEXT NOT NULL,
    remote_id INTEGER NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    device_ids JSON NOT NULL DEFAULT '{}'
);

CREATE TABLE sync_identity (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sync_revisions (batch_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE table_receipts (key TEXT PRIMARY KEY, digest TEXT NOT NULL, batch_id INTEGER NOT NULL);
