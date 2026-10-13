"""Source UUIDs, exact input registration and honest provenance references.

No function commits. Legacy device UUID backfill does not fabricate original ZIP
members or content digests, and remote copies never acquire a new local identity.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Batch, Device, SourceInput, SyncIdentity, SyncRevision

_REMOTE_SOURCES = frozenset({"server", "web"})


class SourceIdentityError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_uuid(value: str) -> str:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SourceIdentityError("SOURCE_IDENTITY_INVALID", "来源UUID无效") from exc
    return value


def validate_relpath(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise SourceIdentityError("SOURCE_INPUT_PATH_INVALID", "输入路径必须为安全POSIX相对路径")
    return value


def ensure_origin_identity(db: Session) -> str:
    db.execute(
        text(
            "INSERT INTO sync_identity (key,value) VALUES ('server',:value) "
            "ON CONFLICT(key) DO NOTHING"
        ),
        {"value": str(uuid4())},
    )
    value = db.scalar(select(SyncIdentity.value).where(SyncIdentity.key == "server"))
    return validate_uuid(value)


def assign_batch_identity(db: Session, batch: Batch) -> bool:
    if batch.origin_id and batch.source_batch_id:
        validate_uuid(batch.origin_id)
        validate_uuid(batch.source_batch_id)
        return False
    if batch.origin_id or batch.source_batch_id:
        raise SourceIdentityError("SOURCE_IDENTITY_INCOMPLETE", "批次来源身份不完整")
    if batch.source in _REMOTE_SOURCES:
        return False
    batch.origin_id = ensure_origin_identity(db)
    batch.source_batch_id = str(uuid4())
    return True


def register_source_input(
    db: Session,
    batch_id: int,
    member_key: str,
    port: str | None,
    input_digest: str,
    current_relpath: str | None,
    original_filename: str,
    *,
    source_device_key: str | None = None,
) -> SourceInput:
    member_key = validate_relpath(member_key)
    if current_relpath is not None:
        validate_relpath(current_relpath)
    if port not in {None, "S11", "S22"} or not input_digest or not original_filename:
        raise SourceIdentityError("SOURCE_INPUT_INVALID", "输入端口、摘要或文件名无效")
    if source_device_key is not None:
        validate_uuid(source_device_key)
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise SourceIdentityError("SOURCE_BATCH_MISSING", "来源批次不存在")
    assign_batch_identity(db, batch)
    stmt = select(SourceInput).where(
        SourceInput.batch_id == batch_id,
        SourceInput.member_key == member_key,
        SourceInput.port_key == (port or ""),
    )
    existing = db.scalar(stmt)
    if existing is None:
        try:
            with db.begin_nested():
                existing = SourceInput(
                    batch_id=batch_id,
                    member_key=member_key,
                    port=port,
                    port_key=port or "",
                    input_digest=input_digest,
                    current_relpath=current_relpath,
                    original_filename=original_filename,
                    source_device_key=source_device_key or str(uuid4()),
                )
                db.add(existing)
                db.flush()
        except IntegrityError as exc:
            existing = db.scalar(stmt)
            if existing is None:
                raise SourceIdentityError(
                    "SOURCE_INPUT_AMBIGUOUS", "器件UUID已属于其他输入"
                ) from exc
    if existing.input_digest != input_digest or (
        source_device_key is not None and existing.source_device_key != source_device_key
    ):
        raise SourceIdentityError("SOURCE_INPUT_CHANGED", "原始成员内容或来源UUID发生变化")
    if current_relpath is not None and existing.current_relpath not in {None, current_relpath}:
        raise SourceIdentityError("SOURCE_PATH_CHANGED", "活动路径只能在结果发布时切换")
    if existing.current_relpath is None:
        existing.current_relpath = current_relpath
    return existing


def resolve_device_identity(
    db: Session,
    batch_id: int,
    member_key: str,
    port: str | None,
    *,
    input_digest: str | None = None,
) -> str:
    validate_relpath(member_key)
    entry = db.scalar(
        select(SourceInput).where(
            SourceInput.batch_id == batch_id,
            SourceInput.member_key == member_key,
            SourceInput.port_key == (port or ""),
        )
    )
    if entry is None:
        raise SourceIdentityError("SOURCE_INPUT_MISSING", "找不到精确登记的原始输入")
    if input_digest is not None and input_digest != entry.input_digest:
        raise SourceIdentityError("SOURCE_INPUT_CHANGED", "原始输入内容摘要已变化")
    return entry.source_device_key


def update_source_input_path(db: Session, source_device_key: str, relpath: str) -> None:
    """Caller must hold the publication lease/transaction; never changes original key."""
    validate_relpath(relpath)
    result = db.execute(
        update(SourceInput)
        .where(SourceInput.source_device_key == source_device_key)
        .values(current_relpath=relpath)
    )
    if result.rowcount != 1:
        raise SourceIdentityError("SOURCE_INPUT_MISSING", "来源登记不存在")


def register_prechecked_sources(db: Session, batch: Batch, report: dict) -> None:
    """Persist original member/port identities before the run captures its base."""
    for item in report.get("items", []):
        if item.get("classification") != "dut" or item.get("status") != "success":
            continue
        member = item["relpath"]
        for port in item.get("ports", []):
            register_source_input(
                db,
                batch.id,
                member,
                port,
                item.get("input_digest") or report["input_digest"],
                None if member.startswith("row/") else member,
                item.get("original_filename") or PurePosixPath(member).name,
            )
    db.flush()


def identity_for_result_path(db: Session, batch_id: int, relpath: str, port: str) -> str | None:
    """Compatibility matcher: only an exact registered current path may inherit UUID."""
    matches = db.scalars(
        select(SourceInput.source_device_key).where(
            SourceInput.batch_id == batch_id,
            SourceInput.current_relpath == relpath,
            SourceInput.port_key == port,
        )
    ).all()
    if len(matches) > 1:
        raise SourceIdentityError("SOURCE_INPUT_AMBIGUOUS", "当前产物路径匹配多个原始输入")
    return matches[0] if matches else None


def backfill_source_refs(db: Session, *, chunk_size: int = 500) -> dict[str, int]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    counts = {"batches": 0, "devices": 0, "unverified_batches": 0}
    after = 0
    while batches := db.scalars(
        select(Batch).where(Batch.id > after).order_by(Batch.id).limit(chunk_size)
    ).all():
        for batch in batches:
            after = batch.id
            if batch.source in _REMOTE_SOURCES:
                counts["unverified_batches"] += int(
                    not batch.origin_id or not batch.source_batch_id
                )
                continue
            counts["batches"] += int(assign_batch_identity(db, batch))
            device_after = 0
            while devices := db.scalars(
                select(Device)
                .where(Device.batch_id == batch.id, Device.id > device_after)
                .order_by(Device.id)
                .limit(chunk_size)
            ).all():
                for device in devices:
                    device_after = device.id
                    if device.source_device_key is None:
                        device.source_device_key = str(uuid4())
                        counts["devices"] += 1
                    else:
                        validate_uuid(device.source_device_key)
                db.flush()
        db.flush()
    return counts


def batch_source_ref(db: Session, batch: Batch) -> dict | None:
    if not batch.origin_id or not batch.source_batch_id:
        return None
    validate_uuid(batch.origin_id)
    validate_uuid(batch.source_batch_id)
    revision = db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
    return {
        "origin_id": batch.origin_id,
        "source_batch_id": batch.source_batch_id,
        "source_device_key": None,
        "source_revision": revision,
    }


def set_replica_revision(db: Session, batch_id: int, revision: int | None) -> None:
    if revision is None:
        db.execute(delete(SyncRevision).where(SyncRevision.batch_id == batch_id))
        return
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise SourceIdentityError("SOURCE_REVISION_INVALID", "来源修订无效")
    db.execute(
        text(
            "INSERT INTO sync_revisions(batch_id,revision) VALUES (:batch_id,:revision) "
            "ON CONFLICT(batch_id) DO UPDATE SET revision=excluded.revision"
        ),
        {"batch_id": batch_id, "revision": revision},
    )
