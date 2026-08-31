"""导出接口：CSV 流式 / XLSX 异步 / 下载。

优化：
- CSV 导出使用 yield_per 逐批从 DB 读取，避免一次性载入内存。
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import DbSession
from app.config import get_settings
from app.schemas.nn_export import NNExportRequest
from app.schemas.query import QueryRequest
from app.services.export_query import build_export_fields_and_stmt, select_export_rows
from app.services.nn_export import build_nn_export_stmt, get_nn_export_header, iter_nn_export_rows

log = logging.getLogger("aln")
router = APIRouter(tags=["export"])

# CSV 流式读取批次大小
_STREAM_YIELD_PER = 2000


@router.post("/export/csv")
def export_csv(req: QueryRequest, db: DbSession) -> StreamingResponse:
    fields, stmt = build_export_fields_and_stmt(req)

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

    fields, rows = select_export_rows(req, db)

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


# NN 训练数据导出：EG/AG/FL + 标量参数 + 稀疏采样点 + 完整 |Z| dB 频谱
_NN_STREAM_YIELD_PER = 500


@router.post("/export/nn-csv")
def export_nn_csv(req: NNExportRequest, db: DbSession) -> StreamingResponse:
    """导出 NN 训练 CSV。

    每行一个器件：自变量 eg/ag/fl，因变量 fs/fp/kt2/Qs/Qp/bodeQ，
    外加稀疏采样点和完整 |Z| dB 频谱。
    """
    # 先在生成器外构建/校验 SQL，确保 filter 异常能在响应开始前抛出 400。
    _, stmt = build_nn_export_stmt(req)
    header = get_nn_export_header(req)

    def gen():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=header)
        writer.writeheader()
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()

        count = 0
        for row in iter_nn_export_rows(db, req, stmt=stmt):
            writer.writerow(row)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
            count += 1
            if count % _NN_STREAM_YIELD_PER == 0:
                log.info("nn-csv 已导出 %d 行", count)

    filename = f"nn_devices_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
