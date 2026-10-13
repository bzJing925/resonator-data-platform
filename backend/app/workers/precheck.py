"""Independent precheck job; the processing executor never creates active Devices here."""

from app.workers.celery_app import celery_app
from app.workers.run_execution import leased_stage


@celery_app.task(name="aln.precheck_upload")
def precheck_upload_task(draft_id: str):
    from app.db import engine
    from app.services.upload_draft_service import run_precheck

    run_precheck(engine, draft_id)


@celery_app.task(bind=True, name="aln.import_prechecked_table")
@leased_stage("compute")
def import_prechecked_table_task(self, task_id: int, run_id: str, lease_token: str):
    import json
    from pathlib import Path
    from uuid import uuid4

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Device, ProcessingRun, SourceInput
    from app.services.device_ingest import bulk_insert_devices
    from app.services.processing_report import record_device_result, summarize_run
    from app.services.processing_run import assert_run_writable
    from app.workers.run_execution import current_run

    with SessionLocal() as db:
        run = db.get(ProcessingRun, run_id)
        rows = json.loads((Path(run.config_json["prepared_dir"]) / "table-rows.json").read_text())
        ticket = current_run.get()
        batch_id = run.batch_id
        chunk = []
        selected = (
            {item["member"] for item in run.config_json["retry_items"]}
            if run.retry_of_run_id else None
        )
        for index, row in enumerate(rows, 1):
            if selected is not None and f"row/{index}" not in selected:
                continue
            row["batch_id"] = batch_id
            row["s_param_port"] = "S22" if row.get("s_param_port") == "S22" else "S11"
            row["source_device_key"] = db.scalar(
                select(SourceInput.source_device_key).where(
                    SourceInput.batch_id == batch_id,
                    SourceInput.member_key == f"row/{index}",
                    SourceInput.port_key == row["s_param_port"],
                )
            ) or str(uuid4())
            row["pf"] = "Y" if str(row.get("pf") or "").upper() in {"Y", "1", "TRUE"} else "N"
            row["deembedded"] = False
            for name in list(row):
                if name not in Device.__table__.columns:
                    row.pop(name)
            record_device_result(
                db,
                run_id,
                f"row/{index}",
                row["s_param_port"],
                result=row,
                source_device_key=row["source_device_key"],
                unit_kind="table_row",
            )
            chunk.append(row)
            if len(chunk) >= 1000:
                bulk_insert_devices(db, chunk)
                db.commit()
                chunk.clear()
        assert_run_writable(db, run_id, ticket.lease_token)
        bulk_insert_devices(db, chunk)
        summarize_run(db, run_id)
        db.commit()
        from app.services.result_publish import publish_run

        publish_run(db.bind, run_id, ticket.lease_token)
        return {"batch_id": batch_id, "device_count": len(rows)}
