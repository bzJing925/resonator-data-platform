"""EPPE/EPE 模态标签提取（freq/z11 数组接口）。

出处：TE1 相关函数（``find_te1_resonances`` / ``estimate_q_factor`` / kt2 恒等式）
适配自 ADSML train-export 工作区 ``src/label_extractor.py``（客户 CLI 脚本移植版），
只保留 freq/z11 数组接口（YAGNI：不含 pandas / s1p 文件批处理部分）。

本次为 IUS2026 修订新增：
- ``find_te2_resonances``：在指定搜索窗（如 >10 GHz）内定位 TE2 谐振对
  （fs2=|Z| 极小，fp2=其后 |Z| 极大，均需满足 prominence 阈值）。
- ``compute_q_bode_phase``：标准 Bode-Q 定义，从 Z→S11(50Ω) 相位实现：
  Q_bode(f0) = (f0/2) * |dφ/df|_{f0}，φ = unwrap(angle(S11))。
  注意：vendor 的 ``compute_q_bode`` 只是 fs/(fp-fs) 占位实现，此处故意不沿用。
- ``extract_eppe_labels``：EPPE 标签汇总（TE1 残余耦合 + TE2 主模态）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.signal import find_peaks

from app.mason_sim.solver import db20, s11_from_z11

# ============================================================
# Labels dataclass
# ============================================================


@dataclass(frozen=True)
class EPPELabels:
    """EPPE 双层模态标签。

    Attributes:
        fs1_hz: TE1 残余串联谐振频率（未检出为 nan）
        fp1_hz: TE1 残余并联谐振频率（未检出为 nan）
        kt2_te1: TE1 残余有效机电耦合系数（未检出为 0.0）
        fs2_hz: TE2 串联谐振频率（未检出为 nan）
        fp2_hz: TE2 并联谐振频率（未检出为 nan）
        kt2_te2: TE2 有效机电耦合系数（未检出为 0.0）
        qs2: TE2 串联 Q（-3 dB 带宽法）
        qp2: TE2 并联 Q（-3 dB 带宽法）
        zp2_ohm: fp2 处 |Z| 幅值 (Ω)
        qbode_fs2: fs2 处 Bode-Q（S11 相位斜率法）
        qbode_fp2: fp2 处 Bode-Q（S11 相位斜率法）
    """

    fs1_hz: float
    fp1_hz: float
    kt2_te1: float
    fs2_hz: float
    fp2_hz: float
    kt2_te2: float
    qs2: float
    qp2: float
    zp2_ohm: float
    qbode_fs2: float
    qbode_fp2: float


# ============================================================
# kt2 identity
# ============================================================
def kt2_identity(fs_hz: float, fp_hz: float) -> float:
    """有效机电耦合系数恒等式：kt2_eff = (π²/4) * (fp - fs) / fp。"""
    return float((np.pi**2 / 4.0) * (fp_hz - fs_hz) / fp_hz)


# ============================================================
# TE1 resonances（适配自 vendor label_extractor.py）
# ============================================================
def _prominence_threshold_db(z_mag_db: np.ndarray) -> float:
    """根据 |Z|_dB 动态范围计算 prominence 阈值。"""
    db_min, db_max = float(np.min(z_mag_db)), float(np.max(z_mag_db))
    dynamic_range = max(db_max - db_min, 1e-12)
    # 至少 3 dB，且不低于动态范围的 10%
    return max(3.0, dynamic_range * 0.1)


def find_te1_resonances(
    freq: np.ndarray,
    z_mag: np.ndarray,
) -> tuple[float, float]:
    """仅提取 TE1 模态的 fs 与 fp，忽略高频 TE2 等更高阶模态。

    策略：
      1. 在 |Z|_dB 上检测显著局部极小值（候选 fs）和极大值（候选 fp）。
      2. 排除靠近扫频边界的假峰（电容区斜率或插值边缘）。
      3. TE1 的 fs 为最低频率的串联谐振点。
      4. TE1 的 fp 为高于 fs 的最低频率的并联谐振点。

    返回:
        (fs_hz, fp_hz)。若未找到有效谐振对，则返回 (nan, nan)。
    """
    if len(freq) < 3:
        return float("nan"), float("nan")

    z_mag_db = db20(z_mag)
    prominence_db = _prominence_threshold_db(z_mag_db)

    # 忽略边界 2% 的点，避免把电容区边缘或插值伪峰当作谐振
    edge_skip = max(1, int(round(len(freq) * 0.02)))

    # 串联谐振 = |Z|_dB 极小值
    fs_indices, _ = find_peaks(-z_mag_db, prominence=prominence_db)
    fs_indices = fs_indices[(fs_indices >= edge_skip) & (fs_indices < len(freq) - edge_skip)]
    if len(fs_indices) == 0:
        # 退化：在有效区间内找全局最小值
        inner = z_mag_db[edge_skip:-edge_skip]
        fs_indices = np.array([edge_skip + int(np.argmin(inner))])
    fs_candidates = sorted([(float(freq[i]), int(i)) for i in fs_indices])

    # 并联谐振 = |Z|_dB 极大值
    fp_indices, _ = find_peaks(z_mag_db, prominence=prominence_db)
    fp_indices = fp_indices[(fp_indices >= edge_skip) & (fp_indices < len(freq) - edge_skip)]
    if len(fp_indices) == 0:
        inner = z_mag_db[edge_skip:-edge_skip]
        fp_indices = np.array([edge_skip + int(np.argmax(inner))])
    fp_candidates = sorted([(float(freq[i]), int(i)) for i in fp_indices])

    # TE1 fs 为最低频率的串联谐振
    fs_hz = fs_candidates[0][0]

    # TE1 fp 为高于 fs 的最低频率并联谐振
    fp_hz = float("nan")
    for fp_cand, _ in fp_candidates:
        if fp_cand > fs_hz:
            fp_hz = fp_cand
            break

    # 如果找不到高于 fs 的 fp，退化为全局最大
    if np.isnan(fp_hz) and len(fp_candidates) > 0:
        fp_hz = fp_candidates[0][0]

    return fs_hz, fp_hz


def find_te2_resonances(
    freq: np.ndarray,
    z_mag: np.ndarray,
    f_min_hz: float,
    f_max_hz: float | None = None,
) -> tuple[float, float]:
    """在指定搜索窗内定位 TE2 谐振对（fs2=|Z| 极小，fp2=其后 |Z| 极大）。

    与 TE1 提取相同的 prominence 策略，但限制在 f >= f_min_hz 的窗口内：
      1. fs2 取窗内 prominence 最大的 |Z|_dB 极小值；
      2. fp2 取 fs2 之后最近的显著 |Z|_dB 极大值；
      3. 窗内无显著谐振（如 TE2 被对称性抑制）时返回 (nan, nan)。

    返回:
        (fs2_hz, fp2_hz)。未检出返回 (nan, nan)。
    """
    mask = freq >= f_min_hz
    if f_max_hz is not None:
        mask &= freq <= f_max_hz
    if int(np.count_nonzero(mask)) < 3:
        return float("nan"), float("nan")

    sub_freq = freq[mask]
    sub_db = db20(z_mag[mask])
    prominence_db = _prominence_threshold_db(sub_db)

    # 忽略窗内边界点，避免把窗口边缘的斜坡端点当作谐振
    edge_skip = max(1, int(round(len(sub_freq) * 0.02)))

    min_indices_all, min_props = find_peaks(-sub_db, prominence=prominence_db)
    keep = (min_indices_all >= edge_skip) & (min_indices_all < len(sub_freq) - edge_skip)
    min_indices = min_indices_all[keep]
    if len(min_indices) == 0:
        return float("nan"), float("nan")
    # fs2：prominence 最大的极小值（prominences 与过滤前索引一一对应，按同一掩膜过滤）
    kept_proms = min_props["prominences"][keep]
    fs2_idx = int(min_indices[int(np.argmax(kept_proms))])

    max_indices, _ = find_peaks(sub_db, prominence=prominence_db)
    max_indices = max_indices[
        (max_indices >= edge_skip) & (max_indices < len(sub_freq) - edge_skip)
    ]
    fp_after = max_indices[max_indices > fs2_idx]
    if len(fp_after) == 0:
        return float("nan"), float("nan")
    fp2_idx = int(fp_after[0])

    return float(sub_freq[fs2_idx]), float(sub_freq[fp2_idx])


# ============================================================
# Q factors
# ============================================================
def estimate_q_factor(
    freq: np.ndarray,
    z_mag: np.ndarray,
    f0: float,
    resonance_type: Literal["series", "parallel"] = "series",
) -> float:
    """用 -3 dB 带宽法估计 Q 值。

    在 f0 附近找到 |Z| 峰值/谷值两侧 -3 dB 点，Q = f0 / BW。
    """
    if not np.isfinite(f0):
        return float("nan")
    idx_peak = int(np.argmin(np.abs(freq - f0)))

    if resonance_type == "series":
        z0 = z_mag[idx_peak]  # 最小值
        z_3db = z0 * np.sqrt(2.0)  # +3 dB 点
    else:
        z0 = z_mag[idx_peak]  # 最大值
        z_3db = z0 / np.sqrt(2.0)  # -3 dB 点

    # 搜索左侧 -3 dB 点
    left_idx = idx_peak
    for i in range(idx_peak - 1, -1, -1):
        if resonance_type == "series":
            if z_mag[i] >= z_3db:
                left_idx = i
                break
        else:
            if z_mag[i] <= z_3db:
                left_idx = i
                break

    # 搜索右侧 -3 dB 点
    right_idx = idx_peak
    for i in range(idx_peak + 1, len(freq)):
        if resonance_type == "series":
            if z_mag[i] >= z_3db:
                right_idx = i
                break
        else:
            if z_mag[i] <= z_3db:
                right_idx = i
                break

    bw = freq[right_idx] - freq[left_idx]
    if bw <= 0:
        return float("nan")
    return float(f0 / bw)


def compute_q_bode_phase(
    freq: np.ndarray,
    z11: np.ndarray,
    f0: float,
    zref: float = 50.0,
) -> float:
    """标准 Bode-Q：Q_bode(f0) = (f0/2) * |dφ/df|_{f0}。

    其中 φ = unwrap(angle(S11))，S11 由 Z11 按参考阻抗 zref（默认 50 Ω）变换，
    相位斜率用中心差分（np.gradient）并在 f0 处线性插值取值。
    频点数 < 2 无法数值微分时返回 nan。
    """
    if not np.isfinite(f0) or f0 <= 0.0 or len(freq) < 2:
        return float("nan")
    s11 = s11_from_z11(z11, zref)
    phase = np.unwrap(np.angle(s11))
    dphi_df = np.gradient(phase, freq)
    slope = float(np.interp(f0, freq, dphi_df))
    return float(0.5 * f0 * abs(slope))


# ============================================================
# EPPE label extraction
# ============================================================
def extract_eppe_labels(
    freq: np.ndarray,
    z11: np.ndarray,
    te2_f_min_hz: float = 10e9,
    te2_f_max_hz: float | None = None,
    zref: float = 50.0,
) -> EPPELabels:
    """从 EPPE/EPE 一端口 Z11 频响提取模态标签。

    - TE1 残余耦合：在 f < te2_f_min_hz 区域用 vendor TE1 方法提取
      （fs1/fp1/kt2_te1），作诊断量；未检出时 kt2_te1 = 0.0。
    - TE2 主模态：在 te2_f_min_hz <= f <= te2_f_max_hz 窗口内定位 (fs2, fp2)，
      kt2_te2 用同一恒等式 (π²/4)(fp2-fs2)/fp2；Qs2/Qp2 用 -3 dB 带宽法；
      Zp2 为 fp2 处 |Z| 幅值；Bode-Q 在 fs2/fp2 各出一个。
      未检出时 kt2_te2 = 0.0，Q/Bode-Q/Zp2 为 nan。
      te2_f_max_hz 用于排除窗内更高阶奇模（如同极性堆叠的 TE3）。
    """
    freq = np.asarray(freq, dtype=float)
    z11 = np.asarray(z11, dtype=complex)
    z_mag = np.abs(z11)

    # ---- TE1 残余（窗口以下） ----
    te1_mask = freq < te2_f_min_hz
    if int(np.count_nonzero(te1_mask)) >= 3:
        fs1, fp1 = find_te1_resonances(freq[te1_mask], z_mag[te1_mask])
    else:
        fs1, fp1 = float("nan"), float("nan")
    if np.isnan(fs1) or np.isnan(fp1) or fp1 <= fs1:
        # 未检出有效谐振对（含 fallback 假对 fp1<=fs1）：统一报 nan / 0.0
        fs1, fp1, kt2_te1 = float("nan"), float("nan"), 0.0
    else:
        kt2_te1 = kt2_identity(fs1, fp1)

    # ---- TE2 主模态（窗口以内） ----
    fs2, fp2 = find_te2_resonances(freq, z_mag, f_min_hz=te2_f_min_hz, f_max_hz=te2_f_max_hz)
    if np.isnan(fs2) or np.isnan(fp2) or fp2 <= fs2:
        fs2, fp2 = float("nan"), float("nan")
        kt2_te2 = 0.0
        qs2 = qp2 = zp2 = qbode_fs2 = qbode_fp2 = float("nan")
    else:
        kt2_te2 = kt2_identity(fs2, fp2)
        qs2 = estimate_q_factor(freq, z_mag, fs2, resonance_type="series")
        qp2 = estimate_q_factor(freq, z_mag, fp2, resonance_type="parallel")
        zp2 = float(z_mag[int(np.argmin(np.abs(freq - fp2)))])
        qbode_fs2 = compute_q_bode_phase(freq, z11, fs2, zref=zref)
        qbode_fp2 = compute_q_bode_phase(freq, z11, fp2, zref=zref)

    return EPPELabels(
        fs1_hz=fs1,
        fp1_hz=fp1,
        kt2_te1=kt2_te1,
        fs2_hz=fs2,
        fp2_hz=fp2,
        kt2_te2=kt2_te2,
        qs2=qs2,
        qp2=qp2,
        zp2_ohm=zp2,
        qbode_fs2=qbode_fs2,
        qbode_fp2=qbode_fp2,
    )
