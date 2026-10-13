"""API 公共依赖与工具。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device

DbSession = Annotated[Session, Depends(get_db)]


def device_columns() -> set[str]:
    """Device 表所有列名集合（用于校验 fields/order_by/group_by 防 SQL 注入）。"""
    return {c.key for c in inspect(Device).c}


# Compatibility aliases; shared query rules live below the API layer.
from app.services.query_spec import ALLOWED_QUERY_FIELDS, DEVICE_COLUMNS  # noqa: E402, F401

VIRTUAL_COLUMNS: set[str] = {"batch_no"}
