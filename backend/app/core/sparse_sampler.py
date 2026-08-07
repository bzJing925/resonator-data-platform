"""无 torch 依赖的稀疏采样工具。

从 S1P/S2P 文件读取 |Z| dB 频谱，并做 fs/fp 附近密集、远带稀疏的固定规则采样。
供训练数据导出和稀疏重建数据集共用。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import skrf as rf
from numpy.typing import NDArray

from app.config import AlgorithmConfig
from app.ml.sparse.region_partition import partition_regions

__all__ = [
    "baseline_interpolate",
    "deterministic_sample_positions",
    "fixed_rule_sample",
    "partition_regions",
    "read_z_mag_db",
    "s1p_to_z11_db",
    "s2p_to_z_samples",
    "uniform_sample_from_mask",
]


def s1p_to_z11_db(
    path: str | Path, target_n_freq: int = 1001
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """读取 S1P/S2P，返回插值后的 (freq_ghz, z_db)。

    使用 skrf 读取阻抗参数 Z，转为 dB，再插值到统一频点数。
    对 S2P 默认取 Z11（端口 0）。
    """
    net = rf.Network(str(path))
    freq_ghz = net.f / 1e9
    z = net.z[:, 0, 0]
    z_mag = np.abs(z)
    z_db = 20.0 * np.log10(np.maximum(z_mag, 1e-12))

    # 插值到统一频点数
    if len(freq_ghz) != target_n_freq:
        freq_new = np.linspace(freq_ghz[0], freq_ghz[-1], target_n_freq)
        z_db_new = np.interp(freq_new, freq_ghz, z_db)
        return freq_new.astype(np.float64), z_db_new.astype(np.float64)

    return freq_ghz.astype(np.float64), z_db.astype(np.float64)


def s2p_to_z_samples(
    path: str | Path, target_n_freq: int = 1001
) -> list[tuple[NDArray[np.float64], NDArray[np.float64], str]]:
    """读取 S2P，返回两个样本的 (freq_ghz, z_db, port_name)。

    port 0 -> S11 (Z11), port 1 -> S22 (Z22)。
    """
    net = rf.Network(str(path))
    if net.nports != 2:
        raise ValueError(f"期望 2-port S2P，得到 {net.nports}-port: {path}")

    freq_ghz = net.f / 1e9
    results = []
    for port, name in [(0, "S11"), (1, "S22")]:
        z = net.z[:, port, port]
        z_mag = np.abs(z)
        z_db = 20.0 * np.log10(np.maximum(z_mag, 1e-12))

        if len(freq_ghz) != target_n_freq:
            freq_new = np.linspace(freq_ghz[0], freq_ghz[-1], target_n_freq)
            z_db_new = np.interp(freq_new, freq_ghz, z_db)
            results.append((freq_new.astype(np.float64), z_db_new.astype(np.float64), name))
        else:
            results.append((freq_ghz.astype(np.float64), z_db.astype(np.float64), name))

    return results


def read_z_mag_db(
    path: str | Path,
    port: str = "S11",
    target_n_freq: int = 1001,
    return_phase: bool = False,
) -> tuple[NDArray[np.float64], ...]:
    """透明读取 S1P/S2P 并返回指定端口的 (freq_ghz, |Z|_dB)。

    Args:
        path: S1P 或 S2P 文件路径。
        port: "S11" 或 "S22"；S1P 时忽略该参数。
        target_n_freq: 统一插值后的频点数。
        return_phase: True 时返回三元组 (freq_ghz, |Z|_dB, phase_rad)，
            相位取自同一 Network 对象（避免调用方二次读文件）。
    """
    path = Path(path)
    net = rf.Network(str(path))
    freq_ghz = net.f / 1e9

    if net.nports == 1:
        z = net.z[:, 0, 0]
    elif port == "S22":
        z = net.z[:, 1, 1]
    else:
        z = net.z[:, 0, 0]

    z_mag = np.abs(z)
    z_db = 20.0 * np.log10(np.maximum(z_mag, 1e-12))
    phase = np.angle(z)

    if len(freq_ghz) != target_n_freq:
        freq_new = np.linspace(freq_ghz[0], freq_ghz[-1], target_n_freq)
        out = (
            freq_new.astype(np.float64),
            np.interp(freq_new, freq_ghz, z_db).astype(np.float64),
        )
        phase_out = np.interp(freq_new, freq_ghz, phase).astype(np.float64)
    else:
        out = (freq_ghz.astype(np.float64), z_db.astype(np.float64))
        phase_out = phase.astype(np.float64)

    if return_phase:
        return (*out, phase_out)
    return out


def uniform_sample_from_mask(
    freq: NDArray[np.float64],
    z_db: NDArray[np.float64],
    mask: NDArray[np.bool_],
    n_samples: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """在 mask 指定的区域内均匀采样 n_samples 个点。

    返回: (sampled_freq, sampled_z_db)
    """
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return np.array([]), np.array([])
    if n_samples >= len(idx):
        return freq[idx], z_db[idx]

    # 均匀间隔取点
    step = len(idx) / n_samples
    selected = [idx[int(i * step)] for i in range(n_samples)]
    return freq[selected], z_db[selected]


def _weighted_sample_from_mask(
    freq: NDArray[np.float64],
    z_db: NDArray[np.float64],
    mask: NDArray[np.bool_],
    n_samples: int,
    weights: NDArray[np.float64] | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """在 mask 指定的区域内按权重采样 n_samples 个点。

    weights 为 None 时退化为均匀采样。
    """
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return np.array([]), np.array([])
    if n_samples >= len(idx):
        return freq[idx], z_db[idx]

    if n_samples == 0 or len(idx) == 0:
        return np.array([], dtype=freq.dtype), np.array([], dtype=z_db.dtype)

    n_samples = min(n_samples, len(idx))

    if weights is not None:
        w = weights[idx]
        w = np.clip(w, 1e-8, None)
        w = w / w.sum()
        selected = np.random.choice(idx, size=n_samples, replace=False, p=w)
    else:
        step = len(idx) / n_samples
        selected = np.array([idx[int(i * step)] for i in range(n_samples)])

    return freq[selected], z_db[selected]


def fixed_rule_sample(
    freq: NDArray[np.float64],
    z_db: NDArray[np.float64],
    region_mask: dict[str, NDArray[np.bool_]],
    target_k: int = 300,
    fs: float = 0.0,
    fp: float = 0.0,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """固定规则采样：按区域配额分配采样点数，主模区内 fs/fp 附近更密。

    配额比例:
        主模区: 50%  (~target_k * 0.5)
        杂模区: 30%  (~target_k * 0.3)
        平滑区: 20%  (~target_k * 0.2)

    主模区内高斯加权：fs/fp 附近采样密度最高，向两侧指数衰减。
    """
    quotas = {
        "main": int(target_k * 0.5),
        "spurious": int(target_k * 0.3),
        "smooth": target_k - int(target_k * 0.5) - int(target_k * 0.3),
    }

    # 动态调整配额：空区域把配额转移给其他区域
    actual = {}
    for region in ["main", "spurious", "smooth"]:
        actual[region] = min(quotas[region], int(region_mask[region].sum()))

    leftover = target_k - sum(actual.values())
    # 把剩余配额按主模区:杂模区:平滑区 = 5:3:2 分配
    while leftover > 0:
        for region, quota in [("main", 5), ("spurious", 3), ("smooth", 2)]:
            if leftover <= 0:
                break
            available = int(region_mask[region].sum()) - actual[region]
            if available > 0:
                add = min(leftover, max(1, quota))
                add = min(add, available)
                actual[region] += add
                leftover -= add

    # 主模区：fs/fp 高斯加权
    main_weights = None
    n_total = len(freq)
    if fs > 0 and fp > fs:
        bw = fp - fs
        d_fs = np.abs(freq - fs) / (bw * 0.3)
        d_fp = np.abs(freq - fp) / (bw * 0.3)
        main_weights = np.zeros(n_total, dtype=np.float64)
        gauss = np.exp(-(np.minimum(d_fs, d_fp) ** 2))
        main_weights[region_mask["main"]] = gauss[region_mask["main"]]

    f_main, z_main = _weighted_sample_from_mask(
        freq, z_db, region_mask["main"], actual["main"], main_weights
    )
    f_spur, z_spur = _weighted_sample_from_mask(
        freq, z_db, region_mask["spurious"], actual["spurious"]
    )
    f_smooth, z_smooth = _weighted_sample_from_mask(
        freq, z_db, region_mask["smooth"], actual["smooth"]
    )

    f_all = np.concatenate([f_main, f_spur, f_smooth])
    z_all = np.concatenate([z_main, z_spur, z_smooth])

    order = np.argsort(f_all)
    return f_all[order], z_all[order]


def baseline_interpolate(
    sample_freq: NDArray[np.float64],
    sample_z: NDArray[np.float64],
    target_freq: NDArray[np.float64],
) -> NDArray[np.float64]:
    """用 cubic spline 将稀疏采样点插值到完整频率轴。

    作为重建网络的基线（残差结构）。
    """
    if len(sample_freq) < 4:
        # 点太少，退化为线性插值
        return np.interp(target_freq, sample_freq, sample_z)
    from scipy.interpolate import CubicSpline

    cs = CubicSpline(sample_freq, sample_z, extrapolate=True)
    return cs(target_freq).astype(np.float64)


def deterministic_sample_positions(
    freq_ghz: NDArray[np.float64],
    fs: float,
    fp: float,
    k: int = AlgorithmConfig.surr_k,
    dense_ratio: float = AlgorithmConfig.surr_dense_ratio,
    transition_ratio: float = AlgorithmConfig.surr_transition_ratio,
    dense_half_bw: float = AlgorithmConfig.surr_dense_half_bw,
    transition_half_bw: float = AlgorithmConfig.surr_transition_half_bw,
) -> NDArray[np.int64]:
    """确定性稀疏采样位置（规格 §5.3）。

    只依赖 fs/fp 与频率轴，不看频谱内容，保证训练/推理一致。
    返回 freq_ghz 网格上的升序索引。

    区域划分（Δ = fp - fs）：
        密集区  [fs-0.5Δ, fp+0.5Δ]   配额 k×dense_ratio
        过渡带  两侧各延伸至 ±1.5Δ    配额 k×transition_ratio（左右两带拼接后整体均匀取样）
        远带    其余                  剩余配额
    区域格点不足配额时，缺口按 密集→过渡→远带 顺序让渡。
    fs/fp 非法（fs<=0 或 fp<=fs）退化为全轴均匀取点。
    """
    n = len(freq_ghz)
    k = min(k, n)
    if fs <= 0 or fp <= fs:
        return np.linspace(0, n - 1, k).round().astype(np.int64)

    delta = fp - fs
    fmin, fmax = float(freq_ghz[0]), float(freq_ghz[-1])

    dense_lo = max(fs - dense_half_bw * delta, fmin)
    dense_hi = min(fp + dense_half_bw * delta, fmax)
    trans_lo = max(fs - transition_half_bw * delta, fmin)
    trans_hi = min(fp + transition_half_bw * delta, fmax)

    def _idx(lo: float, hi: float) -> NDArray[np.int64]:
        return np.where((freq_ghz >= lo) & (freq_ghz <= hi))[0]

    dense_idx = _idx(dense_lo, dense_hi)
    trans_idx = np.concatenate(
        [_idx(trans_lo, dense_lo - 1e-12), _idx(dense_hi + 1e-12, trans_hi)]
    )
    occupied = np.zeros(n, dtype=bool)
    occupied[dense_idx] = True
    occupied[trans_idx] = True
    far_idx = np.where(~occupied)[0]

    def _take(idx: NDArray[np.int64], m: int) -> NDArray[np.int64]:
        """在 idx 内均匀取 m 个（m <= len(idx)）。"""
        if m <= 0 or len(idx) == 0:
            return np.array([], dtype=np.int64)
        sel = np.linspace(0, len(idx) - 1, min(m, len(idx))).round().astype(np.int64)
        return idx[sel]

    # 初始配额
    q_dense = int(round(k * dense_ratio))
    q_trans = int(round(k * transition_ratio))
    q_far = k - q_dense - q_trans

    take_dense = min(q_dense, len(dense_idx))
    take_trans = min(q_trans, len(trans_idx))
    take_far = min(q_far, len(far_idx))

    # 让渡：缺口按 密集→过渡→远带 顺序补给有余量的区域
    leftover = k - (take_dense + take_trans + take_far)
    caps = [
        ("dense", len(dense_idx) - take_dense),
        ("trans", len(trans_idx) - take_trans),
        ("far", len(far_idx) - take_far),
    ]
    while leftover > 0 and any(c > 0 for _, c in caps):
        for i, (name, cap) in enumerate(caps):
            if leftover <= 0:
                break
            if cap <= 0:
                continue
            if name == "dense":
                take_dense += 1
            elif name == "trans":
                take_trans += 1
            else:
                take_far += 1
            caps[i] = (name, cap - 1)
            leftover -= 1

    result = np.concatenate(
        [
            _take(dense_idx, take_dense),
            _take(trans_idx, take_trans),
            _take(far_idx, take_far),
        ]
    )
    return np.sort(result)
