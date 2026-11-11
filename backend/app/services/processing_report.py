"""Durable, idempotent processing evidence; callers own the transaction."""

import hashlib
import json
import math
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import or_, select

from app.models import ProcessingItem, ProcessingRun, QualityIssue, SourceInput

STATUSES = ("pending", "running", "success", "failed", "skipped", "cancelled")


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def record_item_result(
    db,
    run_id,
    unit_kind,
    source_member,
    status,
    *,
    port=None,
    source_device_key=None,
    error_code=None,
    result=None,
    attempt=None,
):
    if status not in STATUSES or unit_kind not in {"file", "device", "table_row"}:
        raise ValueError("invalid processing item")
    from app.workers.run_execution import current_run

    ticket = current_run.get()
    if ticket is not None and ticket.run_id == run_id:
        from app.services.processing_run import fence_run_writes

        fence_run_writes(db, run_id, ticket.lease_token)
    item = db.scalar(
        select(ProcessingItem).where(
            ProcessingItem.run_id == run_id,
            ProcessingItem.unit_kind == unit_kind,
            ProcessingItem.source_member == source_member,
            ProcessingItem.port_key == (port or ""),
        )
    )
    if item is None:
        item = ProcessingItem(
            run_id=run_id,
            unit_kind=unit_kind,
            source_member=source_member,
            port=port,
            port_key=port or "",
        )
        db.add(item)
    item.status, item.error_code = status, error_code
    item.attempt = attempt if attempt is not None else db.get(ProcessingRun, run_id).attempt
    item.source_device_key = source_device_key
    if result is not None:
        item.result_digest = hashlib.sha256(
            json.dumps(
                _finite(result),
                sort_keys=True,
                default=str,
                allow_nan=False,
            ).encode()
        ).hexdigest()
    db.flush()
    return item


def record_issue(db, run_id, code, message, *, severity="error", item_id=None, details=None):
    if severity not in {"error", "warning", "info"}:
        raise ValueError("invalid issue severity")
    details = details or {}
    # Same evidence on fallback/replay is one issue; distinct files retain distinct issues.
    identity = json.dumps([run_id, item_id, severity, code, message, details], sort_keys=True)
    issue_id = str(uuid5(NAMESPACE_URL, identity))
    issue = db.get(QualityIssue, issue_id)
    if issue is None:
        issue = QualityIssue(
            id=issue_id,
            run_id=run_id,
            item_id=item_id,
            severity=severity,
            code=code,
            message=message,
            details_json=details,
        )
        db.add(issue)
        db.flush()
    return issue


def seed_precheck_report(db, run):
    report = run.config_json.get("precheck_report") or {}
    table = "table_stats" in report
    items, issues = {}, {}

    def add_item(unit_kind, source_member, status, *, port=None, error_code=None):
        key = (unit_kind, source_member, port or "")
        item_id = items.get(key, {}).get("id") or str(uuid4())
        items[key] = {
            "id": item_id,
            "run_id": run.id,
            "source_device_key": None,
            "source_member": source_member,
            "port": port,
            "port_key": port or "",
            "unit_kind": unit_kind,
            "status": status,
            "error_code": error_code,
            "result_digest": None,
            "attempt": run.attempt,
        }
        return item_id

    def add_issue(code, message, *, severity="error", item_id=None, details=None):
        details = details or {}
        identity = json.dumps([run.id, item_id, severity, code, message, details], sort_keys=True)
        issue_id = str(uuid5(NAMESPACE_URL, identity))
        issues[issue_id] = {
            "id": issue_id,
            "run_id": run.id,
            "item_id": item_id,
            "severity": severity,
            "code": code,
            "message": message,
            "details_json": details,
        }

    if table:
        add_item("file", report.get("input_name", "table"), "pending")
    for entry in report.get("items", []):
        member = entry.get("relpath") or entry.get("source_member")
        if not member:
            continue
        status = entry.get("status", "skipped")
        if status == "success" and entry.get("classification") == "dut":
            status = "pending"
        if not table:
            add_item("file", member, status)
        if entry.get("classification") == "dut":
            for port in entry.get("ports", []):
                add_item("table_row" if table else "device", member, status, port=port)
    for index, message in enumerate(report.get("row_failures", []), 1):
        item_id = add_item(
            "table_row", f"rejected/{index}", "failed", error_code="TABLE_ROW_REJECTED"
        )
        add_issue("TABLE_ROW_REJECTED", message, severity="warning", item_id=item_id)
    for entry in report.get("issues", []):
        if entry["code"] == "TABLE_ROW_REJECTED" and report.get("row_failures"):
            continue
        add_issue(
            entry["code"],
            entry["message"],
            severity="error" if entry["severity"] == "fatal" else entry["severity"],
            details={"source_member": entry.get("relpath")},
        )
    for model, rows in ((ProcessingItem, items), (QualityIssue, issues)):
        values = list(rows.values())
        for start in range(0, len(values), 1000):
            db.execute(model.__table__.insert(), values[start:start + 1000])
    db.flush()


