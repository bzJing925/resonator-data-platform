"""上传业务逻辑：HTTP 上传与目录监听共用。

抽离后，create_upload 只负责接收流并保存到本地 zip；
本模块负责创建 UploadTask + Batch 并投递 Celery chain。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Batch, Device, Mapping, SourceInput, UploadTask
from app.services.processing_run import create_run, record_dispatch_failure
from app.services.source_identity import (
    assign_batch_identity,
    register_prechecked_sources,
    validate_uuid,
)
from app.services.upload_precheck import build_precheck_report
from app.workers.dispatch import ConfirmedDispatchError, dispatch_batch_task

logger = logging.getLogger(__name__)

_DUP_SUFFIX_RE = re.compile(r"^(?P<base>.+)-(?P<suffix>\d+)$")


class MappingNotFoundError(Exception):
    """请求的对照表 ID 不存在。"""


class TaskDispatchError(Exception):
    """Celery 任务投递失败（如 broker 不可达）。"""


def _split_batch_no(batch_no: str) -> tuple[str, int]:
    """拆分 batch_no 为 base 和末尾数字后缀。

    - ``#17`` -> ("#17", 0)
    - ``#17-1`` -> ("#17", 1)
    - ``#17-abc`` -> ("#17-abc", 0)
    """
    m = _DUP_SUFFIX_RE.match(batch_no)
    if m is None:
        return batch_no, 0
    return m.group("base"), int(m.group("suffix"))


def _generate_unique_batch_no(db: Session, batch_no: str) -> str:
    """当 batch_no 已存在时，自动追加递增数字后缀生成唯一名称。"""
    existing = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if existing is None:
        return batch_no

    base, current_suffix = _split_batch_no(batch_no)
    suffix = current_suffix
    candidate = batch_no
    while existing is not None:
        suffix += 1
        candidate = f"{base}-{suffix}"
        existing = db.scalar(select(Batch).where(Batch.batch_no == candidate))
    logger.info("batch_no %s 已存在，自动重命名为 %s", batch_no, candidate)
    return candidate


def _dispatch_chain(
    task: UploadTask,
    zip_path: Path,
    batch_no: str,
    mapping_id: int,
    *,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
    run_id: str | None = None,
    lease_token: str | None = None,
) -> str | None:
    """投递 extract_batch → compute_batch，返回 celery_task_id 或 None。"""
    celery_task_id = dispatch_batch_task(
        task_id=task.id,
        run_id=run_id,
        lease_token=lease_token,
        zip_path=zip_path,
        batch_no=batch_no,
        mapping_id=mapping_id,
        f_start_ghz=f_start_ghz,
        f_end_ghz=f_end_ghz,
        deembed=deembed,
        deembed_method=deembed_method,
        process_type=process_type,
    )
    if celery_task_id is None:
        raise ConfirmedDispatchError("队列未接受任务")
    return celery_task_id


def create_batch_and_dispatch(
    db: Session,
    zip_path: Path,
    batch_no: str,
    mapping_id: int,
    *,
    source: Literal["http", "watch", "sync"] = "http",
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
    uploaded_by: str = "anonymous",
    source_ref: dict | None = None,
    source_inputs: list | None = None,
) -> UploadTask | None:
    """创建 UploadTask + Batch 并投递处理链。

    若 batch_no 已存在，会自动追加递增数字后缀生成唯一名称。
    返回 None 仅表示发生意外的内部失败（调用方应视为错误）。
    """
    mapping = db.get(Mapping, mapping_id)
    if mapping is None:
        raise MappingNotFoundError(f"对照表 {mapping_id} 不存在")

    directory = zip_path.parent / f"precheck-{uuid4()}"
    report = build_precheck_report(
        zip_path,
        directory,
        {
            "f_start_ghz": f_start_ghz,
            "f_end_ghz": f_end_ghz,
            "deembed": deembed,
            "deembed_method": deembed_method,
            "process_type": process_type,
        },
        Path(mapping.file_path),
    )
    if report["fatal_count"]:
        from fastapi import HTTPException

        raise HTTPException(
            422,
            {
                "code": "PRECHECK_FATAL",
                "message": "输入未通过预检查",
                "retryable": False,
                "context": {"issues": report["issues"]},
            },
        )

    transferred = None
    if source_ref is not None or source_inputs is not None:
        from app.services.source_transfer import validate_transfer
        transferred = validate_transfer(source_ref, source_inputs, report)
    unique_batch_no = _generate_unique_batch_no(db, batch_no)

    batch = Batch(
        batch_no=unique_batch_no,
        mapping_id=mapping_id,
        f_start_ghz=f_start_ghz,
        f_end_ghz=f_end_ghz,
        deembedded=False,
        deembed_method=deembed_method if deembed else "default",
        process_type=process_type,
        file_path=str(directory),
        raw_zip_path=str(zip_path),
        source=source,
        device_count=0,
        uploaded_by=uploaded_by,
    )
    if transferred:
        batch.origin_id, batch.source_batch_id = (transferred[0]['origin_id'],
                                                  transferred[0]['source_batch_id'])
    assign_batch_identity(db, batch)
    db.add(batch)
    db.flush()
    batch_id = batch.id
    if transferred:
        from app.models import SyncRevision
        from app.services.source_identity import set_replica_revision
        from app.services.source_transfer import register_transferred_inputs
        register_transferred_inputs(db, batch, transferred[1])
        current = db.get(SyncRevision, batch_id).revision
        set_replica_revision(db, batch_id, max(current, transferred[0]['source_revision']))
    else:
        register_prechecked_sources(db, batch, report)
    db.commit()
    db.rollback()
    ticket = create_run(
        db.bind,
        batch_id,
        "upload",
        config={
            "f_start_ghz": f_start_ghz,
            "f_end_ghz": f_end_ghz,
            "deembed": deembed,
            "deembed_method": deembed_method,
            "process_type": process_type,
            "prepared_dir": str(directory),
            "prepared_file_names": [
                i["relpath"] for i in report["items"] if i["status"] == "success"
            ],
            "precheck_report": report,
            "source_transfer_inputs": transferred[1] if transferred else [],
        },
    )
    task = db.get(UploadTask, ticket.task_id)

    try:
        celery_task_id = _dispatch_chain(
            task,
            zip_path,
            unique_batch_no,
            mapping_id,
            f_start_ghz=f_start_ghz,
            f_end_ghz=f_end_ghz,
            deembed=deembed,
            deembed_method=deembed_method,
            process_type=process_type,
            run_id=ticket.run_id,
            lease_token=ticket.lease_token,
        )
    except Exception as exc:
        db.rollback()
        record_dispatch_failure(db.bind, ticket, confirmed=isinstance(exc, ConfirmedDispatchError))
        raise TaskDispatchError("任务投递失败；输入与记录已保留") from exc

    if celery_task_id:
        task.celery_task_id = celery_task_id
        db.commit()

    return task


def create_table_batch(
    db: Session,
    table_path: Path,
    batch_no: str,
    mapping_id: int,
    *,
    deembedded: bool = False,
    uploaded_by: str = "anonymous",
) -> UploadTask:
    """导入已提取参数表格，不经过 snp 解压/计算流水线。

    表格列契约与分享包上传器完全一致，故桌面端和网页版会看到同一套字段。
    """
    mapping = db.get(Mapping, mapping_id)
    if mapping is None:
        raise MappingNotFoundError(f"对照表 {mapping_id} 不存在")

    import json

    from app.services.processing_run import claim_run, finish_run

    directory = table_path.parent / f"precheck-{uuid4()}"
    report = build_precheck_report(
        table_path, directory, {"deembed": deembedded}, Path(mapping.file_path)
    )
    if report["fatal_count"]:
        from fastapi import HTTPException

        raise HTTPException(
            422,
            {
                "code": "PRECHECK_FATAL",
                "message": "输入未通过预检查",
                "retryable": False,
                "context": {"issues": report["issues"]},
            },
        )
    rows = json.loads((directory / "table-rows.json").read_text())
    from app.core.mapping import load_mapping

    mapping_dict = load_mapping(mapping.file_path)
    unique_batch_no = _generate_unique_batch_no(db, batch_no)
    batch = Batch(
        batch_no=unique_batch_no,
        mapping_id=mapping_id,
        deembedded=False,
        deembed_method="default",
        process_type=report["table_stats"]["process_type"],
        file_path=str(table_path),
        raw_zip_path=str(table_path),
        source="table",
        device_count=0,
        uploaded_by=uploaded_by,
    )
    assign_batch_identity(db, batch)
    db.add(batch)
    db.commit()
    batch_id = batch.id
    register_prechecked_sources(db, batch, report)
    db.commit()
    db.rollback()
    ticket = create_run(
        db.bind, batch_id, "upload",
        config={"precheck_report": report, "prepared_dir": str(directory)},
    )
    claimed = claim_run(
        db.bind,
        ticket.run_id,
        ticket.lease_token,
        "compute",
        executor_id="synchronous-table-import",
    )
    try:
        from app.workers.run_execution import run_heartbeat

        with run_heartbeat(db.bind, claimed):
            for index, row in enumerate(rows, 1):
                values = {
                    key: row.get(key) for key in Device.__table__.columns.keys() if key in row
                }
                values["batch_id"] = batch_id
                values["matched"] = row.get("mark") in mapping_dict
                values["s_param_port"] = "S22" if values.get("s_param_port") == "S22" else "S11"
                values["source_device_key"] = db.scalar(
                    select(SourceInput.source_device_key).where(
                        SourceInput.batch_id == batch_id,
                        SourceInput.member_key == f"row/{index}",
                        SourceInput.port_key == values["s_param_port"],
                    )
                ) or str(uuid4())
                validate_uuid(values["source_device_key"])
                values["pf"] = (
                    "Y" if str(values.get("pf") or "").upper() in {"Y", "1", "TRUE"} else "N"
                )
                values["deembedded"] = False
                from app.services.result_publish import stage_device_rows

                stage_device_rows(db, claimed.run_id, claimed.lease_token, [values])
                from app.services.processing_report import record_device_result

                record_device_result(
                    db,
                    claimed.run_id,
                    f"row/{index}",
                    values["s_param_port"],
                    result=values,
                    source_device_key=values["source_device_key"],
                    unit_kind="table_row",
                )
                if index % 1000 == 0:
                    db.commit()
            db.commit()
        from app.services.result_publish import publish_run

        publish_run(db.bind, claimed.run_id, claimed.lease_token)
    except Exception:
        db.rollback()
        finish_run(
            db.bind, claimed.run_id, claimed.lease_token, "failed", error_code="TABLE_IMPORT_FAILED"
        )
        raise
    return db.get(UploadTask, ticket.task_id)


def ensure_default_mapping(db: Session) -> int | None:
    """返回第一个 mapping 的 id；无 mapping 返回 None。"""
    mapping = db.scalar(select(Mapping).order_by(Mapping.id).limit(1))
    return mapping.id if mapping else None


def settings() -> Settings:
    return get_settings()
