"""Locked, idempotent retry admission; historical evidence is immutable."""

import hashlib
import json
from pathlib import Path

from sqlalchemy import or_, select, text

from app.models import (
    Mapping,
    ProcessingItem,
    ProcessingRun,
    QualityIssue,
    SourceInput,
    StagedDeviceRow,
    SyncRevision,
)
from app.services.processing_report import record_issue, record_item_result, summarize_run
from app.services.processing_run import (
    RunConflictError,
    RunTicket,
    _locked_batch,
    _locked_session,
    create_run_in_session,
)
from app.services.source_identity import validate_uuid
from app.services.upload_precheck import file_digest


def directory_digest(path):
    """Hash the retained input tree with exact relative paths, including calibration."""
    root = Path(path) if path else None
    if root is None or not root.is_dir():
        return None
    digest = hashlib.sha256()
    for entry in sorted(root.rglob("*")):
        if entry.is_file():
            record = [entry.relative_to(root).as_posix(), file_digest(entry)]
            digest.update(json.dumps(record).encode())
    return digest.hexdigest()


def configuration_digest(config):
    fields = ("f_start_ghz", "f_end_ghz", "deembed", "deembed_method", "process_type", "metrics")
    accepted = {name: config.get(name) for name in fields}
    return hashlib.sha256(
        json.dumps(accepted, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def algorithm_digest():
    root = Path(__file__).parents[1]
    digest = hashlib.sha256()
    paths = sorted((root / "core").rglob("*.py")) + [root / "share_pack.py"]
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _changed(message="来源、配置或输入已改变，请选择整批新处理"):
    raise RunConflictError("RETRY_INPUT_CHANGED", message)


def retry_items_query(run, *, require_identity=False):
    """Shared failed-unit selection; read options additionally hide unknown identities."""
    stmt = select(ProcessingItem).where(
        ProcessingItem.run_id == run.id,
        ProcessingItem.unit_kind.in_(("device", "table_row")),
        ProcessingItem.status.in_(("failed", "cancelled")),
    )
    if require_identity:
        source = select(SourceInput.id).where(
            SourceInput.batch_id == run.batch_id,
            SourceInput.member_key == ProcessingItem.source_member,
            SourceInput.port_key == ProcessingItem.port_key,
        ).exists()
        stmt = stmt.where(or_(ProcessingItem.source_device_key.is_not(None), source))
    return stmt


def create_retry_run(engine, task_id, *, request_key, expected_source_revision, item_ids=None):
    validate_uuid(request_key)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "task_id": task_id,
                "revision": expected_source_revision,
                "items": sorted(set(item_ids)) if item_ids is not None else None,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with _locked_session(engine) as db:
        if engine.dialect.name == "postgresql":
            key = int.from_bytes(
                hashlib.sha256(request_key.encode()).digest()[:8], "big", signed=True
            )
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        # Both databases support JSON extraction; the key is globally bound under the lock.
        previous = db.scalar(
            select(ProcessingRun).where(
                ProcessingRun.config_json["retry_request_key"].as_string() == request_key,
            )
        )
        if previous:
            if previous.config_json.get("retry_request_digest") != fingerprint:
                raise RunConflictError("RETRY_KEY_CONFLICT", "请求键已绑定另一重试")
            return RunTicket(previous.id, previous.task_id, previous.batch_id, previous.lease_token)
        parent = db.scalar(select(ProcessingRun).where(ProcessingRun.task_id == task_id))
        if parent is None:
            raise RunConflictError("RUN_MISSING", "旧任务没有可验证的处理记录")
        batch = _locked_batch(db, parent.batch_id)
        db.refresh(parent)
        if parent.status not in {"success", "failed", "cancelled"}:
            raise RunConflictError("RETRY_NOT_READY", "旧任务尚未结束")
        revision = db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
        baseline = parent.summary_json.get("published_source_revision", parent.base_source_revision)
        if revision != expected_source_revision or baseline != revision:
            _changed()
        mapping = db.get(Mapping, batch.mapping_id)
        if (
            not mapping
            or not Path(mapping.file_path).is_file()
            or file_digest(Path(mapping.file_path)) != parent.mapping_digest
            or parent.algorithm_digest != algorithm_digest()
            or parent.config_json.get("accepted_config_digest")
            != configuration_digest(parent.config_json)
        ):
            _changed()
        report = parent.config_json.get("precheck_report") or {}
        if report:
            rows_digest = parent.config_json.get("prepared_rows_digest")
            if rows_digest:
                rows_path = Path(parent.config_json["prepared_dir"]) / "table-rows.json"
                if not rows_path.is_file():
                    raise RunConflictError("RETRY_INPUT_MISSING", "已检查的表格行文件缺失")
                if file_digest(rows_path) != rows_digest:
                    _changed()
            raw = Path(batch.raw_zip_path) if batch.raw_zip_path else None
            if raw is None or not raw.is_file():
                raise RunConflictError("RETRY_INPUT_MISSING", "重试输入缺失，原报告仍保留")
            if file_digest(raw) != parent.input_digest:
                _changed()
            prepared = Path(parent.config_json.get("prepared_dir") or "")
            for entry in report.get("items", []):
                if not entry.get("input_digest") or entry["relpath"].startswith("row/"):
                    continue
                path = prepared / entry["relpath"]
                if not path.is_file():
                    raise RunConflictError("RETRY_INPUT_MISSING", "输入或校准文件缺失")
                if file_digest(path) != entry["input_digest"]:
                    _changed()
        elif parent.config_json.get("input_digest_kind") == "archive":
            raw = Path(batch.raw_zip_path) if batch.raw_zip_path else None
            if raw is None or not raw.is_file():
                raise RunConflictError("RETRY_INPUT_MISSING", "重试原始包缺失")
            if file_digest(raw) != parent.input_digest:
                _changed()
        elif not parent.input_digest or directory_digest(batch.file_path) != parent.input_digest:
            _changed()
        items = db.scalars(select(ProcessingItem).where(ProcessingItem.run_id == parent.id)).all()
        eligible = {item.id: item for item in db.scalars(retry_items_query(parent))}
        selected_ids = set(item_ids) if item_ids is not None else set(eligible)
        if not selected_ids or not selected_ids.issubset(eligible):
            raise RunConflictError("RETRY_ITEMS_INVALID", "仅可选择原任务的失败或取消器件")
        selected = [eligible[key] for key in sorted(selected_ids)]
        for item in selected:
            source = db.scalar(
                select(SourceInput).where(
                    SourceInput.batch_id == batch.id,
                    SourceInput.member_key == item.source_member,
                    SourceInput.port_key == item.port_key,
                )
            )
            if not item.source_device_key and not source:
                raise RunConflictError("RETRY_SOURCE_UNKNOWN", "失败项没有可验证的源身份")
        config = {
            **parent.config_json,
            "retry_request_key": request_key,
            "retry_request_digest": fingerprint,
            "retry_items": [
                {
                    "member": item.source_member,
                    "port": item.port,
                    "source_device_key": item.source_device_key
                    or db.scalar(
                        select(SourceInput.source_device_key).where(
                            SourceInput.batch_id == batch.id,
                            SourceInput.member_key == item.source_member,
                            SourceInput.port_key == item.port_key,
                        )
                    ),
                }
                for item in selected
            ],
            "retry_parent_had_results": parent.config_json.get(
                "retry_parent_had_results", parent.config_json.get("base_has_results", False)
            ),
        }
        config.pop("retry_delivery_started", None)
        if parent.kind == "remap":
            raise RunConflictError("RETRY_KIND_UNAVAILABLE", "当前remap执行入口尚不可用")
        ticket = create_run_in_session(db, batch.id, parent.kind, config=config)
        run = db.get(ProcessingRun, ticket.run_id)
        run.retry_of_run_id, run.attempt = parent.id, parent.attempt + 1
        run.input_digest = parent.input_digest
        item_map = {}
        for item in items:
            copied = record_item_result(
                db,
                run.id,
                item.unit_kind,
                item.source_member,
                "pending" if item.id in selected_ids else item.status,
                port=item.port,
                source_device_key=item.source_device_key,
                error_code=None if item.id in selected_ids else item.error_code,
                attempt=run.attempt,
            )
            copied.result_digest = None if item.id in selected_ids else item.result_digest
            item_map[item.id] = copied.id
        for issue in db.scalars(select(QualityIssue).where(QualityIssue.run_id == parent.id)):
            if issue.item_id in selected_ids:
                continue
            record_issue(
                db,
                run.id,
                issue.code,
                issue.message,
                severity=issue.severity,
                item_id=item_map.get(issue.item_id),
                details=issue.details_json,
            )
        selected_keys = {item["source_device_key"] for item in config["retry_items"]}
        for row in db.scalars(
            select(StagedDeviceRow).where(StagedDeviceRow.run_id == parent.id)
        ).yield_per(1000):
            if row.source_device_key in selected_keys:
                continue
            db.add(
                StagedDeviceRow(
                    run_id=run.id,
                    source_device_key=row.source_device_key,
                    payload_json=row.payload_json,
                    payload_digest=row.payload_digest,
                )
            )
        summarize_run(db, run.id)
        run.summary_json = {
            **run.summary_json,
            "retry_of_run_id": parent.id,
            "inherited_success_count": sum(
                item.status == "success"
                for item in items
                if item.unit_kind in {"device", "table_row"}
            ),
        }
        return ticket
