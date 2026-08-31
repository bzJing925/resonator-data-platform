"""通用响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class PaginatedResponse[T](BaseModel):
    """通用分页响应。"""

    total: int
    page: int
    size: int
    items: list[T]
