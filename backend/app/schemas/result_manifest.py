"""查询结果的范围、来源和一致性说明。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.analysis import QuerySpec
from app.schemas.source import SourceRef


class SamplingSpec(BaseModel):
    method: Literal["none", "systematic"]
    rate: float | None = Field(ge=0, le=1)


class ResultManifest(BaseModel):
    schema_version: Literal[1]
    query_spec: QuerySpec
    source_refs: list[SourceRef]
    matched_total: int | None = Field(ge=0)
    total_relation: Literal["exact", "lower_bound", "unknown"]
    returned: int = Field(ge=0)
    truncated: bool
    sampling: SamplingSpec
    local_edits: int = Field(ge=0)
    excluded_rows: int = Field(ge=0)
    generated_at: datetime
    consistency: Literal["verified", "unverified"]

    @model_validator(mode="after")
    def validate_total_relation(self) -> ResultManifest:
        if self.total_relation == "unknown" and self.matched_total is not None:
            raise ValueError("unknown total_relation requires matched_total=null")
        if self.total_relation != "unknown" and self.matched_total is None:
            raise ValueError("exact and lower_bound total_relation require matched_total")
        return self
