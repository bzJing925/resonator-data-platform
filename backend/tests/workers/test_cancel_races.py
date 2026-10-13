"""Late writers and native join timeout cannot pretend cancellation is complete."""

from threading import Event, Thread
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models import ProcessingRun, UploadTask
from app.services.processing_cancel import request_cancellation
from app.services.processing_run import RunConflictError, claim_run, create_run
from app.services.result_publish import stage_device_rows
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize("kind", DATABASES)
def test_cancel_blocks_current_and_late_writes(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "recompute", config={"metrics": ["qs"]})
        claimed = claim_run(engine, ticket.run_id, ticket.lease_token, "compute")
        request_cancellation(engine, ticket.task_id)
        for token in (ticket.lease_token, claimed.lease_token):
            with Session(engine) as writer, pytest.raises(RunConflictError):
                stage_device_rows(writer, ticket.run_id, token, [])
        with Session(engine) as reader:
            assert reader.get(ProcessingRun, ticket.run_id).status == "cancel_requested"
            assert reader.get(UploadTask, ticket.task_id).finished_at is None


@pytest.mark.parametrize("kind", DATABASES)
def test_native_join_timeout_keeps_requested_until_writer_exits(kind, tmp_path, monkeypatch):
    from app.services import processing_cancel
    from app.workers import local_worker
    from app.workers.local_queue import LocalTask
    from tests.workers.test_local_worker_subprocess import _FakeProcess, _FakeQueue

    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "recompute", config={"metrics": ["qs"]})
        claimed = claim_run(
            engine, ticket.run_id, ticket.lease_token, "compute", executor_id="local-12345"
        )
        assert claimed.task_id == ticket.task_id
        request_cancellation(engine, ticket.task_id)
        queue = _FakeQueue(
            LocalTask(task_id=ticket.task_id, kind="recompute"), cancel_after_start=True
        )
        release, unconfirmed = Event(), Event()
        monkeypatch.setattr(local_worker, "get_local_queue", lambda: queue)
        monkeypatch.setattr(local_worker, "multiprocessing", SimpleNamespace(Process=_FakeProcess))
        monkeypatch.setattr(local_worker, "_run_recompute", lambda task: release.wait(timeout=10))
        monkeypatch.setattr(local_worker, "SessionLocal", sessionmaker(engine))
        monkeypatch.setattr("app.db.SessionLocal", sessionmaker(engine))
        monkeypatch.setattr(
            "app.config.get_settings", lambda: SimpleNamespace(CANCEL_GRACE_SECONDS=0)
        )
        real_record = processing_cancel.record_stop_unconfirmed

        def record(*args):
            real_record(*args)
            unconfirmed.set()

        monkeypatch.setattr(processing_cancel, "record_stop_unconfirmed", record)
        thread = Thread(target=local_worker.local_worker_loop, daemon=True)
        thread.start()
        try:
            assert unconfirmed.wait(timeout=5)
            with Session(engine) as reader:
                task = reader.get(UploadTask, ticket.task_id)
                assert task.status == "cancel_requested" and task.finished_at is None
                assert task.error_msg == "STOP_NOT_CONFIRMED"
            assert thread.is_alive()
        finally:
            release.set()
            thread.join(timeout=5)
        assert not thread.is_alive()
        with Session(engine) as reader:
            assert reader.get(ProcessingRun, ticket.run_id).status == "cancelled"
            assert reader.get(UploadTask, ticket.task_id).status == "cancelled"


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize("cancelling", [False, True])
def test_restart_needs_dead_native_process_evidence(kind, cancelling, tmp_path, monkeypatch):
    from app.workers.local_worker import recover_interrupted_tasks

    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "recompute", config={"metrics": ["qs"]})
        claimed = claim_run(
            engine, ticket.run_id, ticket.lease_token, "compute", executor_id="local-2147483647"
        )
        assert claimed.task_id == ticket.task_id
        if cancelling:
            request_cancellation(engine, ticket.task_id)
        monkeypatch.setattr("app.workers.local_worker.SessionLocal", sessionmaker(engine))
        monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(is_desktop=False))
        assert recover_interrupted_tasks() == 0
        monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(is_desktop=True))
        assert recover_interrupted_tasks() == 1
        with Session(engine) as reader:
            run = reader.get(ProcessingRun, ticket.run_id)
            assert run.status == ("cancelled" if cancelling else "failed")
            assert reader.get(UploadTask, ticket.task_id).status == run.status


