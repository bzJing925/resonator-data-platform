"""HTTP reprocess contract using initialized, isolated SQLite and PostgreSQL."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import ProcessingRun, UploadTask
from app.workers.dispatch import ConfirmedDispatchError
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def reprocess_client(request, tmp_path):
    with platform_database(request.param, tmp_path) as (db, batch):
        old = UploadTask(batch_no=batch.batch_no, status="success")
        db.add(old)
        db.flush()
        batch.task_id = old.id
        db.commit()
        engine = db.bind

        def override_db():
            with Session(engine) as session:
                yield session

        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)  # HTTP test only; does not start background workers.
        try:
            yield client, db, batch, old
        finally:
            client.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)


def test_recompute_rejects_missing_batch(reprocess_client):
    client, _db, _batch, _old = reprocess_client
    response = client.post("/api/batches/NOT_EXISTS/recompute", json={"metrics": ["qs"]})
    assert response.status_code == 404


def test_recompute_retains_history_and_rejects_second_writer(reprocess_client, monkeypatch):
    client, db, batch, old = reprocess_client
    seen = []
    monkeypatch.setattr(
        "app.api.batches.dispatch_reprocess_task",
        lambda **kwargs: seen.append(kwargs) or "accepted",
    )
    response = client.post(f"/api/batches/{batch.batch_no}/recompute", json={"metrics": ["qs"]})
    assert response.status_code == 200
    accepted = response.json()
    assert accepted["task_id"] != str(old.id)
    assert accepted["run_id"] == seen[0]["run_id"] and seen[0]["lease_token"]
    db.expire_all()
    assert db.get(UploadTask, old.id).status == "success"
    response = client.post(f"/api/batches/{batch.batch_no}/recompute", json={"metrics": ["qs"]})
    assert response.status_code == 409


def test_confirmed_dispatch_failure_preserves_old_task(reprocess_client, monkeypatch):
    client, db, batch, old = reprocess_client
    seen = []

    def rejected(**kwargs):
        seen.append(kwargs)
        raise ConfirmedDispatchError("not accepted by queue")

    monkeypatch.setattr("app.api.batches.dispatch_reprocess_task", rejected)
    response = client.post(f"/api/batches/{batch.batch_no}/recompute", json={"metrics": ["qs"]})
    assert response.status_code == 503
    db.expire_all()
    run = db.get(ProcessingRun, seen[0]["run_id"])
    assert run.status == db.get(UploadTask, run.task_id).status == "failed"
    assert batch.active_run_id is None and db.get(UploadTask, old.id).status == "success"
