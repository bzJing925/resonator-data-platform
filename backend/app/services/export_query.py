"""导出/查询语句构建服务。

把 export.py 中重复的字段默认值、字段校验、过滤、排序、limit 上限逻辑集中到一处。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.api.deps import ALLOWED_QUERY_FIELDS
from app.api.query import _build_filters, _resolve_column
from app.models import Batch, Device
from app.schemas.query import QueryRequest

EXPORT_HARD_CAP = 200_000

# 导出默认字段
DEFAULT_EXPORT_FIELDS: list[str] = [
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


def _validate_fields(fields: list[str]) -> None:
    for f in fields:
        if f not in ALLOWED_QUERY_FIELDS:
            raise HTTPException(status_code=400, detail=f"未知字段: {f}")


def build_export_fields_and_stmt(
    req: QueryRequest,
) -> tuple[list[str], Any]:
    """构建导出用的字段列表和 SELECT statement。

    返回 (fields, stmt)，stmt 已包含 select_from/join/where/order_by/limit。
    """
    fields = req.fields or list(DEFAULT_EXPORT_FIELDS)
    _validate_fields(fields)

    where = _build_filters(req.filters)
    select_cols: list[ColumnElement[Any]] = [_resolve_column(f).label(f) for f in fields]
    # 必须显式 select_from(Device)：当用户只选 Batch 表字段（如 batch_no）时，
    # SQLAlchemy 无法从 select_cols 推断左侧表，会抛 InvalidRequestError。
    stmt = select(*select_cols).select_from(Device).join(Batch, Device.batch_id == Batch.id)
    if where:
        stmt = stmt.where(*where)
    if req.order_by is not None:
        order_key = req.order_by.lstrip("-+")
        if order_key not in ALLOWED_QUERY_FIELDS:
            raise HTTPException(status_code=400, detail=f"未知排序字段: {order_key}")
        order_col = _resolve_column(order_key)
        stmt = stmt.order_by(order_col.desc() if req.order_by.startswith("-") else order_col.asc())
    stmt = stmt.limit(min(req.limit, EXPORT_HARD_CAP))
    return fields, stmt


def select_export_rows(req: QueryRequest, db: Session) -> tuple[list[str], list[dict[str, Any]]]:
    """执行导出查询并返回字段列表与数据行。"""
    fields, stmt = build_export_fields_and_stmt(req)
    rows = db.execute(stmt).mappings().all()
    return fields, [dict(r) for r in rows]
