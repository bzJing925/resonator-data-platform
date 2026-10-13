"""Isolated staging and a single guarded transaction for visible results."""

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from itertools import batched, groupby
from pathlib import Path

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Batch,
    CleanupJob,
    Device,
    Mapping,
    ProcessingItem,
    ProcessingRun,
    SourceInput,
    StagedDeviceRow,
    SyncRevision,
    UploadTask,
)
from app.services.artifact_lifecycle import cleanup_path_intent
from app.services.processing_report import _finite, record_issue, summarize_run
from app.services.processing_run import (
    RunConflictError,
    _locked_session,
    _owned_run,
    assert_run_writable,
    fence_run_writes,
)
from app.services.source_identity import validate_uuid
from app.services.task_state import transition_task
from app.services.upload_precheck import file_digest

PAYLOAD_FIELDS = set(Device.__table__.columns.keys()) - {"id", "batch_id"}


def prepare_stage_directory(db, ticket, *, copy_from=None):
    assert_run_writable(db, ticket.run_id, ticket.lease_token)
    run = db.get(ProcessingRun, ticket.run_id)
    batch = db.get(Batch, run.batch_id)
    directory = (
        get_settings().files_dir / ".runs" / batch.source_batch_id / run.id / ticket.lease_token
    )
    if copy_from:
        shutil.copytree(copy_from, directory)
    else:
        directory.mkdir(parents=True, exist_ok=False)
    fence_run_writes(db, ticket.run_id, ticket.lease_token)
    run.stage_dir = str(directory)
    db.commit()
    return directory


def stage_device_rows(db, run_id, lease_token, rows):
    """Upsert only this run's staging; never mutate active Device rows."""
    assert_run_writable(db, run_id, lease_token)
    fence_run_writes(db, run_id, lease_token)
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    for chunk in batched(rows, get_settings().STAGE_INSERT_CHUNK):
        values = {}
        for row in chunk:
            key = row.get("source_device_key")
            validate_uuid(key)
            payload = _finite(
                {name: value for name, value in row.items() if name in PAYLOAD_FIELDS}
            )
            encoded = json.dumps(payload, sort_keys=True, allow_nan=False, default=str)
            values[key] = {
                "run_id": run_id,
                "source_device_key": key,
                "payload_json": json.loads(encoded),
                "payload_digest": hashlib.sha256(encoded.encode()).hexdigest(),
            }
        statement = insert(StagedDeviceRow).values(list(values.values()))
        db.execute(
            statement.on_conflict_do_update(
                index_elements=["run_id", "source_device_key"],
                set_={
                    "payload_json": statement.excluded.payload_json,
                    "payload_digest": statement.excluded.payload_digest,
                },
            )
        )
    db.flush()


def validate_staged_result(db, run):
    count = (
        db.scalar(
            select(func.count())
            .select_from(StagedDeviceRow)
            .where(StagedDeviceRow.run_id == run.id)
        )
        or 0
    )
    if not count:
        raise RunConflictError("NO_SUCCESSFUL_RESULT", "没有成功结果，保留活动数据")
    bad = db.scalar(
        select(ProcessingItem.id)
        .where(
            ProcessingItem.run_id == run.id,
            ProcessingItem.unit_kind.in_(("device", "table_row")),
            ProcessingItem.status != "success",
        )
        .limit(1)
    )
    report = run.config_json.get("precheck_report") or {}
    excluded = any(
        i.get("status") in {"failed", "skipped"} and i.get("classification") == "dut"
        for i in report.get("items", [])
    )
    has_old = run.base_result_revision > 0 or run.config_json.get("base_has_results", False)
    if run.retry_of_run_id:
        has_old = run.config_json.get("retry_parent_had_results", has_old)
    if run.kind != "recompute" and has_old and (bad or excluded):
        raise RunConflictError("INCOMPLETE_REPLACEMENT", "全量处理存在失败，保留旧结果")
    return count


def _unpublished_device_delete(batch_id, run_id):
    published = (
        select(StagedDeviceRow.source_device_key)
        .where(
            StagedDeviceRow.run_id == run_id,
            StagedDeviceRow.source_device_key == Device.source_device_key,
        )
        .correlate(Device)
        .exists()
    )
    # NOT IN can materialize and rescan all staging rows for every device.
    # Its NULL-key behavior is preserved explicitly for unmigrated legacy rows.
    return delete(Device).where(
        Device.batch_id == batch_id, Device.source_device_key.is_not(None), ~published
    )


