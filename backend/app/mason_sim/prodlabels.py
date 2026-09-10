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
"""

from __future__ import annotations

import numpy as np

from app.core.extract import ExtractError, calc_bodeq, calc_q_phase
from app.mason_sim.labels import find_te1_resonances
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
