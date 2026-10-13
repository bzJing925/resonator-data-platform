"""Real transaction and lease gates for publication on file SQLite and PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Batch, Device, Mapping, ProcessingRun, UploadTask
from app.services.file_tree_service import build_file_tree_from_disk
from app.services.processing_report import record_device_result
from app.services.processing_run import RunConflictError, claim_run, create_run
from app.services.result_publish import publish_run, stage_device_rows
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def publication(request, tmp_path):
    with platform_database(request.param, tmp_path) as (db, batch):
        mapping = db.get(Mapping, batch.mapping_id)
        mapping_path = tmp_path / "mapping.csv"
        mapping_path.write_text("stable mapping")
        mapping.file_path = str(mapping_path)
        old = tmp_path / "old-results"
        old.mkdir()
        (old / "one.s1p").write_text("old one")
        (old / "two.s1p").write_text("old two")
        batch.file_path, batch.result_revision = str(old), 1
        keys = [str(uuid4()), str(uuid4())]
        devices = [
            Device(
                batch_id=batch.id,
                source_device_key=key,
                original_filename=f"{i}.s1p",
                s_param_path=f"{i}.s1p",
                s_param_port="S11",
                qs=10.0,
            )
            for i, key in enumerate(keys)
        ]
        db.add_all(devices)
        build_file_tree_from_disk(db, batch)
        db.commit()
        ids = [device.id for device in devices]
        yield db.bind, batch.id, keys, ids, old, mapping_path


def prepare(publication, kind="recompute", failed=False, stage_dir=None):
    engine, batch_id, keys, *_ = publication
    ticket = create_run(engine, batch_id, kind, config={"metrics": ["qs"]})
    ticket = claim_run(engine, ticket.run_id, ticket.lease_token, "compute")
    with Session(engine) as db:
        if stage_dir:
            db.get(ProcessingRun, ticket.run_id).stage_dir = str(stage_dir)
        for i, key in enumerate(keys):
            bad = failed and i == 1
            row = {
                "source_device_key": key,
                "qs": 20.0,
                "original_filename": f"{i}.s1p",
                "s_param_path": f"{i}.s1p",
                "s_param_port": "S11",
                "deembedded": False,
            }
            if not bad:
                stage_device_rows(db, ticket.run_id, ticket.lease_token, [row, row])
            record_device_result(
                db,
                ticket.run_id,
                f"{i}.s1p",
                "S11",
                result=None if bad else row,
                source_device_key=key,
                error_code="METRIC_EXTRACTION_FAILED" if bad else None,
            )
        db.commit()
    return ticket


def test_staging_is_invisible_and_partial_recompute_preserves_ids(publication):
    from app.models import DataEpoch

    engine, batch_id, keys, ids, old, _ = publication
    ticket = prepare(publication, failed=True)
    with Session(engine) as reader:
        assert [reader.get(Device, i).qs for i in ids] == [10, 10]
        assert reader.get(Batch, batch_id).file_path == str(old)
    result = publish_run(engine, ticket.run_id, ticket.lease_token)
    assert result["published_count"] == 1 and result["result_revision"] == 2
    with Session(engine) as reader:
        assert [reader.get(Device, i).qs for i in ids] == [20, 10]
        run = reader.get(ProcessingRun, ticket.run_id)
        assert run.status == "success" and run.quality_status == "partial"
        assert reader.get(UploadTask, ticket.task_id).status == "success"
        assert reader.get(Batch, batch_id).active_run_id is None
        epoch = reader.get(DataEpoch, 1).epoch
    assert publish_run(engine, ticket.run_id, ticket.lease_token) == result
    with Session(engine) as reader:
        assert reader.get(DataEpoch, 1).epoch == epoch
    assert old.exists()


@pytest.mark.parametrize("conflict", ["cancel", "old_token", "mapping", "revision"])
def test_conflicts_keep_old_results(publication, conflict):
    engine, batch_id, _, ids, old, mapping_path = publication
    ticket = prepare(publication)
    token = ticket.lease_token
    with Session(engine) as db:
        if conflict == "cancel":
            db.execute(
                update(ProcessingRun)
                .where(ProcessingRun.id == ticket.run_id)
                .values(status="cancel_requested")
            )
            db.execute(
                update(UploadTask)
                .where(UploadTask.id == ticket.task_id)
                .values(status="cancel_requested")
            )
        elif conflict == "old_token":
            token = str(uuid4())
        elif conflict == "mapping":
            mapping_path.write_text("changed mapping")
        else:
            db.get(Batch, batch_id).result_revision += 1
        db.commit()
    with pytest.raises(RunConflictError):
        publish_run(engine, ticket.run_id, token)
    with Session(engine) as reader:
        assert [reader.get(Device, i).qs for i in ids] == [10, 10]
        assert reader.get(Batch, batch_id).file_path == str(old)


def test_full_replacement_failure_keeps_old_results(publication):
    engine, batch_id, _, ids, old, _ = publication
    ticket = prepare(publication, kind="reextract", failed=True)
    with pytest.raises(RunConflictError, match="全量处理存在失败"):
        publish_run(engine, ticket.run_id, ticket.lease_token)
    with Session(engine) as reader:
        assert [reader.get(Device, i).qs for i in ids] == [10, 10]
        assert reader.get(Batch, batch_id).file_path == str(old)


def test_file_tree_failure_rolls_back_and_success_switches_once(publication, tmp_path, monkeypatch):
    from app.services import file_tree_service

    engine, batch_id, keys, ids, old, _ = publication
    new = tmp_path / "new-results"
    new.mkdir()
    (new / "0.s1p").write_text("new result")
    ticket = prepare(publication, kind="reextract", stage_dir=new)
    original = file_tree_service.build_file_tree_from_disk

    def fail_after_tree(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crashed before commit")

    monkeypatch.setattr(file_tree_service, "build_file_tree_from_disk", fail_after_tree)
    with pytest.raises(RuntimeError, match="before commit"):
        publish_run(engine, ticket.run_id, ticket.lease_token)
    with Session(engine) as reader:
        assert [reader.get(Device, i).qs for i in ids] == [10, 10]
        assert reader.get(Batch, batch_id).result_revision == 1
        assert reader.get(Batch, batch_id).file_path == str(old)
    monkeypatch.setattr(file_tree_service, "build_file_tree_from_disk", original)
    publish_run(engine, ticket.run_id, ticket.lease_token)
    with Session(engine) as reader:
        assert [reader.get(Device, i).source_device_key for i in ids] == keys
        assert reader.get(Batch, batch_id).file_path == str(new)
        assert len(reader.scalars(select(Device)).all()) == 2
    assert old.exists()


def test_publish_cancel_race_has_one_winner(publication):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.services.processing_run import _locked_session, _owned_run
    from app.services.task_state import transition_task

    engine, batch_id, _, ids, old, _ = publication
    ticket = prepare(publication)
    barrier = Barrier(2)

    def publish():
        barrier.wait(timeout=5)
        try:
            publish_run(engine, ticket.run_id, ticket.lease_token)
            return True
        except RunConflictError:
            return False

    def cancel():
        barrier.wait(timeout=5)
        try:
            with _locked_session(engine) as db:
                run, _batch = _owned_run(db, ticket.run_id, ticket.lease_token)
                return (
                    transition_task(
                        db,
                        ticket.task_id,
                        run.id,
                        ticket.lease_token,
                        "running",
                        "cancel_requested",
                    )
                    == "changed"
                )
        except RunConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        published = pool.submit(publish)
        cancelled = pool.submit(cancel)
        assert published.result(timeout=10) != cancelled.result(timeout=10)
    with Session(engine) as reader:
        run = reader.get(ProcessingRun, ticket.run_id)
        assert run.status in {"success", "cancel_requested"}
        assert reader.get(UploadTask, ticket.task_id).status == run.status
        expected = 20 if run.status == "success" else 10
        assert [reader.get(Device, i).qs for i in ids] == [expected, expected]
        assert reader.get(Batch, batch_id).file_path == str(old)


def test_tampered_staging_cannot_publish(publication):
    from app.models import StagedDeviceRow

    engine, batch_id, keys, ids, old, _ = publication
    ticket = prepare(publication)
    with Session(engine) as db:
        row = db.get(StagedDeviceRow, (ticket.run_id, keys[0]))
        row.payload_json = {**row.payload_json, "qs": 99}
        db.commit()
    with pytest.raises(RunConflictError, match="摘要不匹配"):
        publish_run(engine, ticket.run_id, ticket.lease_token)
    with Session(engine) as reader:
        assert [reader.get(Device, i).qs for i in ids] == [10, 10]
        assert reader.get(Batch, batch_id).result_revision == 1


def test_first_partial_and_zero_success(publication):
    from sqlalchemy import delete

    from app.models import StagedDeviceRow
    from app.services.processing_run import finish_run

    engine, batch_id, keys, _, old, _ = publication
    with Session(engine) as db:
        db.execute(delete(Device).where(Device.batch_id == batch_id))
        db.get(Batch, batch_id).result_revision = 0
        db.commit()
    ticket = prepare(publication, kind="upload", failed=True)
    publish_run(engine, ticket.run_id, ticket.lease_token)
    with Session(engine) as reader:
        assert reader.get(ProcessingRun, ticket.run_id).quality_status == "partial"
        assert reader.get(Batch, batch_id).device_count == 1
    second = prepare(publication, kind="reextract")
    with Session(engine) as db:
        db.execute(delete(StagedDeviceRow).where(StagedDeviceRow.run_id == second.run_id))
        db.commit()
    with pytest.raises(RunConflictError, match="没有成功结果"):
        publish_run(engine, second.run_id, second.lease_token)
    finish_run(engine, second.run_id, second.lease_token, "failed")
    with Session(engine) as reader:
        assert reader.get(Batch, batch_id).device_count == 1
        assert reader.get(Batch, batch_id).result_revision == 1
