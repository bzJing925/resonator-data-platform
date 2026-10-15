"""Read retry provenance without changing original evidence or current revisions."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import ProcessingRun, UploadTask
from app.services.processing_report import record_item_result
from app.services.processing_run import create_run
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize("kind", DATABASES)
def test_retry_options_page_identity_parent_and_unknown(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "upload")
        run = db.get(ProcessingRun, ticket.run_id)
        run.status = "failed"
        run.base_source_revision = 7
        for index in range(3):
            record_item_result(db, run.id, "device", f"dut/{index}.s2p", "failed",
                               port="S11", source_device_key=str(uuid4()))
        record_item_result(db, run.id, "device", "unknown.s1p", "failed", port="S11")
        record_item_result(db, run.id, "file", "file.zip", "failed")
        record_item_result(db, run.id, "device", "good.s1p", "success", port="S11",
                           source_device_key=str(uuid4()))
        parent_task = UploadTask(batch_no="parent", status="failed")
        legacy = UploadTask(batch_no="legacy", status="failed")
        db.add_all([parent_task, legacy])
        db.flush()
        parent = ProcessingRun(task_id=parent_task.id, kind="upload", status="failed")
        db.add(parent)
        db.flush()
        run.retry_of_run_id = parent.id
        db.commit()
        original_summary = dict(run.summary_json)
        legacy_id, parent_id = legacy.id, parent_task.id

        def sessions():
            with Session(engine) as reader:
                yield reader

        old = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = sessions
        try:
            client = TestClient(app)
            url = f"/api/tasks/{ticket.task_id}/retry-options"
            first = client.get(url + "?limit=2").json()
            last = client.get(url + "?limit=2&offset=2").json()
            assert first["expected_source_revision"] == 7
            assert first["retry_of_task_id"] == parent_id
            assert first["total"] == 3 and last["total"] == 3
            assert len(first["items"]) == 2 and first["next_offset"] == 2
            assert len(last["items"]) == 1 and last["next_offset"] is None
            assert len({i["id"] for i in first["items"] + last["items"]}) == 3
            assert all(i["unit_kind"] == "device" for i in first["items"])
            unknown = client.get(f"/api/tasks/{legacy_id}/retry-options").json()
            assert unknown["expected_source_revision"] is None and unknown["items"] == []
            assert client.get("/api/tasks/99999/retry-options").status_code == 404
            db.expire_all()
            assert db.get(ProcessingRun, run.id).summary_json == original_summary
            run.summary_json = {"published_source_revision": 9}
            db.commit()
            assert client.get(url).json()["expected_source_revision"] == 9
            run.status = "cancel_requested"
            db.commit()
            assert client.get(url).json()["items"] == []
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old)


def test_upstream_task_details_and_retry_parent_never_alias_local_task_ids():
    from app.services.remote_access import _translate

    detail = _translate({"id": 12, "run_id": "run", "status": "running"}, {}, "/api/tasks/-12")
    assert detail["id"] == -12
    options = _translate({"task_id": 12, "retry_of_task_id": 8,
                          "items": [{"id": "item-uuid", "status": "failed"}]},
                         {}, "/api/tasks/-12/retry-options")
    assert options["task_id"] == -12 and options["retry_of_task_id"] == -8
    assert options["items"][0]["id"] == "item-uuid"
