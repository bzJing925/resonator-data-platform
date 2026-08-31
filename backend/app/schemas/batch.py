"""批次相关响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import PaginatedResponse


class RecomputeRequest(BaseModel):
    metrics: list[str] = Field(
        default=["qs", "qp", "kt2", "qbode"],
        min_length=1,
    )


class ReprocessResponse(BaseModel):
    task_id: str
    batch_no: str
    stream_url: str


class BatchListItem(BaseModel):
    batch_no: str
    mapping_name: str | None
    device_count: int
    f_start_ghz: float | None
    f_end_ghz: float | None
    deembedded: bool
    deembed_method: str
    process_type: str
    uploaded_at: datetime


class BatchListResponse(PaginatedResponse[BatchListItem]):
    pass


class BatchStats(BaseModel):
    fs_ghz_mean: float | None
    fs_ghz_median: float | None
    pass_rate: float | None


class BatchDetail(BaseModel):
    batch_no: str
    mapping_id: int
    mapping_name: str | None
    device_count: int
    f_start_ghz: float | None
    f_end_ghz: float | None
    deembedded: bool
    deembed_method: str
    process_type: str
    file_path: str
    uploaded_at: datetime
    uploaded_by: str
    task_id: int | None
    wafers: list[int]
    stats: BatchStats
