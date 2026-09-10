"""EPPE 双层物理正确性测试。

- 同材料同极性双层 ≈ 双倍厚 EPE（接线正确性的黄金判据）
- 同材料反极性对称等分：TE1 受抑、TE2 强耦合、静电容减半
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mason_sim.labels import extract_eppe_labels
from app.mason_sim.materials import ALN1, MO
from app.mason_sim.solver import (
    EPPEParams,
    FBARParams,
    build_frequency_grid,
    nm_to_m,
    run_eppe_sweep,
    run_fbar_sweep,
    solve_eppe_one_port,
    solve_fbar_one_port,
    um2_to_m2,
)

T_PIEZO_NM = 250.0  # 每层压电厚度（对称等分；使反极性 TE2 落在 10-16 GHz 窗内）
T_MO_NM = 150.0
AREA_UM2 = 1000.0
ALPHA = 0.02
TE2_F_MIN_HZ = 10e9
# TE2 窗上限：排除同极性堆叠的 TE3（奇模，~17 GHz 起弱耦合），
# 使 "TE2 死" 断言只针对被对称性抑制的偶模。
TE2_F_MAX_HZ = 16e9


def _eppe_params(reverse_bot: float, reverse_top: float, alpha: float = ALPHA) -> EPPEParams:
    return EPPEParams(
        t_piezo_bot=nm_to_m(T_PIEZO_NM),
        t_piezo_top=nm_to_m(T_PIEZO_NM),
        t_mo_bot=nm_to_m(T_MO_NM),
        t_mo_top=nm_to_m(T_MO_NM),
        area=um2_to_m2(AREA_UM2),
        alpha=alpha,
        reverse_bot=reverse_bot,
        reverse_top=reverse_top,
    )


def _epe_double_params() -> FBARParams:
    return FBARParams(
        t_aln=nm_to_m(2.0 * T_PIEZO_NM),
        t_mo=nm_to_m(T_MO_NM),
        area=um2_to_m2(AREA_UM2),
        alpha=ALPHA,
    )


def test_same_material_same_polarity_matches_double_thickness_epe() -> None:
    """同材料同极性（reverse=+1,+1）双层与双倍厚 EPE 全频点一致（|ΔZ|/|Z| < 1e-6）。"""
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=10e6)
    eppe = run_eppe_sweep(freq, ALN1, ALN1, MO, _eppe_params(+1.0, +1.0))
    epe = run_fbar_sweep(freq, ALN1, MO, _epe_double_params())

    rel_diff = np.abs(eppe.z11 - epe.z11) / np.maximum(np.abs(epe.z11), 1e-300)
    assert float(np.max(rel_diff)) < 1e-6


def test_same_polarity_te1_active_te2_dead() -> None:
    """同极性双层：TE1 活跃且 kt2 接近双倍厚 EPE；TE2 死（kt2_te2 < 0.2%）。"""
    freq = build_frequency_grid(3e9, 22e9, f_step_hz=10e6)
    eppe = run_eppe_sweep(freq, ALN1, ALN1, MO, _eppe_params(+1.0, +1.0))
    epe = run_fbar_sweep(freq, ALN1, MO, _epe_double_params())

    lab_eppe = extract_eppe_labels(
        freq, eppe.z11, te2_f_min_hz=TE2_F_MIN_HZ, te2_f_max_hz=TE2_F_MAX_HZ
    )
    lab_epe = extract_eppe_labels(
        freq, epe.z11, te2_f_min_hz=TE2_F_MIN_HZ, te2_f_max_hz=TE2_F_MAX_HZ
    )

    # TE1 活跃，且与双倍厚 EPE 的 kt2 一致
    assert lab_epe.kt2_te1 > 0.03
    assert lab_eppe.kt2_te1 == pytest.approx(lab_epe.kt2_te1, rel=1e-2)
    # TE2 死
    assert lab_eppe.kt2_te2 < 0.002
    assert lab_epe.kt2_te2 < 0.002


def test_reverse_polarity_te1_suppressed_te2_strong() -> None:
    """反极性对称等分：TE1 受抑（kt2_te1 < 0.2%），TE2 强耦合（kt2_te2 > 3%）。"""
    freq = build_frequency_grid(3e9, 22e9, f_step_hz=10e6)
    eppe = run_eppe_sweep(freq, ALN1, ALN1, MO, _eppe_params(+1.0, -1.0))
    lab = extract_eppe_labels(freq, eppe.z11, te2_f_min_hz=TE2_F_MIN_HZ, te2_f_max_hz=TE2_F_MAX_HZ)

    assert lab.kt2_te1 < 0.002
    assert lab.kt2_te2 > 0.03  # AlN 材料 kt2≈7.2% 量级
    assert lab.fs2_hz > TE2_F_MIN_HZ
    assert lab.fp2_hz > lab.fs2_hz
    assert lab.qs2 > 0.0
    assert lab.qp2 > 0.0
    assert lab.zp2_ohm > 0.0
    assert np.isfinite(lab.qbode_fs2)
    assert np.isfinite(lab.qbode_fp2)


def test_reverse_polarity_static_capacitance_halved() -> None:
    """反极性双层静电容减半（两 C0 串联）。

    注意两点（2026-09-10 实测确认）：
    1. pytest.approx 默认 abs=1e-12，对 ~1e-13 F 的电容会让 rel 容差失效，
       必须显式 abs=0.0。
    2. vendor Mason 模型（golden 回归锚定，不可改）的低频输入电容含机电修正：
       变压器反射支路 N²·Zc = 1/(jω·kt2·C0) 与第二 C0 支路串联，
       单层低频 C = C0·(1 + kt2/(1+kt2))，并非纯静电 C0=eps·A/t。
       因此解析期望值取 ½·eps·A/t·(1+kt2/(1+kt2))；
       "串联减半"这一核心物理由与单层 EPE 的对比精确验证。
    """
    f_qs = 1e8  # 远低于 TE1，准静态频点
    area = um2_to_m2(AREA_UM2)

    z_eppe, _ = solve_eppe_one_port(f_qs, ALN1, ALN1, MO, _eppe_params(+1.0, -1.0, alpha=0.0))
    c_eppe = -1.0 / (2.0 * np.pi * f_qs * z_eppe.imag)

    # 串联解析期望值（含 vendor 模型固有的机电修正因子）
    kt2 = ALN1.kt2
    c_expected = 0.5 * ALN1.eps * area / nm_to_m(T_PIEZO_NM) * (1.0 + kt2 / (1.0 + kt2))
    assert c_eppe == pytest.approx(c_expected, rel=0.01, abs=0.0)

    # 与单层 EPE（同层厚 t）对比：正好减半
    single = FBARParams(
        t_aln=nm_to_m(T_PIEZO_NM),
        t_mo=nm_to_m(T_MO_NM),
        area=area,
        alpha=0.0,
    )
    z_single, _ = solve_fbar_one_port(f_qs, ALN1, MO, single)
    c_single = -1.0 / (2.0 * np.pi * f_qs * z_single.imag)
    assert c_eppe == pytest.approx(0.5 * c_single, rel=0.01, abs=0.0)
