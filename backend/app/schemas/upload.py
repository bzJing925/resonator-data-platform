"""上传相关请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class UploadAccepted(BaseModel):
    task_id: str
    batch_no: str
    status: str
    stream_url: str


class DraftCommitRequest(BaseModel):
    request_key: str
    check_digest: str
    acknowledged_warning_codes: list[str] = []
