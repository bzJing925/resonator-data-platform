"""无 torch 依赖的稀疏采样工具。

从 S1P/S2P 文件读取 |Z| dB 频谱，并做 fs/fp 附近密集、远带稀疏的固定规则采样。
供训练数据导出和稀疏重建数据集共用。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import skrf as rf
from numpy.typing import NDArray

from app.ml.sparse.region_partition import partition_regions

__all__ = [
    "baseline_interpolate",
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
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """透明读取 S1P/S2P 并返回指定端口的 (freq_ghz, |Z|_dB)。

    Args:
        path: S1P 或 S2P 文件路径。
        port: "S11" 或 "S22"；S1P 时忽略该参数。
        target_n_freq: 统一插值后的频点数。
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

    if len(freq_ghz) != target_n_freq:
        freq_new = np.linspace(freq_ghz[0], freq_ghz[-1], target_n_freq)
        z_db_new = np.interp(freq_new, freq_ghz, z_db)
        return freq_new.astype(np.float64), z_db_new.astype(np.float64)

    return freq_ghz.astype(np.float64), z_db.astype(np.float64)


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
