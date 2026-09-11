"""EPE 语料的生产口径标签封装。

关键约束（IUS2026 WP3 校准前提）：语料标签口径必须与生产代理（实测数据管线）
完全一致。因此本模块**不重新发明任何公式**：

- TE1 峰定位：``app.mason_sim.labels.find_te1_resonances``（freq/z11 数组接口，
  返回 fs/fp 频率后转网格索引）——这是本模块唯一自己负责的部分；
- k²eff：生产 ``app/core/extract.py:512`` 内联公式
  ``(π²/4)·(fs/fp)·((fp−fs)/fp)·100``（注意与 WP1 vendor 恒等式差一个 (fs/fp) 因子），
  由 ``k2eff_pct_production`` 原样承载；
- Qs/Qp：生产 ``calc_q_phase``（Z 相位斜率法，np.angle 不 unwrap，保持原样调用）；
- Bode-Q：生产 ``calc_bodeq``（bodeq=|ω·τ·|S||/(1−|S|²)，τ=群延迟，savgol 平滑，
  窗口/阶次取 AlgorithmConfig 默认值），S11 由 ``s11_from_z11(z, 50)`` 变换。

``extract_epe_production_labels`` 返回 dict，7 个参数键序与生产
``AlgorithmConfig.surr_param_cols`` 完全一致：
(fs_ghz, fp_ghz, k2eff_pct, qs, qp, qs_bodeq, qp_bodeq)，外加 valid 标志。
valid=False 规则：找不到谐振对或 fp<=fs；或 qs/qp/qs_bodeq/qp_bodeq 任一为 nan
（含 calc_bodeq 抛 ExtractError 的情形，如无损耗网络 |S11|≡1）。

``extract_eppe_production_labels``（IUS2026 WP2b 新增）为 EPPE 反极性双层的同口径
封装：7 参数锚在 TE2 对（(10,20] GHz 窗，find_te2_resonances），另附 TE1 诊断
字段（[3,8) GHz 窗；反极性抑制下找不到记 nan，不影响 valid）与 reason 分类
（ok/no_resonance/te2_out_of_window/label_nan）。
"""

from __future__ import annotations

import numpy as np

from app.core.extract import ExtractError, calc_bodeq, calc_q_phase
from app.mason_sim.labels import find_te1_resonances, find_te2_resonances
from app.mason_sim.solver import s11_from_z11

# 7 个参数键，顺序固定（= 生产 AlgorithmConfig.surr_param_cols）
PARAM_KEYS: tuple[str, ...] = (
    "fs_ghz",
    "fp_ghz",
    "k2eff_pct",
    "qs",
    "qp",
    "qs_bodeq",
    "qp_bodeq",
)


def k2eff_pct_production(fs_hz: float, fp_hz: float) -> float:
    """生产 k²eff 公式（extract.py:512）：(π²/4)·(fs/fp)·((fp−fs)/fp)·100。"""
    return float((np.pi**2 / 4) * (fs_hz / fp_hz) * ((fp_hz - fs_hz) / fp_hz) * 100)


