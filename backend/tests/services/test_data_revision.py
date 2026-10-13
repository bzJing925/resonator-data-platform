"""Both databases advance transactional clocks only for changed business data."""

from uuid import uuid4

import pytest
from sqlalchemy import select, text, update

from app.models import Device, SyncRevision
from app.services.data_revision import cache_version, read_epoch
from app.services.replica_service import init_replication
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize("kind", DATABASES)
def test_query_aggregate_distinct_cache_invalidated_after_bulk_update(kind, tmp_path, monkeypatch):
    from app.api import query
    from app.schemas.query import AggregateRequest, QueryRequest
    from tests.api.test_query_cache import _FakeRedis

    redis = _FakeRedis()
    monkeypatch.setattr(query, "get_redis_client", lambda: redis)
    with platform_database(kind, tmp_path) as (db, batch):
        db.add(Device(batch_id=batch.id, original_filename="dut.s1p", fs_ghz=1.0))
        db.commit()
        request = QueryRequest(fields=["id", "fs_ghz"], limit=10)
        agg = AggregateRequest(group_by=[], metrics=[{"field": "fs_ghz", "agg": ["avg"]}])
        assert query.query_devices(request, db).rows[0]["fs_ghz"] == 1.0
        assert query.aggregate(agg, db).groups[0]["fs_ghz"]["avg"] == 1.0
        assert query.distinct_values(db, "fs_ghz", 10)["values"] == [1.0]
        assert len(redis._store) == 3
        db.execute(update(Device).where(Device.batch_id == batch.id).values(fs_ghz=3.0))
        db.commit()
        assert query.query_devices(request, db).rows[0]["fs_ghz"] == 3.0
        assert query.aggregate(agg, db).groups[0]["fs_ghz"]["avg"] == 3.0
        assert query.distinct_values(db, "fs_ghz", 10)["values"] == [3.0]
        assert len(redis._store) == 6


