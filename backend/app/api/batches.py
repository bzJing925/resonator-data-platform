"""批次接口：列表 / 详情 / 删除 / 器件列表。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import zipstream
from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import DEVICE_COLUMNS, DbSession
from app.config import get_settings
from app.models import Batch, Device, Mapping, UploadTask
from app.schemas.batch import (
    BatchDetail,
    BatchListItem,
    BatchListResponse,
    BatchStats,
    RecomputeRequest,
    ReprocessResponse,
)
from app.services.batch_stats_service import get_batch_stats
from app.services.cleanup_service import delete_batch_and_files
from app.workers.dispatch import dispatch_reprocess_task

router = APIRouter(prefix="/batches", tags=["batches"])

_SORT_FIELDS = {
    "uploaded_at": Batch.uploaded_at,
    "batch_no": Batch.batch_no,
    "device_count": Batch.device_count,
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

    total = db.scalar(select(func.count()).select_from(Batch)) or 0
    stmt = (
        select(Batch, Mapping.name)
        .join(Mapping, Batch.mapping_id == Mapping.id, isouter=True)
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
        )
        for b, mname in rows
    ]
    return BatchListResponse(total=total, page=page, size=size, items=items)


@router.get("/{batch_no}", response_model=BatchDetail)
def get_batch(batch_no: str, db: DbSession) -> BatchDetail:
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
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    raw_zip = Path(batch.raw_zip_path) if batch.raw_zip_path else None

    if kind == "reextract":
        if raw_zip is None or not raw_zip.exists():
            raise HTTPException(
                status_code=400,
                detail="原始数据包已清理，无法重新解压",
            )

    task = db.get(UploadTask, batch.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="批次关联任务不存在")
    if task.status in ("pending", "running"):
        raise HTTPException(status_code=409, detail="该批次已有进行中的任务")

    task.status = "pending"
    task.kind = kind
    task.stage = {"reextract": "extract", "redeembed": "deembed", "recompute": "metrics"}[kind]
    task.progress_pct = 0
    task.stage_progress_pct = 0
    task.progress_msg = "排队中"
    task.error_msg = None
    task.cancelled_at = None
    task.finished_at = None
    db.commit()

    celery_task_id = dispatch_reprocess_task(
        task_id=task.id,
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
    if celery_task_id:
        task.celery_task_id = celery_task_id
        db.commit()

    return ReprocessResponse(
        task_id=str(task.id),
        batch_no=batch.batch_no,
        stream_url=f"/api/tasks/{task.id}/stream",
    )


@router.delete("/{batch_no}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_no: str, db: DbSession) -> None:
    if not delete_batch_and_files(db, batch_no):
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")


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
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    conditions = [Device.batch_id == batch.id]
    if wafer is not None:
        conditions.append(Device.wafer == wafer)
    if pf is not None:
        if pf not in ("Y", "N"):
            raise HTTPException(status_code=400, detail="pf 必须为 Y / N")
        conditions.append(Device.pf == pf)

    total = db.scalar(select(func.count()).select_from(Device).where(*conditions)) or 0
    stmt = (
        select(Device).where(*conditions).order_by(Device.id).offset((page - 1) * size).limit(size)
    )
    devices = db.scalars(stmt).all()
    rows = [{col: getattr(d, col) for col in DEVICE_COLUMNS if col != "batch_id"} for d in devices]
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
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    settings = get_settings()
    base_dir = settings.files_dir / batch_no
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
