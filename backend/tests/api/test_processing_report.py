"""HTTP pagination and streamed export include every persisted issue."""

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import UploadTask
from app.services.processing_report import record_issue, summarize_run
from app.services.processing_run import create_run
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize("kind", DATABASES)
def test_all_issues_export_and_legacy_unknown(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "upload")
        for index in range(1107):
            record_issue(
                db,
                ticket.run_id,
                "BAD_TOUCHSTONE",
                "输入不能读取",
                details={"source_member": f"bad/{index}.s1p"},
            )
        summarize_run(db, ticket.run_id)
        legacy = UploadTask(batch_no="legacy", status="failed")
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id

        def sessions():
            with Session(engine) as reader:
                yield reader

        old = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = sessions
        try:
            client = TestClient(app)
            report = client.get(f"/api/tasks/{ticket.task_id}/report?limit=500").json()
            assert len(report["issues"]) == 500 and report["next_offset"] == 500
            last = client.get(f"/api/tasks/{ticket.task_id}/report?offset=1000&limit=500").json()
            assert len(last["issues"]) == 107 and last["next_offset"] is None
            response = client.get(f"/api/tasks/{ticket.task_id}/issues.csv")
            rows = list(csv.DictReader(io.StringIO(response.text)))
            assert response.status_code == 200 and len(rows) == 1107
            assert len({row["id"] for row in rows}) == 1107
            detail = client.get(f"/api/tasks/{ticket.task_id}").json()
            assert detail["run_id"] == ticket.run_id and detail["summary"]["published_count"] == 0
            assert (
                client.get(f"/api/tasks/{legacy_id}/report").json()["quality_status"] == "unknown"
            )
            assert client.get("/api/tasks/99999/report").status_code == 404
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old)
