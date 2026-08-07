"""代理模型训练数据提取纯函数（规格 §5，v4）。torch-free。

记录 dict 结构见计划"数据契约"；has_pf/mark 由脚本经 mapping JOIN 提供。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.config import AlgorithmConfig
from app.core.sparse_sampler import deterministic_sample_positions, read_z_mag_db


def build_device_record(
    device_row: dict,
    files_dir: str | Path,
    freq_axis: NDArray[np.float64],
    k: int = AlgorithmConfig.surr_k,
) -> dict | None:
    """单器件 → 记录 dict；文件缺失/读取失败返回 None。"""
    rel = device_row.get("s_param_path")
    if not rel:
        return None
    path = Path(files_dir) / str(device_row.get("batch_no")) / str(rel)
    if not path.exists():
        return None
    try:
        freq, z_db, phase = read_z_mag_db(
            path,
            port=device_row.get("s_param_port") or "S11",
            target_n_freq=len(freq_axis),
            return_phase=True,
        )
        if not np.allclose(freq, freq_axis):
            # 文件轴与统一轴不一致（如扫频范围不同）：插值对齐，防静默错位
            z_db = np.interp(freq_axis, freq, z_db)
            phase = np.interp(freq_axis, freq, phase)
    except Exception:
        return None

    fs = float(device_row.get("fs_ghz") or 0.0)
    fp = float(device_row.get("fp_ghz") or 0.0)
    pos = deterministic_sample_positions(freq_axis, fs, fp, k=k)
    params = np.array([float(device_row.get(c) or 0.0) for c in AlgorithmConfig.surr_param_cols])
    eg = float(device_row.get("eg") or 0.0)
    fl = float(device_row.get("fl") or 0.0)
    area = int(device_row.get("area_um2") or 0)
    pf = int(device_row.get("has_pf") or 0)
    return {
        "device_id": int(device_row.get("id") or 0),
        "eg": eg, "fl": fl, "ag": AlgorithmConfig.surr_default_ag, "area_um2": area,
        "has_pf": pf, "mark": str(device_row.get("mark") or ""),
        "cell_key": f"{eg}|{fl}|{area}|{pf}",
        "params": params,
        "sample_pos": pos.astype(np.int32),
        "sample_z": z_db[pos].astype(np.float32),
        "sparse_base": np.zeros(k, dtype=np.float32),  # 基准表构建后回填
        "full_z": z_db.astype(np.float32),
        "phase": phase.astype(np.float32),
    }


def clean_records(
    records: list[dict],
    fs_tol_mhz: float = AlgorithmConfig.surr_clean_fs_tol_mhz,
    trend_sigma: float = AlgorithmConfig.surr_clean_trend_sigma,
) -> tuple[list[dict], list[dict]]:
    """两级清洗（规格 §5.2）。返回 (kept, dropped)，dropped 元素含 reason 字段。

    判定顺序：单元内 fs/fp 离群 → 跨单元趋势面 → 物理非法（qs/qp<=0 或 fp<=fs）。
    物理非法放最后：同时违反多项的记录按更具体的原因（cell_outlier/trend_outlier）
    归类；物理非法记录仍参与单元中位数与趋势面拟合（中位数/最小二乘对其鲁棒）。
    趋势面用 MAD 稳健尺度（修正 z 分数）：std 尺度在少键数时被离群格自身撑大
    （自掩蔽漏检）；格子数 <~10 时检出能力仍有限，靠单元内过滤与物理判定兜底。
    """
    dropped: list[dict] = []

    # 第 1 级：单元内 fs/fp 中位数 ± tol（单元 = eg|fl|area|pf）
    by_cell: dict[str, list[dict]] = {}
    for r in records:
        by_cell.setdefault(r["cell_key"], []).append(r)
    tol_ghz = fs_tol_mhz / 1000.0
    in_cell: list[dict] = []
    for group in by_cell.values():
        fs_med = float(np.median([g["params"][0] for g in group]))
        fp_med = float(np.median([g["params"][1] for g in group]))
        for g in group:
            if abs(g["params"][0] - fs_med) > tol_ghz or abs(g["params"][1] - fp_med) > tol_ghz:
                dropped.append({**g, "reason": "cell_outlier"})
            else:
                in_cell.append(g)

    # 第 2 级：跨单元趋势面（每 (area, pf)：fs/fp 格中位数 ~ (1, eg, fl) 最小二乘）
    by_area_pf: dict[tuple[int, int], dict[str, list[dict]]] = {}
    for r in in_cell:
        by_area_pf.setdefault((r["area_um2"], r["has_pf"]), {}).setdefault(
            r["cell_key"], []).append(r)
    in_trend: list[dict] = []
    for (_area, _pf), cells in by_area_pf.items():
        keys = sorted(cells)
        if len(keys) < 4:
            for key in keys:
                in_trend.extend(cells[key])
            continue
        design = np.array([[1.0, cells[key][0]["eg"], cells[key][0]["fl"]] for key in keys])
        bad: set[str] = set()  # fs/fp 任一维超阈值即整格剔除（并集，判定一次）
        for param_idx in (0, 1):
            med = np.array([
                float(np.median([g["params"][param_idx] for g in cells[key]]))
                for key in keys
            ])
            coef, *_ = np.linalg.lstsq(design, med, rcond=None)
            resid = med - design @ coef
            # MAD 稳健尺度 + 居中比较（修正 z 分数），下限防常量数据数值误杀
            center = float(np.median(resid))
            sigma = max(1.4826 * float(np.median(np.abs(resid - center))), 1e-12)
            bad |= {
                keys[i]
                for i in range(len(keys))
                if abs(resid[i] - center) > trend_sigma * sigma
            }
        for key in keys:
            if key in bad:
                dropped.extend({**g, "reason": "trend_outlier"} for g in cells[key])
            else:
                in_trend.extend(cells[key])

    # 第 3 级（最后判定）：物理非法（规格 §5.2 第 3 条）
    kept: list[dict] = []
    for r in in_trend:
        fs, fp, qs, qp = r["params"][0], r["params"][1], r["params"][3], r["params"][4]
        if qs <= 0 or qp <= 0 or fp <= fs:
            dropped.append({**r, "reason": "physical"})
        else:
            kept.append(r)
    return kept, dropped


def select_per_cell(
    records: list[dict],
    max_per_cell: int = AlgorithmConfig.surr_per_cell_max,
) -> list[dict]:
    """每结构组合按 |fs−格中位数| 升序取前 max_per_cell 条（v4，规格 §5.1）。"""
    by_cell: dict[str, list[dict]] = {}
    for r in records:
        by_cell.setdefault(r["cell_key"], []).append(r)
    selected: list[dict] = []
    for key in sorted(by_cell):
        group = by_cell[key]
        fs_med = float(np.median([g["params"][0] for g in group]))
        group_sorted = sorted(group, key=lambda g: (abs(g["params"][0] - fs_med), g["device_id"]))
        selected.extend(group_sorted[:max_per_cell])
    return selected


def split_groups(
    groups: list[tuple[float, float, int, str]],
    val_n: int = AlgorithmConfig.surr_val_groups,
    test_n: int = AlgorithmConfig.surr_test_groups,
    seed: int = AlgorithmConfig.surr_split_seed,
) -> dict[tuple[float, float, int, str], str]:
    """物理器件组划分（v4，规格 §5.5）。eg=0 且 fl=0 的基准组固定 train。"""
    baseline = {g for g in groups if g[0] == 0.0 and g[1] == 0.0}
    others = sorted(g for g in groups if g not in baseline)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(others))
    val_groups = {others[i] for i in perm[:val_n]}
    test_groups = {others[i] for i in perm[val_n:val_n + test_n]}
    splits: dict[tuple[float, float, int, str], str] = {}
    for g in groups:
        if g in baseline:
            splits[g] = "train"
        elif g in val_groups:
            splits[g] = "val"
        elif g in test_groups:
            splits[g] = "test"
        else:
            splits[g] = "train"
    return splits


def compute_norm_stats(records: list[dict], splits: dict) -> dict[str, list[float]]:
    """归一化统计：只用 train 组的记录（防 test 泄漏）。x 为 5 维 [eg,fl,ag,area,pf]。"""
    train = [r for r in records if splits.get(_group_tuple(r), "train") == "train"]
    x = np.array([[r["eg"], r["fl"], r["ag"], r["area_um2"], r["has_pf"]] for r in train])
    params = np.stack([r["params"] for r in train])
    z = np.concatenate([r["full_z"] for r in train])
    return {
        "x_mean": x.mean(axis=0).tolist(),
        "x_std": np.maximum(x.std(axis=0), 1e-6).tolist(),
        "params_mean": params.mean(axis=0).tolist(),
        "params_std": np.maximum(params.std(axis=0), 1e-6).tolist(),
        "z_mean": [float(z.mean())],
        "z_std": [max(float(z.std()), 1e-6)],
    }


def _group_tuple(r: dict) -> tuple[float, float, int, str]:
    return (r["eg"], r["fl"], r["has_pf"], r["mark"])
