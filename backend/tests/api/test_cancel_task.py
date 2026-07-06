from __future__ import annotations

from sqlalchemy import select


def test_cancel_pending_task_cleans_up(db, client):
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
    assert data["status"] == "cancelled"
    assert data["progress_msg"] == "已取消并清理文件"
    assert data["cancelled_at"] is not None
    assert data["finished_at"] is not None
    assert data["raw_zip_deleted"] is True
    assert db.scalar(select(Batch).where(Batch.batch_no == "B.02")) is None


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
