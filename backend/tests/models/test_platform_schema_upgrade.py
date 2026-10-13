"""Desktop/native SQLite v4->v5 migration safety and PostgreSQL head migration."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.desktop_setup import SCHEMA_VERSION, _init_schema
from app.models.base import Base

BACKEND = Path(__file__).parents[2]
V4_SNAPSHOT = Path(__file__).parents[1] / "fixtures" / "schema_v4.sql"


def _sqlite_engine(path: Path):
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _create_v4_snapshot(engine) -> None:
    with engine.begin() as connection:
        for statement in V4_SNAPSHOT.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "CREATE TABLE _aln_schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql("INSERT INTO _aln_schema_version(version) VALUES (4)")
        connection.exec_driver_sql(
            "INSERT INTO mappings(id,name,file_path,entry_count) "
            "VALUES (3,'map-v4','maps/map.xlsx',1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO upload_tasks "
            "(id,batch_no,status,progress_pct,stage,stage_progress_pct,progress_msg,"
            "started_at,finished_at,cancelled_at,kind) "
            "VALUES (7,'LOT-7','success',100,'done',100,'complete','2026-01-01',"
            "'2026-01-02','2026-01-03','recompute')"
        )
        connection.exec_driver_sql(
            "INSERT INTO batches "
            "(id,batch_no,mapping_id,f_start_ghz,f_end_ghz,deembedded,deembed_method,"
            "process_type,file_path,raw_zip_path,source,device_count,uploaded_at,uploaded_by,"
            "synced_at,task_id) VALUES "
            "(11,'LOT-7',3,2.0,4.0,1,'open_short','S2P','files/LOT-7','raw/LOT-7.zip',"
            "'http',1,'2026-01-01','operator','2026-01-04',7)"
        )
        connection.exec_driver_sql(
            "INSERT INTO devices "
            "(id,batch_id,original_filename,display_name,mark,wafer,coord,x,y,eg,fl,ag,pf,"
            "area_n,area_um2,fs_ghz,fp_ghz,qs,qp,deembedded,s_param_path,s_param_port) "
            "VALUES (19,11,'dut.s2p','DUT-19','M19',1,'X2Y3',2,3,0.2,0.4,0.6,'Y',"
            "100,200,2.45,2.80,42.5,31.5,1,'wafer/dut.s2p','S22')"
        )


def _table_columns(engine, table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _foreign_keys(engine, table: str) -> set[tuple[str, str, str, str]]:
    return {
        (
            fk["constrained_columns"][0],
            fk["referred_table"],
            fk["referred_columns"][0],
            fk["options"].get("ondelete", ""),
        )
        for fk in inspect(engine).get_foreign_keys(table)
    }


def test_new_database_starts_at_v5_with_final_constraints(tmp_path: Path) -> None:
    engine = _sqlite_engine(tmp_path / "new.db")
    try:
        _init_schema(engine)
        with engine.begin() as connection:
            version = connection.execute(
                text("SELECT max(version) FROM _aln_schema_version")
            ).scalar_one()
            assert version == SCHEMA_VERSION == 5
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            connection.execute(
                text(
                    "INSERT INTO upload_tasks "
                    "(id,batch_no,status,progress_pct,stage,stage_progress_pct,kind) "
                    "VALUES (1,'NEW-1','cancel_requested',0,'extract',0,'upload')"
                )
            )
        assert {
            "origin_id",
            "source_batch_id",
            "active_run_id",
            "result_revision",
            "deleted_at",
            "purge_after",
            "synced_at",
        } <= _table_columns(engine, "batches")
        assert "source_device_key" in _table_columns(engine, "devices")
        assert "processing_runs" in inspect(engine).get_table_names()
        assert "upload_drafts" in inspect(engine).get_table_names()
        assert "sync_operations" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_v4_rows_columns_indexes_and_foreign_keys_survive_and_upgrade_is_idempotent(
    tmp_path: Path,
) -> None:
    engine = _sqlite_engine(tmp_path / "v4.db")
    _create_v4_snapshot(engine)
    old_task_columns = _table_columns(engine, "upload_tasks")
    old_batch_columns = _table_columns(engine, "batches")
    old_device_columns = _table_columns(engine, "devices")
    old_batch_fks = _foreign_keys(engine, "batches")
    old_device_fks = _foreign_keys(engine, "devices")

    try:
        _init_schema(engine)
        _init_schema(engine)

        assert old_task_columns == _table_columns(engine, "upload_tasks")
        assert old_batch_columns <= _table_columns(engine, "batches")
        assert old_device_columns <= _table_columns(engine, "devices")
        assert "cancelled_at" in _table_columns(engine, "upload_tasks")
        assert "kind" in _table_columns(engine, "upload_tasks")
        assert "synced_at" in _table_columns(engine, "batches")
        assert "source_device_key" in _table_columns(engine, "devices")
        assert old_batch_fks == _foreign_keys(engine, "batches")
        assert old_device_fks == _foreign_keys(engine, "devices")

        with engine.begin() as connection:
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            assert connection.execute(text("SELECT count(*) FROM upload_tasks")).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM batches")).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM devices")).scalar_one() == 1
            task = connection.execute(
                text("SELECT batch_no,status,cancelled_at,kind FROM upload_tasks WHERE id=7")
            ).one()
            assert tuple(task) == ("LOT-7", "success", "2026-01-03", "recompute")
            batch = connection.execute(
                text(
                    "SELECT raw_zip_path,synced_at,task_id,result_revision FROM batches WHERE id=11"
                )
            ).one()
            assert tuple(batch) == ("raw/LOT-7.zip", "2026-01-04", 7, 0)
            device = connection.execute(
                text("SELECT qs,s_param_port,source_device_key FROM devices WHERE id=19")
            ).one()
            assert tuple(device) == (42.5, "S22", None)
            connection.execute(
                text(
                    "INSERT INTO upload_tasks "
                    "(id,batch_no,status,progress_pct,stage,stage_progress_pct) "
                    "VALUES (8,'LOT-7','cancel_requested',0,'extract',0)"
                )
            )
            version = connection.execute(
                text("SELECT max(version) FROM _aln_schema_version")
            ).scalar_one()
            assert version == 5
    finally:
        engine.dispose()


def test_v4_upgrade_rolls_back_structure_and_version_on_mid_migration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _sqlite_engine(tmp_path / "rollback.db")
    _create_v4_snapshot(engine)

    def fail_after_existing_tables(*_args, **_kwargs) -> None:
        raise RuntimeError("injected v5 migration failure")

    monkeypatch.setattr(Base.metadata, "create_all", fail_after_existing_tables)
    try:
        with pytest.raises(RuntimeError, match="injected v5 migration failure"):
            _init_schema(engine)

        assert "origin_id" not in _table_columns(engine, "batches")
        assert "source_device_key" not in _table_columns(engine, "devices")
        with engine.connect() as connection:
            old_task_ddl = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE name='upload_tasks'")
            ).scalar_one()
            assert "cancel_requested" not in old_task_ddl
        assert "processing_runs" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT max(version) FROM _aln_schema_version")
                ).scalar_one()
                == 4
            )
            assert connection.execute(text("SELECT count(*) FROM devices")).scalar_one() == 1
    finally:
        engine.dispose()


def test_higher_schema_version_is_not_downgraded(tmp_path: Path) -> None:
    engine = _sqlite_engine(tmp_path / "future.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE _aln_schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql("INSERT INTO _aln_schema_version(version) VALUES (6)")
        connection.exec_driver_sql("CREATE TABLE future_marker (value TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO future_marker(value) VALUES ('keep')")

    try:
        with pytest.raises(RuntimeError, match="高于程序支持"):
            _init_schema(engine)
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT value FROM future_marker")).scalar_one() == "keep"
            )
            assert (
                connection.execute(
                    text("SELECT max(version) FROM _aln_schema_version")
                ).scalar_one()
                == 6
            )
    finally:
        engine.dispose()


def test_postgresql_migration_runs_only_in_an_explicit_temporary_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_url = os.environ.get("ALN_TEST_POSTGRES_URL")
    if not test_url:
        pytest.skip("ALN_TEST_POSTGRES_URL is not configured; PostgreSQL migration not run")

    from alembic.config import Config

    from alembic import command

    admin_url = make_url(test_url)
    schema = f"aln_migration_test_{uuid4().hex[:12]}"
    admin_engine = create_engine(admin_url)
    scoped_url = admin_url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        monkeypatch.setenv("DATABASE_URL", str(scoped_url))
        get_settings.cache_clear()
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        command.upgrade(config, "head")
        scoped_engine = create_engine(scoped_url)
        try:
            assert "processing_runs" in inspect(scoped_engine).get_table_names()
            assert "sync_operations" in inspect(scoped_engine).get_table_names()
        finally:
            scoped_engine.dispose()
    finally:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin_engine.dispose()
        get_settings.cache_clear()


def test_existing_v5_export_columns_upgrade_without_losing_job(tmp_path):
    engine = _sqlite_engine(tmp_path / "export-v5.sqlite")
    _init_schema(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO export_jobs "
            "(id,request_key,format,status,progress_pct,rows,query_spec,expected_sources,"
            "artifacts,expires_at) VALUES "
            "('old-job','old-key','csv','pending',0,0,'{}','[]','[]','2026-12-31')")
        for name in ('request_digest', 'lease_token', 'executor_id', 'heartbeat_at',
                     'lease_expires_at', 'source_epoch', 'scope_digest'):
            conn.exec_driver_sql(f'ALTER TABLE export_jobs DROP COLUMN {name}')
    _init_schema(engine)
    _init_schema(engine)
    assert 'lease_token' in _table_columns(engine, 'export_jobs')
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT request_key FROM export_jobs").scalar_one() == 'old-key'
    engine.dispose()
