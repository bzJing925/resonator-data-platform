"""Real server-side bulk clock work, values and rollback stay bounded and atomic."""

import runpy
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import insert, text

from app.models import Batch, Device
from app.services.processing_run import create_run
from app.services.result_publish import _unpublished_device_delete
from tests.fixtures.platform_db import platform_database


@pytest.mark.integration
def test_bulk_insert_clock_counts_and_rollback(tmp_path):
    with platform_database("postgresql", tmp_path) as (db, batch):
        before_epoch = db.scalar(text("SELECT epoch FROM data_epoch WHERE id=1"))
        before_revision = db.scalar(
            text("SELECT revision FROM sync_revisions WHERE batch_id=:bid"), {"bid": batch.id}
        )
        plan = db.scalar(
            text("""EXPLAIN (ANALYZE, FORMAT JSON)
            INSERT INTO devices(batch_id,original_filename,deembedded,s_param_port)
            SELECT :bid, i::text, false, 'S11' FROM generate_series(1,8000) i"""),
            {"bid": batch.id},
        )
        assert db.scalar(text("SELECT epoch FROM data_epoch WHERE id=1")) == before_epoch + 8000
        assert db.scalar(
            text("SELECT revision FROM sync_revisions WHERE batch_id=:bid"), {"bid": batch.id}
        ) == before_revision + 8000
        # Server profiler measures the repeated clock work, not just client cursor calls.
        clock = next(t for t in plan[0]["Triggers"] if t["Trigger Name"] == "sync_devices_INSERT")
        assert clock["Calls"] == 1, clock
        db.rollback()
        assert db.scalar(text("SELECT epoch FROM data_epoch WHERE id=1")) == before_epoch
        assert db.scalar(text("SELECT count(*) FROM devices")) == 0


@pytest.mark.integration
def test_copy_multiple_batches_and_empty_insert_preserve_exact_deltas(tmp_path):
    with platform_database("postgresql", tmp_path) as (db, batch):
        other = Batch(batch_no="second", mapping_id=batch.mapping_id, file_path="second")
        db.add(other)
        db.commit()
        ids = (batch.id, other.id)
        before = db.scalar(text("SELECT epoch FROM data_epoch WHERE id=1"))
        revisions = dict(db.execute(text("SELECT batch_id,revision FROM sync_revisions")).all())
        driver = db.connection().connection.driver_connection
        with driver.cursor() as cursor, cursor.copy(
            "COPY devices (batch_id,original_filename,deembedded,s_param_port) FROM STDIN"
        ) as copier:
            for i in range(100):
                copier.write_row((ids[i % 2], f"{i}.s1p", False, "S11"))
        assert db.scalar(text("SELECT epoch FROM data_epoch WHERE id=1")) == before + 100
        for bid in ids:
            assert db.scalar(text("SELECT revision FROM sync_revisions WHERE batch_id=:bid"),
                             {"bid": bid}) == revisions[bid] + 50
        db.execute(text("INSERT INTO devices SELECT * FROM devices WHERE false"))
        assert db.scalar(text("SELECT epoch FROM data_epoch WHERE id=1")) == before + 100
        db.commit()
        assert db.scalar(text("SELECT count(*) FROM devices")) == 100


@pytest.mark.integration
def test_frozen_clock_migration_upgrade_and_downgrade(tmp_path):
    migration = runpy.run_path(str(Path(__file__).parents[2] / "alembic/versions"
                                   / "a41d0c7e52ab_bounded_device_insert_clocks.py"))
    with platform_database("postgresql", tmp_path) as (db, batch):
        bid = batch.id
        with Operations.context(MigrationContext.configure(db.connection())):
            for action, calls in (("downgrade", 3), ("upgrade", 1)):
                migration[action]()
                before = db.scalar(text("SELECT epoch FROM data_epoch WHERE id=1"))
                plan = db.scalar(text("""EXPLAIN (ANALYZE, FORMAT JSON)
                    INSERT INTO devices(batch_id,original_filename,deembedded,s_param_port)
                    SELECT :bid, i::text, false, 'S11' FROM generate_series(1,3) i"""),
                                 {"bid": bid})
                clock = next(t for t in plan[0]["Triggers"]
                             if t["Trigger Name"] == "sync_devices_INSERT")
                assert clock["Calls"] == calls
                assert db.scalar(text("SELECT epoch FROM data_epoch WHERE id=1")) == before + 3


@pytest.mark.integration
def test_replacement_delete_bounded_with_small_work_mem_and_legacy_null(tmp_path):
    with platform_database("postgresql", tmp_path) as (db, batch):
        bid = batch.id
        db.rollback()
        ticket = create_run(db.bind, bid, "upload", config={})
        db.execute(text("""INSERT INTO devices
            (batch_id,original_filename,deembedded,s_param_port,source_device_key)
            SELECT :bid, i::text, false, 'S11', md5(i::text)::uuid::text
            FROM generate_series(1,100000) i"""), {"bid": bid})
        db.execute(text("""INSERT INTO staged_device_rows
            (run_id,source_device_key,payload_json,payload_digest)
            SELECT :run, md5(i::text)::uuid::text, '{}'::json, 'fixture'
            FROM generate_series(1,100000) i"""), {"run": ticket.run_id})
        db.execute(insert(Device), [
            {"batch_id": bid, "original_filename": "legacy-null", "source_device_key": None},
            {"batch_id": bid, "original_filename": "obsolete",
             "source_device_key": "00000000-0000-0000-0000-000000000001"},
        ])
        db.commit()
        # Match the real million-row run after autovacuum collected cardinality.
        db.execute(text("ANALYZE staged_device_rows"))
        db.execute(text("ANALYZE devices"))
        db.commit()
        db.execute(text("SET LOCAL work_mem='64kB'"))
        db.execute(text("SET LOCAL statement_timeout='5s'"))
        db.execute(_unpublished_device_delete(bid, ticket.run_id))
        assert db.scalar(text("SELECT count(*) FROM devices")) == 100001
        assert db.scalar(
            text("SELECT count(*) FROM devices WHERE original_filename='legacy-null'")
        ) == 1
