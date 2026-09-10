"""生产口径 EPE 标签封装（prodlabels）测试。

核心判据：封装结果必须与"手工逐步调生产管线函数"逐值完全一致
（k²eff 用 extract.py:512 公式、Qs/Qp 用 calc_q_phase、Bode-Q 用 calc_bodeq，
TE1 峰定位用 mason_sim.labels.find_te1_resonances）。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.extract import calc_bodeq, calc_q_phase
from app.mason_sim import (
    ALN1,
    MO,
    SIN,
    EPEStackParams,
    build_frequency_grid,
    find_te1_resonances,
    nm_to_m,
    run_epe_stack_sweep,
    s11_from_z11,
    um2_to_m2,
)
from app.mason_sim.prodlabels import extract_epe_production_labels, k2eff_pct_production

PARAM_KEYS = ("fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "qs_bodeq", "qp_bodeq")


def _solve_sample() -> tuple[np.ndarray, np.ndarray]:
    """非对称电极 + PF 加载的 EPE 堆叠，1 MHz 网格（与语料生成同口径）。"""
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=1e6)
    params = EPEStackParams(
        t_piezo=nm_to_m(700.0),
        t_top=nm_to_m(180.0),
        t_bot=nm_to_m(120.0),
        t_pf=nm_to_m(40.0),
        area=um2_to_m2(2000.0),
        alpha=0.02,
    )
    result = run_epe_stack_sweep(freq, ALN1, MO, SIN, params)
    return freq, result.z11


def test_k2eff_handcheck_4p8_over_5p0() -> None:
    """k²eff 生产公式手算核对：fs=4.8/fp=5.0 → (π²/4)(4.8/5)(0.2/5)×100。

    精确手算值：2.4674011002723395 × 0.96 = 2.3687050562614459；
    × 0.04 = 0.09474820225045784；× 100 = 9.474820225045784。
    """
    assert k2eff_pct_production(4.8e9, 5.0e9) == pytest.approx(9.474820225045784, rel=1e-12)


def test_wrapper_matches_manual_production_calls() -> None:
    """同一合成 Z11：封装 7 参数与手工逐步调生产函数的结果完全相等（==）。"""
    freq, z11 = _solve_sample()
    labels = extract_epe_production_labels(freq, z11)
    assert labels["valid"] is True
    assert tuple(labels.keys()) == PARAM_KEYS + ("valid",)

    # ---- 手工逐步复现（生产口径） ----
    fs_hz, fp_hz = find_te1_resonances(freq, np.abs(z11))
    fs_idx = int(np.argmin(np.abs(freq - fs_hz)))
    fp_idx = int(np.argmin(np.abs(freq - fp_hz)))

    assert labels["fs_ghz"] == fs_hz / 1e9
    assert labels["fp_ghz"] == fp_hz / 1e9

    # k²eff：extract.py:512 内联公式
    k2eff = float((np.pi**2 / 4) * (fs_hz / fp_hz) * ((fp_hz - fs_hz) / fp_hz) * 100)
    assert labels["k2eff_pct"] == k2eff

    # Qs/Qp：Z 相位斜率法（生产 calc_q_phase，np.angle 不 unwrap）
    qs, qp = calc_q_phase(z11, freq, fs_idx, fp_idx)
    assert labels["qs"] == qs
    assert labels["qp"] == qp

    # Bode-Q：生产 calc_bodeq，S11 由 50 Ω 变换
    s11 = s11_from_z11(z11, 50.0)
    bodeq = calc_bodeq(s11, freq, fs_idx, fp_idx)
    assert labels["qs_bodeq"] == bodeq["qs_bodeq"]
    assert labels["qp_bodeq"] == bodeq["qp_bodeq"]

    # 物理 sanity：量级检查（防"一致但错"）
    assert 0.0 < labels["k2eff_pct"] < 15.0
    assert labels["qs"] > 0.0
    assert labels["qp"] > 0.0


def test_pure_capacitor_is_invalid() -> None:
    """纯电容（无谐振对）→ valid=False，k²eff 与 Q 全为 nan。"""
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=1e6)
    z_cap = 1.0 / (1j * 2.0 * np.pi * freq * 1e-12)
    labels = extract_epe_production_labels(freq, z_cap)

    assert labels["valid"] is False
    for key in ("k2eff_pct", "qs", "qp", "qs_bodeq", "qp_bodeq"):
        assert np.isnan(labels[key]), key


def test_lossless_bodeq_failure_marks_invalid() -> None:
    """无损耗 mBVD：|S11|=1 处处成立 → calc_bodeq 无有效点 → valid=False。

    fs/fp/k²eff/qs/qp 仍可计算（保留诊断值），但 bodeq 两列为 nan。
    """
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=1e6)
    w = 2.0 * np.pi * freq
    # mBVD 无损耗：fs=5.000 GHz（格点上），fp≈5.0498 GHz（格点间，避免真极点 inf）
    c1 = 1e-13
    l1 = 1.0 / ((2.0 * np.pi * 5.0e9) ** 2 * c1)
    c0 = c1 / ((5.0498e9 / 5.0e9) ** 2 - 1.0)
    z_mbvd = (1.0 - w**2 * l1 * c1) / (1j * w * (c0 * (1.0 - w**2 * l1 * c1) + c1))

    labels = extract_epe_production_labels(freq, z_mbvd)

    assert labels["valid"] is False
    assert labels["fs_ghz"] == pytest.approx(5.0, abs=1e-3)
    assert labels["fp_ghz"] > labels["fs_ghz"]
    assert np.isfinite(labels["k2eff_pct"])
    assert np.isfinite(labels["qs"])
    assert np.isfinite(labels["qp"])
    assert np.isnan(labels["qs_bodeq"])
    assert np.isnan(labels["qp_bodeq"])


def test_param_column_order_matches_production_surr_param_cols() -> None:
    """输出 7 参数键序与生产 AlgorithmConfig.surr_param_cols 完全一致。"""
    from app.config import get_algorithm_config

    freq, z11 = _solve_sample()
    labels = extract_epe_production_labels(freq, z11)
    assert tuple(k for k in labels if k != "valid") == get_algorithm_config().surr_param_cols
