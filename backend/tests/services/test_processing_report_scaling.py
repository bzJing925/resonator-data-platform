"""Large report summaries must not scan all devices for each file."""
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.models import ProcessingItem, ProcessingRun, QualityIssue, SourceInput
from app.services.processing_report import summarize_run
from app.services.processing_run import _locked_session, create_run_in_session
from app.services.source_identity import SourceIdentityError, register_prechecked_sources
from tests.fixtures.platform_db import platform_database


def test_large_summary_groups_children_once():
    class Item:
        reads = 0

        def __init__(self, kind, member, status):
            self.unit_kind, self.member, self.status = kind, member, status

        @property
        def source_member(self):
            Item.reads += 1
            return self.member

    count = 22050
    files = [Item("file", str(i), "pending") for i in range(count)]
    devices = [Item("device", str(i), status) for i in range(count)
               for status in ("success", "failed" if i == 0 else "success")]
    run = SimpleNamespace(config_json={}, summary_json={}, published_count=count * 2 - 1)
    results = iter([files + devices, []])
    db = SimpleNamespace(get=lambda *_: run,
                         scalars=lambda *_: SimpleNamespace(all=lambda: next(results)),
                         flush=lambda: None)
    summary = summarize_run(db, "run")
    assert summary["file_counts"]["failed"] == 1
    assert summary["device_counts"]["success"] == count * 2 - 1
    assert run.quality_status == "partial"
    assert Item.reads <= count * 4


def test_large_precheck_admission_does_not_block_other_sqlite_writers(tmp_path):
    count = 3000
    items = [
        {
            "relpath": f"DUT/{index}.s2p",
            "original_filename": f"{index}.s2p",
            "classification": "dut",
            "status": "success",
            "ports": ["S11", "S22"],
            "input_digest": "d" * 64,
        }
        for index in range(count)
    ]
    report = {
        "input_name": "bulk.zip",
        "input_digest": "d" * 64,
        "items": items,
        "issues": [
            {
                "code": "MAPPING_NOT_FOUND",
                "severity": "info",
                "message": "器件未命中对照表：入库但不参与分析",
                "relpath": item["relpath"],
            }
            for item in items
        ],
    }
    with platform_database("sqlite", tmp_path) as (db, batch):
        engine = db.bind
        db.rollback()

        def admit():
            with _locked_session(engine) as session:
                register_prechecked_sources(session, batch, report)
                return create_run_in_session(
                    session, batch.id, "upload", config={"precheck_report": report}
                )

        from app.services import upload_precheck

        upload_precheck.validate_upload_options({})
        with ThreadPoolExecutor(max_workers=1) as pool:
            ticket_future = pool.submit(admit)
            time.sleep(0.05)
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA busy_timeout=1000")
                connection.execute(text("BEGIN IMMEDIATE"))
                connection.rollback()
            ticket = ticket_future.result(timeout=30)

        register_prechecked_sources(db, batch, report)
        assert db.scalar(select(func.count(SourceInput.id))) == count * 2
        unique_sources = select(func.count(func.distinct(SourceInput.source_device_key)))
        assert db.scalar(unique_sources) == count * 2
        assert db.scalar(select(func.count(ProcessingItem.id))) == count * 3
        assert db.scalar(select(func.count(QualityIssue.id))) == count
        issue = db.scalar(select(QualityIssue).limit(1))
        assert issue.severity == "info" and issue.details_json["source_member"].startswith("DUT/")
        run = db.get(ProcessingRun, ticket.run_id)
        assert run.summary_json["file_counts"]["pending"] == count
        assert run.summary_json["device_counts"]["pending"] == count * 2
        assert run.summary_json["issue_counts"]["info"] == count
        report["items"][0]["input_digest"] = "0" * 64
        with pytest.raises(SourceIdentityError, match="内容"):
            register_prechecked_sources(db, batch, report)
