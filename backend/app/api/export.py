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

from app.api.deps import DbSession
from app.config import get_settings
from app.schemas.query import QueryRequest
from app.services.export_query import build_export_fields_and_stmt, select_export_rows

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
