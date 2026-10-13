"""Retry identity, guarded inputs and merged publication on both real databases."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Device, ProcessingItem, ProcessingRun, SyncRevision
from app.services.processing_report import record_device_result
from app.services.processing_retry import create_retry_run
from app.services.processing_run import RunConflictError, claim_run, create_run
from app.services.result_publish import publish_run, stage_device_rows
from tests.fixtures.platform_db import DATABASES, platform_database


def _parent(db, batch, tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "OPEN.s2p").write_text("retained calibration input")
    mapping = tmp_path / "mapping.csv"
    mapping.write_text("E6-1,EG0 FL0 700&5500\n")
    batch.file_path, batch.mapping.file_path = str(inputs), str(mapping)
    for index in range(2):
        name = f"17_E6-1_X{index}Y0.s1p"
        (inputs / name).write_text(f"retained input {index}")
        db.add(
            Device(
                batch_id=batch.id,
                original_filename=name,
                s_param_path=name,
                s_param_port="S11",
                qs=1,
            )
        )
    db.commit()
    batch_id, engine = batch.id, db.bind
    db.rollback()
    ticket = create_run(engine, batch_id, "recompute", config={"metrics": ["qs"]})
    claimed = claim_run(engine, ticket.run_id, ticket.lease_token, "compute")
    with Session(engine) as writer:
        devices = writer.scalars(select(Device).order_by(Device.id)).all()
        stage_device_rows(
            writer,
            claimed.run_id,
            claimed.lease_token,
            [{"source_device_key": devices[0].source_device_key, "qs": 10}],
        )
        record_device_result(
            writer,
            claimed.run_id,
            devices[0].s_param_path,
            "S11",
            result={"qs": 10},
            source_device_key=devices[0].source_device_key,
        )
        record_device_result(
            writer,
            claimed.run_id,
            devices[1].s_param_path,
            "S11",
            error_code="METRIC_EXTRACTION_FAILED",
            source_device_key=devices[1].source_device_key,
        )
        writer.commit()
    publish_run(engine, claimed.run_id, claimed.lease_token)
    with Session(engine) as reader:
        failed = reader.scalar(
            select(ProcessingItem).where(
                ProcessingItem.run_id == claimed.run_id, ProcessingItem.status == "failed"
            )
        )
        revision = reader.scalar(
            select(SyncRevision.revision).where(SyncRevision.batch_id == batch_id)
        )
        return claimed, failed.id, revision, mapping, inputs


@pytest.mark.parametrize("kind", DATABASES)
def test_retry_merges_only_failed_metrics_and_replays_after_completion(kind, tmp_path, monkeypatch):
    from app.workers.reprocess_batch import recompute_batch_task

    with platform_database(kind, tmp_path) as (db, batch):
        parent, failed_id, revision, _, _ = _parent(db, batch, tmp_path)
        engine = db.bind
        with Session(engine) as reader:
            before = [
                (item.id, item.status, item.error_code, item.result_digest)
                for item in reader.scalars(
                    select(ProcessingItem)
                    .where(ProcessingItem.run_id == parent.run_id)
                    .order_by(ProcessingItem.id)
                )
            ]
        request_key = str(uuid4())
        retry = create_retry_run(
            engine,
            parent.task_id,
            request_key=request_key,
            expected_source_revision=revision,
            item_ids=[failed_id],
        )
        assert retry.task_id != parent.task_id and retry.run_id != parent.run_id
        factory = sessionmaker(engine)
        for module in ("app.db", "app.workers.reprocess_batch", "app.workers.cancel"):
            monkeypatch.setattr(f"{module}.SessionLocal", factory)
        monkeypatch.setattr("app.db.engine", engine)
        monkeypatch.setattr(
            "app.workers.progress.get_settings", lambda: SimpleNamespace(is_desktop=True)
        )
        seen = []

        def compute(path, **kwargs):
            seen.append(Path(path).name)
            return {"qs": 55}

        monkeypatch.setattr("app.workers.reprocess_batch.extract_resonator_params", compute)
        result = recompute_batch_task.apply(
            kwargs={
                "upload_task_id": retry.task_id,
                "batch_no": "source-lot",
                "metrics": ["qs"],
                "run_id": retry.run_id,
                "lease_token": retry.lease_token,
            }
        ).get(propagate=True)
        assert seen == ["17_E6-1_X1Y0.s1p"] and result["failures"] == 0
        repeated = create_retry_run(
            engine,
            parent.task_id,
            request_key=request_key,
            expected_source_revision=revision,
            item_ids=[failed_id],
        )
        assert repeated.task_id == retry.task_id
        with Session(engine) as reader:
            assert [row.qs for row in reader.scalars(select(Device).order_by(Device.id))] == [
                10,
                55,
            ]
            run = reader.get(ProcessingRun, retry.run_id)
            assert run.retry_of_run_id == parent.run_id and run.attempt == 2
            assert run.status == "success" and run.summary_json["device_counts"]["success"] == 2
            after = [
                (item.id, item.status, item.error_code, item.result_digest)
                for item in reader.scalars(
                    select(ProcessingItem)
                    .where(ProcessingItem.run_id == parent.run_id)
                    .order_by(ProcessingItem.id)
                )
            ]
            assert before == after


@pytest.mark.parametrize("kind", DATABASES)
def test_simultaneous_retry_request_key_is_one_attempt(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        parent, _, revision, _, _ = _parent(db, batch, tmp_path)
        barrier = Barrier(2)
        request_key = str(uuid4())

        def retry():
            barrier.wait(timeout=5)
            return create_retry_run(
                db.bind, parent.task_id, request_key=request_key, expected_source_revision=revision
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: retry(), range(2)))
        assert first.task_id == second.task_id
        with pytest.raises(RunConflictError, match="请求键"):
            create_retry_run(
                db.bind,
                parent.task_id,
                request_key=request_key,
                expected_source_revision=revision,
                item_ids=[],
            )


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize(
    "change", ["mapping", "input", "missing_calibration", "revision", "item", "config", "source"]
)
def test_retry_rejects_changed_inputs_and_wrong_items(kind, change, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        parent, _, revision, mapping, inputs = _parent(db, batch, tmp_path)
        kwargs = {"request_key": str(uuid4()), "expected_source_revision": revision}
        if change == "mapping":
            mapping.write_text("E6-1,EG9 FL0 700&5500\n")
        elif change == "input":
            (inputs / "17_E6-1_X1Y0.s1p").write_text("changed")
        elif change == "missing_calibration":
            (inputs / "OPEN.s2p").unlink()
        elif change == "revision":
            kwargs["expected_source_revision"] += 1
        elif change == "item":
            kwargs["item_ids"] = [str(uuid4())]
        elif change == "source":
            with Session(db.bind) as editor:
                device = editor.scalar(select(Device))
                device.qs = 999
                editor.commit()
        elif change == "config":
            with Session(db.bind) as editor:
                run = editor.get(ProcessingRun, parent.run_id)
                run.config_json = {**run.config_json, "metrics": ["qp"]}
                editor.commit()
        with pytest.raises(RunConflictError) as caught:
            create_retry_run(db.bind, parent.task_id, **kwargs)
        assert caught.value.code == (
            "RETRY_ITEMS_INVALID" if change == "item" else "RETRY_INPUT_CHANGED"
        )
        with Session(db.bind) as reader:
            assert len(reader.scalars(select(ProcessingRun)).all()) == 1


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize("old_results", [False, True])
def test_archive_retry_preserves_success_ids_and_full_replacement_gate(
    kind, old_results, tmp_path, monkeypatch
):
    from app.services.processing_run import finish_run
    from app.services.source_identity import register_prechecked_sources
    from app.services.upload_precheck import build_precheck_report
    from app.workers import compute_batch
    from app.workers.extract_batch import extract_batch_task
    from tests.fixtures.platform_cases import _algorithm_samples, _write_archive

    samples = _algorithm_samples(tmp_path)
    raw = tmp_path / "raw.zip"
    names = [f"17_E6-1_X{index}Y0.s1p" for index in range(2)]
    _write_archive(raw, [(name, samples["dut_s1p"]) for name in names])
    mapping = tmp_path / "mapping.csv"
    mapping.write_text("E6-1,EG0 FL0 700&5500\n")
    prepared = tmp_path / "prepared"
    report = build_precheck_report(raw, prepared, {}, mapping)
    assert report["fatal_count"] == 0
    with platform_database(kind, tmp_path) as (db, batch):
        batch.file_path, batch.raw_zip_path = str(prepared), str(raw)
        batch.mapping.file_path = str(mapping)
        db.commit()
        register_prechecked_sources(db, batch, report)
        db.commit()
        batch_id, engine = batch.id, db.bind
        if old_results:
            from app.models import SourceInput

            for name in names:
                source = db.scalar(select(SourceInput).where(SourceInput.member_key == name))
                db.add(
                    Device(
                        batch_id=batch_id,
                        original_filename=name,
                        s_param_path=name,
                        s_param_port="S11",
                        source_device_key=source.source_device_key,
                        qs=1,
                    )
                )
            db.commit()
        db.rollback()
        ticket = create_run(
            engine,
            batch_id,
            "upload",
            config={
                "precheck_report": report,
                "prepared_dir": str(prepared),
                "prepared_file_names": names,
            },
        )
        claimed = claim_run(engine, ticket.run_id, ticket.lease_token, "compute")
        with Session(engine) as writer:
            values = {
                "original_filename": names[0],
                "s_param_path": names[0],
                "qs": 10,
                "s_param_port": "S11",
            }
            item = record_device_result(writer, claimed.run_id, names[0], "S11", result=values)
            values["source_device_key"] = item.source_device_key
            stage_device_rows(writer, claimed.run_id, claimed.lease_token, [values])
            record_device_result(
                writer, claimed.run_id, names[1], "S11", error_code="METRIC_EXTRACTION_FAILED"
            )
            writer.commit()
        if old_results:
            with pytest.raises(RunConflictError, match="全量处理"):
                publish_run(engine, claimed.run_id, claimed.lease_token)
            finish_run(engine, claimed.run_id, claimed.lease_token, "failed")
        else:
            publish_run(engine, claimed.run_id, claimed.lease_token)
        with Session(engine) as reader:
            before_ids = {row.source_device_key: row.id for row in reader.scalars(select(Device))}
            revision = reader.scalar(
                select(SyncRevision.revision).where(SyncRevision.batch_id == batch_id)
            )
        retry = create_retry_run(
            engine, claimed.task_id, request_key=str(uuid4()), expected_source_revision=revision
        )
        factory = sessionmaker(engine)
        for module in (
            "app.db",
            "app.workers.compute_batch",
            "app.workers.extract_batch",
            "app.workers.cancel",
        ):
            monkeypatch.setattr(f"{module}.SessionLocal", factory)
        monkeypatch.setattr("app.db.engine", engine)
        monkeypatch.setattr(
            "app.workers.progress.get_settings", lambda: SimpleNamespace(is_desktop=True)
        )
        seen = []
        real_compute = compute_batch._extract_single

        def observed(args):
            seen.append(Path(args[0]).name)
            return real_compute(args)

        monkeypatch.setattr(compute_batch, "_extract_single", observed)
        extracted = extract_batch_task.apply(
            kwargs={
                "upload_task_id": retry.task_id,
                "zip_path": str(raw),
                "batch_no": "source-lot",
                "mapping_id": batch.mapping_id,
                "run_id": retry.run_id,
                "lease_token": retry.lease_token,
            }
        ).get(propagate=True)
        compute_batch.compute_batch_task.apply(args=[extracted]).get(propagate=True)
        assert seen == [names[1]]
        with Session(engine) as reader:
            rows = reader.scalars(select(Device).order_by(Device.original_filename)).all()
            assert len(rows) == 2 and rows[0].qs == 10 and rows[1].qs is not None
            assert all(
                row.id == before_ids[row.source_device_key]
                for row in rows
                if row.source_device_key in before_ids
            )
            assert reader.get(ProcessingRun, retry.run_id).summary_json["device_counts"] == {
                "total": 2,
                "pending": 0,
                "running": 0,
                "success": 2,
                "failed": 0,
                "skipped": 0,
                "cancelled": 0,
            }
            assert raw.is_file() and prepared.is_dir()


@pytest.mark.parametrize("kind", DATABASES)
def test_http_replay_dispatches_once_and_returns_same_terminal_task(kind, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.tasks import router
    from app.db import get_db
    from app.services.processing_run import finish_run

    with platform_database(kind, tmp_path) as (db, batch):
        parent, _, revision, _, _ = _parent(db, batch, tmp_path)
        engine = db.bind
        app = FastAPI()
        app.include_router(router, prefix="/api")

        def session():
            with Session(engine) as connection:
                yield connection

        app.dependency_overrides[get_db] = session
        deliveries = []

        def dispatch(*args, **kwargs):
            deliveries.append(kwargs)
            claimed = claim_run(engine, kwargs["run_id"], kwargs["lease_token"], "compute")
            finish_run(engine, claimed.run_id, claimed.lease_token, "failed")
            return "accepted"

        monkeypatch.setattr("app.workers.dispatch.dispatch_reprocess_task", dispatch)
        with TestClient(app) as client:
            body = {
                "request_key": str(uuid4()),
                "mode": "failed",
                "expected_source_revision": revision,
            }
            first = client.post(f"/api/tasks/{parent.task_id}/retry", json=body)
            assert first.status_code == 202, first.text
            second = client.post(f"/api/tasks/{parent.task_id}/retry", json=body)
            assert second.status_code == 202 and first.json() == second.json()
            assert first.json()["status"] == "failed" and len(deliveries) == 1
            # A different attempt must not inherit the previous delivery flag.
            next_attempt = client.post(
                f"/api/tasks/{first.json()['task_id']}/retry",
                json={
                    **body,
                    "request_key": str(uuid4()),
                },
            )
            assert next_attempt.status_code == 202, next_attempt.text
            assert len(deliveries) == 2


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize("tamper", [False, True])
def test_table_retry_reuses_source_identity_and_checks_prepared_rows(
    kind, tamper, tmp_path, monkeypatch
):
    import json

    from app.services.source_identity import register_prechecked_sources
    from app.services.upload_precheck import build_precheck_report
    from app.workers.precheck import import_prechecked_table_task
    from tests.services.test_upload_precheck import table_frame

    raw = tmp_path / "table.xlsx"
    frame = table_frame().iloc[:1].copy()
    frame.to_excel(raw, index=False)
    mapping = tmp_path / "mapping.csv"
    mapping.write_text("E6-1,EG0 FL0 700&5500\n")
    prepared = tmp_path / "prepared"
    report = build_precheck_report(raw, prepared, {}, mapping)
    assert report["fatal_count"] == 0
    with platform_database(kind, tmp_path) as (db, batch):
        batch.file_path, batch.raw_zip_path = str(raw), str(raw)
        batch.mapping.file_path = str(mapping)
        db.commit()
        register_prechecked_sources(db, batch, report)
        db.commit()
        engine, batch_id = db.bind, batch.id
        db.rollback()
        parent = create_run(
            engine,
            batch_id,
            "upload",
            config={
                "precheck_report": report,
                "prepared_dir": str(prepared),
            },
        )
        claimed = claim_run(engine, parent.run_id, parent.lease_token, "compute")
        from app.services.processing_run import finish_run

        finish_run(engine, claimed.run_id, claimed.lease_token, "failed")
        with Session(engine) as reader:
            revision = reader.scalar(
                select(SyncRevision.revision).where(SyncRevision.batch_id == batch_id)
            )
        if tamper:
            rows_path = prepared / "table-rows.json"
            rows = json.loads(rows_path.read_text())
            rows[0]["qs"] = 999
            rows_path.write_text(json.dumps(rows))
            with pytest.raises(RunConflictError) as caught:
                create_retry_run(
                    engine,
                    parent.task_id,
                    request_key=str(uuid4()),
                    expected_source_revision=revision,
                )
            assert caught.value.code == "RETRY_INPUT_CHANGED"
            return
        retry = create_retry_run(
            engine, parent.task_id, request_key=str(uuid4()), expected_source_revision=revision
        )
        monkeypatch.setattr("app.db.engine", engine)
        monkeypatch.setattr("app.db.SessionLocal", sessionmaker(engine))
        result = import_prechecked_table_task.apply(
            args=[
                retry.task_id,
                retry.run_id,
                retry.lease_token,
            ]
        ).get(propagate=True)
        assert result["device_count"] == 1
        with Session(engine) as reader:
            from app.models import SourceInput

            device = reader.scalar(select(Device))
            source = reader.scalar(select(SourceInput))
            assert device.source_device_key == source.source_device_key and device.qs == 5
            item = reader.scalar(
                select(ProcessingItem).where(
                    ProcessingItem.run_id == retry.run_id, ProcessingItem.unit_kind == "table_row"
                )
            )
            assert item.status == "success" and item.attempt == 2


@pytest.mark.parametrize("kind", DATABASES)
def test_confirmed_cancelled_items_can_start_a_new_attempt(kind, tmp_path):
    from app.services.processing_cancel import confirm_stopped, request_cancellation
    from app.services.processing_report import record_item_result

    with platform_database(kind, tmp_path) as (db, batch):
        _, _, revision, _, _ = _parent(db, batch, tmp_path)
        engine, batch_id = db.bind, batch.id
        db.rollback()
        parent = create_run(engine, batch_id, "recompute", config={"metrics": ["qs"]})
        claimed = claim_run(engine, parent.run_id, parent.lease_token, "compute")
        with Session(engine) as writer:
            for device in writer.scalars(select(Device)):
                record_item_result(
                    writer,
                    parent.run_id,
                    "device",
                    device.s_param_path,
                    "pending",
                    port=device.s_param_port,
                    source_device_key=device.source_device_key,
                )
            writer.commit()
        request_cancellation(engine, parent.task_id)
        confirm_stopped(
            engine, parent.task_id, run_id=parent.run_id, lease_token=claimed.lease_token
        )
        retry = create_retry_run(
            engine, parent.task_id, request_key=str(uuid4()), expected_source_revision=revision
        )
        with Session(engine) as reader:
            assert reader.get(ProcessingRun, parent.run_id).status == "cancelled"
            assert reader.get(ProcessingRun, retry.run_id).status == "pending"
            items = reader.scalars(
                select(ProcessingItem).where(ProcessingItem.run_id == retry.run_id)
            ).all()
            assert len(items) == 2 and all(item.status == "pending" for item in items)


@pytest.mark.parametrize("kind", DATABASES)
def test_reextract_retry_checks_raw_archive_not_only_active_directory(kind, tmp_path):
    from app.services.processing_run import finish_run

    with platform_database(kind, tmp_path) as (db, batch):
        _parent(db, batch, tmp_path)
        raw = tmp_path / "retained.zip"
        raw.write_bytes(b"original archive bytes")
        batch.raw_zip_path = str(raw)
        db.commit()
        engine, batch_id = db.bind, batch.id
        db.rollback()
        parent = create_run(engine, batch_id, "reextract")
        claimed = claim_run(engine, parent.run_id, parent.lease_token, "compute")
        with Session(engine) as writer:
            device = writer.scalar(select(Device))
            record_device_result(
                writer,
                parent.run_id,
                device.s_param_path,
                device.s_param_port,
                error_code="METRIC_EXTRACTION_FAILED",
                source_device_key=device.source_device_key,
            )
            writer.commit()
        finish_run(engine, parent.run_id, claimed.lease_token, "failed")
        with Session(engine) as reader:
            revision = reader.scalar(
                select(SyncRevision.revision).where(SyncRevision.batch_id == batch_id)
            )
        raw.write_bytes(b"changed archive with the same active directory")
        with pytest.raises(RunConflictError) as caught:
            create_retry_run(
                engine, parent.task_id, request_key=str(uuid4()), expected_source_revision=revision
            )
        assert caught.value.code == "RETRY_INPUT_CHANGED"
