"""Real PostgreSQL ORM/COPY paths must retain supplied source UUIDs."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models import Base, Batch, Device, Mapping
from app.services import device_ingest
from app.services.data_revision import read_epoch
from app.services.replica_service import init_replication


@pytest.mark.integration
@pytest.mark.parametrize("count", [1, 3000])
def test_supplied_source_keys_survive_real_orm_and_copy(count, monkeypatch):
    test_url = os.environ.get("ALN_TEST_POSTGRES_URL")
    if not test_url:
        pytest.skip("ALN_TEST_POSTGRES_URL missing: real COPY not verified")
    url = make_url(test_url)
    schema = f"aln_copy_test_{uuid4().hex[:12]}"
    admin = create_engine(url)
    engine = create_engine(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    successful_copies = []
    original_copy = device_ingest.copy_insert_devices

    def checked_copy(db, rows):
        original_copy(db, rows)
        successful_copies.append(len(rows))

    monkeypatch.setattr(device_ingest, "copy_insert_devices", checked_copy)
    try:
        with admin.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        Base.metadata.create_all(engine)
        init_replication(engine)
        with Session(engine) as db:
            mapping = Mapping(name="copy-map", file_path="map", entry_count=0)
            db.add(mapping)
            db.flush()
            batch = Batch(batch_no="copy-lot", mapping_id=mapping.id, file_path="files")
            db.add(batch)
            db.flush()
            expected = {str(uuid4()) for _ in range(count)}
            db.commit()
            epoch = read_epoch(db)
            rows = [
                {
                    "batch_id": batch.id,
                    "original_filename": f"dut-{i}.s1p",
                    "s_param_port": "S11",
                    "deembedded": False,
                    "source_device_key": key,
                }
                for i, key in enumerate(expected)
            ]
            device_ingest.bulk_insert_devices(db, rows, commit=True)
            assert set(db.scalars(select(Device.source_device_key))) == expected
            assert successful_copies == ([count] if count >= 3000 else [])
            assert read_epoch(db) == epoch + count
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()
