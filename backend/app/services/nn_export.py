"""NN 训练数据导出服务。

从当前筛选出的器件中读取 S1P，提取标量参数 + 稀疏采样点 + 完整 |Z| dB 频谱，
组织成可直接喂神经网络的宽表 CSV。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.query import _build_filters
from app.config import get_settings
from app.core.sparse_sampler import fixed_rule_sample, partition_regions, read_z_mag_db
from app.models import Batch, Device
from app.schemas.nn_export import NNExportRequest
from app.services.batch_scope import active_batch_clause

log = logging.getLogger("aln")

# 标量列：自变量 + 因变量（除阻抗谱外）
_SCALAR_COLUMNS: list[str] = [
    "id",
    "eg",
    "fl",
    "ag",
    "fs_ghz",
    "fp_ghz",
    "k2eff_pct",
    "qs",
    "qp",
    "bodeq_raw",
    "bodeq_smooth",
    "bodeq_fitted",
    "fbode_ghz",
    "qs_bodeq",
    "qp_bodeq",
    "s_param_path",
    "s_param_port",
]


def _resolve_sparam_path(rel_or_abs: str, batch_no: str | None = None) -> Path:
    """解析 S 参数文件路径。"""
    settings = get_settings()
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    base = settings.files_dir
    if batch_no:
        base = base / batch_no
    return base / p


def _build_header(target_k: int, target_n_freq: int) -> list[str]:
    """构建 CSV header。"""
    header = [
        "device_id",
        "eg",
        "ag",
        "fl",
        "fs_ghz",
        "fp_ghz",
        "k2eff_pct",
        "qs",
        "qp",
        "bodeq_raw",
        "bodeq_smooth",
        "bodeq_fitted",
        "fbode_ghz",
        "qs_bodeq",
        "qp_bodeq",
    ]
    header.extend(f"sample_freq_{i}" for i in range(target_k))
    header.extend(f"sample_z_{i}" for i in range(target_k))
    header.extend(f"full_freq_{i}" for i in range(target_n_freq))
    header.extend(f"full_z_{i}" for i in range(target_n_freq))
    return header


def _pad_array(arr: np.ndarray, target_len: int, fill_value: float = math.nan) -> np.ndarray:
    """将一维数组填充/截断到目标长度。"""
    if len(arr) >= target_len:
        return arr[:target_len]
    padded = np.full(target_len, fill_value, dtype=np.float64)
    padded[: len(arr)] = arr
    return padded


def _build_nn_export_row(
    device_row: dict[str, Any],
    target_k: int,
    target_n_freq: int,
) -> dict[str, Any] | None:
    """为单个器件构建 NN 训练数据行。

    读取 S1P、分区、固定规则采样，返回宽表 dict；失败返回 None。
    """
    s_param_path = device_row.get("s_param_path")
    if not s_param_path:
        return None

    batch_no = device_row.get("batch_no")
    port = device_row.get("s_param_port") or "S11"
    path = (Path(device_row["batch_file_path"]) / s_param_path
            if device_row.get("batch_file_path") and not Path(s_param_path).is_absolute()
            else _resolve_sparam_path(s_param_path, batch_no=batch_no))
    if not path.exists():
        log.warning("S 参数文件不存在，跳过 device_id=%s: %s", device_row.get("id"), path)
        return None

    try:
        freq, z_db = read_z_mag_db(path, port=port, target_n_freq=target_n_freq)
    except Exception as exc:
        log.warning("读取 S 参数失败，跳过 device_id=%s: %s", device_row.get("id"), exc)
        return None

    fs = device_row.get("fs_ghz") or 0.0
    fp = device_row.get("fp_ghz") or 0.0
    if fs <= 0 or fp <= fs:
        # fs/fp 异常时仍尝试采样，但加权会退化
        log.debug("device_id=%s 的 fs/fp 异常: fs=%s fp=%s", device_row.get("id"), fs, fp)

    try:
        region_mask = partition_regions(z_db, freq)
        sample_freq, sample_z = fixed_rule_sample(
            freq, z_db, region_mask, target_k=target_k, fs=float(fs), fp=float(fp)
        )
    except Exception as exc:
        log.warning("采样失败，跳过 device_id=%s: %s", device_row.get("id"), exc)
        return None

    sample_freq = _pad_array(sample_freq, target_k)
    sample_z = _pad_array(sample_z, target_k)

    out: dict[str, Any] = {
        "device_id": device_row.get("id"),
        "eg": device_row.get("eg"),
        "ag": device_row.get("ag"),
        "fl": device_row.get("fl"),
        "fs_ghz": device_row.get("fs_ghz"),
        "fp_ghz": device_row.get("fp_ghz"),
        "k2eff_pct": device_row.get("k2eff_pct"),
        "qs": device_row.get("qs"),
        "qp": device_row.get("qp"),
        "bodeq_raw": device_row.get("bodeq_raw"),
        "bodeq_smooth": device_row.get("bodeq_smooth"),
        "bodeq_fitted": device_row.get("bodeq_fitted"),
        "fbode_ghz": device_row.get("fbode_ghz"),
        "qs_bodeq": device_row.get("qs_bodeq"),
        "qp_bodeq": device_row.get("qp_bodeq"),
    }

    for i in range(target_k):
        out[f"sample_freq_{i}"] = sample_freq[i]
        out[f"sample_z_{i}"] = sample_z[i]

    for i in range(target_n_freq):
        out[f"full_freq_{i}"] = freq[i]
        out[f"full_z_{i}"] = z_db[i]

    return out


def build_nn_export_stmt(req: NNExportRequest) -> tuple[list[str], Any]:
    """构建 NN 导出用的字段列表和 SELECT statement。

    返回 (fields, stmt)，stmt 已包含 select_from/join/where/order_by/limit。
    """
    fields = [*_SCALAR_COLUMNS, "batch_no", "batch_file_path"]
    select_cols = [getattr(Device, c).label(c) for c in _SCALAR_COLUMNS]
    select_cols.append(Batch.batch_no.label("batch_no"))
    select_cols.append(Batch.file_path.label("batch_file_path"))

    where = [active_batch_clause(), *_build_filters(req.filters)]
    stmt = select(*select_cols).select_from(Device).join(Batch, Device.batch_id == Batch.id)
    if where:
        stmt = stmt.where(*where)
    stmt = stmt.order_by(Device.id.asc())
    stmt = stmt.limit(req.limit)
    return fields, stmt


def iter_nn_export_rows(
    db: Session,
    req: NNExportRequest,
    stmt: Any | None = None,
) -> Iterator[dict[str, Any]]:
    """流式生成 NN 训练数据行。"""
    if stmt is None:
        _, stmt = build_nn_export_stmt(req)
    result = db.execute(stmt).mappings().yield_per(100)

    target_k = req.target_k
    target_n_freq = req.target_n_freq

    for row in result:
        device_row = dict(row)
        out = _build_nn_export_row(device_row, target_k=target_k, target_n_freq=target_n_freq)
        if out is not None:
            yield out


def get_nn_export_header(req: NNExportRequest) -> list[str]:
    """返回 NN 导出 CSV 的 header。"""
    return _build_header(req.target_k, req.target_n_freq)
