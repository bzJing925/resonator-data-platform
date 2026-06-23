"""导出接口：CSV 流式 / XLSX 异步 / 下载。

优化：
- CSV 导出使用 yield_per 逐批从 DB 读取，避免一次性载入内存。
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.api.deps import ALLOWED_QUERY_FIELDS, DbSession
from app.api.query import _build_filters, _resolve_column
from app.config import get_settings
from app.models import Batch, Device
from app.schemas.query import QueryRequest

router = APIRouter(tags=["export"])

EXPORT_HARD_CAP = 200_000
# CSV 流式读取批次大小
_STREAM_YIELD_PER = 2000


def _select_rows(req: QueryRequest, db: Session) -> tuple[list[str], list[dict[str, Any]]]:
    fields = req.fields or [
        "id",
        "batch_no",
        "wafer",
        "coord",
        "x",
        "y",
        "fs_ghz",
        "qs",
        "k2eff_pct",
    ]
    for f in fields:
        if f not in ALLOWED_QUERY_FIELDS:
            raise HTTPException(status_code=400, detail=f"未知字段: {f}")

    where = _build_filters(req.filters)
    select_cols: list[ColumnElement[Any]] = [_resolve_column(f).label(f) for f in fields]
    # 必须显式 select_from(Device)：当用户只选 Batch 表的字段（如 batch_no）时，
    # SQLAlchemy 无法从 select_cols 推断左侧表，会抛 InvalidRequestError。
    stmt = select(*select_cols).select_from(Device).join(Batch, Device.batch_id == Batch.id)
    if where:
        stmt = stmt.where(*where)
    if req.order_by is not None:
        order_key = req.order_by.lstrip("-+")
        if order_key not in ALLOWED_QUERY_FIELDS:
            raise HTTPException(status_code=400, detail=f"未知排序字段: {order_key}")
        order_col = _resolve_column(order_key)
        stmt = stmt.order_by(
            order_col.desc() if req.order_by.startswith("-") else order_col.asc()
        )
    stmt = stmt.limit(min(req.limit, EXPORT_HARD_CAP))

    rows = db.execute(stmt).mappings().all()
    return fields, [dict(r) for r in rows]


def _build_export_stmt(req: QueryRequest):
    """构建导出用的 SELECT statement（不含 limit/execution）。"""
    fields = req.fields or [
        "id", "batch_no", "wafer", "coord", "x", "y", "fs_ghz", "qs", "k2eff_pct",
    ]
    for f in fields:
        if f not in ALLOWED_QUERY_FIELDS:
            raise HTTPException(status_code=400, detail=f"未知字段: {f}")

    where = _build_filters(req.filters)
    select_cols: list[ColumnElement[Any]] = [_resolve_column(f).label(f) for f in fields]
    stmt = select(*select_cols).select_from(Device).join(Batch, Device.batch_id == Batch.id)
    if where:
        stmt = stmt.where(*where)
    if req.order_by is not None:
        order_key = req.order_by.lstrip("-+")
        if order_key not in ALLOWED_QUERY_FIELDS:
            raise HTTPException(status_code=400, detail=f"未知排序字段: {order_key}")
        order_col = _resolve_column(order_key)
        stmt = stmt.order_by(
            order_col.desc() if req.order_by.startswith("-") else order_col.asc()
        )
    stmt = stmt.limit(min(req.limit, EXPORT_HARD_CAP))
    return fields, stmt


@router.post("/export/csv")
def export_csv(req: QueryRequest, db: DbSession) -> StreamingResponse:
    fields, stmt = _build_export_stmt(req)

    def gen():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()

        # 真流式：yield_per 逐批从 PostgreSQL server-side cursor 读取
        result = db.execute(stmt).mappings().yield_per(_STREAM_YIELD_PER)
        for row in result:
            writer.writerow({k: row.get(k) for k in fields})
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    filename = f"devices_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export/xlsx")
def export_xlsx(req: QueryRequest, db: DbSession) -> dict[str, Any]:
    settings = get_settings()
    settings.exports_dir.mkdir(parents=True, exist_ok=True)

    fields, rows = _select_rows(req, db)

    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="openpyxl 未安装") from exc

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("devices")
    ws.append(fields)
    for r in rows:
        ws.append([r.get(k) for k in fields])

    export_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"devices_{export_id}.xlsx"
    out_path = settings.exports_dir / filename
    wb.save(str(out_path))

    return {
        "id": export_id,
        "filename": filename,
        "rows": len(rows),
        "download_url": f"/api/exports/{export_id}",
    }


@router.get("/exports/{export_id}")
def download_export(export_id: str) -> FileResponse:
    settings = get_settings()
    if "/" in export_id or "\\" in export_id or ".." in export_id:
        raise HTTPException(status_code=400, detail="非法 export_id")

    matches = list(settings.exports_dir.glob(f"devices_{export_id}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail=f"导出文件 {export_id} 不存在或已过期")
    path: Path = matches[0]
    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/octet-stream",
    )
