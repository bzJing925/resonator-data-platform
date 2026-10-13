from __future__ import annotations

from sqlalchemy import select


def test_cancel_pending_task_retains_batch_until_executor_confirmation(db, client):
    from app.models import Batch, Mapping, UploadTask

    mapping = Mapping(name="M1", file_path="/tmp/mapping.xlsx")
    db.add(mapping)
    db.commit()

    task = UploadTask(batch_no="B.02", status="pending", progress_msg="排队中")
    db.add(task)
    db.flush()
    batch = Batch(
        batch_no="B.02",
        mapping_id=mapping.id,
        file_path="/tmp/fake",
        raw_zip_path="/tmp/fake.zip",
        task_id=task.id,
    )
    db.add(batch)
    db.commit()

    res = client.post(f"/api/tasks/{task.id}/cancel")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "cancel_requested"
    assert data["progress_msg"] == "已请求停止，等待执行器确认"
    assert data["cancelled_at"] is not None
    assert data["finished_at"] is None
    assert data["raw_zip_deleted"] is True
    assert db.scalar(select(Batch).where(Batch.batch_no == "B.02")) is not None


def test_cancel_finished_task_returns_409(db, client):
    from app.models import UploadTask

    task = UploadTask(batch_no="B.03", status="success")
    db.add(task)
    db.commit()

    res = client.post(f"/api/tasks/{task.id}/cancel")
    assert res.status_code == 409


def test_cancel_nonexistent_task_returns_404(client):
    res = client.post("/api/tasks/999999/cancel")
    assert res.status_code == 404


def test_cancel_already_cancelled_is_idempotent(db, client):
    from app.models import UploadTask

    task = UploadTask(batch_no="B.05", status="cancelled", progress_msg="已取消")
    db.add(task)
    db.commit()

    res = client.post(f"/api/tasks/{task.id}/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"


def test_cancel_running_task_revokes_celery(db, client, monkeypatch):
    from unittest.mock import MagicMock

    from app.models import UploadTask

    task = UploadTask(batch_no="B.04", status="running", celery_task_id="celery-123")
    db.add(task)
    db.commit()

    mock_revoke = MagicMock()
    monkeypatch.setattr("app.api.tasks.celery_app.control", MagicMock(revoke=mock_revoke))

    res = client.post(f"/api/tasks/{task.id}/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "cancel_requested"
    mock_revoke.assert_called_once_with("celery-123", terminate=False)


def test_cancel_reprocess_does_not_delete_batch(db, client):
    from app.models import Batch, Mapping, UploadTask

    mapping = Mapping(name="M1", file_path="/tmp/mapping.xlsx")
    db.add(mapping)
    db.commit()

    task = UploadTask(batch_no="B.06", status="running", kind="recompute")
    db.add(task)
    db.flush()
    batch = Batch(
        batch_no="B.06",
        mapping_id=mapping.id,
        file_path="/tmp/fake",
        raw_zip_path="/tmp/fake.zip",
        task_id=task.id,
    )
    db.add(batch)
    db.commit()

    res = client.post(f"/api/tasks/{task.id}/cancel")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "cancel_requested"
    assert data["raw_zip_deleted"] is True  # Fixture path does not exist; no cleanup occurred.
    assert db.scalar(select(Batch).where(Batch.batch_no == "B.06")) is not None
