"""Only complete source cohorts are handled by the server comparison endpoint."""

from pydantic import BaseModel, model_validator

from app.schemas.analysis import AnalysisComparison, QualityRuleV1


class ComparisonRequest(BaseModel):
    comparison: AnalysisComparison
    quality_rule: QualityRuleV1 | None = None

    @model_validator(mode="after")
    def source_scope(self):
        if self.comparison.scope != "source":
            raise ValueError("工作区快照比较在客户端执行")
        if not self.comparison.metrics or len(self.comparison.metrics) != len(
            set(self.comparison.metrics)
        ):
            raise ValueError("指标必须非空且唯一")
        if len(self.comparison.group_fields) != len(set(self.comparison.group_fields)):
            raise ValueError("分组字段不能重复")
        frequency = self.comparison.common_frequency_ghz
        if frequency and frequency[0] >= frequency[1]:
            raise ValueError("频段起点必须小于终点")
        return self
