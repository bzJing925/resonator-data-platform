"""Idempotent SQLite schema upgrades shared by desktop and native server modes."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import Connection

from app.models import Base

SCHEMA_VERSION = 5

_TASK_COLUMNS = (
    "id",
    "batch_no",
    "status",
    "progress_pct",
    "stage",
    "stage_progress_pct",
    "progress_msg",
    "error_msg",
    "started_at",
    "finished_at",
    "cancelled_at",
    "celery_task_id",
    "kind",
)


def _table_exists(conn: Connection, table: str) -> bool:
    return table in inspect(conn).get_table_names()


def _columns(conn: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(conn).get_columns(table)}


def _add_column_if_missing(conn: Connection, table: str, name: str, ddl: str) -> None:
    if name not in _columns(conn, table):
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _rebuild_upload_tasks_v5(conn: Connection) -> None:
    row = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='upload_tasks'")
    ).first()
    if row is None:
        raise RuntimeError("v4 SQLite schema is missing upload_tasks")
    original_ddl = row[0] or ""
    if "cancel_requested" in original_ddl:
        return

    columns = _columns(conn, "upload_tasks")
    if columns != set(_TASK_COLUMNS):
        missing = sorted(set(_TASK_COLUMNS) - columns)
        unexpected = sorted(columns - set(_TASK_COLUMNS))
        raise RuntimeError(
            f"Unsupported upload_tasks v4 columns; missing={missing}, unexpected={unexpected}"
        )

    task_foreign_keys = conn.exec_driver_sql("PRAGMA foreign_key_list(upload_tasks)").all()
    if task_foreign_keys:
        raise RuntimeError("Unexpected upload_tasks foreign keys; refusing a lossy table rebuild")

    preserved_objects = conn.execute(
        text(
            "SELECT type, sql FROM sqlite_master "
            "WHERE tbl_name='upload_tasks' AND type IN ('index','trigger') AND sql IS NOT NULL"
        )
    ).all()
    conn.exec_driver_sql(
        """
        CREATE TABLE _upload_tasks_v5 (
            id INTEGER NOT NULL PRIMARY KEY,
            batch_no TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            progress_pct SMALLINT NOT NULL DEFAULT 0,
            stage TEXT NOT NULL DEFAULT 'extract',
            stage_progress_pct SMALLINT NOT NULL DEFAULT 0,
            progress_msg TEXT,
            error_msg TEXT,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            finished_at DATETIME,
            cancelled_at DATETIME,
            celery_task_id TEXT,
            kind TEXT NOT NULL DEFAULT 'upload',
            CONSTRAINT ck_uptask_status CHECK (
                status IN ('pending','running','success','failed','cancel_requested','cancelled')
            ),
            CONSTRAINT ck_uptask_stage
                CHECK (stage IN ('extract','deembed','metrics','done','failed')),
            CONSTRAINT ck_uptask_kind
                CHECK (kind IN ('upload','reextract','redeembed','recompute','remap')),
            CONSTRAINT ck_uptask_progress CHECK (progress_pct BETWEEN 0 AND 100),
            CONSTRAINT ck_uptask_stage_progress CHECK (stage_progress_pct BETWEEN 0 AND 100)
        )
        """
    )
    column_sql = ", ".join(_TASK_COLUMNS)
    conn.exec_driver_sql(
        f"INSERT INTO _upload_tasks_v5 ({column_sql}) SELECT {column_sql} FROM upload_tasks"
    )
    conn.exec_driver_sql("DROP TABLE upload_tasks")
    conn.exec_driver_sql("ALTER TABLE _upload_tasks_v5 RENAME TO upload_tasks")
    for object_type, definition in preserved_objects:
        if object_type == "index":
            conn.exec_driver_sql(definition)
        else:
            conn.exec_driver_sql(definition)


def _upgrade_existing_columns(conn: Connection) -> None:
    if _table_exists(conn, "sync_operations"):
        _add_column_if_missing(conn, "sync_operations", "expected", "JSON")
    if _table_exists(conn, "export_jobs"):
        for name, ddl in (("request_digest", "TEXT"), ("lease_token", "TEXT"),
                          ("executor_id", "TEXT"), ("heartbeat_at", "DATETIME"),
                          ("lease_expires_at", "DATETIME"), ("source_epoch", "INTEGER"),
                          ("scope_digest", "TEXT")):
            _add_column_if_missing(conn, "export_jobs", name, ddl)
    if _table_exists(conn, "batches"):
        batch_columns: Sequence[tuple[str, str]] = (
            ("synced_at", "DATETIME"),
            ("origin_id", "TEXT"),
            ("source_batch_id", "TEXT"),
            ("active_run_id", "TEXT"),
            ("result_revision", "INTEGER NOT NULL DEFAULT 0"),
            ("deleted_at", "DATETIME"),
            ("purge_after", "DATETIME"),
        )
        for name, ddl in batch_columns:
            _add_column_if_missing(conn, "batches", name, ddl)
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_batch_source_identity "
            "ON batches (origin_id, source_batch_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_batches_deleted_at ON batches (deleted_at)"
        )
    if _table_exists(conn, "devices"):
        _add_column_if_missing(conn, "devices", "source_device_key", "TEXT")
        # 存量器件在旧规则下全部命中对照表，默认 1 正确
        _add_column_if_missing(conn, "devices", "matched", "BOOLEAN NOT NULL DEFAULT 1")
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_device_source_key ON devices (source_device_key)"
        )


def _migrate_v4_to_v5_in_transaction(conn: Connection) -> None:
    _upgrade_existing_columns(conn)
    _rebuild_upload_tasks_v5(conn)
    Base.metadata.create_all(bind=conn)
    violations = conn.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        raise RuntimeError(f"SQLite foreign_key_check failed during v5 upgrade: {violations[:5]}")


def _migrate_v4_to_v5(engine: Engine) -> None:
    """Upgrade an existing SQLite v4 database atomically and safely on retry."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError("schema v4-to-v5 helper is reserved for SQLite executors")

    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        try:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            _migrate_v4_to_v5_in_transaction(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()
