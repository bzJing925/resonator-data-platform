"""Native SQLite replication: transactional revisions, completed batches, stable device IDs.

Only server-owned replicas are replaced/deleted. Local uploads never lose to a name
collision. Tables travel upward as immutable, idempotent imports. Desktop-owned
raw files are queued for upload; server-owned raw files stay remote. Revision
triggers also cover bulk SQL worker writes.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path, PureWindowsPath

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models import (
    Batch,
    DataEpoch,
    Device,
    Mapping,
    MappingEntry,
    ReplicaBatch,
    SyncIdentity,
    SyncRevision,
    TableReceipt,
    Tombstone,
    UploadTask,
)
from app.services.data_revision import install_data_triggers, seed_revision_metadata
from app.services.snapshot_service import _mapping_payload
from app.services.source_identity import batch_source_ref, set_replica_revision, validate_uuid


def init_replication(engine) -> None:
    for model in (ReplicaBatch, SyncIdentity, SyncRevision, TableReceipt, DataEpoch):
        model.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
        seed_revision_metadata(conn, str(uuid.uuid4()))
        install_data_triggers(conn)


def identity(db: Session) -> str:
    return db.get(SyncIdentity, "server").value


def ready(db: Session, batch: Batch) -> bool:
    if batch.deleted_at is not None:
        return False
    # Reprocess tasks may not be the original batch.task_id.
    active = db.scalar(select(UploadTask.id).where(
        UploadTask.batch_no == batch.batch_no,
        UploadTask.status.in_(("pending", "running", "cancel_requested")),
    ).limit(1))
    original = db.get(UploadTask, batch.task_id) if batch.task_id else None
    return active is None and (original is None or original.status == "success")


def catalog(db: Session) -> dict:
    revisions = dict(db.execute(select(SyncRevision.batch_id, SyncRevision.revision)).all())
    result = {"protocol": 2, "server_id": identity(db), "batches": [
        {"id": b.id, "batch_no": b.batch_no, "revision": revisions.get(b.id, 1),
         "ready": ready(db, b), "source_ref": batch_source_ref(db, b)}
        for b in db.scalars(select(Batch)).all()
    ], "mappings": [_mapping_payload(m) for m in db.scalars(select(Mapping)).all()]}
    # Legacy tombstones without a Batch remain visible to v2 readers as unavailable entries.
    present = {(b["source_ref"]["origin_id"], b["source_ref"]["source_batch_id"])
               for b in result["batches"] if b["source_ref"]}
    for tombstone in db.scalars(select(Tombstone)).all():
        if (tombstone.origin_id, tombstone.source_batch_id) not in present:
            result["batches"].append({"id": -len(result["batches"]) - 1,
                "batch_no": "deleted-" + tombstone.source_batch_id,
                "revision": tombstone.revision, "ready": False,
                "source_ref": {"origin_id": tombstone.origin_id,
                    "source_batch_id": tombstone.source_batch_id,
                    "source_device_key": None, "source_revision": tombstone.revision}})
    return result



def import_mapping(db: Session, sid: str, payload: dict) -> bool:
    """Create or refresh one server-owned mapping without touching local mappings."""
    prefix = f"server-{sid[:8]}-{payload['id']}-"
    name = prefix + payload["name"]
    mapping = db.scalar(select(Mapping).where(
        Mapping.file_path == f"server://{sid}", Mapping.name.like(prefix + "%")
    ))
    created = mapping is None
    if mapping is None:
        if db.scalar(select(Mapping.id).where(Mapping.name == name)) is not None:
            raise ValueError(f"对照表名冲突：{name}")
        mapping = Mapping(name=name, file_path=f"server://{sid}", entry_count=0)
        db.add(mapping)
        db.flush()
    else:
        mapping.name = name
    current = _mapping_payload(mapping)["entries"]
    if sorted(current, key=lambda item: item["mark"]) != sorted(
        payload["entries"], key=lambda item: item["mark"]
    ):
        db.execute(delete(MappingEntry).where(MappingEntry.mapping_id == mapping.id))
        for entry in payload["entries"]:
            db.add(MappingEntry(mapping_id=mapping.id, **entry))
    mapping.entry_count = len(payload["entries"])
    return created


def export_batch(db: Session, batch_id: int) -> dict:
    # sqlite3 legacy transaction mode does not BEGIN for SELECT. Explicitly start a
    # read transaction so revision, metadata and rows describe the same snapshot.
    raw = db.connection().connection.driver_connection
    if db.bind.dialect.name == "sqlite" and not raw.in_transaction:
        db.execute(text("BEGIN"))
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "批次已删除，请重新同步")
    if batch.deleted_at is not None:
        raise HTTPException(410, "批次已在回收站")
    if not ready(db, batch):
        raise HTTPException(409, "批次正在处理或尚未成功，请稍后同步")
    revision = db.get(SyncRevision, batch_id)
    fields = ("batch_no", "f_start_ghz", "f_end_ghz", "deembedded", "deembed_method",
              "process_type", "device_count", "uploaded_by", "origin_id", "source_batch_id")
    rows = []
    for device in db.scalars(select(Device).where(Device.batch_id == batch_id)):
        row = {c.name: getattr(device, c.name) for c in Device.__table__.columns
               if c.name != "batch_id"}
        # Never interpret the server's absolute path on a client machine.
        path = row.get("s_param_path")
        if path and (Path(path).is_absolute() or PureWindowsPath(path).is_absolute()):
            row["s_param_path"] = None
        rows.append(row)
    return {"server_id": identity(db), "remote_id": batch.id,
            "revision": revision.revision if revision else 1,
            "source_ref": batch_source_ref(db, batch),
            "batch": {f: getattr(batch, f) for f in fields},
            "mapping": _mapping_payload(batch.mapping), "devices": rows}


def _delete_unretained_devices(db: Session, batch_id: int, retained: list[int]) -> None:
    """Delete stale rows without binding every retained ID in one SQL statement."""
    retained_ids = set(retained)
    stale_ids = [device_id for device_id in db.scalars(
        select(Device.id).where(Device.batch_id == batch_id)
    ) if device_id not in retained_ids]
    # Stay below SQLite's legacy 999-variable limit, including the batch predicate.
    for offset in range(0, len(stale_ids), 500):
        db.execute(delete(Device).where(
            Device.batch_id == batch_id,
            Device.id.in_(stale_ids[offset:offset + 500]),
        ))


def import_batch(db: Session, payload: dict, *, existing: Batch | None = None) -> dict:
    sid, rid = payload["server_id"], payload["remote_id"]
    replica = db.scalar(select(ReplicaBatch).where(
        ReplicaBatch.server_id == sid, ReplicaBatch.remote_id == rid))
    batch = db.get(Batch, replica.batch_id) if replica else existing
    added = batch is None
    incoming = payload["batch"]
    for field in ("origin_id", "source_batch_id"):
        if incoming.get(field) is not None:
            validate_uuid(incoming[field])
    collision = db.scalar(select(Batch).where(Batch.batch_no == incoming["batch_no"]))
    if collision is not None and collision is not batch:
        # Upgrade a legacy server snapshot, but never overwrite a local table/raw batch.
        if collision.source == "server" and db.get(ReplicaBatch, collision.id) is None:
            batch = collision
            added = False
        elif (
            collision.source in ("http", "watch")
            and collision.synced_at is not None
            and collision.device_count == incoming["device_count"]
        ):
            if collision.origin_id is not None and (
                incoming.get('origin_id') != collision.origin_id
                or incoming.get('source_batch_id') != collision.source_batch_id
            ):
                raise ValueError('同名回传来源UUID无法核验；本地身份与原始文件保留')
            # v1 回传批次：本机即数据源头且器件数一致，收养为副本而非冲突报错。
            # 本地原始文件保留在盘上，相对 s_param_path 与服务器一致，仍优先本地读图。
            batch = collision
            added = False
        else:
            raise ValueError(f"批次名冲突：{incoming['batch_no']}，请先重命名本地批次")
    remote_mapping = payload["mapping"]
    mapping_added = import_mapping(db, sid, remote_mapping)
    mapping_name = f"server-{sid[:8]}-{remote_mapping['id']}-{remote_mapping['name']}"
    mapping = db.scalar(select(Mapping).where(Mapping.name == mapping_name))
    if batch is None:
        batch = Batch(batch_no=incoming["batch_no"], mapping_id=mapping.id,
                      file_path=f"server://{sid}/{rid}", source="server")
        db.add(batch)
        db.flush()
    for key, value in incoming.items():
        setattr(batch, key, value)
    batch.mapping_id = mapping.id
    batch.source = "server"
    batch.task_id = None
    if replica is None:
        replica = ReplicaBatch(batch_id=batch.id, server_id=sid, remote_id=rid,
                               revision=0, device_ids={})
        db.add(replica)
    old_ids = replica.device_ids or {}
    old_keys = {d.source_device_key: d.id for d in db.scalars(
        select(Device).where(Device.batch_id == batch.id)
    ) if d.source_device_key is not None}
    incoming_keys = [row.get("source_device_key") for row in payload["devices"]]
    supplied = [key for key in incoming_keys if key is not None]
    if len(set(supplied)) != len(supplied):
        raise ValueError("来源器件UUID重复")
    for key in supplied:
        validate_uuid(key)
    incoming_ids = {str(row["id"]) for row in payload["devices"]}
    retain = [lid for rid, lid in old_ids.items() if rid in incoming_ids]
    retain.extend(old_keys[key] for key in supplied if key in old_keys)
    # Re-extraction creates new remote device IDs at the same paths. Remove stale
    # records first, and release path uniqueness before any path swaps.
    _delete_unretained_devices(db, batch.id, retain)
    db.query(Device).filter(Device.batch_id == batch.id).update({"s_param_path": None})
    db.flush()
    new_ids = {}
    for row in payload["devices"]:
        remote_id = str(row["id"])
        local_id = old_keys.get(row.get("source_device_key")) or old_ids.get(remote_id)
        device = db.get(Device, local_id) if local_id is not None else None
        if device is None:
            device = Device(batch_id=batch.id, original_filename=row["original_filename"])
            db.add(device)
        for key, value in row.items():
            if key not in {"id", "batch_id"} and key in Device.__table__.columns:
                setattr(device, key, value)
        db.flush()
        new_ids[remote_id] = device.id
    keep = list(new_ids.values())
    _delete_unretained_devices(db, batch.id, keep)
    replica.device_ids = new_ids
    replica.revision = payload["revision"]
    db.flush()
    source_ref = payload.get("source_ref")
    set_replica_revision(db, batch.id, source_ref.get("source_revision") if source_ref else None)
    db.commit()
    return {"batches_added": int(added), "batches_updated": int(not added),
            "devices_added": len(new_ids) if added else 0, "mappings_added": int(mapping_added)}


def table_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def accept_table(db: Session, key: str, payload: dict) -> dict:
    # sqlite3 驱动的 SELECT 不入事务（见 export_batch）：进入即显式开启写事务，
    # 使 revision 检查与 metrics 更新同属一个事务。并发推送同 revision 时后者
    # 在 BEGIN IMMEDIATE 上阻塞至前者提交，随后读到新 revision 而 409，不会静默覆盖。
    if db.bind.dialect.name == "sqlite":
        raw = db.connection().connection.driver_connection
        if not raw.in_transaction:
            db.execute(text("BEGIN IMMEDIATE"))
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                   {"key": "table-receipt:" + key})
    digest = table_digest(payload)
    receipt = db.get(TableReceipt, key)
    if receipt:
        if receipt.digest != digest:
            raise HTTPException(409, "已上传的表格发生变化，请作为新批次导入")
        return export_batch(db, receipt.batch_id)
    target = payload.get("target")
    if target:
        batch = db.scalar(select(Batch).where(Batch.batch_no == target["batch_no"])
                          .with_for_update().execution_options(populate_existing=True))
        if batch is None or not ready(db, batch):
            raise HTTPException(409, "关联的原始批次不存在或正在处理")
        reference = target.get("source_ref")
        if reference is not None and (
            reference.get("origin_id") != batch.origin_id
            or reference.get("source_batch_id") != batch.source_batch_id
            or reference.get("source_revision") != target.get("revision")
        ):
            raise HTTPException(409, "关联来源身份/原始版本不一致")
        rev = db.get(SyncRevision, batch.id)
        if target.get("revision") is None:
            raise HTTPException(409, "请先同步目标原始批次，再重新导入关联表格")
        if bool(payload["batch"].get("deembedded")) != batch.deembedded:
            raise HTTPException(409, "表格与原始批次的去嵌状态不一致")
        if rev.revision != target["revision"]:
            raise HTTPException(409, "服务器指标已有更新，请同步后重新导入表格以免覆盖他人修改")
        devices = db.scalars(select(Device).where(Device.batch_id == batch.id)).all()
        matches = []
        used = set()
        for row in payload["devices"]:
            candidates = [d for d in devices if d.original_filename == row["original_filename"]
                          and d.s_param_port == row.get("s_param_port", "S11")
                          and (not row.get("folder_name") or d.folder_name == row["folder_name"])]
            if len(candidates) != 1 or candidates[0].id in used:
                raise HTTPException(409, f"文件匹配不唯一或不存在: {row['original_filename']}")
            used.add(candidates[0].id)
            matches.append((candidates[0], row))
        protected = {"id", "batch_id", "s_param_path", "original_filename", "s_param_port",
                     "folder_name", "deembedded", "source_device_key"}
        for device, row in matches:
            for field, value in row.items():
                if field in Device.__table__.columns and field not in protected:
                    setattr(device, field, value)
        db.add(TableReceipt(key=key, digest=digest, batch_id=batch.id))
        db.commit()
        return export_batch(db, batch.id)
    name = payload["batch"]["batch_no"]
    if db.scalar(select(Batch.id).where(Batch.batch_no == name)) is not None:
        raise HTTPException(409, f"服务器已有同名批次 {name}，请重命名本地批次")
    from app.api.sync import MappingEnsureRequest, ensure_mapping_data
    mapping = payload["mapping"]
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"),
                   {"name": "table-mapping:" + mapping["name"]})
    result = ensure_mapping_data(MappingEnsureRequest(name=mapping["name"],
                                                    entries=mapping["entries"]), db, commit=False)
    batch = Batch(**payload["batch"], mapping_id=result["mapping_id"],
                  file_path="table-sync://" + key, source="table")
    from app.services.source_identity import assign_batch_identity
    assign_batch_identity(db, batch)
    db.add(batch)
    db.flush()
    for row in payload["devices"]:
        values = {k: v for k, v in row.items()
                  if k in Device.__table__.columns and k not in {"id", "batch_id", "s_param_path"}}
        if values.get("source_device_key") is None:
            values["source_device_key"] = str(uuid.uuid4())
        db.add(Device(batch_id=batch.id, **values))
    db.add(TableReceipt(key=key, digest=digest, batch_id=batch.id))
    db.commit()
    return export_batch(db, batch.id)


def synchronize(db: Session, client, *, protocol=2, initial_catalog=None) -> dict:
    """Serialize callers at API boundary. Failed batches remain retryable."""
    from app.services import desktop_sync_service
    from app.services.server_client import ServerError

    result = dict(batches_added=0, batches_updated=0, batches_deleted=0,
                  devices_added=0, mappings_added=0, mappings_pushed=0,
                  tables_pushed=0, raw_pushed=0, errors=[])
    catalog_path = f"/sync/v{protocol}/catalog"
    catalog_data = initial_catalog or client.request_json("GET", catalog_path)
    if protocol == 3:
        from app.services.replication_v3 import validate_catalog
        validate_catalog(catalog_data)
    sid = catalog_data["server_id"]
    previous = db.get(SyncIdentity, "upstream")
    if previous and previous.value != sid:
        raise ServerError("服务器身份改变；请使用独立的桌面数据目录连接另一服务器")
    if previous is None:
        db.add(SyncIdentity(key="upstream", value=sid))
        db.commit()
    # A per-client database UUID and local batch id provide an idempotency key.
    local_sid = identity(db)
    # Standalone mappings must reach the server even when no batch uses them yet.
    # Imported server mappings are copies and must not be sent back upstream.
    for mapping in db.scalars(select(Mapping).order_by(Mapping.id)).all():
        if mapping.file_path.startswith("server://"):
            continue
        try:
            reply = client.ensure_mapping(
                mapping.name, desktop_sync_service._mapping_entries_payload(mapping)
            )
            result["mappings_pushed"] += int(reply["created"])
        except ServerError as exc:
            result["errors"].append(f"对照表 {mapping.name}: {exc}")

    # Finished local SNP uploads are queued for automatic delivery. The original
    # selected file and the desktop's extracted files are never removed here.
    for batch in desktop_sync_service.list_unsynced_batches(db):
        try:
            if protocol == 3:
                desktop_sync_service.push_batch_to_server(db, client, batch.batch_no,
                                                          preserve_source_identity=True)
            else:
                desktop_sync_service.push_batch_to_server(db, client, batch.batch_no)
            result["raw_pushed"] += 1
        except (ServerError, OSError, ValueError, HTTPException) as exc:
            db.rollback()
            result["errors"].append(f"原始批次 {batch.batch_no}: {exc}")

    for batch in db.scalars(select(Batch).where(
        Batch.source == "table", Batch.deleted_at.is_(None))).all():
        if db.get(ReplicaBatch, batch.id):
            continue
        operation = None
        try:
            original_ref = batch_source_ref(db, batch)
            payload = export_batch(db, batch.id)
            payload = {k: payload[k] for k in ("batch", "mapping", "devices")}
            payload["mapping"]["name"] = desktop_sync_service.mapping_server_name(batch.mapping)
            target = db.get(SyncIdentity, f"table-target:{batch.id}")
            submission = db.get(SyncIdentity, f"table-submission:{batch.id}")
            request_key = f"{local_sid}:{batch.id}"
            if submission is not None:
                proof = json.loads(submission.value)
                if proof["reviewed_digest"] != table_digest(payload):
                    raise HTTPException(409, "表格在复核后已修改，请以新请求键重新核对")
                payload["target"] = proof["target"]
                request_key += ":" + proof["request_key"]
            elif target:
                payload["target"] = json.loads(target.value)
            from app.services import sync_status
            if protocol == 3:
                db.rollback()
                operation = sync_status.begin(db.bind, original_ref, 'server', payload,
                                              request_key=request_key)
                expected = payload
                if payload.get('target'):
                    reference = payload['target']['source_ref']
                    resolved = client.request_json(
                        'GET', f"/sources/batches/{reference['origin_id']}/"
                        f"{reference['source_batch_id']}/resolve")
                    if resolved['source_ref'] != reference:
                        raise HTTPException(409, 'SOURCE_REVISION_CONFLICT')
                    base = client.request_json('GET', f"/sync/v3/batches/{resolved['batch_id']}")
                    expected = sync_status.merged_table_packet(base, payload)
                db.rollback()
                sync_status.expect_content(db.bind, operation, expected)
            reply = client.request_json("POST", f"/sync/v{protocol}/tables", json={
                "key": request_key, "payload": payload})
            if operation:
                sync_status.expect_target(db.bind, operation, reply['source_ref'])
                readback = client.request_json('GET', f"/sync/v3/batches/{reply['remote_id']}")
                if not sync_status.confirm_readback(db.bind, operation, readback):
                    raise HTTPException(409, 'READBACK_MISMATCH')
                reply = readback
            if payload.get('target'):
                # The receipt is durable on the server before removing the local staging batch.
                # Its original spreadsheet remains in uploads/.
                import_batch(db, reply)
                # Retain local source identity, original input and history for 30 days.
                from app.services.batch_lifecycle import soft_delete
                db.rollback()
                soft_delete(db.bind, batch.batch_no)
                db.expire_all()
            else:
                import_batch(db, reply, existing=batch)
            result["tables_pushed"] += 1
        except (ServerError, ValueError, HTTPException) as exc:
            db.rollback()
            if operation:
                conflict = getattr(exc, 'status_code', None) == 409
                sync_status.transition(db.bind, operation, 'conflict' if conflict else 'unknown',
                    'TARGET_CONFLICT' if conflict else 'TARGET_UNAVAILABLE', retryable=not conflict)
            result["errors"].append(f"{batch.batch_no}: {exc}")
    # Refresh catalog after uploads; deletions are applied only from this successful,
    # complete catalog, never inferred from a failed/partial HTTP response.
    catalog_data = client.request_json("GET", catalog_path)
    if protocol == 3:
        from app.services.replication_v3 import apply_catalog
        received = apply_catalog(db, catalog_data, lambda batch_id: client.request_json(
            "GET", f"/sync/v3/batches/{batch_id}"))
        for key, value in received.items():
            if key == "errors":
                result[key].extend(value)
            else:
                result[key] += value
        return result
    if "mappings" in catalog_data:
        remote_mapping_ids = {m["id"] for m in catalog_data["mappings"]}
        for payload in catalog_data["mappings"]:
            try:
                result["mappings_added"] += int(import_mapping(db, sid, payload))
                db.commit()
            except (ValueError, KeyError, TypeError) as exc:
                db.rollback()
                result["errors"].append(f"服务器对照表 {payload.get('name', '?')}: {exc}")
        prefix = f"server-{sid[:8]}-"
        for mapping in db.scalars(select(Mapping).where(
            Mapping.file_path == f"server://{sid}"
        )).all():
            try:
                remote_id = int(mapping.name.removeprefix(prefix).split("-", 1)[0])
            except ValueError:
                continue
            if remote_id not in remote_mapping_ids and not mapping.batches:
                db.delete(mapping)
        db.commit()
    # v2 has no explicit source state; a missing entry never authorizes deletion.
    for remote in catalog_data["batches"]:
        if not remote["ready"]:
            continue
        replica = db.scalar(select(ReplicaBatch).where(
            ReplicaBatch.server_id == sid, ReplicaBatch.remote_id == remote["id"]))
        if replica and replica.revision == remote["revision"]:
            continue
        try:
            payload = client.request_json("GET", f"/sync/v2/batches/{remote['id']}")
            if payload["server_id"] != sid:
                raise ServerError("同步期间服务器身份改变")
            stats = import_batch(db, payload)
            for key, value in stats.items():
                result[key] += value
        except (ServerError, ValueError) as exc:
            db.rollback()
            result["errors"].append(f"{remote['batch_no']}: {exc}")
    return result


def synchronize_auto(db, client):
    from app.services.server_client import ServerError
    try:
        catalog_data = client.request_json("GET", "/sync/v3/catalog")
    except ServerError as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        return synchronize(db, client)
    return synchronize(db, client, protocol=3, initial_catalog=catalog_data)
