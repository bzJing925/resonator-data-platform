"""设备批量入库服务。

集中管理 devices 表的批量写入：
- COPY 目标列定义
- 小批量用 SQLAlchemy bulk_insert_mappings
- 大批量走 PostgreSQL COPY FROM（失败降级到 ORM）
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Device

logger = logging.getLogger(__name__)

# COPY 目标列（排除自增 id，与 devices 表定义顺序一致）
DEVICE_COPY_COLUMNS: list[str] = [
    "batch_id",
    "original_filename",
    "display_name",
    "mark",
    "wafer",
    "folder_name",
    "coord",
    "x",
    "y",
    "eg",
    "fl",
    "ag",
    "pf",
    "area_n",
    "area_um2",
    "fs_ghz",
    "fp_ghz",
    "zs_ohm",
    "zp_ohm",
    "qs",
    "qp",
    "qs_bodeq",
    "qp_bodeq",
    "dbqs",
    "dbqp",
    "bodeq_fitted",
    "bodeq_smooth",
    "bodeq_raw",
    "fbode_ghz",
    "k2eff_pct",
    "fp2_ghz",
    "fs2_ghz",
    "zp2_ohm",
    "zs2_ohm",
    "deembedded",
    "s_param_path",
    "s_param_port",
]

# 启用 COPY 协议的行数阈值；低于此值 ORM bulk insert 更快。
_COPY_THRESHOLD = 3000


def copy_insert_devices(db: Session, rows: list[dict[str, Any]]) -> None:
    """用 PostgreSQL COPY FROM 批量插入 devices。"""
    raw_conn = db.connection().connection
    cols_sql = ", ".join(DEVICE_COPY_COLUMNS)
    copy_sql = f"COPY devices ({cols_sql}) FROM STDIN"

    with raw_conn.cursor() as cur:
        with cur.copy(copy_sql) as copy:
            for r in rows:
                copy.write_row(tuple(r.get(c) for c in DEVICE_COPY_COLUMNS))

    db.commit()


def bulk_insert_devices(db: Session, rows: list[dict[str, Any]]) -> None:
    """批量插入 Device；大行数走 PostgreSQL COPY，失败降级到 ORM。"""
    if not rows:
        return

    if len(rows) >= _COPY_THRESHOLD:
        try:
            copy_insert_devices(db, rows)
            return
        except Exception:
            logger.exception("COPY 批量插入失败，降级到 bulk_insert")

    db.bulk_insert_mappings(Device, rows)
    db.commit()
