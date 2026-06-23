"""对照表请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MappingListItem(BaseModel):
    id: int
    name: str
    entry_count: int
    uploaded_at: datetime
    in_use_by_batches: int


class MappingEntryItem(BaseModel):
    mark: str
    description: str | None
    eg: float | None
    fl: float | None
    ag: float | None
    area_s11: int | None
    area_s22: int | None
    has_pf: bool


class MappingEntriesResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[MappingEntryItem]