def extract_epe_production_labels(
    freq_hz: np.ndarray,
    z11: np.ndarray,
    zref: float = 50.0,
) -> dict[str, float | bool]:
    """从 EPE 一端口 Z11 频响提取生产口径的 7 参数标签 + valid 标志。

    Args:
        freq_hz: 频率网格 (Hz)，等距。
        z11: 输入阻抗频响（复数）。
        zref: S11 变换参考阻抗（Ω），默认 50（与生产一致）。

    Returns:
        dict，键序为 PARAM_KEYS + ("valid",)。valid=False 时未计算的参数为 nan；
        可计算的诊断值（如 fs_ghz/fp_ghz/k2eff_pct/qs/qp）仍保留。
    """
    freq = np.asarray(freq_hz, dtype=float)
    z = np.asarray(z11, dtype=complex)

    nan = float("nan")
    labels: dict[str, float | bool] = {k: nan for k in PARAM_KEYS}
    labels["valid"] = False

    # ---- TE1 峰定位（本模块唯一自负责部分）：频率 → 网格索引 ----
    fs_hz, fp_hz = find_te1_resonances(freq, np.abs(z))
    if not (np.isfinite(fs_hz) and np.isfinite(fp_hz)) or fp_hz <= fs_hz:
        return labels

    fs_idx = int(np.argmin(np.abs(freq - fs_hz)))
    fp_idx = int(np.argmin(np.abs(freq - fp_hz)))

    labels["fs_ghz"] = float(freq[fs_idx]) / 1e9
    labels["fp_ghz"] = float(freq[fp_idx]) / 1e9
    labels["k2eff_pct"] = k2eff_pct_production(float(freq[fs_idx]), float(freq[fp_idx]))

    # ---- Qs/Qp：生产相位斜率法（原样调用，np.angle 不 unwrap） ----
    qs, qp = calc_q_phase(z, freq, fs_idx, fp_idx)
    labels["qs"] = qs
    labels["qp"] = qp

    # ---- Bode-Q：生产 calc_bodeq（savgol + 洛伦兹拟合），S11 按 zref 变换 ----
    s11 = s11_from_z11(z, zref)
    try:
        bodeq = calc_bodeq(s11, freq, fs_idx, fp_idx)
    except ExtractError:
        return labels  # qs_bodeq/qp_bodeq 保持 nan → valid=False
    labels["qs_bodeq"] = bodeq["qs_bodeq"]
    labels["qp_bodeq"] = bodeq["qp_bodeq"]

    q_keys = ("qs", "qp", "qs_bodeq", "qp_bodeq")
    labels["valid"] = all(np.isfinite(float(labels[k])) for k in q_keys)
    return labels


# ============================================================
# EPPE 生产口径标签（TE2 主模态 + TE1 诊断）
# ============================================================

# EPPE 语料的固定窗口约定（IUS2026 WP2b）：
# - TE2 主模态窗 (10, 20] GHz（求解网格 3–22 GHz @2 MHz 保证窗内 5001 点）；
# - TE1 诊断窗 [3, 8) GHz（反极性器件的 TE1 残余；找不到不判无效，记 nan）。
EPPE_TE2_WINDOW_HZ: tuple[float, float] = (10.0e9, 20.0e9)
EPPE_TE1_DIAG_WINDOW_HZ: tuple[float, float] = (3.0e9, 8.0e9)

# 诊断字段键（顺序固定，对应语料 diag 数组前 3 列）
EPPE_DIAG_KEYS: tuple[str, ...] = ("fs1_ghz", "fp1_ghz", "k2eff_te1_pct")

# reason 分类：ok / no_resonance（窗内无有效 TE2 对）/ te2_out_of_window（断言防御：
# 找到对但 fs2 越窗，正常路径不可达）/ label_nan（对有效但 Q/Bode-Q 任一为 nan）
EPPE_REASONS: tuple[str, ...] = ("ok", "no_resonance", "te2_out_of_window", "label_nan")


def _te1_diagnostic(
    freq: np.ndarray,
    z_mag: np.ndarray,
    f_min_hz: float,
    f_max_hz: float,
) -> tuple[float, float, float]:
    """TE1 诊断：诊断窗内跑 TE1 提取（生产 k²eff 式）。找不到有效对记 nan。

    反极性 EPPE 的 TE1 被对称性抑制，找不到是【正常】情形，绝不影响 valid。
    """
    nan = float("nan")
    mask = (freq >= f_min_hz) & (freq < f_max_hz)
    if int(np.count_nonzero(mask)) < 3:
        return nan, nan, nan
    fs1_hz, fp1_hz = find_te1_resonances(freq[mask], z_mag[mask])
    if not (np.isfinite(fs1_hz) and np.isfinite(fp1_hz)) or fp1_hz <= fs1_hz:
        return nan, nan, nan
    return fs1_hz / 1e9, fp1_hz / 1e9, k2eff_pct_production(fs1_hz, fp1_hz)


