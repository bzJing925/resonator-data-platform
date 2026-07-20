"""NN 训练数据导出请求模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

NN_EXPORT_HARD_CAP = 50_000


class NNExportRequest(BaseModel):
    """导出 NN 训练 CSV 的请求体。

    复用现有查询过滤语法，但字段固定为训练所需集合。
    """

    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 50_000
    target_k: int = 300
    target_n_freq: int = 1001

    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v: int) -> int:
        if v < 1:
            return 1
        return min(v, NN_EXPORT_HARD_CAP)

    @field_validator("target_k")
    @classmethod
    def _validate_target_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError("target_k 必须 >= 1")
        return v

    @field_validator("target_n_freq")
    @classmethod
    def _validate_target_n_freq(cls, v: int) -> int:
        if v < 2:
            raise ValueError("target_n_freq 必须 >= 2")
        return v
