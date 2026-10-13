"""查询、分析、质量规则与比较配置契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.source import SourceRef

Aggregation = Literal["all", "mean", "min", "max", "p50", "p25", "p75"]


class QuerySpec(BaseModel):
    filters: dict[str, Any]
    fields: list[str]
    order_by: str | None


class QualityPredicate(BaseModel):
    field: str
    op: Literal["gte", "lte"]
    value: float
    unit: str


class QualityRuleV1(BaseModel):
    id: str
    version: int = Field(ge=1)
    label: str
    predicates: list[QualityPredicate]
    combine: Literal["and"]


class AnalysisField(BaseModel):
    name: str
    aggregation: Aggregation


class AnalysisChart(BaseModel):
    type: Literal["scatter", "box", "violin", "swarm", "line", "wafer"]
    x_fields: list[str]
    y_fields: list[AnalysisField]
    z_field: AnalysisField | None
    z_mode: Literal["facet", "color"]
    wafer_z: AnalysisField | None
    wafer_facet: str | None
    show_data_points: bool


class ComparisonEndpoint(BaseModel):
    source: SourceRef | None
    dataset_id: str | None
    query_spec: QuerySpec


class AnalysisComparison(BaseModel):
    scope: Literal["source", "workspace_snapshot"]
    a: ComparisonEndpoint
    b: ComparisonEndpoint
    metrics: list[str]
    group_fields: list[str]
    common_frequency_ghz: tuple[float, float] | None
    condition_policy: Literal["require_equal", "common_subset"]
    wafer_metric: str | None
    unified_color_scale: bool

    @model_validator(mode="after")
    def validate_scope_refs(self) -> AnalysisComparison:
        endpoints = (self.a, self.b)
        if self.scope == "source":
            if any(
                endpoint.source is None
                or endpoint.source.source_device_key is not None
                or endpoint.dataset_id is not None
                for endpoint in endpoints
            ):
                raise ValueError("source comparisons require batch SourceRefs and no dataset_id")
        elif any(
            endpoint.source is not None or endpoint.dataset_id is None for endpoint in endpoints
        ):
            raise ValueError("workspace snapshot comparisons require dataset_id and no SourceRef")
        return self


class AnalysisSpec(BaseModel):
    schema_version: Literal[1]
    query_spec: QuerySpec
    plot_limit: int = Field(gt=0)
    chart: AnalysisChart
    units: dict[str, str]
    quality_rule: QualityRuleV1 | None
    comparison: AnalysisComparison | None
