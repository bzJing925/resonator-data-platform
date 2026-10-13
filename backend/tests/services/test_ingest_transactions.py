"""Real SQLite/PG rollback boundaries, including failed COPY savepoint recovery."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Device, FileNode
from app.services import device_ingest
from app.services.compute_service import _upsert_device, sync_batch_device_count
from app.services.file_tree_service import build_file_tree_from_disk
from tests.fixtures.platform_db import DATABASES, platform_database


def row_for(batch_id):
    return {
        "batch_id": batch_id,
        "original_filename": "dut.s1p",
        "s_param_port": "S11",
        "s_param_path": "dut.s1p",
        "deembedded": False,
    }


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize("operation", ["bulk", "compute", "copy"])
def test_device_and_tree_writes_rollback_together(kind, operation, tmp_path, monkeypatch):
    with platform_database(kind, tmp_path) as (db, batch):
        if operation == "copy" and kind == "sqlite":
            # SQLite uses the ORM path even for a COPY-sized chunk.
            monkeypatch.setattr(device_ingest, "_COPY_THRESHOLD", 1)
        batch_id = batch.id
        row = row_for(batch_id)
        if operation == "compute":
            _upsert_device(db, batch, "dut.s1p", row)
        elif operation == "copy" and kind == "postgresql":
            device_ingest.copy_insert_devices(db, [row])
        else:
            device_ingest.bulk_insert_devices(db, [row])
        assert sync_batch_device_count(db, batch_id) == 1
        monkeypatch.setattr(
            "app.services.file_tree_service.scan_disk_files",
            lambda _: pytest.fail("Publication must not scan disk"),
        )
        build_file_tree_from_disk(db, batch, manifest=[(tmp_path / "dut.s1p", "dut.s1p")])
        assert db.scalar(select(func.count(FileNode.id))) > 0
        db.rollback()
        with Session(db.bind) as observer:
            assert observer.scalar(select(func.count(Device.id))) == 0
            assert observer.scalar(select(func.count(FileNode.id))) == 0
            assert observer.get(type(batch), batch_id).device_count == 0


@pytest.mark.parametrize("kind", DATABASES)
def test_caller_can_explicitly_commit(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        device_ingest.bulk_insert_devices(db, [row_for(batch.id)], commit=True)
        db.rollback()
        with Session(db.bind) as observer:
            assert observer.scalar(select(func.count(Device.id))) == 1


@pytest.mark.integration
def test_failed_copy_savepoint_preserves_outer_work_and_allows_fallback(tmp_path, monkeypatch):
    with platform_database("postgresql", tmp_path) as (db, batch):
        batch.batch_no = "outer-work-retained"
        monkeypatch.setattr(device_ingest, "_COPY_THRESHOLD", 1)
        real_copy = device_ingest.copy_insert_devices

        def fail_after_copy(session, rows):
            real_copy(session, rows)
            session.execute(text("SELECT 1/0"))  # Abort the actual PG savepoint transaction.

        monkeypatch.setattr(device_ingest, "copy_insert_devices", fail_after_copy)
        device_ingest.bulk_insert_devices(db, [row_for(batch.id)])
        assert db.scalar(select(func.count(Device.id))) == 1  # No partially copied duplicate.
        db.commit()
        with Session(db.bind) as observer:
            assert observer.get(type(batch), batch.id).batch_no == "outer-work-retained"
            assert observer.scalar(select(func.count(Device.id))) == 1
