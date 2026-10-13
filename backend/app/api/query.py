"""查询接口：跨批次器件查询 / 聚合 / 字段元数据。

优化：
- 热点查询结果走 Redis 缓存（5 分钟 TTL），减少重复 DB 压力。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, distinct, func, null, select

from app.api.deps import ALLOWED_QUERY_FIELDS, DbSession
from app.models import Batch, Device
from app.schemas.query import (
    AggregateRequest,
    AggregateResponse,
    QueryRequest,
    QueryResponse,
)
from app.services.batch_scope import active_batch_clause
from app.services.data_revision import cache_version
from app.services.field_metadata import NUMERIC_UNITS
from app.services.query_manifest import (
    make_manifest,
    matching_sources,
    scope_version,
    snapshot_query,
)
from app.services.query_spec import (
    FILTER_MAX_DEPTH as FILTER_MAX_DEPTH,
)
from app.services.query_spec import (
    _build_filter_clause as _build_filter_clause,
)
from app.services.query_spec import (
    _build_filters,
    _resolve_column,
    _validate_field_set,
    query_spec,
    stable_order,
)
from app.services.query_spec import (
    _build_node as _build_node,
)
from app.services.query_spec import (
    _escape_like as _escape_like,
)
from app.services.query_spec import (
    _is_tree_filter as _is_tree_filter,
)
from app.services.query_spec import (
    _leaf_clause as _leaf_clause,
)
from app.services.redis_service import get_redis_client

log = logging.getLogger("aln")
router = APIRouter(prefix="/query", tags=["query"])

LIMIT_HARD_CAP = 200_000
# filter 树深度上限。一个真实 UI 极少超过 ~5 层；32 给手写 JSON 留充足余量，
# 又能挡住恶意嵌套（Python 默认递归上限约 1000）触发 RecursionError → 500。

# 查询缓存 TTL（秒）
_QUERY_CACHE_TTL = 300


def _query_cache_key(
    prefix: str, req: QueryRequest | AggregateRequest, version: tuple[str, int] | None = None,
) -> str:
    """生成查询缓存 key。基于请求体 JSON 的 SHA256 前 16 位。"""
    payload = req.model_dump_json()
    h = hashlib.sha256(payload.encode()).hexdigest()[:16]
    suffix = f":{version[0]}:{version[1]}" if version is not None else ""
    return f"aln:query:{prefix}:{h}{suffix}"


def _cache_get(key: str) -> dict[str, Any] | None:
    r = get_redis_client()
    if r is None:
        return None
    try:
        raw = r.get(key)
        if raw:
            return json.loads(raw)
    except Exception as exc:
        log.warning("查询缓存读取失败: %s", exc)
    return None


def _cache_set(key: str, value: dict[str, Any], ttl: int = _QUERY_CACHE_TTL) -> None:
    r = get_redis_client()
    if r is None:
        return
    try:
        r.setex(key, ttl, json.dumps(value, default=str))
    except Exception as exc:
        log.warning("查询缓存写入失败: %s", exc)


def _aggregate_expr(field: str, op: str, *, dialect: str | None = None) -> ColumnElement[Any]:
    col = _resolve_column(field)
    if op == "count":
        return func.count(col)
    if op == "min":
        return func.min(col)
    if op == "max":
        return func.max(col)
    if op == "avg":
        return func.avg(col)
    if op == "sum":
        return func.sum(col)
    if op in ("p25", "p50", "p75"):
        pct = {"p25": 0.25, "p50": 0.5, "p75": 0.75}[op]
        # percentile_cont 是 PostgreSQL 有序集聚合，SQLite 不支持。
        # SQLite 上回退为 NULL（前端会展示为缺失），避免直接崩溃；
        # 若运行在 SQLite 又必须用 percentile，应换 PostgreSQL 部署。
        if dialect == "sqlite":
            return null()
        return func.percentile_cont(pct).within_group(col.asc())
    raise HTTPException(status_code=400, detail=f"不支持的聚合操作: {op}")


# 超过该阈值时进行系统采样，减轻前端渲染压力。
_SAMPLE_THRESHOLD = 20000


@router.post("/devices", response_model=QueryResponse)
@snapshot_query
def query_devices(req: QueryRequest, db: DbSession) -> QueryResponse:
    if req.limit < 1:
        raise HTTPException(status_code=400, detail="limit 必须 >= 1")
    limit = min(req.limit, LIMIT_HARD_CAP)

    # ── 尝试读缓存 ──────────────────────────────────────────────
    version = cache_version(db)
    cache_key = _query_cache_key("devices", req, version) + ":" + scope_version(db)[1]
    cached = _cache_get(cache_key) if version is not None else None
    if cached is not None:
        log.debug("query_devices 命中缓存 key=%s", cache_key)
        return QueryResponse(**cached)

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
    _validate_field_set(fields)
    if req.order_by is not None:
        order_key = req.order_by.lstrip("-+")
        if order_key not in ALLOWED_QUERY_FIELDS:
            raise HTTPException(status_code=400, detail=f"未知排序字段: {order_key}")

    where = [active_batch_clause(), Device.matched.is_(True), *_build_filters(req.filters)]

    select_cols: list[ColumnElement[Any]] = [_resolve_column(f).label(f) for f in fields]
    select_cols.extend([Device.batch_id.label('_source_batch_id'),
                        Device.source_device_key.label('_source_device_key')])
    # 必须显式 select_from(Device)，否则用户只选 Batch 字段（如 batch_no）时
    # SQLAlchemy 无法从 select_cols 推断左侧表 → InvalidRequestError 500。
    stmt = select(*select_cols).select_from(Device).join(Batch, Device.batch_id == Batch.id)
    if where:
        stmt = stmt.where(*where)

    stmt = stmt.order_by(*stable_order(req.order_by))
    # ── COUNT 优化 ──────────────────────────────────────────────
    # skip_count=True 时避免精确 COUNT(*)，用 LIMIT+1 判断 truncated。
    # 适合大数据量探索分析，可减少 ~50% DB 往返。
    if req.skip_count:
        stmt = stmt.limit(limit + 1)
        rows = db.execute(stmt).mappings().all()
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]
        total = len(rows) if not truncated else limit + 1
    else:
        count_stmt = (
            select(func.count()).select_from(Device).join(Batch, Device.batch_id == Batch.id)
        )
        if where:
            count_stmt = count_stmt.where(*where)
        total = db.scalar(count_stmt) or 0

        stmt = stmt.limit(limit)
        rows = db.execute(stmt).mappings().all()
        truncated = int(total) > limit

    sources, verified = matching_sources(db, where)
    rows_out = []
    for row in rows:
        item = dict(row)
        ref = sources.get(item.pop('_source_batch_id'))
        device_key = item.pop('_source_device_key')
        item['_source_ref'] = {**ref, 'source_device_key': device_key} if ref else None
        verified = verified and device_key is not None
        rows_out.append(item)

    # ── 应用层降采样 ────────────────────────────────────────────
    # 当返回行数超过阈值时，均匀采样到阈值以下，减轻前端渲染负担。
    sampled = False
    sample_rate = None
    if req.sampling == "auto" and len(rows_out) > _SAMPLE_THRESHOLD:
        step = (len(rows_out) // _SAMPLE_THRESHOLD) + 1
        rows_out = rows_out[::step]
        sampled = True
        sample_rate = round(1.0 / step, 4)
        log.info(
            "query_devices 降采样: %d → %d 行 (rate=%s)",
            len(rows),
            len(rows_out),
            sample_rate,
        )

    resp = QueryResponse(
        total=int(total),
        returned=len(rows_out),
        truncated=truncated,
        rows=rows_out,
        sampled=sampled,
        sample_rate=sample_rate,
        manifest=make_manifest(query_spec(req.filters, fields, req.order_by), sources, verified,
            None if req.skip_count and truncated else int(total), len(rows_out),
            truncated, sample_rate), epoch=version[1] if version else None,
        active_scope='active',
    )

    # ── 写入缓存（仅缓存非截断结果，避免缓存不完整数据） ──────────
    if version is not None and not resp.truncated and resp.returned > 0:
        _cache_set(cache_key, resp.model_dump())

    return resp


@router.post("/aggregate", response_model=AggregateResponse)
@snapshot_query
def aggregate(req: AggregateRequest, db: DbSession) -> AggregateResponse:
    if not req.metrics:
        raise HTTPException(status_code=400, detail="metrics 不能为空")

    # ── 尝试读缓存 ──────────────────────────────────────────────
    version = cache_version(db)
    cache_key = _query_cache_key("aggregate", req, version) + ":" + scope_version(db)[1]
    cached = _cache_get(cache_key) if version is not None else None
    if cached is not None:
        log.debug("aggregate 命中缓存 key=%s", cache_key)
        return AggregateResponse(**cached)

    _validate_field_set(req.group_by)
    for m in req.metrics:
        if m.field not in ALLOWED_QUERY_FIELDS:
            raise HTTPException(status_code=400, detail=f"未知字段: {m.field}")

    where = [active_batch_clause(), Device.matched.is_(True), *_build_filters(req.filters)]

    dialect = db.bind.dialect.name if db.bind is not None else None

    group_cols = [_resolve_column(g).label(g) for g in req.group_by]
    metric_cols: list[ColumnElement[Any]] = []
    metric_keys: list[tuple[str, str]] = []
    for m in req.metrics:
        for op in m.agg:
            metric_cols.append(
                _aggregate_expr(m.field, op, dialect=dialect).label(f"{m.field}__{op}")
            )
            metric_keys.append((m.field, op))

    select_cols = [*group_cols, *metric_cols]
    # 显式 select_from(Device) 同 query_devices —— group_by 只含 batch_no（Batch 表字段）
    # 时同样会让 SQLAlchemy 无法推断左侧表。
    stmt = select(*select_cols).select_from(Device).join(Batch, Device.batch_id == Batch.id)
    if where:
        stmt = stmt.where(*where)
    if group_cols:
        stmt = stmt.group_by(*[_resolve_column(g) for g in req.group_by])
        stmt = stmt.order_by(func.min(Device.id))

    rows = db.execute(stmt).mappings().all()
    groups: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {}
        for g in req.group_by:
            item[g] = r[g]
        for field, op in metric_keys:
            item.setdefault(field, {})
            val = r[f"{field}__{op}"]
            item[field][op] = float(val) if val is not None and op != "count" else val
        groups.append(item)

    sources, verified = matching_sources(db, where)
    total = db.scalar(select(func.count()).select_from(Device)
        .join(Batch, Device.batch_id == Batch.id).where(*where)) or 0
    resp = AggregateResponse(groups=groups, active_scope='active',
        epoch=version[1] if version else None,
        manifest=make_manifest(query_spec(req.filters,
            list(dict.fromkeys([*req.group_by, *[m.field for m in req.metrics]]))),
            sources, verified, int(total), len(groups)))

    # ── 写入缓存 ────────────────────────────────────────────────
    if version is not None and groups:
        _cache_set(cache_key, resp.model_dump())

    return resp


# 大 text 列 distinct 容易慢，限制更小 + 缓存更久。
_DISTINCT_SLOW_FIELDS = {"original_filename", "display_name"}
_DISTINCT_CACHE_TTL = 600  # 10 分钟（distinct 结果变化极慢）


@router.get("/distinct")
@snapshot_query
def distinct_values(
    db: DbSession,
    field: Annotated[str, Query(...)],
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> dict[str, Any]:
    # 大 text 列限制更小，避免全表扫描 + 排序拖垮 DB
    effective_limit = 100 if field in _DISTINCT_SLOW_FIELDS else limit

    version = cache_version(db)
    cache_key = f"aln:distinct:{field}:{effective_limit}:{version}:{scope_version(db)[1]}"
    cached = _cache_get(cache_key) if version is not None else None
    if cached is not None:
        log.debug("distinct 命中缓存 field=%s", field)
        return cached

    has_null = False
    if field == "batch_no":
        stmt = (select(distinct(Batch.batch_no)).where(active_batch_clause())
                .order_by(Batch.batch_no).limit(effective_limit + 1))
    else:
        col = _resolve_column(field)
        matched_only = (active_batch_clause(), Device.matched.is_(True))
        has_null = db.scalar(select(Device.id).join(Batch, Device.batch_id == Batch.id)
            .where(*matched_only, col.is_(None)).limit(1)) is not None
        stmt = (select(distinct(col)).select_from(Device)
                .join(Batch, Device.batch_id == Batch.id)
                .where(*matched_only, col.is_not(None))
                .order_by(col).limit(effective_limit + 1))
    rows = db.execute(stmt).all()
    raw = [r[0] for r in rows]
    values = raw
    truncated = len(values) > effective_limit
    if truncated:
        values = values[:effective_limit]

    sources, verified = matching_sources(db, [active_batch_clause()])
    if field == 'batch_no':
        from app.services.source_identity import batch_source_ref
        sources = {batch.id: batch_source_ref(db, batch) for batch in
                   db.scalars(select(Batch).where(active_batch_clause()).order_by(Batch.id))}
        verified = verified and all(ref and ref['source_revision'] is not None
                                    for ref in sources.values())
    result = {
        "manifest": make_manifest(query_spec({}, [field], field), sources, verified,
            None if truncated else len(values), len(values), truncated).model_dump(mode='json'),
        "epoch": version[1] if version else None,
        "active_scope": "active",
        "field": field,
        "values": values,
        "has_null": has_null,
        "truncated": truncated,
    }
    if version is not None:
        _cache_set(cache_key, result, ttl=_DISTINCT_CACHE_TTL)
    return result


@router.get("/fields")
def fields_metadata() -> dict[str, Any]:
    metadata = {
        "categorical": [
            {
                "name": "batch_no",
                "label": "批次号",
                "values_endpoint": "/api/query/distinct?field=batch_no",
            },
            {
                "name": "wafer",
                "label": "晶圆 (Wafer)",
                "values_endpoint": "/api/query/distinct?field=wafer",
            },
            {"name": "pf", "label": "PF", "values": ["Y", "N"]},
            {"name": "folder_name", "label": "端口 (S11/S22)", "values": ["S11", "S22"]},
            {
                "name": "original_filename",
                "label": "原始文件名",
                "values_endpoint": "/api/query/distinct?field=original_filename",
            },
            {
                "name": "display_name",
                "label": "展示名",
                "values_endpoint": "/api/query/distinct?field=display_name",
            },
            {
                "name": "mark",
                "label": "代号",
                "values_endpoint": "/api/query/distinct?field=mark",
            },
            {
                "name": "coord",
                "label": "坐标",
                "values_endpoint": "/api/query/distinct?field=coord",
            },
            {
                "name": "area_n",
                "label": "区域编号",
                "values_endpoint": "/api/query/distinct?field=area_n",
            },
        ],
        "geometric": [
            {"name": "x", "label": "X 坐标"},
            {"name": "y", "label": "Y 坐标"},
        ],
        "numeric": [
            {"name": "fs_ghz", "label": "fs(GHz)", "unit": ""},
            {"name": "fp_ghz", "label": "fp(GHz)", "unit": ""},
            {"name": "zs_ohm", "label": "Zs", "unit": "Ω"},
            {"name": "zp_ohm", "label": "Zp", "unit": "Ω"},
            {"name": "qs", "label": "Qs", "unit": ""},
            {"name": "qp", "label": "Qp", "unit": ""},
            {"name": "qs_bodeq", "label": "bodeQs", "unit": ""},
            {"name": "qp_bodeq", "label": "bodeQp", "unit": ""},
            {"name": "dbqs", "label": "dBQs", "unit": "dB"},
            {"name": "dbqp", "label": "dBQp", "unit": "dB"},
            {"name": "bodeq_fitted", "label": "BodeQ 拟合", "unit": ""},
            {"name": "bodeq_smooth", "label": "BodeQ 平滑", "unit": ""},
            {"name": "bodeq_raw", "label": "BodeQ 原始", "unit": ""},
            {"name": "fbode_ghz", "label": "fBode(GHz)", "unit": ""},
            {"name": "k2eff_pct", "label": "k²eff(%)", "unit": ""},
            {"name": "fp2_ghz", "label": "fp2(GHz)", "unit": ""},
            {"name": "fs2_ghz", "label": "fs2(GHz)", "unit": ""},
            {"name": "zp2_ohm", "label": "二次并联阻抗 (Zp2)", "unit": "Ω"},
            {"name": "zs2_ohm", "label": "二次串联阻抗 (Zs2)", "unit": "Ω"},
        ],
        "process": [
            {
                "name": "eg",
                "label": "EG",
                "values_endpoint": "/api/query/distinct?field=eg",
            },
            {
                "name": "fl",
                "label": "FL",
                "values_endpoint": "/api/query/distinct?field=fl",
            },
            {
                "name": "ag",
                "label": "AG",
                "values_endpoint": "/api/query/distinct?field=ag",
            },
            {
                "name": "area_um2",
                "label": "面积",
                "unit": "",
                "values_endpoint": "/api/query/distinct?field=area_um2",
            },
        ],
    }

    for field in metadata['numeric'] + metadata['process']:
        if field['name'] in NUMERIC_UNITS:
            field['unit'] = NUMERIC_UNITS[field['name']]
    return metadata
