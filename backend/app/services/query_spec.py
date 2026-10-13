"""Shared SQL field/filter/order validation and canonical query specifications."""
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, and_, inspect, null, or_

from app.models import Batch, Device
from app.schemas.analysis import QuerySpec

DEVICE_COLUMNS = {column.key for column in inspect(Device).c}
ALLOWED_QUERY_FIELDS = DEVICE_COLUMNS | {"batch_no"}
FILTER_MAX_DEPTH = 32

def _resolve_column(name: str) -> ColumnElement[Any]:
    if name == "batch_no":
        return Batch.batch_no
    if name in DEVICE_COLUMNS:
        return getattr(Device, name)
    raise HTTPException(status_code=400, detail=f"未知字段: {name}")


def _escape_like(val: str) -> str:
    """转义 SQL LIKE 通配符 % 和 _ 与转义符 \\，防止用户输入被解释为通配模式。"""
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _leaf_clause(field: str, op: str, val: Any) -> ColumnElement[bool]:
    col = _resolve_column(field)
    if op == "in":
        if not isinstance(val, list) or not val:
            raise HTTPException(status_code=400, detail=f"{field}.in 必须是非空列表")
        return col.in_(val)
    if op == "not_in":
        if not isinstance(val, list) or not val:
            raise HTTPException(status_code=400, detail=f"{field}.not_in 必须是非空列表")
        return ~col.in_(val)
    if op == "eq":
        return col == val
    if op == "neq":
        return col != val
    if op == "gte":
        return col >= val
    if op == "gt":
        return col > val
    if op == "lte":
        return col <= val
    if op == "lt":
        return col < val
    if op == "between":
        if not isinstance(val, list) or len(val) != 2:
            raise HTTPException(
                status_code=400, detail=f"{field}.between 必须为长度 2 的列表 [lo, hi]"
            )
        lo, hi = val
        return and_(col >= lo, col <= hi)
    if op == "is_null":
        return col.is_(None)
    if op == "not_null":
        return col.isnot(None)
    if op == "like":
        if not isinstance(val, str):
            raise HTTPException(status_code=400, detail=f"{field}.like 必须为字符串")
        return col.like(_escape_like(val), escape="\\")
    if op == "contains":
        if not isinstance(val, str):
            raise HTTPException(status_code=400, detail=f"{field}.contains 必须为字符串")
        return col.like(f"%{_escape_like(val)}%", escape="\\")
    raise HTTPException(status_code=400, detail=f"不支持的操作符: {field}.{op}")


def _build_filter_clause(name: str, spec: Any) -> ColumnElement[bool]:
    """旧版按字段聚合的格式：
    - list  →  IN
    - dict  →  多操作符 AND
    - 标量  →  等值
    """
    if isinstance(spec, list):
        if not spec:
            raise HTTPException(status_code=400, detail=f"过滤器 {name} 列表不能为空")
        return _leaf_clause(name, "in", spec)
    if isinstance(spec, dict):
        if not spec:
            raise HTTPException(status_code=400, detail=f"过滤器 {name} 操作不能为空")
        clauses = [_leaf_clause(name, op, val) for op, val in spec.items()]
        return and_(*clauses) if len(clauses) > 1 else clauses[0]
    return _resolve_column(name) == spec


def _build_node(node: dict[str, Any], depth: int = 0) -> ColumnElement[bool]:
    """新版树形格式：
       - 组节点  {"op": "and"|"or", "children": [...]}
       - 叶节点  {"field": str, "op": str, "value": Any}

    depth 上限防止深度嵌套触发 Python RecursionError → 500 DoS。
    """
    if depth > FILTER_MAX_DEPTH:
        raise HTTPException(
            status_code=400,
            detail=f"filter 树嵌套过深（上限 {FILTER_MAX_DEPTH} 层）",
        )
    if not isinstance(node, dict):
        raise HTTPException(status_code=400, detail="filter 节点必须是对象")
    if "field" in node:
        field = node["field"]
        op = node.get("op", "eq")
        val = node.get("value")
        return _leaf_clause(field, op, val)
    if "children" in node:
        op = (node.get("op") or "and").lower()
        children = node["children"]
        if not isinstance(children, list):
            raise HTTPException(status_code=400, detail="children 必须是列表")
        if not children:
            # 空组视作恒真，方便 UI 在用户尚未配置任何条件时也能发请求。
            return null().is_(None)
        clauses = [_build_node(c, depth + 1) for c in children]
        if op == "or":
            return or_(*clauses)
        if op == "and":
            return and_(*clauses)
        raise HTTPException(status_code=400, detail=f"不支持的组合操作符: {op}")
    raise HTTPException(status_code=400, detail="filter 节点必须含 field 或 children")


def _is_tree_filter(filters: Any) -> bool:
    return (
        isinstance(filters, dict)
        and "children" in filters
        and isinstance(filters.get("children"), list)
    )


def _build_filters(filters: Any) -> list[ColumnElement[bool]]:
    if not filters:
        return []
    if _is_tree_filter(filters):
        return [_build_node(filters)]
    if isinstance(filters, dict):
        return [_build_filter_clause(k, v) for k, v in filters.items()]
    raise HTTPException(status_code=400, detail="filters 必须是字典")


def _validate_field_set(fields: list[str]) -> None:
    for f in fields:
        if f not in ALLOWED_QUERY_FIELDS:
            raise HTTPException(status_code=400, detail=f"未知字段: {f}")



def stable_order(order_by: str | None):
    if order_by is None:
        return [Device.id.asc()]
    key = order_by.lstrip("-+")
    column = _resolve_column(key)
    order = column.desc() if order_by.startswith("-") else column.asc()
    return [order] if key == "id" else [order, Device.id.asc()]


def normalize_filters(filters: dict) -> dict:
    _build_filters(filters)
    if _is_tree_filter(filters):
        return filters
    children = []
    for field, value in filters.items():
        operations = (value if isinstance(value, dict)
                      else {"in" if isinstance(value, list) else "eq": value})
        children.extend({"field": field, "op": op, "value": val} for op, val in operations.items())
    return {"op": "and", "children": children}


def query_spec(filters: dict, fields: list[str], order_by: str | None = None) -> QuerySpec:
    _validate_field_set(fields)
    stable_order(order_by)
    return QuerySpec(filters=normalize_filters(filters), fields=fields, order_by=order_by)
