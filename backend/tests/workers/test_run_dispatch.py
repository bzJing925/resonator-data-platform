"""Transport metadata and shared real-database execution lease entry."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import ProcessingRun, UploadTask
from app.services.processing_run import RunConflictError, create_run
from app.workers import dispatch
from app.workers.local_queue import LocalTaskQueue
from app.workers.run_execution import current_run, leased_stage
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize("native", [False, True])
def test_dispatch_transports_run_id_and_reserved_token(native, monkeypatch):
    queue = LocalTaskQueue()
    seen = []
    monkeypatch.setattr(dispatch, "get_settings", lambda: SimpleNamespace(is_desktop=native))
    monkeypatch.setattr("app.workers.local_queue.get_local_queue", lambda: queue)

    def chain(*signatures):
        seen.extend(signatures)
        return SimpleNamespace(apply_async=lambda: SimpleNamespace(id="delivery-id"))

    monkeypatch.setattr("celery.chain", chain)
    monkeypatch.setattr(
        dispatch,
        "_reserve_executor_ids",
        lambda run_id, token, stages: {stage: str(uuid4()) for stage in stages},
    )
    run, token = str(uuid4()), str(uuid4())
    dispatch.dispatch_batch_task(9, Path("raw.zip"), "lot", 2, run_id=run, lease_token=token)
    if native:
        task = queue.get(timeout=0)
        assert task.task_id == 9 and task.run_id == run and task.lease_token == token
    else:
        assert seen[0].kwargs["run_id"] == run and seen[0].kwargs["lease_token"] == token
        assert seen[1].task == "aln.compute_batch"


@pytest.mark.parametrize("kind", DATABASES)
def test_shared_stage_entry_claims_once_and_hands_off_token(kind, tmp_path, monkeypatch):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        monkeypatch.setattr("app.db.engine", engine)
        monkeypatch.setattr("app.db.SessionLocal", sessionmaker(engine))
        ticket = create_run(engine, batch_id, "upload")
        tokens = []

        @leased_stage("extract")
        def extract(*, run_id, lease_token):
            tokens.append(current_run.get().lease_token)
            return {"batch_id": batch_id}

        @leased_stage("compute")
        def compute(extract_result):
            tokens.append(current_run.get().lease_token)
            return {"batch_id": extract_result["batch_id"]}

        result = extract(run_id=ticket.run_id, lease_token=ticket.lease_token)
        assert result["run_id"] == ticket.run_id and result["lease_token"] == tokens[0]
        compute(result)
        with pytest.raises(RunConflictError):
            compute(result)
        assert tokens[0] != tokens[1]
        db.expire_all()
        assert db.get(ProcessingRun, ticket.run_id).status == "success"
        assert db.get(UploadTask, ticket.task_id).status == "success"


@pytest.mark.parametrize("kind", DATABASES)
def test_heartbeat_runs_independently_of_blocked_main_computation(kind, tmp_path, monkeypatch):
    from threading import Event

    from app.workers import run_execution

    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        monkeypatch.setattr("app.db.engine", engine)
        heartbeat_seen = Event()
        real_heartbeat = run_execution.heartbeat_run

        def observed(*args):
            result = real_heartbeat(*args)
            if result:
                heartbeat_seen.set()
            return result

        monkeypatch.setattr(run_execution, "heartbeat_run", observed)
        monkeypatch.setattr(
            run_execution, "get_settings", lambda: SimpleNamespace(WORKER_HEARTBEAT_SECONDS=0.02)
        )
        ticket = create_run(engine, batch_id, "recompute")

        @leased_stage("compute")
        def blocked(*, run_id, lease_token):
            assert heartbeat_seen.wait(timeout=5), "No independent database heartbeat"
            return {}

        blocked(run_id=ticket.run_id, lease_token=ticket.lease_token)
        db.expire_all()
        assert db.get(ProcessingRun, ticket.run_id).heartbeat_at is not None


@pytest.mark.parametrize("kind", DATABASES)
def test_actual_recompute_worker_retains_lease_and_rejects_duplicate(kind, tmp_path, monkeypatch):
    from app.models import Device
    from app.workers.reprocess_batch import recompute_batch_task
    from tests.fixtures.platform_cases import _algorithm_samples

    samples = _algorithm_samples(tmp_path)
    filename = "17_E6-1_X0Y0.s1p"
    (tmp_path / filename).write_bytes(samples["dut_s1p"])
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        batch.file_path = str(tmp_path)
        mapping_path = tmp_path / "mapping.csv"
        mapping_path.write_text("mark,description\nE6-1,EG0 FL0 700&5500\n")
        batch.mapping.file_path = str(mapping_path)
        device = Device(
            batch_id=batch_id, original_filename=filename, s_param_path=filename, s_param_port="S11"
        )
        db.add(device)
        db.commit()
        device_id, batch_no = device.id, batch.batch_no
        db.rollback()
        factory = sessionmaker(engine)
        for module in ("app.db", "app.workers.reprocess_batch", "app.workers.cancel"):
            monkeypatch.setattr(f"{module}.SessionLocal", factory)
        monkeypatch.setattr("app.db.engine", engine)
        monkeypatch.setattr("app.workers.reprocess_batch.load_mapping", lambda path: {})
        monkeypatch.setattr(
            "app.workers.progress.get_settings", lambda: SimpleNamespace(is_desktop=True)
        )
        ticket = create_run(engine, batch_id, "recompute", config={"metrics": ["qs"]})
        kwargs = dict(
            upload_task_id=ticket.task_id,
            batch_no=batch_no,
            metrics=["qs"],
            run_id=ticket.run_id,
            lease_token=ticket.lease_token,
        )
        result = recompute_batch_task.apply(kwargs=kwargs).get(propagate=True)
        assert result["device_count"] == 1 and result["failures"] == 0
        db.expire_all()
        assert db.get(Device, device_id).qs is not None
        assert db.get(ProcessingRun, ticket.run_id).status == "success"
        assert db.get(UploadTask, ticket.task_id).status == "success"
        db.rollback()
        with pytest.raises(RunConflictError):
            recompute_batch_task.apply(kwargs=kwargs).get(propagate=True)


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize("claimed", [False, True])
def test_legacy_restart_recovery_cannot_split_leased_task_and_run(
    kind, claimed, tmp_path, monkeypatch
):
    from app.services.processing_run import claim_run
    from app.workers.local_worker import recover_interrupted_tasks

    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "recompute")
        if claimed:
            claim_run(engine, ticket.run_id, ticket.lease_token, "compute")
        monkeypatch.setattr("app.workers.local_worker.SessionLocal", sessionmaker(engine))
        assert recover_interrupted_tasks() == 0
        db.expire_all()
        expected = "running" if claimed else "pending"
        assert db.get(ProcessingRun, ticket.run_id).status == expected
        assert db.get(UploadTask, ticket.task_id).status == expected
        assert batch.active_run_id == ticket.run_id
