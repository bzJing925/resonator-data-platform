"""Independent history and leases on file SQLite and real PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import ProcessingRun, UploadTask
from app.services.processing_run import (
    RunConflictError,
    assert_run_writable,
    claim_run,
    create_run,
    finish_run,
    handoff_run,
    heartbeat_run,
)
from app.services.task_state import transition_task
from tests.fixtures.platform_db import DATABASES, platform_database


def test_run_conflict_survives_pickle_and_celery_exception_transport():
    import pickle

    from celery.utils.serialization import get_pickleable_exception, get_pickled_exception

    original = RunConflictError("RUN_LEASE_LOST", "处理租约已失效")
    transported = get_pickled_exception(
        pickle.loads(pickle.dumps(get_pickleable_exception(original)))
    )
    assert type(transported) is RunConflictError
    assert transported.code == "RUN_LEASE_LOST"
    assert str(transported) == "处理租约已失效"


@pytest.mark.parametrize("kind", DATABASES)
def test_two_connections_competing_for_admission_and_claim(kind, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()

        def compete(operation):
            barrier = Barrier(2)

            def contender():
                barrier.wait(timeout=5)
                try:
                    return operation()
                except RunConflictError as exc:
                    return exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = pool.submit(contender), pool.submit(contender)
                results = [first.result(timeout=10), second.result(timeout=10)]
            assert sum(isinstance(result, RunConflictError) for result in results) == 1
            return next(result for result in results if not isinstance(result, RunConflictError))

        ticket = compete(lambda: create_run(engine, batch_id, "recompute"))
        claimed = compete(lambda: claim_run(engine, ticket.run_id, ticket.lease_token, "compute"))
        assert heartbeat_run(engine, claimed.run_id, claimed.lease_token)
        assert len(db.scalars(select(ProcessingRun)).all()) == 1


@pytest.mark.parametrize("kind", DATABASES)
def test_late_progress_and_terminal_do_not_override_new_stage_or_cancel(
    kind, tmp_path, monkeypatch
):
    from sqlalchemy.orm import sessionmaker

    from app.workers.progress import ProgressPublisher

    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        monkeypatch.setattr("app.db.SessionLocal", sessionmaker(engine))
        ticket = create_run(engine, batch_id, "upload")
        extract = claim_run(engine, ticket.run_id, ticket.lease_token, "extract")
        handoff_run(engine, extract.run_id, extract.lease_token)
        compute = claim_run(engine, extract.run_id, extract.lease_token, "compute")
        old_publisher = ProgressPublisher(
            ticket.task_id, run_id=ticket.run_id, lease_token=extract.lease_token
        )
        old_publisher.update(db, 99, "late progress")
        old_publisher.done(db, batch_id, 99)
        db.rollback()
        assert db.get(UploadTask, ticket.task_id).status == "running"
        assert db.get(UploadTask, ticket.task_id).progress_pct == 0
        assert (
            transition_task(
                db,
                ticket.task_id,
                ticket.run_id,
                compute.lease_token,
                "running",
                "cancel_requested",
            )
            == "changed"
        )
        db.commit()
        publisher = ProgressPublisher(
            ticket.task_id, run_id=ticket.run_id, lease_token=compute.lease_token
        )
        publisher.update(db, 100, "late after stop")
        publisher.done(db, batch_id, 99)
        db.expire_all()
        assert db.get(UploadTask, ticket.task_id).status == "cancel_requested"
        assert db.get(ProcessingRun, ticket.run_id).status == "cancel_requested"


@pytest.mark.parametrize("kind", DATABASES)
def test_attempt_history_and_duplicate_admission(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "recompute")
        with pytest.raises(RunConflictError, match="活动"):
            create_run(engine, batch_id, "recompute")
        claimed = claim_run(engine, ticket.run_id, ticket.lease_token, "compute")
        with pytest.raises(RunConflictError):
            claim_run(engine, ticket.run_id, ticket.lease_token, "compute")
        finish_run(engine, claimed.run_id, claimed.lease_token, "success")
        new = create_run(engine, batch_id, "recompute")
        assert new.task_id != ticket.task_id and new.run_id != ticket.run_id
        db.expire_all()
        assert db.get(UploadTask, ticket.task_id).status == "success"
        assert db.get(UploadTask, new.task_id).status == "pending"
        assert batch.task_id == new.task_id and batch.active_run_id == new.run_id


@pytest.mark.parametrize("kind", DATABASES)
def test_handoff_invalidates_extract_and_duplicate_compute_lease(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "upload")
        extract = claim_run(engine, ticket.run_id, ticket.lease_token, "extract")
        assert heartbeat_run(engine, extract.run_id, extract.lease_token)
        handoff_run(engine, extract.run_id, extract.lease_token)
        compute = claim_run(engine, extract.run_id, extract.lease_token, "compute")
        assert compute.lease_token != extract.lease_token
        assert not heartbeat_run(engine, extract.run_id, extract.lease_token)
        with pytest.raises(RunConflictError):
            assert_run_writable(db, extract.run_id, extract.lease_token)
        db.rollback()
        assert_run_writable(db, compute.run_id, compute.lease_token)
        db.rollback()
        with pytest.raises(RunConflictError):
            claim_run(engine, compute.run_id, compute.lease_token, "compute")


@pytest.mark.parametrize("kind", DATABASES)
def test_confirmed_queued_dispatch_failure_is_atomic_and_preserves_input(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        path = tmp_path / "input.zip"
        path.write_bytes(b"retained raw input")
        batch.raw_zip_path = str(path)
        db.commit()
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "upload")
        assert (
            transition_task(
                db, ticket.task_id, ticket.run_id, ticket.lease_token, "pending", "failed"
            )
            == "conflict"
        )
        db.rollback()
        with pytest.raises(RunConflictError):
            finish_run(
                engine,
                ticket.run_id,
                str(uuid4()),
                "failed",
                error_code="DISPATCH_FAILED",
                confirmed_dispatch_failure=True,
            )
        finish_run(
            engine,
            ticket.run_id,
            ticket.lease_token,
            "failed",
            error_code="DISPATCH_FAILED",
            confirmed_dispatch_failure=True,
        )
        db.expire_all()
        run = db.get(ProcessingRun, ticket.run_id)
        assert run.status == db.get(UploadTask, ticket.task_id).status == "failed"
        assert run.summary_json["error_code"] == "DISPATCH_FAILED"
        assert batch.active_run_id is None
        assert path.read_bytes() == b"retained raw input"
        assert batch.raw_zip_path == str(path)


@pytest.mark.parametrize("kind", DATABASES)
def test_dispatch_failure_cannot_overwrite_claimed_run(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "recompute")
        claimed = claim_run(engine, ticket.run_id, ticket.lease_token, "compute")
        with pytest.raises(RunConflictError):
            finish_run(
                engine,
                claimed.run_id,
                claimed.lease_token,
                "failed",
                error_code="DISPATCH_FAILED",
                confirmed_dispatch_failure=True,
            )
        db.expire_all()
        assert db.get(ProcessingRun, ticket.run_id).status == "running"
        assert (
            db.scalar(select(UploadTask.status).where(UploadTask.id == ticket.task_id)) == "running"
        )


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize("confirmed", [False, True])
def test_upload_dispatch_failure_respects_confirmation(kind, confirmed, tmp_path, monkeypatch):
    from app.services.upload_service import TaskDispatchError, create_batch_and_dispatch
    from app.workers.dispatch import ConfirmedDispatchError

    path = tmp_path / "raw.zip"
    path.write_bytes(b"original raw input")
    with platform_database(kind, tmp_path) as (db, batch):

        def reject(**kwargs):
            if confirmed:
                raise ConfirmedDispatchError("not enqueued")
            raise TimeoutError("unknown")

        # This test isolates the transport confirmation contract; real precheck
        # input acceptance is covered separately by test_upload_drafts.
        monkeypatch.setattr(
            "app.services.upload_service.build_precheck_report",
            lambda *args, **kwargs: {"fatal_count": 0, "items": [], "issues": []},
        )
        monkeypatch.setattr("app.services.upload_service.dispatch_batch_task", reject)
        with pytest.raises(TaskDispatchError):
            create_batch_and_dispatch(db, path, "new-upload", batch.mapping_id)
        db.expire_all()
        run = db.scalar(
            select(ProcessingRun).join(UploadTask).where(UploadTask.batch_no == "new-upload")
        )
        task = db.get(UploadTask, run.task_id)
        assert run.status == task.status == ("failed" if confirmed else "pending")
        assert run.summary_json["error_code"] == (
            "DISPATCH_FAILED" if confirmed else "DISPATCH_OUTCOME_UNKNOWN"
        )
        assert path.read_bytes() == b"original raw input"


@pytest.mark.parametrize("kind", DATABASES)
def test_reprocess_endpoint_keeps_old_task_and_returns_new_run(kind, tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.api.batches import _start_reprocess

    with platform_database(kind, tmp_path) as (db, batch):
        old = UploadTask(batch_no=batch.batch_no, status="success")
        db.add(old)
        db.flush()
        batch.task_id = old.id
        db.commit()
        seen = []
        monkeypatch.setattr(
            "app.api.batches.dispatch_reprocess_task",
            lambda **kwargs: seen.append(kwargs) or "accepted-id",
        )
        result = _start_reprocess(db, batch.batch_no, "recompute", ["qs"])
        assert result.task_id != str(old.id)
        assert seen[0]["run_id"] == result.run_id and seen[0]["lease_token"]
        assert db.get(UploadTask, old.id).status == "success"
        with pytest.raises(HTTPException) as error:
            _start_reprocess(db, batch.batch_no, "recompute", ["qs"])
        assert error.value.status_code == 409
