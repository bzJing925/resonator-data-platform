"""Durable report identities and balanced file/port accounting on both databases."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProcessingItem, ProcessingRun, QualityIssue
from app.services.processing_report import record_device_result, record_issue, summarize_run
from app.services.processing_run import create_run
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize("kind", DATABASES)
def test_report_balance_replay_and_restart(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(
            engine,
            batch_id,
            "upload",
            config={
                "precheck_report": {
                    "items": [
                        {"relpath": "dual.s2p", "classification": "dut", "status": "success"},
                        {"relpath": "bad.s1p", "classification": "dut", "status": "failed"},
                        {"relpath": "notes.txt", "classification": "ignored", "status": "skipped"},
                    ],
                    "issues": [
                        {
                            "code": "BAD_TOUCHSTONE",
                            "severity": "warning",
                            "message": "坏输入排除",
                            "relpath": "bad.s1p",
                        }
                    ],
                }
            },
        )
        for _ in range(2):
            record_device_result(db, ticket.run_id, "dual.s2p", "S11", result={"fs": 1})
            record_device_result(
                db, ticket.run_id, "dual.s2p", "S22", error_code="METRIC_EXTRACTION_FAILED"
            )
            record_issue(db, ticket.run_id, "POOL_FALLBACK", "计算降级", severity="info")
        summary = summarize_run(db, ticket.run_id)
        assert summary["file_counts"] == {
            "total": 3,
            "success": 0,
            "failed": 2,
            "skipped": 1,
            "pending": 0,
            "running": 0,
            "cancelled": 0,
        }
        assert summary["device_counts"]["total"] == 2
        assert summary["device_counts"]["success"] == 1
        assert summary["published_count"] == 0
        assert db.get(ProcessingRun, ticket.run_id).quality_status == "partial"
        db.commit()
        with Session(engine) as restarted:
            run = restarted.get(ProcessingRun, ticket.run_id)
            assert run.summary_json == summary
            assert len(restarted.scalars(select(ProcessingItem)).all()) == 5
            assert len(restarted.scalars(select(QualityIssue)).all()) == 3


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize("failed,quality", [(False, "complete"), (True, "invalid")])
def test_terminal_quality(kind, failed, quality, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(engine, batch_id, "upload")
        record_device_result(
            db,
            ticket.run_id,
            "input.s1p",
            "S11",
            error_code="METRIC_EXTRACTION_FAILED" if failed else None,
            result=None if failed else {"fs": float("nan")},
        )
        summarize_run(db, ticket.run_id)
        assert db.get(ProcessingRun, ticket.run_id).quality_status == quality


@pytest.mark.parametrize("kind", DATABASES)
def test_table_file_and_rows_are_separate(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        engine, batch_id = db.bind, batch.id
        db.rollback()
        ticket = create_run(
            engine,
            batch_id,
            "upload",
            config={
                "precheck_report": {
                    "input_name": "measurements.csv",
                    "table_stats": {},
                    "items": [
                        {
                            "relpath": "row/1",
                            "status": "success",
                            "classification": "dut",
                            "ports": ["S11"],
                        }
                    ],
                    "row_failures": ["row 2 invalid"],
                    "issues": [
                        {
                            "code": "TABLE_ROW_REJECTED",
                            "severity": "warning",
                            "message": "row 2 invalid",
                        }
                    ],
                }
            },
        )
        record_device_result(
            db, ticket.run_id, "row/1", "S11", result={"fs": 1}, unit_kind="table_row"
        )
        summary = summarize_run(db, ticket.run_id)
        assert summary["file_counts"]["total"] == 1
        assert summary["file_counts"]["failed"] == 1
        assert summary["device_counts"]["total"] == 2
        assert summary["device_counts"]["failed"] == 1
        assert summary["issue_counts"]["warning"] == 1
