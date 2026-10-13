"""Explicit complete catalog, UUID source operations and local replica preference."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.source import SourceRef


class ReplicaPreferenceRequest(BaseModel):
    source_ref: SourceRef
    hidden: bool

    @model_validator(mode="after")
    def batch_reference(self):
        if self.source_ref.source_device_key is not None:
            raise ValueError("隐藏偏好必须引用批次")
        return self


class SourceOperation(BaseModel):
    expected_revision: int = Field(ge=0)


class CatalogBatch(BaseModel):
    id: int
    batch_no: str
    revision: int = Field(ge=0)
    ready: bool
    state: Literal['active', 'deleted']
    source_ref: SourceRef


    @model_validator(mode="after")
    def same_revision(self):
        if (self.source_ref.source_revision != self.revision
                or self.source_ref.source_device_key is not None):
            raise ValueError("目录批次来源版本不一致")
        return self


class CatalogTombstone(BaseModel):
    origin_id: UUID
    source_batch_id: UUID
    revision: int = Field(ge=0)
    state: Literal['active', 'deleted']
    deleted_revision: int | None = Field(default=None, ge=0)
    restored_revision: int | None = Field(default=None, ge=0)
    deleted_at: datetime | None = None
    restored_at: datetime | None = None
    purged_at: datetime | None = None
    updated_at: datetime | None = None


class CatalogV3(BaseModel):
    protocol: Literal[3]
    complete: Literal[True]
    server_id: UUID
    epoch: int = Field(ge=0)
    batches: list[CatalogBatch]
    tombstones: list[CatalogTombstone]
    mappings: list[dict[str, Any]]


class TableReconfirmRequest(BaseModel):
    request_key: str = Field(min_length=1, max_length=80)
    source_ref: SourceRef
