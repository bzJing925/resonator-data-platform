"""Durable sync attempts; only independent target reads produce receipts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Device, SyncOperation
from app.schemas.capabilities import SyncStatus
from app.schemas.source import SourceRef
from app.services.source_identity import batch_source_ref

BATCH_FIELDS = (
    "f_start_ghz",
    "f_end_ghz",
    "deembedded",
    "deembed_method",
    "process_type",
    "device_count",
)
DEVICE_FIELDS = tuple(
    c.name for c in Device.__table__.columns if c.name not in {"id", "batch_id", "s_param_path"}
)


def packet_digest(packet):
    """Canonical portable values; host IDs, names and physical paths are not identities."""
    rows = packet["devices"]
    keys = [r.get("source_device_key") for r in rows]
    if not keys or None in keys or len(keys) != len(set(keys)):
        raise ValueError("SOURCE_IDENTITY_UNKNOWN")
    for key in keys:
        SourceRef(
            origin_id=packet["batch"]["origin_id"],
            source_batch_id=packet["batch"]["source_batch_id"],
            source_device_key=key,
            source_revision=None,
        )

    def clean(value):
        if isinstance(value, float):
            # 不同环境的数值库/序列化路径会产生 ULP 级差异；
            # 统一取 6 位有效数字，避免把传输噪声误判为内容不一致
            return float(f"{value:.6g}") if math.isfinite(value) else None
        return value

    canonical = {
        "batch": {f: clean(packet["batch"].get(f)) for f in BATCH_FIELDS},
        "devices": [
            {f: clean(row.get(f)) for f in DEVICE_FIELDS}
            for row in sorted(rows, key=lambda r: r["source_device_key"])
        ],
    }
    return hashlib.sha256(
        json.dumps(
            canonical, sort_keys=True, allow_nan=False, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def begin(engine, reference, target, packet, *, request_key=None):
    ref = SourceRef.model_validate(reference).model_dump(mode="json")
    if ref["source_revision"] is None:
        raise ValueError("SOURCE_REVISION_UNKNOWN")
    expected = {
        "digest": packet_digest(packet),
        "source_ref": ref,
        "input_digest": packet_digest(packet),
    }
    with Session(engine) as db:
        key = request_key or str(uuid4())
        if engine.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        else:
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": "sync-operation:" + key},
            )
        old = db.scalar(select(SyncOperation).where(SyncOperation.request_key == key))
        if old:
            if (old.origin_id, old.source_batch_id, old.source_revision, old.target) != (
                ref["origin_id"],
                ref["source_batch_id"],
                ref["source_revision"],
                target,
            ):
                raise HTTPException(409, "SYNC_REQUEST_CONFLICT")
            if not old.expected or old.expected.get("input_digest") != expected["input_digest"]:
                raise HTTPException(409, "SYNC_REQUEST_CONFLICT")
            return old.id
        row = SyncOperation(
            origin_id=ref["origin_id"],
            source_batch_id=ref["source_batch_id"],
            source_revision=ref["source_revision"],
            target=target,
            request_key=key,
            state="syncing",
            expected=expected,
        )
        db.add(row)
        db.commit()
        return row.id


def transition(engine, operation_id, state, code=None, *, retryable=False):
    if state not in {"pending", "syncing", "conflict", "failed", "unknown"}:
        raise ValueError("receipt required for confirmation")
    with Session(engine) as db:
        row = db.get(SyncOperation, operation_id, with_for_update=True)
        if row.state != "confirmed":
            row.state, row.error_code, row.retryable = state, code, retryable
            db.commit()


def expect_target(engine, operation_id, reference):
    """Acceptance establishes target version, never a receipt or data digest."""
    ref = SourceRef.model_validate(reference).model_dump(mode="json")
    if ref["source_revision"] is None:
        raise ValueError("SOURCE_REVISION_UNKNOWN")
    with Session(engine) as db:
        row = db.get(SyncOperation, operation_id, with_for_update=True)
        row.expected = {**row.expected, "source_ref": ref}
        db.commit()


def confirm_readback(engine, operation_id, packet):
    try:
        ref = SourceRef.model_validate(packet["source_ref"]).model_dump(mode="json")
        digest = packet_digest(packet)
    except (KeyError, ValueError, TypeError):
        transition(engine, operation_id, "unknown", "READBACK_INVALID", retryable=True)
        return False
    with Session(engine) as db:
        row = db.get(SyncOperation, operation_id, with_for_update=True)
        if (
            row.expected is None
            or ref != row.expected["source_ref"]
            or (digest != row.expected["digest"])
        ):
            row.state, row.error_code, row.retryable = "conflict", "READBACK_MISMATCH", False
            db.commit()
            return False
        row.state, row.receipt = "confirmed", {"source_ref": ref, "digest": digest}
        row.confirmed_revision = ref["source_revision"]
        row.last_success_at = datetime.now(UTC)
        row.error_code, row.retryable = None, False
        db.commit()
        return True


def statuses(db, batch):
    reference = batch_source_ref(db, batch)
    result = {}
    for target in ("server",):
        ops = (
            []
            if reference is None
            else db.scalars(
                select(SyncOperation)
                .where(
                    SyncOperation.origin_id == reference["origin_id"],
                    SyncOperation.source_batch_id == reference["source_batch_id"],
                    SyncOperation.target == target,
                )
                .order_by(SyncOperation.created_at.desc(), SyncOperation.id.desc())
            ).all()
        )
        latest = ops[0] if ops else None
        success = next((op for op in ops if op.receipt and op.last_success_at is not None), None)
        state = latest.state if latest else ("unknown" if batch.synced_at else "local_only")
        if (
            latest
            and state == "confirmed"
            and (latest.source_revision != reference["source_revision"])
        ):
            state = "pending"
        result[target] = SyncStatus(
            target=target,
            state=state,
            confirmed_revision=success.confirmed_revision if success else None,
            pending_revision=reference["source_revision"]
            if reference and state != "confirmed"
            else None,
            last_success_at=success.last_success_at if success else None,
            error_code=latest.error_code if latest else None,
            retryable=latest.retryable if latest else bool(batch.synced_at),
        ).model_dump(mode="json")
    return result


def merged_table_packet(base, table):
    """Predict the accepted merge without changing source keys or protected fields."""
    import copy

    result = copy.deepcopy(base)
    protected = {
        "id",
        "batch_id",
        "s_param_path",
        "original_filename",
        "s_param_port",
        "folder_name",
        "deembedded",
        "source_device_key",
    }
    used = set()
    for row in table["devices"]:
        matches = [
            device
            for device in result["devices"]
            if device["original_filename"] == row["original_filename"]
            and device.get("s_param_port", "S11") == row.get("s_param_port", "S11")
            and (not row.get("folder_name") or device.get("folder_name") == row["folder_name"])
        ]
        if len(matches) != 1 or matches[0]["source_device_key"] in used:
            raise HTTPException(409, "TABLE_MATCH_CONFLICT")
        device = matches[0]
        used.add(device["source_device_key"])
        device.update(
            {
                field: value
                for field, value in row.items()
                if field in DEVICE_FIELDS and field not in protected
            }
        )
    return result


def expect_content(engine, operation_id, packet):
    with Session(engine) as db:
        row = db.get(SyncOperation, operation_id, with_for_update=True)
        row.expected = {**row.expected, "digest": packet_digest(packet)}
        db.commit()
