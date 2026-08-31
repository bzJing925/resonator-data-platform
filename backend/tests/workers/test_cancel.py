from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch


def test_local_queue_request_cancel_removes_pending():
    from app.workers.local_queue import LocalTask, LocalTaskQueue

    q = LocalTaskQueue()
    q.put(LocalTask(task_id=1, batch_no="B1", mapping_id=1))
    q.put(LocalTask(task_id=2, batch_no="B2", mapping_id=1))

    assert q.request_cancel(1) is True
    assert q.list_pending() == [LocalTask(task_id=2, batch_no="B2", mapping_id=1)]
    assert q.is_cancelled(1) is True


@patch("app.workers.cancel.get_settings")
def test_is_task_cancelled_queries_db(mock_get_settings, db):
    mock_get_settings.return_value.is_desktop = False

    from app.models import UploadTask
    from app.workers.cancel import is_task_cancelled

    t = UploadTask(batch_no="T.04", status="running", cancelled_at=datetime.now(UTC))
    db.add(t)
    db.commit()
    db.refresh(t)

    assert is_task_cancelled(t.id) is True
    mock_get_settings.assert_called_once()