def record_device_result(
    db,
    run_id,
    member,
    port,
    *,
    result=None,
    error_code=None,
    source_device_key=None,
    skipped=False,
    unit_kind="device",
):
    run = db.get(ProcessingRun, run_id)
    predicate = (
        SourceInput.member_key == member
        if unit_kind == "table_row"
        else or_(SourceInput.current_relpath == member, SourceInput.member_key == member)
    )
    source = db.scalar(
        select(SourceInput).where(
            SourceInput.batch_id == run.batch_id,
            predicate,
            SourceInput.port_key == port,
        )
    )
    if source is not None:
        member, source_device_key = source.member_key, source.source_device_key
    item = record_item_result(
        db,
        run_id,
        unit_kind,
        member,
        "skipped" if skipped else "failed" if error_code else "success",
        port=port,
        result=result,
        error_code=error_code,
        source_device_key=source_device_key,
        attempt=run.attempt,
    )
    if error_code:
        record_issue(
            db,
            run_id,
            error_code,
            "器件处理失败，输入保留供检查",
            item_id=item.id,
            details={"source_member": member, "port": port},
        )
    return item


def summarize_run(db, run_id):
    run = db.get(ProcessingRun, run_id)
    items = db.scalars(select(ProcessingItem).where(ProcessingItem.run_id == run_id)).all()
    devices = [item for item in items if item.unit_kind in {"device", "table_row"}]
    files = [item for item in items if item.unit_kind == "file"]
    by_member = {}
    table_states = set()
    table = "table_stats" in (run.config_json.get("precheck_report") or {})
    for item in devices:
        by_member.setdefault(item.source_member, set()).add(item.status)
        if table and item.unit_kind == "table_row":
            table_states.add(item.status)
    for file in files:
        states = by_member.get(file.source_member, set()) | table_states
        if states:
            file.status = next(
                (
                    s
                    for s in ("running", "pending", "failed", "cancelled", "success")
                    if s in states
                ),
                "skipped",
            )

    def counts(rows):
        return {"total": len(rows), **{s: sum(i.status == s for i in rows) for s in STATUSES}}

    device_counts = counts(devices)
    file_counts = counts(files)
    severities = db.scalars(
        select(QualityIssue.severity).where(QualityIssue.run_id == run_id)
    ).all()
    issue_counts = {s: severities.count(s) for s in ("error", "warning", "info")}
    if not devices or device_counts["pending"] or device_counts["running"]:
        quality = "unknown"
    elif not device_counts["success"]:
        quality = "invalid"
    elif (
        issue_counts["warning"]
        or issue_counts["error"]
        or device_counts["success"] < device_counts["total"]
        or any(file_counts[s] for s in ("failed", "skipped", "cancelled"))
    ):
        quality = "partial"
    else:
        quality = "complete"
    run.quality_status = quality
    run.summary_json = {
        **run.summary_json,
        "file_counts": file_counts,
        "device_counts": device_counts,
        "issue_counts": issue_counts,
        "published_count": run.published_count,
    }
    db.flush()
    return run.summary_json
