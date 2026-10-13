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
from app.services.source_identity import identity_for_result_path, validate_uuid

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
    "matched",
    "s_param_path",
    "s_param_port",
    "source_device_key",
]

# 启用 COPY 协议的行数阈值；低于此值 ORM bulk insert 更快。
_COPY_THRESHOLD = 3000


def copy_insert_devices(db: Session, rows: list[dict[str, Any]], *, commit: bool = False) -> None:
    """用 PostgreSQL COPY FROM 批量插入 devices。"""
    raw_conn = db.connection().connection
    cols_sql = ", ".join(DEVICE_COPY_COLUMNS)
    copy_sql = f"COPY devices ({cols_sql}) FROM STDIN"

    with raw_conn.cursor() as cur, cur.copy(copy_sql) as copy:
        for r in rows:
            copy.write_row(tuple(r.get(c) for c in DEVICE_COPY_COLUMNS))

    if commit:
        db.commit()


def bulk_insert_devices(db: Session, rows: list[dict[str, Any]], *, commit: bool = False) -> None:
    """批量插入 Device；大行数走 PostgreSQL COPY，失败降级到 ORM。"""
    if not rows:
        return
    from app.services.processing_run import assert_run_writable
    from app.workers.run_execution import current_run

    run = current_run.get()
    if run is not None:
        assert_run_writable(db, run.run_id, run.lease_token)

    for row in rows:
        if row.get("source_device_key") is None and row.get("s_param_path"):
            key = identity_for_result_path(
                db, row["batch_id"], row["s_param_path"], row.get("s_param_port", "S11")
            )
            if key is not None:
                row["source_device_key"] = key
        if row.get("source_device_key") is not None:
            validate_uuid(row["source_device_key"])

    if run is not None:
        from app.services.result_publish import stage_device_rows

        stage_device_rows(db, run.run_id, run.lease_token, rows)
        if commit:
            db.commit()
        return

    if len(rows) >= _COPY_THRESHOLD:
        try:
            with db.begin_nested():
                copy_insert_devices(db, rows)
            if commit:
                db.commit()
            return
        except Exception:
            logger.exception("COPY 批量插入失败，降级到 bulk_insert")

    db.bulk_insert_mappings(Device, rows)
    db.flush()
    if commit:
        db.commit()
