"""Null-safe business-field triggers shared by native SQLite and PostgreSQL."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.models import (
    DataEpoch,
    Device,
    MappingEntry,
    ProcessAlnDepRun,
    ProcessIcpEtchRun,
    ProcessMoDepRun,
    SyncIdentity,
)

BUSINESS_FIELDS = {
    "batches": (
        "batch_no", "mapping_id", "f_start_ghz", "f_end_ghz", "deembedded",
        "deembed_method", "process_type", "file_path", "device_count", "result_revision",
        "deleted_at", "origin_id", "source_batch_id",
    ),
    "devices": tuple(c.name for c in Device.__table__.columns if c.name != "id"),
    "mappings": ("name", "file_path", "entry_count"),
    "mapping_entries": tuple(c.name for c in MappingEntry.__table__.columns if c.name != "id"),
}
for _model in (ProcessIcpEtchRun, ProcessMoDepRun, ProcessAlnDepRun):
    BUSINESS_FIELDS[_model.__tablename__] = tuple(
        c.name for c in _model.__table__.columns if c.name not in {"id", "created_at", "updated_at"}
    )

_PG_FUNCTION = """
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
            (old_row->>CASE WHEN TG_TABLE_NAME='mappings' THEN 'id' ELSE 'mapping_id' END)::bigint,
            (new_row->>CASE WHEN TG_TABLE_NAME='mappings' THEN 'id' ELSE 'mapping_id' END)::bigint
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
"""

# Same per-row deltas, one clock write per INSERT statement. Repeated HOT updates
# to the singleton clock inside a large transaction otherwise grow quadratically.
_PG_DEVICE_INSERT_FUNCTION = """
CREATE OR REPLACE FUNCTION aln_track_device_insert() RETURNS trigger AS $$
DECLARE
    inserted_count bigint;
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
$$ LANGUAGE plpgsql
"""


def postgres_trigger_sql() -> list[str]:
    statements = [_PG_FUNCTION, _PG_DEVICE_INSERT_FUNCTION]
    for table, fields in BUSINESS_FIELDS.items():
        for op in ("INSERT", "UPDATE", "DELETE"):
            name = f"sync_{table}_{op}"
            statements.append(f'DROP TRIGGER IF EXISTS "{name}" ON {table}')
            if table == "devices" and op == "INSERT":
                statements.append(
                    f'CREATE TRIGGER "{name}" AFTER INSERT ON devices '
                    "REFERENCING NEW TABLE AS aln_inserted_devices FOR EACH STATEMENT "
                    "EXECUTE FUNCTION aln_track_device_insert()"
                )
                continue
            condition = ""
            if op == "UPDATE":
                comparison = " OR ".join(
                    f"to_jsonb(OLD.{col}) IS DISTINCT FROM to_jsonb(NEW.{col})" for col in fields
                )
                condition = f" WHEN ({comparison})"
            statements.append(
                f'CREATE TRIGGER "{name}" AFTER {op} ON {table} FOR EACH ROW'
                f"{condition} EXECUTE FUNCTION aln_track_data_revision()"
            )
    return statements


def _sqlite_batch_ids(table: str, rows: tuple[str, ...]) -> str | None:
    if table in {"batches", "devices"}:
        column = "id" if table == "batches" else "batch_id"
        return " UNION ".join(f"SELECT {row}.{column} AS batch_id" for row in rows)
    if table in {"mappings", "mapping_entries"}:
        column = "id" if table == "mappings" else "mapping_id"
        keys = ",".join(f"{row}.{column}" for row in rows)
        return f"SELECT id AS batch_id FROM batches WHERE mapping_id IN ({keys})"
    return None


def install_data_triggers(conn: Connection) -> None:
    if conn.dialect.name == "postgresql":
        for statement in postgres_trigger_sql():
            conn.exec_driver_sql(statement)
        return
    if conn.dialect.name != "sqlite":
        raise RuntimeError("data revision triggers require SQLite or PostgreSQL")
    for op in ("INSERT", "UPDATE", "DELETE"):
        conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS sync_file_nodes_{op}")
    for table, fields in BUSINESS_FIELDS.items():
        for op in ("INSERT", "UPDATE", "DELETE"):
            name = f"sync_{table}_{op}"
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            condition = ""
            rows = ("OLD", "NEW") if op == "UPDATE" else ("NEW" if op == "INSERT" else "OLD",)
            if op == "UPDATE":
                comparison = " OR ".join(f"OLD.{col} IS NOT NEW.{col}" for col in fields)
                condition = f" WHEN {comparison}"
            ids = _sqlite_batch_ids(table, rows)
            revisions = ""
            if ids is not None:
                revisions = (
                    "INSERT INTO sync_revisions(batch_id,revision) "
                    f"SELECT batch_id,1 FROM ({ids}) WHERE batch_id IS NOT NULL "
                    "ON CONFLICT(batch_id) DO UPDATE SET revision=revision+1;"
                )
            conn.exec_driver_sql(
                f"CREATE TRIGGER {name} AFTER {op} ON {table}{condition} BEGIN "
                f"UPDATE data_epoch SET epoch=epoch+1 WHERE id=1; {revisions} END"
            )


def cache_version(db: Session) -> tuple[str, int] | None:
    """Missing initialized metadata disables caching rather than inventing an epoch."""
    epoch = db.scalar(select(DataEpoch.epoch).where(DataEpoch.id == 1))
    origin = db.scalar(select(SyncIdentity.value).where(SyncIdentity.key == "server"))
    return (origin, epoch) if origin is not None and epoch is not None else None


def read_epoch(db: Session) -> int:
    value = db.scalar(select(DataEpoch.epoch).where(DataEpoch.id == 1))
    if value is None:
        raise RuntimeError("data epoch has not been initialized")
    return value


def seed_revision_metadata(conn: Connection, origin: str) -> None:
    conn.execute(
        text(
            "INSERT INTO sync_identity(key,value) VALUES ('server',:origin) "
            "ON CONFLICT(key) DO NOTHING"
        ),
        {"origin": origin},
    )
    conn.execute(text("INSERT INTO data_epoch(id,epoch) VALUES (1,0) ON CONFLICT(id) DO NOTHING"))
    conn.execute(
        text(
            "INSERT INTO sync_revisions(batch_id,revision) "
            "SELECT id,1 FROM batches WHERE source NOT IN ('server','web') "
            "ON CONFLICT(batch_id) DO NOTHING"
        )
    )
