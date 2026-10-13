"""Cancellation intent, executor acknowledgment, and preservation on both databases."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_db
from app.main import app
from app.models import ProcessingRun, UploadTask
from app.schemas.processing import ProcessingEvent
from app.services.processing_cancel import acknowledge_cancelled_delivery, request_cancellation
from app.services.processing_run import claim_run, create_run
from app.workers.local_queue import LocalTask, LocalTaskQueue
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize("running", [False, True])
def test_cancel_waits_for_executor_and_retains_every_file(kind, running, tmp_path, monkeypatch):
    with platform_database(kind, tmp_path) as (db, batch):
        active = tmp_path / "active"
        active.mkdir()
        raw = tmp_path / "input.zip"
        raw.write_bytes(b"original bytes")
        curve = active / "curve.s1p"
        curve.write_text("old curve")
        batch.file_path, batch.raw_zip_path = str(active), str(raw)
        db.commit()
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "recompute", config={"metrics": ["qs"]})
        if running:
            ticket = claim_run(
                engine, ticket.run_id, ticket.lease_token, "compute", executor_id="compute-id"
            )
        with Session(engine) as writer:
            run = writer.get(ProcessingRun, ticket.run_id)
            run.executor_ids = {"extract": "extract-id", "compute": "compute-id"}
            writer.commit()
        delivered = []
        monkeypatch.setattr("app.api.tasks.get_settings", lambda: SimpleNamespace(is_desktop=False))
        monkeypatch.setattr(
            "app.api.tasks.celery_app.control",
            SimpleNamespace(
                revoke=lambda executor_id, **kwargs: delivered.append((executor_id, kwargs))
            ),
        )
        factory = sessionmaker(engine)
        monkeypatch.setattr("app.db.SessionLocal", factory)
        monkeypatch.setattr("app.db.engine", engine)

        def sessions():
            with factory() as reader:
                yield reader

        old = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = sessions
        try:
            client = TestClient(app)
            response = client.post(f"/api/tasks/{ticket.task_id}/cancel")
            assert response.status_code == 200 and response.json()["status"] == "cancel_requested"
            assert response.json()["finished_at"] is None
            assert response.json()["raw_zip_deleted"] is False
            assert set(executor_id for executor_id, _ in delivered) == {"extract-id", "compute-id"}
            assert all(options == {"terminate": False} for _, options in delivered)
            assert raw.read_bytes() == b"original bytes" and curve.read_text() == "old curve"
            from app.api.tasks import _snapshot_event

            event, terminal = _snapshot_event(ticket.task_id)
            payload = json.loads(event["data"])
            ProcessingEvent.model_validate(payload)
            assert not terminal and event["event"] == "progress"
            assert payload["status"] == "cancel_requested" and payload["run_id"] == ticket.run_id
            if running:
                assert not acknowledge_cancelled_delivery(engine, ticket.run_id, ticket.lease_token)
            else:
                assert acknowledge_cancelled_delivery(engine, ticket.run_id, ticket.lease_token)
                assert client.get(f"/api/tasks/{ticket.task_id}").json()["status"] == "cancelled"
                assert raw.exists() and active.exists()
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old)


@pytest.mark.parametrize("kind", DATABASES)
def test_native_queued_removal_confirms_without_deleting_input(kind, tmp_path, monkeypatch):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "upload")
        queue = LocalTaskQueue()
        queue.put(
            LocalTask(task_id=ticket.task_id, run_id=ticket.run_id, lease_token=ticket.lease_token)
        )
        request_cancellation(engine, ticket.task_id)
        assert queue.request_cancel(ticket.task_id)
        assert acknowledge_cancelled_delivery(engine, ticket.run_id, ticket.lease_token)
        with Session(engine) as reader:
            assert reader.get(ProcessingRun, ticket.run_id).status == "cancelled"
            assert reader.get(UploadTask, ticket.task_id).status == "cancelled"