def publish_run(session_or_engine, run_id, lease_token, *, manifest=None):
    """Own a fresh session; old readers retain their paths after atomic publication."""
    engine = (
        session_or_engine.get_bind()
        if isinstance(session_or_engine, Session)
        else session_or_engine
    )
    with Session(engine) as reader:
        existing = reader.get(ProcessingRun, run_id)
        if existing is None:
            raise RunConflictError("RUN_MISSING", "处理记录不存在")
        stage_dir = existing.stage_dir
    # Prescan before the write lock, never scan the filesystem inside publication.
    if manifest is None and stage_dir:
        from app.services.file_tree_service import scan_disk_files

        manifest = scan_disk_files(Path(stage_dir))
    with _locked_session(engine) as db:
        existing = db.get(ProcessingRun, run_id)
        if existing.status == "success" and existing.lease_token == lease_token:
            return {
                "run_id": run_id,
                "published_count": existing.published_count,
                "result_revision": existing.summary_json.get("result_revision"),
            }
        run, batch = _owned_run(db, run_id, lease_token)
        assert_run_writable(db, run_id, lease_token)
        revision = db.scalar(select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id))
        if (
            batch.result_revision != run.base_result_revision
            or revision != run.base_source_revision
        ):
            raise RunConflictError("RESULT_VERSION_CONFLICT", "来源或结果版本已变化")
        mapping = db.get(Mapping, batch.mapping_id)
        if run.mapping_digest and (
            mapping is None
            or not Path(mapping.file_path).is_file()
            or file_digest(Path(mapping.file_path)) != run.mapping_digest
        ):
            raise RunConflictError("MAPPING_CHANGED", "对照表已变化")
        count = validate_staged_result(db, run)
        staged = db.scalars(select(StagedDeviceRow).where(StagedDeviceRow.run_id == run_id))
        for chunk in batched(staged.yield_per(1000), 1000):
            keys = [row.source_device_key for row in chunk]
            active = {
                device.source_device_key: device
                for device in db.scalars(select(Device).where(Device.source_device_key.in_(keys)))
            }
            sources = {
                source.source_device_key: source
                for source in db.scalars(
                    select(SourceInput).where(SourceInput.source_device_key.in_(keys))
                )
            }
            updates, inserts, source_updates = [], [], []
            for row in chunk:
                device = active.get(row.source_device_key)
                payload = row.payload_json
                encoded = json.dumps(payload, sort_keys=True, allow_nan=False, default=str)
                if hashlib.sha256(encoded.encode()).hexdigest() != row.payload_digest:
                    raise RunConflictError("STAGED_RESULT_CHANGED", "暂存结果摘要不匹配")
                if device is not None and device.batch_id != batch.id:
                    raise RunConflictError("SOURCE_IDENTITY_CONFLICT", "器件身份属于其他批次")
                if run.kind == "recompute":
                    if device is None:
                        raise RunConflictError("SOURCE_DEVICE_MISSING", "重算器件已不存在")
                    from app.workers.reprocess_batch import _METRIC_COLUMNS

                    columns = set().union(*(_METRIC_COLUMNS[m] for m in run.config_json["metrics"]))
                    updates.append(
                        {
                            "id": device.id,
                            **{name: payload[name] for name in columns if name in payload},
                        }
                    )
                else:
                    if device is None:
                        inserts.append({"batch_id": batch.id, **payload})
                    else:
                        updates.append({"id": device.id, **payload})
                    source = sources.get(row.source_device_key)
                    if source is not None:
                        source_updates.append(
                            {"id": source.id, "current_relpath": payload.get("s_param_path")}
                        )
            if updates:
                db.bulk_update_mappings(Device, updates)
            if inserts:
                if engine.dialect.name == "postgresql":
                    # Preserve ORM render_nulls=False and consecutive key groups,
                    # but send a bounded multi-value statement to the server.
                    values = [{k: v for k, v in row.items() if v is not None} for row in inserts]
                    for _, group in groupby(values, lambda row: tuple(sorted(row))):
                        db.execute(insert(Device).values(list(group)))
                else:
                    db.bulk_insert_mappings(Device, inserts)
            if source_updates:
                db.bulk_update_mappings(SourceInput, source_updates)
        if run.kind != "recompute":
            # Indexed anti-join avoids unbounded parameters and staging rescans.
            db.execute(_unpublished_device_delete(batch.id, run_id))
        db.flush()
        old_path = batch.file_path
        if stage_dir:
            batch.file_path = stage_dir
            from app.services.file_tree_service import build_file_tree_from_disk

            build_file_tree_from_disk(db, batch, manifest=manifest, commit=False)
            from sqlalchemy import update

            from app.models import FileNode

            paths = {relpath for _, relpath in manifest}
            nodes = db.scalars(
                select(FileNode).where(FileNode.batch_id == batch.id, FileNode.node_type == "file")
            ).yield_per(1000)
            for chunk in batched(nodes, 1000):
                missing = [node.id for node in chunk if node.relpath not in paths]
                if missing:
                    db.execute(
                        update(FileNode).where(FileNode.id.in_(missing)).values(is_deleted=True)
                    )
            if old_path and old_path != stage_dir:
                db.add(
                    CleanupJob(
                        batch_id=batch.id,
                        run_id=run_id,
                        resource_kind="old_result",
                        resource_path=cleanup_path_intent(batch, old_path),
                        not_before=datetime.now(UTC)
                        + timedelta(hours=get_settings().RESULT_FILE_RETENTION_HOURS),
                    )
                )
        batch.device_count = db.scalar(
            select(func.count(Device.id)).where(Device.batch_id == batch.id)
        )
        if run.kind != "recompute":
            for field in ("f_start_ghz", "f_end_ghz", "process_type", "deembed_method"):
                if field in run.config_json:
                    setattr(batch, field, run.config_json[field])
            batch.deembedded = bool(
                db.scalar(
                    select(Device.id)
                    .where(Device.batch_id == batch.id, Device.deembedded.is_(True))
                    .limit(1)
                )
            )
        batch.result_revision += 1
        run.published_count = count
        summarize_run(db, run_id)
        run.summary_json = {**run.summary_json, "result_revision": batch.result_revision}
        if transition_task(db, run.task_id, run.id, lease_token, "running", "success") != "changed":
            raise RunConflictError("RUN_FINISH_CONFLICT", "发布时任务状态已变化")
        run.dispatch_phase, run.finished_at = "finished", datetime.now(UTC)
        task = db.get(UploadTask, run.task_id)
        task.stage, task.progress_pct, task.stage_progress_pct = "done", 100, 100
        task.finished_at = run.finished_at
        batch.active_run_id = None
        if not get_settings().KEEP_RAW_ZIP and batch.raw_zip_path:
            db.add(
                CleanupJob(
                    batch_id=batch.id,
                    run_id=run_id,
                    resource_kind="raw_zip",
                    resource_path=cleanup_path_intent(batch, batch.raw_zip_path),
                    not_before=datetime.now(UTC)
                    + timedelta(hours=get_settings().RESULT_FILE_RETENTION_HOURS),
                )
            )
        db.flush()
        run.summary_json = {
            **run.summary_json,
            "published_source_revision": db.scalar(
                select(SyncRevision.revision).where(SyncRevision.batch_id == batch.id)
            ),
        }
        result = {
            "run_id": run.id,
            "published_count": count,
            "result_revision": batch.result_revision,
        }
    # Cache maintenance is after commit; its failure must not undo visible results.
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text, update

        from app.models import DataEpoch

        try:
            with engine.begin() as connection:
                epoch = connection.scalar(select(DataEpoch.epoch).where(DataEpoch.id == 1))
                connection.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_batch_stats"))
                connection.execute(
                    update(DataEpoch)
                    .where(DataEpoch.id == 1, DataEpoch.epoch == epoch)
                    .values(stats_epoch=epoch)
                )
        except Exception:
            with Session(engine) as db:
                record_issue(
                    db,
                    run_id,
                    "STATS_REFRESH_FAILED",
                    "统计缓存刷新失败，可重新生成",
                    severity="warning",
                )
                db.commit()
    return result