def extract_eppe_production_labels(
    freq_hz: np.ndarray,
    z11: np.ndarray,
    zref: float = 50.0,
    te2_f_min_hz: float = EPPE_TE2_WINDOW_HZ[0],
    te2_f_max_hz: float = EPPE_TE2_WINDOW_HZ[1],
    te1_diag_f_min_hz: float = EPPE_TE1_DIAG_WINDOW_HZ[0],
    te1_diag_f_max_hz: float = EPPE_TE1_DIAG_WINDOW_HZ[1],
) -> dict[str, float | bool | str]:
    """从 EPPE 一端口 Z11 频响提取生产口径的 TE2 七参数标签 + TE1 诊断 + valid。

    与 extract_epe_production_labels 同口径（不重新发明任何公式），但全部锚在
    TE2 对（fs2/fp2）上：
    - TE2 对定位：find_te2_resonances，(te2_f_min_hz, te2_f_max_hz] 窗
      （默认 (10, 20] GHz）；
    - k²eff：生产 extract.py:512 公式（k2eff_pct_production）；
    - Qs/Qp：生产 calc_q_phase；Bode-Q：生产 calc_bodeq（S11 按 zref 变换）。

    valid 规则：TE2 对找到且 fp2>fs2 且 qs/qp/qs_bodeq/qp_bodeq 全非 nan，
    且 fs2 ∈ (te2_f_min_hz, te2_f_max_hz]（窗由求解网格与检测窗保证，此处为
    断言防御，越窗时 reason="te2_out_of_window"）。

    Returns:
        dict，键序为 PARAM_KEYS + EPPE_DIAG_KEYS + ("valid", "reason")。
        TE1 诊断（fs1_ghz/fp1_ghz/k2eff_te1_pct）找不到时记 nan，不影响 valid。
    """
    freq = np.asarray(freq_hz, dtype=float)
    z = np.asarray(z11, dtype=complex)

    nan = float("nan")
    labels: dict[str, float | bool | str] = {k: nan for k in PARAM_KEYS}
    labels["fs1_ghz"], labels["fp1_ghz"], labels["k2eff_te1_pct"] = _te1_diagnostic(
        freq, np.abs(z), te1_diag_f_min_hz, te1_diag_f_max_hz
    )
    labels["valid"] = False
    labels["reason"] = "no_resonance"

    # ---- TE2 对定位（窗内 prominence 最强对） ----
    fs2_hz, fp2_hz = find_te2_resonances(
        freq, np.abs(z), f_min_hz=te2_f_min_hz, f_max_hz=te2_f_max_hz
    )
    if not (np.isfinite(fs2_hz) and np.isfinite(fp2_hz)) or fp2_hz <= fs2_hz:
        return labels

    fs2_idx = int(np.argmin(np.abs(freq - fs2_hz)))
    fp2_idx = int(np.argmin(np.abs(freq - fp2_hz)))
    fs2_hz = float(freq[fs2_idx])  # 网格对齐后的频率（与生产一致用网格值）
    fp2_hz = float(freq[fp2_idx])

    # 断言防御：检测窗已保证 fs2 ∈ 窗，此处拦截理论上的越窗（如调用方自造对）
    if not (te2_f_min_hz < fs2_hz <= te2_f_max_hz):
        labels["reason"] = "te2_out_of_window"
        return labels

    labels["fs_ghz"] = fs2_hz / 1e9
    labels["fp_ghz"] = fp2_hz / 1e9
    labels["k2eff_pct"] = k2eff_pct_production(fs2_hz, fp2_hz)

    # ---- Qs/Qp：生产相位斜率法（原样调用，np.angle 不 unwrap） ----
    qs, qp = calc_q_phase(z, freq, fs2_idx, fp2_idx)
    labels["qs"] = qs
    labels["qp"] = qp

    # ---- Bode-Q：生产 calc_bodeq（savgol + 洛伦兹拟合），S11 按 zref 变换 ----
    s11 = s11_from_z11(z, zref)
    try:
        bodeq = calc_bodeq(s11, freq, fs2_idx, fp2_idx)
    except ExtractError:
        labels["reason"] = "label_nan"  # qs_bodeq/qp_bodeq 保持 nan → valid=False
        return labels
    labels["qs_bodeq"] = bodeq["qs_bodeq"]
    labels["qp_bodeq"] = bodeq["qp_bodeq"]

    q_keys = ("qs", "qp", "qs_bodeq", "qp_bodeq")
    labels["valid"] = all(np.isfinite(float(labels[k])) for k in q_keys)
    labels["reason"] = "ok" if labels["valid"] else "label_nan"
    return labels