@pytest.mark.parametrize("kind", DATABASES)
def test_business_bulk_noop_operational_and_rollback(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        initial = read_epoch(db)
        db.execute(
            text("UPDATE batches SET active_run_id=:run WHERE id=:id"),
            {"run": str(uuid4()), "id": batch.id},
        )
        db.commit()
        assert read_epoch(db) == initial
        device = Device(batch_id=batch.id, original_filename="dut.s1p", fs_ghz=None)
        db.add(device)
        db.commit()
        before = read_epoch(db)
        revision = db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
        db.execute(update(Device).where(Device.id == device.id).values(fs_ghz=2.5))
        db.commit()
        assert read_epoch(db) == before + 1
        assert (
            db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
            == revision + 1
        )
        before = read_epoch(db)
        db.execute(update(Device).where(Device.id == device.id).values(fs_ghz=2.5))
        db.commit()
        assert read_epoch(db) == before
        version = cache_version(db)
        db.execute(update(Device).where(Device.id == device.id).values(fs_ghz=None))
        assert read_epoch(db) == before + 1
        db.rollback()
        assert read_epoch(db) == before and cache_version(db) == version
        db.rollback()
        init_replication(db.bind)
        assert cache_version(db) == version


@pytest.mark.parametrize("kind", DATABASES)
def test_run_heartbeat_staging_do_not_invalidate_captured_base(kind, tmp_path):
    from datetime import UTC, datetime

    from app.models import Batch, ProcessingRun, StagedDeviceRow, UploadTask

    with platform_database(kind, tmp_path) as (db, batch):
        base = db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
        epoch = read_epoch(db)
        task = UploadTask(batch_no=batch.batch_no)
        db.add(task)
        db.flush()
        run = ProcessingRun(
            task_id=task.id,
            batch_id=batch.id,
            kind="recompute",
            base_source_revision=base,
            status="running",
            lease_token=str(uuid4()),
        )
        db.add(run)
        db.flush()
        db.execute(
            update(Batch).where(Batch.id == batch.id).values(task_id=task.id, active_run_id=run.id)
        )
        run.heartbeat_at = datetime.now(UTC)
        db.add(
            StagedDeviceRow(
                run_id=run.id,
                source_device_key=str(uuid4()),
                payload_json={"fs_ghz": 4.0},
                payload_digest="digest",
            )
        )
        db.commit()
        assert read_epoch(db) == epoch
        assert (
            db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
            == base
            == run.base_source_revision
        )


@pytest.mark.parametrize("kind", DATABASES)
def test_mapping_and_process_business_changes_advance_clock(kind, tmp_path):
    from app.models import MappingEntry, ProcessMoDepRun

    with platform_database(kind, tmp_path) as (db, batch):
        epoch = read_epoch(db)
        batch.mapping.name = "changed"
        db.commit()
        assert read_epoch(db) == epoch + 1
        entry = MappingEntry(mapping_id=batch.mapping_id, mark="E6-1", has_pf=False)
        process = ProcessMoDepRun(run_no="R1")
        db.add_all([entry, process])
        db.commit()
        before = read_epoch(db)
        revision = db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
        entry.eg = 2.0
        process.dc_power_kw = 1.0
        db.commit()
        assert read_epoch(db) == before + 2
        assert (
            db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
            == revision + 1
        )


@pytest.mark.parametrize("kind", DATABASES)
def test_curve_caches_invalidate_with_same_file_stat(kind, tmp_path, monkeypatch):
    import os

    import numpy as np

    from app.api import devices

    path = tmp_path / "cache.s1p"
    path.write_text("# Hz S RI R 50\n1000000000 0.2 0.0\n2000000000 0.3 0.0\n3000000000 0.4 0.0\n")
    original_stat = path.stat()
    devices._load_network.cache_clear()
    devices._calc_bodeq_cached.cache_clear()
    monkeypatch.setattr(
        "app.core.extract.calc_bodeq_curve",
        lambda spectrum, frequency: {
            "marker": float(np.real(spectrum[0])),
        },
    )
    with platform_database(kind, tmp_path) as (db, batch):
        device = Device(batch_id=batch.id, original_filename="cache.s1p", s_param_path=str(path))
        db.add(device)
        db.commit()
        old = devices.build_sparam_response(device, device.id, "s11_db")
        assert devices.build_bodeq_response(device, device.id)["marker"] == 0.2
        path.write_text(path.read_text().replace("0.2", "0.8"))
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        assert path.stat().st_size == original_stat.st_size
        device.fs_ghz = 2.0
        db.commit()
        new = devices.build_sparam_response(device, device.id, "s11_db")
        assert old["values"][0] != new["values"][0]
        assert devices.build_bodeq_response(device, device.id)["marker"] == 0.8


@pytest.mark.integration
def test_real_postgresql_stats_reject_stale_materialized_view(tmp_path):
    from app.services.batch_stats_service import get_batch_stats, refresh_mv_batch_stats

    with platform_database("postgresql", tmp_path) as (db, batch):
        db.add(Device(batch_id=batch.id, original_filename="dut.s1p", fs_ghz=1.0, pf="Y", wafer=1))
        db.commit()
        db.execute(
            text("""CREATE MATERIALIZED VIEW mv_batch_stats AS
            SELECT batch_id, wafer, COUNT(*) AS total_count, COUNT(*) FILTER (WHERE pf='Y') AS pass_count,
            AVG(fs_ghz) AS avg_fs_ghz,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY fs_ghz) AS median_fs_ghz
            FROM devices GROUP BY batch_id,wafer""")
        )
        db.execute(text("CREATE UNIQUE INDEX mv_stats_pk ON mv_batch_stats(batch_id,wafer)"))
        db.commit()
        refresh_mv_batch_stats(db)
        assert get_batch_stats(db, batch.id, 1)["fs_ghz_mean"] == 1.0
        db.execute(update(Device).where(Device.batch_id == batch.id).values(fs_ghz=5.0, pf="N"))
        db.commit()
        assert db.scalar(text("SELECT avg_fs_ghz FROM mv_batch_stats")) == 1.0
        assert get_batch_stats(db, batch.id, 1) == {
            "fs_ghz_mean": 5.0,
            "fs_ghz_median": 5.0,
            "pass_rate": 0.0,
        }
        refresh_mv_batch_stats(db)
        assert db.scalar(text("SELECT stats_epoch=epoch FROM data_epoch WHERE id=1")) is True
        assert db.scalar(text("SELECT avg_fs_ghz FROM mv_batch_stats")) == 5.0
