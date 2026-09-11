"""EPPE 生产口径 TE2 标签封装（prodlabels.extract_eppe_production_labels）测试。

核心判据：7 参数必须与"手工逐步调生产管线函数"逐值完全一致
（TE2 对定位用 find_te2_resonances (10,20] GHz 窗；k²eff 用 extract.py:512 生产公式；
Qs/Qp 用生产 calc_q_phase；Bode-Q 用生产 calc_bodeq，S11 经 50 Ω 变换）。
TE1 诊断（[3,8) GHz，生产 k²eff 式）永不影响 valid。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.extract import ExtractError, calc_bodeq, calc_q_phase
from app.mason_sim import (
    ALN1,
    ALSCN,
    MO,
    EPPEParams,
    build_frequency_grid,
    find_te1_resonances,
    find_te2_resonances,
    nm_to_m,
    prodlabels,
    run_eppe_sweep,
    s11_from_z11,
    um2_to_m2,
)
from app.mason_sim.prodlabels import (
    PARAM_KEYS,
    extract_eppe_production_labels,
    k2eff_pct_production,
)

FREQ = build_frequency_grid(3e9, 22e9, f_step_hz=2e6)  # 语料求解网格
TE2_WIN_HZ = (10e9, 20e9)
TE1_DIAG_WIN_HZ = (3e9, 8e9)

# 几何 A（非对称、支路核验可通过）：2026-09-11 探测 fs2≈13.75 GHz 为窗内最强对
# 且即设计的偶模支路；TE1 残余 ~6.0 GHz 落在 [3,8) 诊断窗内。
GEOM_A = dict(t_aln=100.0, t_alscn=300.0, t_mo=100.0)
# 几何 B（对称同材料反极性）：TE1 被对称性抑制（[3,8) 诊断应为 nan），TE2≈11.15 GHz。
GEOM_B = dict(t_aln=250.0, t_alscn=250.0, t_mo=150.0)


def _solve(geom: dict[str, float], alpha: float = 0.02, symmetric_aln: bool = False):
    piezo_top = ALN1 if symmetric_aln else ALSCN
    params = EPPEParams(
        t_piezo_bot=nm_to_m(geom["t_aln"]),
        t_piezo_top=nm_to_m(geom["t_alscn"]),
        t_mo_bot=nm_to_m(geom["t_mo"]),
        t_mo_top=nm_to_m(geom["t_mo"]),
        area=um2_to_m2(2000.0),
        alpha=alpha,
        reverse_bot=+1.0,
        reverse_top=-1.0,
    )
    return run_eppe_sweep(FREQ, ALN1, piezo_top, MO, params).z11


def test_te2_labels_match_manual_production_calls() -> None:
    """几何 A：封装 7 参数与手工逐步调生产函数完全相等（==），锚在 TE2 对。"""
    z11 = _solve(GEOM_A)
    labels = extract_eppe_production_labels(FREQ, z11)
    assert labels["valid"] is True
    assert labels["reason"] == "ok"
    assert tuple(labels.keys()) == PARAM_KEYS + (
        "fs1_ghz",
        "fp1_ghz",
        "k2eff_te1_pct",
        "valid",
        "reason",
    )

    # ---- 手工逐步复现（生产口径，TE2 锚点） ----
    fs2_hz, fp2_hz = find_te2_resonances(FREQ, np.abs(z11), TE2_WIN_HZ[0], TE2_WIN_HZ[1])
    assert np.isfinite(fs2_hz) and np.isfinite(fp2_hz) and fp2_hz > fs2_hz
    fs2_idx = int(np.argmin(np.abs(FREQ - fs2_hz)))
    fp2_idx = int(np.argmin(np.abs(FREQ - fp2_hz)))

    assert labels["fs_ghz"] == float(FREQ[fs2_idx]) / 1e9
    assert labels["fp_ghz"] == float(FREQ[fp2_idx]) / 1e9
    assert labels["k2eff_pct"] == k2eff_pct_production(float(FREQ[fs2_idx]), float(FREQ[fp2_idx]))

    qs, qp = calc_q_phase(z11, FREQ, fs2_idx, fp2_idx)
    assert labels["qs"] == qs
    assert labels["qp"] == qp

    s11 = s11_from_z11(z11, 50.0)
    bodeq = calc_bodeq(s11, FREQ, fs2_idx, fp2_idx)
    assert labels["qs_bodeq"] == bodeq["qs_bodeq"]
    assert labels["qp_bodeq"] == bodeq["qp_bodeq"]

    # ---- TE1 诊断（[3,8) 窗，手工复现；本几何残余存在 → 有限值） ----
    mask = (FREQ >= TE1_DIAG_WIN_HZ[0]) & (FREQ < TE1_DIAG_WIN_HZ[1])
    fs1_hz, fp1_hz = find_te1_resonances(FREQ[mask], np.abs(z11)[mask])
    assert np.isfinite(fs1_hz) and np.isfinite(fp1_hz) and fp1_hz > fs1_hz
    assert labels["fs1_ghz"] == fs1_hz / 1e9
    assert labels["fp1_ghz"] == fp1_hz / 1e9
    assert labels["k2eff_te1_pct"] == k2eff_pct_production(fs1_hz, fp1_hz)

    # 物理 sanity：TE2 在 (10,20] 窗内，k²eff 量级合理（AlScN kt2≈19% 天花板）
    assert TE2_WIN_HZ[0] < labels["fs_ghz"] * 1e9 <= TE2_WIN_HZ[1]
    assert 0.0 < labels["k2eff_pct"] < 20.0


def test_param_key_order_matches_production_surr_param_cols() -> None:
    """前 7 个参数键序与生产 AlgorithmConfig.surr_param_cols 完全一致。"""
    from app.config import get_algorithm_config

    labels = extract_eppe_production_labels(FREQ, _solve(GEOM_A))
    assert (
        tuple(k for k in labels if k not in ("valid", "reason") and not k.endswith("te1_pct"))[:7]
        == get_algorithm_config().surr_param_cols
    )
    assert PARAM_KEYS == get_algorithm_config().surr_param_cols


def test_te1_dead_symmetric_still_valid_with_nan_diag() -> None:
    """对称反极性（几何 B）：TE1 被抑制 → 诊断 nan，但 TE2 正常 → valid=True。"""
    z11 = _solve(GEOM_B, symmetric_aln=True)
    labels = extract_eppe_production_labels(FREQ, z11)

    assert labels["valid"] is True
    assert labels["reason"] == "ok"
    assert np.isnan(labels["fs1_ghz"])
    assert np.isnan(labels["fp1_ghz"])
    assert np.isnan(labels["k2eff_te1_pct"])
    # TE2 参数完好
    assert TE2_WIN_HZ[0] < labels["fs_ghz"] * 1e9 <= TE2_WIN_HZ[1]
    assert np.isfinite(labels["k2eff_pct"]) and labels["k2eff_pct"] > 0.0


def test_no_te2_in_window_is_no_resonance() -> None:
    """纯电容（窗内无任何谐振对）→ valid=False，reason=no_resonance，参数全 nan。"""
    z_cap = 1.0 / (1j * 2.0 * np.pi * FREQ * 1e-12)
    labels = extract_eppe_production_labels(FREQ, z_cap)

    assert labels["valid"] is False
    assert labels["reason"] == "no_resonance"
    for key in PARAM_KEYS:
        assert np.isnan(labels[key]), key


def test_te2_out_of_window_defensive_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """防御分支：TE2 对落在 (10,20] 窗外 → valid=False，reason=te2_out_of_window。

    正常路径下 find_te2_resonances 的窗内掩膜+edge_skip 已保证 fs2∈窗，
    此分支为断言防御；用 monkeypatch 构造越窗对直接验证（同 EPE 的 monkeypatch 测试风格）。
    """
    monkeypatch.setattr(prodlabels, "find_te2_resonances", lambda *a, **k: (9.5e9, 9.6e9))
    labels = extract_eppe_production_labels(FREQ, _solve(GEOM_A))
    assert labels["valid"] is False
    assert labels["reason"] == "te2_out_of_window"


def test_lossless_bodeq_failure_marks_label_nan() -> None:
    """无损耗（alpha=0）：|S11|≡1 → calc_bodeq 无有效点 → reason=label_nan。

    fs/fp/k²eff/qs/qp 仍可计算（保留诊断值），bodeq 两列 nan，valid=False。
    与 EPE 无损耗 mBVD 测试同物理来源，但走真实 EPPE 求解路径（非 mock）。
    """
    z11 = _solve(GEOM_B, alpha=0.0, symmetric_aln=True)
    labels = extract_eppe_production_labels(FREQ, z11)

    assert labels["valid"] is False
    assert labels["reason"] == "label_nan"
    assert np.isfinite(labels["fs_ghz"])
    assert np.isfinite(labels["k2eff_pct"])
    assert np.isfinite(labels["qs"])
    assert np.isfinite(labels["qp"])
    assert np.isnan(labels["qs_bodeq"])
    assert np.isnan(labels["qp_bodeq"])


def test_calc_bodeq_extracterror_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """calc_bodeq 抛 ExtractError 时封装为 label_nan（不向上抛）。"""

    def _boom(*args: object, **kwargs: object) -> None:
        raise ExtractError("synthetic")

    monkeypatch.setattr(prodlabels, "calc_bodeq", _boom)
    labels = extract_eppe_production_labels(FREQ, _solve(GEOM_A))
    assert labels["valid"] is False
    assert labels["reason"] == "label_nan"
