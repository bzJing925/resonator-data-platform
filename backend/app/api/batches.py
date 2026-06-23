"""批次接口：列表 / 详情 / 删除 / 器件列表。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any

import zipstream
from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete, func, select

from app.api.deps import DEVICE_COLUMNS, DbSession
from app.config import get_settings
from app.models import Batch, Device, Mapping
from app.schemas.batch import BatchDetail, BatchListItem, BatchListResponse, BatchStats

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

    # ── 优先从物化视图读取批次统计（避免对 devices 大表实时聚合）────
    fs_mean = fs_median = pass_rate = None
    total_dev = batch.device_count  # 已冗余存储，无需再 count
    try:
        from sqlalchemy import text

        mv_rows = db.execute(
            text("""
                SELECT
                    COALESCE(SUM(pass_count), 0) AS pass_count,
                    AVG(avg_fs_ghz) AS fs_mean,
                    AVG(median_fs_ghz) AS fs_median
                FROM mv_batch_stats
                WHERE batch_id = :batch_id
            """),
            {"batch_id": batch.id},
        ).mappings().all()
        if mv_rows:
            mv = mv_rows[0]
            pass_count = int(mv["pass_count"] or 0)
            fs_mean = mv["fs_mean"]
            fs_median = mv["fs_median"]
            pass_rate = (pass_count / total_dev) if total_dev > 0 else None
    except Exception:
        # 物化视图不存在或查询失败时回退到实时聚合
        fs_mean = db.scalar(select(func.avg(Device.fs_ghz)).where(Device.batch_id == batch.id))
        fs_median = db.scalar(
            select(func.percentile_cont(0.5).within_group(Device.fs_ghz.asc())).where(
                Device.batch_id == batch.id
            )
        )
        pass_count = db.scalar(
            select(func.count())
            .select_from(Device)
            .where(Device.batch_id == batch.id, Device.pf == "Y")
        ) or 0
        pass_rate = (pass_count / total_dev) if total_dev > 0 else None

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
        wafers=wafers,
        stats=BatchStats(
            fs_ghz_mean=float(fs_mean) if fs_mean is not None else None,
            fs_ghz_median=float(fs_median) if fs_median is not None else None,
            pass_rate=pass_rate,
        ),
    )


@router.delete("/{batch_no}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_no: str, db: DbSession) -> None:
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    settings = get_settings()
    files_dir = settings.files_dir / batch_no
    zip_path = Path(batch.file_path) if batch.file_path else None

    db.execute(delete(Batch).where(Batch.id == batch.id))
    db.commit()

    if files_dir.exists():
        try:
            shutil.rmtree(files_dir)
        except Exception:
            pass
    if zip_path and zip_path.exists():
        try:
            zip_path.unlink()
        except Exception:
            pass


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
        select(Device)
        .where(*conditions)
        .order_by(Device.id)
        .offset((page - 1) * size)
        .limit(size)
    )
    devices = db.scalars(stmt).all()
    rows = [
        {col: getattr(d, col) for col in DEVICE_COLUMNS if col != "batch_id"}
        for d in devices
    ]
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
