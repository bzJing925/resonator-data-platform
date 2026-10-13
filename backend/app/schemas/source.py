"""跨实例来源身份契约。"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    """稳定的来源批次或器件引用；旧来源可明确保留未知修订。"""

    origin_id: UUID
    source_batch_id: UUID
    source_device_key: UUID | None
    source_revision: int | None = Field(ge=0)