@pytest.mark.parametrize("kind", DATABASES)
def test_revoked_delivery_only_confirms_never_started_stage(kind, tmp_path, monkeypatch):
    from app.workers.cancel_signals import revoked_before_start
    from app.workers.dispatch import _reserve_executor_ids

    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "upload")
        monkeypatch.setattr("app.db.engine", engine)
        monkeypatch.setattr("app.db.SessionLocal", sessionmaker(engine))
        ids = _reserve_executor_ids(ticket.run_id, ticket.lease_token, ("extract", "compute"))
        assert ids["extract"] != ids["compute"]
        request_cancellation(engine, ticket.task_id)
        revoked_before_start(request=SimpleNamespace(id=ids["extract"]), terminated=True)
        with Session(engine) as reader:
            assert reader.get(ProcessingRun, ticket.run_id).status == "cancel_requested"
        revoked_before_start(request=SimpleNamespace(id=ids["extract"]), terminated=False)
        with Session(engine) as reader:
            run = reader.get(ProcessingRun, ticket.run_id)
            assert run.status == "cancelled" and run.summary_json["stop_confirmed"]
            assert run.executor_ids == ids


@pytest.mark.parametrize("kind", DATABASES)
def test_recompute_heartbeat_after_first_result_during_next_computation(
    kind, tmp_path, monkeypatch
):
    from app.models import Device
    from app.workers import reprocess_batch, run_execution

    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id, batch_no = db.bind, batch.id, batch.batch_no
        batch.file_path = str(tmp_path)
        mapping = tmp_path / "mapping.csv"
        mapping.write_text("mark,description\nE6-1,EG0 FL0 700&5500\n")
        batch.mapping.file_path = str(mapping)
        for index in range(2):
            filename = f"17_E6-1_X{index}Y0.s1p"
            (tmp_path / filename).write_text("temporary input")
            db.add(Device(batch_id=batch_id, original_filename=filename,
                          s_param_path=filename, s_param_port="S11", qs=1))
        db.commit()
        db.rollback()
        factory = sessionmaker(engine)
        for module in ("app.db", "app.workers.reprocess_batch", "app.workers.cancel"):
            monkeypatch.setattr(f"{module}.SessionLocal", factory)
        monkeypatch.setattr("app.db.engine", engine)
        monkeypatch.setattr(reprocess_batch, "load_mapping", lambda path: {})
        monkeypatch.setattr("app.workers.progress.get_settings",
                            lambda: SimpleNamespace(is_desktop=True))
        monkeypatch.setattr(run_execution, "get_settings",
                            lambda: SimpleNamespace(WORKER_HEARTBEAT_SECONDS=0.02))
        first_result, during_second = Event(), Event()
        heartbeat_seen = Event()
        real_heartbeat = run_execution.heartbeat_run

        def observed(*args):
            result = real_heartbeat(*args)
            if result and during_second.is_set():
                heartbeat_seen.set()
            return result

        def compute(path, **kwargs):
            if first_result.is_set():
                during_second.set()
                assert heartbeat_seen.wait(timeout=5), "Report writes blocked the lease heartbeat"
                with Session(engine) as reader:
                    assert [row.qs for row in reader.query(Device).all()] == [1, 1]
            else:
                first_result.set()
            return {"qs": 10}

        monkeypatch.setattr(run_execution, "heartbeat_run", observed)
        monkeypatch.setattr(reprocess_batch, "extract_resonator_params", compute)
        ticket = create_run(engine, batch_id, "recompute", config={"metrics": ["qs"]})
        result = reprocess_batch.recompute_batch_task.apply(kwargs={
            "upload_task_id": ticket.task_id, "batch_no": batch_no, "metrics": ["qs"],
            "run_id": ticket.run_id, "lease_token": ticket.lease_token,
        }).get(propagate=True)
        assert result["failures"] == 0 and heartbeat_seen.is_set()
        with Session(engine) as reader:
            assert [row.qs for row in reader.query(Device).all()] == [10, 10]
