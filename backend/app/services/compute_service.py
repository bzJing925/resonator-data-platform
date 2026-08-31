"""单文件指标计算服务。

把 /api/files/compute 中的算法调用与 DB 写入逻辑下沉到服务层，
API 路由只负责 HTTP 参数解析与异常转换。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import DEVICE_COLUMNS
from app.core.extract import ExtractError, extract_resonator_params
from app.core.mapping import load_mapping
from app.models import Batch, Device, Mapping

logger = logging.getLogger(__name__)


class ComputeServiceError(Exception):
    """单文件计算服务异常。"""


DEFAULT_S_PARAM_PORT = "S11"


def _wafer_from_batch_no(batch_no: str) -> int | None:
    """从 batch_no 末尾提取 wafer 编号。"""
    m = re.search(r"\.(\d+)$", batch_no)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _upsert_device(
    db: Session,
    batch: Batch,
    relpath: str,
    row: dict[str, Any],
) -> Device:
    """根据计算结果插入或更新 Device 行。"""
    existing = db.scalar(
        select(Device).where(
            Device.batch_id == batch.id,
            Device.s_param_path == relpath,
            Device.s_param_port == row.get("s_param_port", DEFAULT_S_PARAM_PORT),
        )
    )
    if existing is not None:
        for col in DEVICE_COLUMNS:
            if col == "id":
                continue
            if col in row:
                setattr(existing, col, row[col])
        device = existing
    else:
        device = Device(**{col: row.get(col) for col in DEVICE_COLUMNS if col != "id"})
        db.add(device)
    db.commit()
    db.refresh(device)
    return device


def compute_single_device(
    db: Session,
    batch: Batch,
    relpath: str,
    target_path: Path,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembedded: bool = False,
) -> Device:
    """对单个文件执行指标计算并写入/更新 devices 表。

    参数：
    - db: 数据库会话
    - batch: 批次 ORM 对象（须已存在）
    - relpath: 文件相对批次目录的路径
    - target_path: 磁盘真实路径（可能为 .gz）
    - f_start_ghz, f_end_ghz: 频率裁剪范围
    - deembedded: 是否已去嵌

    返回：写入/更新后的 Device 对象。
    抛出：ComputeServiceError（业务异常）或 ExtractError（算法异常）。
    """
    mapping_row = db.get(Mapping, batch.mapping_id) if batch.mapping_id else None
    mapping_dict = load_mapping(mapping_row.file_path) if mapping_row else {}

    wafer = _wafer_from_batch_no(batch.batch_no)

    try:
        row = extract_resonator_params(
            target_path,
            mapping=mapping_dict,
            wafer=wafer,
            s_param_relpath=relpath,
            deembedded=deembedded,
            f_start_ghz=f_start_ghz,
            f_end_ghz=f_end_ghz,
            skip_validation=True,
        )
    except ExtractError:
        raise
    except Exception as exc:
        logger.exception("单文件计算异常 %s", relpath)
        raise ComputeServiceError(f"计算异常: {exc}") from exc

    row["batch_id"] = batch.id
    return _upsert_device(db, batch, relpath, row)


def sync_batch_device_count(db: Session, batch_id: int) -> int:
    """重新统计 batch 下 device 数量并更新 batches 表。"""
    device_count = db.scalar(select(func.count(Device.id)).where(Device.batch_id == batch_id)) or 0
    db.execute(update(Batch).where(Batch.id == batch_id).values(device_count=device_count))
    db.commit()
    return int(device_count)
