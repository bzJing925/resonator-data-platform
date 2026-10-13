"""Full filtered export admission; graph limits are not part of this protocol."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.analysis import QuerySpec
from app.schemas.source import SourceRef


class ExportCreate(BaseModel):
    request_key: str = Field(min_length=1, max_length=200)
    format: Literal['csv', 'xlsx']
    query_spec: QuerySpec
    expected_sources: list[SourceRef]

    @model_validator(mode='after')
    def batch_references(self):
        keys = [(ref.origin_id, ref.source_batch_id) for ref in self.expected_sources]
        if len(keys) != len(set(keys)) or any(ref.source_device_key is not None
            or ref.source_revision is None for ref in self.expected_sources):
            raise ValueError('导出需要唯一、版本已知的批次来源')
        return self
