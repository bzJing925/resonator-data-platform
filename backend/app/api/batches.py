"""批次接口：列表 / 详情 / 删除 / 器件列表。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import zipstream
from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import false, func, select

from app.api.deps import DEVICE_COLUMNS, DbSession
from app.models import Batch, Device, Mapping, MappingEntry, UploadTask
from app.schemas.batch import (
    BatchDetail,
    BatchListItem,
    BatchListResponse,
    BatchRenameRequest,
    BatchRenameResponse,
    BatchStats,
    RecomputeRequest,
    ReprocessResponse,
)
from app.services.batch_rename_service import (
    BatchNotFoundError as RenameBatchNotFoundError,
)
from app.services.batch_rename_service import (
    BatchRenameActiveTaskError,
    BatchRenameDuplicateError,
    BatchRenameError,
    BatchRenameInvalidNameError,
    rename_batch,
)
from app.services.batch_scope import active_batch_clause, get_active_batch
from app.services.batch_stats_service import get_batch_stats
from app.services.processing_run import RunConflictError, create_run, record_dispatch_failure
from app.workers.dispatch import ConfirmedDispatchError, dispatch_reprocess_task

router = APIRouter(prefix="/batches", tags=["batches"])

_SORT_FIELDS = {
    "uploaded_at": Batch.uploaded_at,
    "batch_no": Batch.batch_no,
    "device_count": Batch.device_count,
}

_DEVICE_SORT_FIELDS = {
    name: getattr(Device, name)
    for name in (
        "id", "original_filename", "s_param_port", "mark", "wafer", "coord", "x", "y", "pf",
        "eg", "fl", "ag", "area_um2", "fs_ghz", "fp_ghz", "zs_ohm", "zp_ohm", "qs", "qp",
        "fbode_ghz", "qs_bodeq", "qp_bodeq", "k2eff_pct", "fp2_ghz", "fs2_ghz",
    )
}


@router.get("", response_model=BatchListResponse)
def list_batches(
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 20,
    sort: str = "-uploaded_at",
) -> BatchListResponse:
    desc = sort.startswith("-")
    key = sort.lstrip("-+")
    col = _SORT_FIELDS.get(key, Batch.uploaded_at)
    order = col.desc() if desc else col.asc()

    total = db.scalar(select(func.count()).select_from(Batch).where(active_batch_clause())) or 0
    stmt = (
        select(Batch, Mapping.name)
        .join(Mapping, Batch.mapping_id == Mapping.id, isouter=True)
        .where(active_batch_clause())
        .order_by(order)
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = db.execute(stmt).all()
    items = [
        BatchListItem(
            batch_no=b.batch_no,
            mapping_name=mname,
            device_count=b.device_count,
            f_start_ghz=b.f_start_ghz,
            f_end_ghz=b.f_end_ghz,
            deembedded=b.deembedded,
            deembed_method=b.deembed_method,
            process_type=b.process_type,
            uploaded_at=b.uploaded_at,
            synced_at=b.synced_at,
            source=b.source,
        )
        for b, mname in rows
    ]
    return BatchListResponse(total=total, page=page, size=size, items=items)


@router.get("/{batch_no}", response_model=BatchDetail)
def get_batch(batch_no: str, db: DbSession) -> BatchDetail:
    get_active_batch(db, batch_no)
    # 一次 join 查询带出 mapping_name，避免 N+1
    row = db.execute(
        select(Batch, Mapping.name)
        .join(Mapping, Batch.mapping_id == Mapping.id, isouter=True)
        .where(Batch.batch_no == batch_no)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")
    batch, mapping_name = row

    wafers_rows = db.execute(
        select(Device.wafer)
        .where(Device.batch_id == batch.id, Device.wafer.is_not(None))
        .distinct()
        .order_by(Device.wafer)
    ).all()
    wafers = [int(w[0]) for w in wafers_rows if w[0] is not None]

    # ── 批次统计 ─────────────────────────────────────────────────
    stats = get_batch_stats(db, batch.id, total_dev=batch.device_count)

    return BatchDetail(
        batch_no=batch.batch_no,
        mapping_id=batch.mapping_id,
        mapping_name=mapping_name,
        device_count=batch.device_count,
        f_start_ghz=batch.f_start_ghz,
        f_end_ghz=batch.f_end_ghz,
        deembedded=batch.deembedded,
        deembed_method=batch.deembed_method,
        process_type=batch.process_type,
        file_path=batch.file_path,
        uploaded_at=batch.uploaded_at,
        uploaded_by=batch.uploaded_by,
        synced_at=batch.synced_at,
        source=batch.source,
        task_id=batch.task_id,
        wafers=wafers,
        stats=BatchStats(**stats),
    )


_ReprocessKind = Literal["reextract", "redeembed", "recompute"]


def _start_reprocess(
    db: DbSession,
    batch_no: str,
    kind: _ReprocessKind,
    metrics: list[str] | None = None,
) -> ReprocessResponse:
    batch = get_active_batch(db, batch_no)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    raw_zip = Path(batch.raw_zip_path) if batch.raw_zip_path else None

    if kind == "reextract":
        if raw_zip is None or not raw_zip.exists():
            raise HTTPException(
                status_code=400,
                detail="原始数据包已清理，无法重新解压",
            )

    batch_id = batch.id
    options = {
        "f_start_ghz": batch.f_start_ghz,
        "f_end_ghz": batch.f_end_ghz,
        "metrics": metrics or [],
        "deembed": batch.deembedded,
    }
    db.rollback()
    try:
        ticket = create_run(db.bind, batch_id, kind, config=options)
    except RunConflictError as exc:
        raise HTTPException(
            409, detail={"code": exc.code, "message": str(exc), "retryable": False, "context": {}}
        ) from exc
    task = db.get(UploadTask, ticket.task_id)
    batch = db.get(Batch, batch_id)
    try:
        celery_task_id = dispatch_reprocess_task(
            task_id=ticket.task_id,
            run_id=ticket.run_id,
            lease_token=ticket.lease_token,
            batch_no=batch.batch_no,
            mapping_id=batch.mapping_id,
            kind=kind,
            zip_path=raw_zip,
            f_start_ghz=batch.f_start_ghz,
            f_end_ghz=batch.f_end_ghz,
            deembed=batch.deembedded,
            deembed_method=batch.deembed_method,
            process_type=batch.process_type,
            metrics=metrics,
        )
        if celery_task_id is None:
            raise ConfirmedDispatchError("队列未接受任务")
    except Exception as exc:
        db.rollback()
        record_dispatch_failure(db.bind, ticket, confirmed=isinstance(exc, ConfirmedDispatchError))
        raise HTTPException(
            503,
            detail={
                "code": "DISPATCH_FAILED",
                "message": "任务投递异常，输入已保留",
                "retryable": True,
                "context": {"task_id": ticket.task_id},
            },
        ) from exc
    if celery_task_id:
        task.celery_task_id = celery_task_id
        db.commit()

    return ReprocessResponse(
        run_id=ticket.run_id,
        task_id=str(task.id),
        batch_no=batch.batch_no,
        stream_url=f"/api/tasks/{task.id}/stream",
    )


@router.delete("/{batch_no}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_no: str, db: DbSession) -> None:
    from app.services.batch_lifecycle import soft_delete

    db.rollback()
    soft_delete(db.bind, batch_no)


@router.post("/{batch_no}/rename", response_model=BatchRenameResponse)
def rename_batch_endpoint(
    batch_no: str,
    body: BatchRenameRequest,
    db: DbSession,
) -> BatchRenameResponse:
    """重命名批次号，同时重命名磁盘目录并同步相关 DB 字段。"""
    try:
        new_batch_no = rename_batch(db, batch_no, body.new_batch_no)
    except RenameBatchNotFoundError:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在") from None
    except BatchRenameDuplicateError:
        raise HTTPException(status_code=409, detail="新批次号已存在") from None
    except BatchRenameActiveTaskError:
        raise HTTPException(status_code=409, detail="该批次有进行中的任务，无法重命名") from None
    except BatchRenameInvalidNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except BatchRenameError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return BatchRenameResponse(batch_no=new_batch_no)


@router.post("/{batch_no}/reextract", response_model=ReprocessResponse)
def reextract_batch(batch_no: str, db: DbSession) -> ReprocessResponse:
    return _start_reprocess(db, batch_no, "reextract")


@router.post("/{batch_no}/redeembed", response_model=ReprocessResponse)
def redeembed_batch(batch_no: str, db: DbSession) -> ReprocessResponse:
    return _start_reprocess(db, batch_no, "redeembed")


@router.post("/{batch_no}/recompute", response_model=ReprocessResponse)
def recompute_batch(
    batch_no: str,
    body: RecomputeRequest,
    db: DbSession,
) -> ReprocessResponse:
    return _start_reprocess(db, batch_no, "recompute", metrics=body.metrics)


@router.get("/{batch_no}/devices")
def list_batch_devices(
    batch_no: str,
    db: DbSession,
    wafer: int | None = None,
    pf: str | None = None,
    massloading: str | None = None,
    sort: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    batch = get_active_batch(db, batch_no)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    conditions = [Device.batch_id == batch.id]
    if wafer is not None:
        conditions.append(Device.wafer == wafer)
    if pf is not None:
        if pf not in ("Y", "N"):
            raise HTTPException(status_code=400, detail="pf 必须为 Y / N")
        conditions.append(Device.pf == pf)
    if massloading is not None:
        if massloading not in ("Y", "N"):
            raise HTTPException(status_code=400, detail="massloading 必须为 Y / N")
        if batch.mapping_id is None:
            conditions.append(false())
        else:
            marks = db.scalars(
                select(MappingEntry.mark).where(
                    MappingEntry.mapping_id == batch.mapping_id,
                    MappingEntry.has_pf == (massloading == "Y"),
                )
            ).all()
            if marks:
                conditions.append(Device.mark.in_(marks))
            else:
                conditions.append(false())
    total = db.scalar(select(func.count()).select_from(Device).where(*conditions)) or 0
    if sort:
        desc = sort.startswith("-")
        key = sort.lstrip("-+")
        col = _DEVICE_SORT_FIELDS.get(key)
        if col is None:
            raise HTTPException(status_code=400, detail=f"不支持的排序字段: {key}")
        order_cols = [col.desc() if desc else col.asc(), Device.id]
    else:
        # 默认按源文件分组排序，前端按原始文件名合并相邻行
        order_cols = [Device.original_filename, Device.id]
    stmt = (
        select(Device).where(*conditions).order_by(*order_cols)
        .offset((page - 1) * size).limit(size)
    )
    devices = db.scalars(stmt).all()
    rows = [{col: getattr(d, col) for col in DEVICE_COLUMNS if col != "batch_id"} for d in devices]

    # 批量补充每个 device 对应的对照表 description
    if devices and batch.mapping_id is not None:
        marks = [d.mark for d in devices if d.mark]
        mark_to_desc: dict[str, str | None] = {}
        if marks:
            entries = db.scalars(
                select(MappingEntry).where(
                    MappingEntry.mapping_id == batch.mapping_id,
                    MappingEntry.mark.in_(marks),
                )
            ).all()
            mark_to_desc = {e.mark: e.description for e in entries}
        for r, d in zip(rows, devices, strict=True):
            r["description"] = mark_to_desc.get(d.mark) if d.mark else None

    for r, d in zip(rows, devices, strict=True):
        r["batch_no"] = batch_no
        r["id"] = d.id

    return {"total": total, "page": page, "size": size, "items": rows}


@router.get("/{batch_no}/download-zip")
def download_batch_zip(
    batch_no: str,
    db: DbSession,
) -> Response:
    """下载该批次全部 snp 文件的打包 zip（自动选取）。"""
    batch = get_active_batch(db, batch_no)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    from app.services.file_tree_service import batch_files_dir

    base_dir = batch_files_dir(batch)
    if not base_dir.exists():
        raise HTTPException(status_code=404, detail="批次解压目录不存在")

    allowed_suffixes = {".s1p", ".s2p", ".snp"}
    selected = [
        (p, str(p.relative_to(base_dir)))
        for p in sorted(base_dir.rglob("*"))
        if p.is_file() and p.suffix.lower() in allowed_suffixes
    ]
    if not selected:
        raise HTTPException(status_code=404, detail="没有可下载的 snp 文件")

    zs = zipstream.ZipStream(compress_type=zipstream.ZIP_DEFLATED)
    for target, arcname in selected:
        zs.add_path(str(target), arcname=arcname, recurse=False)

    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{batch_no}.zip"'},
    )


@router.get("/{batch_no}/deletion-impact")
def batch_deletion_impact(batch_no: str, db: DbSession):
    from app.services.batch_lifecycle import deletion_impact

    return deletion_impact(db, get_active_batch(db, batch_no))


@router.post("/{batch_no}/restore")
def restore_batch(batch_no: str, db: DbSession):
    from app.services.batch_lifecycle import restore

    db.rollback()
    return restore(db.bind, batch_no)
