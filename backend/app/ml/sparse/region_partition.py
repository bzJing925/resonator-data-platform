"""谐振频谱物理分区器。

将 Z11(dB) 频谱划分为主模区、杂模区、平滑区，为自适应稀疏采样提供先验。
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.signal import find_peaks, savgol_filter


def compute_curvature(z_db: NDArray[np.float64], freq: NDArray[np.float64]) -> NDArray[np.float64]:
    """计算 Z11(dB) 对频率的归一化二阶导数（相对曲率）。

    步骤:
    1. Savitzky-Golay 平滑去噪
    2. 计算二阶导数
    3. 除以 (1 + |一阶导数|) 做归一化，避免谐振峰尖导致曲率爆炸
    """
    # 平滑去噪（窗口自动适应数据长度）
    window = min(51, len(z_db) // 10 * 2 + 1)
    if window >= 5:
        z_smooth = savgol_filter(z_db, window_length=window, polyorder=3)
    else:
        z_smooth = z_db

    dz = np.gradient(z_smooth, freq)
    d2z = np.gradient(dz, freq)

    # 归一化：相对曲率 = |d²z| / (1 + |dz|)
    # 这样谐振峰附近的一阶导数大，会抑制二阶导数的绝对值
    rel_curvature = np.abs(d2z) / (1.0 + np.abs(dz))
    return rel_curvature


def find_resonances_simple(
    z_db: NDArray[np.float64],
) -> tuple[int, int]:
    """简化的 fs/fp 检测：直接在 Z11_dB 上找最小/最大。

    fs: Z11_dB 最低点（阻抗最小 = 串联谐振）
    fp: Z11_dB 最高点（阻抗最大 = 并联谐振）

    Returns:
        (fs_idx, fp_idx)，保证 fs_idx < fp_idx
    """
    fs_idx = int(np.argmin(z_db))
    # fp 必须在 fs 之后
    fp_candidates = z_db[fs_idx:]
    fp_rel = int(np.argmax(fp_candidates))
    fp_idx = fs_idx + fp_rel
    return fs_idx, fp_idx


def detect_spurious_peaks(
    z_db: NDArray[np.float64],
    freq: NDArray[np.float64],
    main_mask: NDArray[np.bool_],
    prominence_db: float = 2.0,
) -> NDArray[np.bool_]:
    """检测平滑区内的异常局部峰（杂模候选）。

    只在非主模区搜索，避免把主谐振峰误判为杂模。
    """
    spur_mask = np.zeros_like(z_db, dtype=np.bool_)

    # 对非主模区平滑后找峰
    search_region = ~main_mask
    if not search_region.any():
        return spur_mask

    # 提取非主模区片段
    z_smooth = savgol_filter(z_db, window_length=min(51, len(z_db) // 10 * 2 + 1), polyorder=3)

    # 只在非主模区找峰
    z_search = np.where(search_region, z_smooth, -np.inf)
    peaks, props = find_peaks(z_search, prominence=prominence_db)

    for p in peaks:
        # 扩展峰区域 ±5 点
        start = max(0, p - 5)
        end = min(len(z_db), p + 6)
        spur_mask[start:end] = True

    return spur_mask


def partition_regions(
    z_db: NDArray[np.float64],
    freq: NDArray[np.float64],
    alpha: float = 0.5,
    curvature_threshold: float = 0.5,
    spur_prominence_db: float = 2.0,
) -> dict[str, NDArray[np.bool_]]:
    """将 Z11(dB) 频谱分区为主模/杂模/平滑三区。

    Args:
        z_db: Z11(dB) 数组，shape (N,)
        freq: 频率数组 (GHz)，shape (N,)
        alpha: 主模区扩展系数，覆盖 [fs - α·BW, fp + α·BW]
        curvature_threshold: 曲率阈值 (dB/GHz²)，超过视为杂模
        spur_prominence_db: 异常峰 prominence 阈值 (dB)

    Returns:
        {
            "main":     bool mask (N,) — 主模区
            "spurious": bool mask (N,) — 杂模区
            "smooth":   bool mask (N,) — 平滑区
        }
        三个 mask 互斥且并集为全 True。
    """
    n = len(z_db)
    fs_idx, fp_idx = find_resonances_simple(z_db)
    fs = float(freq[fs_idx])
    fp = float(freq[fp_idx])
    bw = fp - fs

    # 1. 主模区
    main_mask = np.zeros(n, dtype=np.bool_)
    f_main_low = fs - alpha * bw
    f_main_high = fp + alpha * bw
    main_mask = (freq >= f_main_low) & (freq <= f_main_high)

    # 2. 杂模区 — 条件A: 高曲率（用自适应阈值：95%分位数）
    rel_curv = compute_curvature(z_db, freq)
    adaptive_thr = np.percentile(rel_curv, 95) * 0.5  # 取 95% 分位数的一半作为阈值
    curvature_mask = rel_curv > max(curvature_threshold, adaptive_thr)

    # 2. 杂模区 — 条件B: 平滑区异常峰
    spur_peak_mask = detect_spurious_peaks(z_db, freq, main_mask, prominence_db=spur_prominence_db)

    # 杂模 = 高曲率 或 异常峰，但不能在主模区内
    spurious_mask = (curvature_mask | spur_peak_mask) & (~main_mask)

    # 3. 平滑区 = 其余
    smooth_mask = ~(main_mask | spurious_mask)

    return {
        "main": main_mask,
        "spurious": spurious_mask,
        "smooth": smooth_mask,
    }


def get_region_stats(
    region_mask: dict[str, NDArray[np.bool_]],
) -> dict[str, int]:
    """返回各区域的点数统计。"""
    return {
        "main": int(region_mask["main"].sum()),
        "spurious": int(region_mask["spurious"].sum()),
        "smooth": int(region_mask["smooth"].sum()),
    }
