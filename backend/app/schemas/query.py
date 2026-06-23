"""查询/聚合 请求 + 响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AggOp = Literal["min", "max", "count", "p25", "p50", "p75", "avg", "sum"]


class MetricSpec(BaseModel):
    field: str
    agg: list[AggOp]


FilterValue = Any

# `filters` 接受两种格式：
#   1. 旧版 dict-of-fields：{field: list | dict-of-ops | scalar}，字段间按 AND。
#   2. 新版 AND/OR 树：{op: "and"|"or", children: [...]}，叶节点 {field, op, value}。
# 解析逻辑见 app.api.query._build_filters。


class QueryRequest(BaseModel):
    filters: dict[str, FilterValue] = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)
    limit: int = 20000
    order_by: str | None = None
    # 为 true 时跳过精确 COUNT(*)，用 LIMIT+1 判断是否有更多数据。
    # 适合大数据量探索分析场景，可显著减少 DB 压力。
    skip_count: bool = False


class QueryResponse(BaseModel):
    total: int
    returned: int
    truncated: bool
    rows: list[dict[str, Any]]
    # 降采样标记：当后端返回行数超过阈值时进行系统采样
    sampled: bool = False
    sample_rate: float | None = None


class AggregateRequest(BaseModel):
    filters: dict[str, FilterValue] = Field(default_factory=dict)
    group_by: list[str] = Field(default_factory=list)
    metrics: list[MetricSpec] = Field(default_factory=list)


class AggregateResponse(BaseModel):
    groups: list[dict[str, Any]]
