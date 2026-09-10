"""EPE 单层堆叠（非对称电极 + 可选 PF 质量加载层）测试。

- t_pf=0 且 t_top=t_bot 时与 build_fbar_one_port 逐位一致（接线正确性黄金判据）
- t_top≠t_bot：谱形合理（TE1 可检出、耦合量级正常）且满足无源性
- t_pf 增大 → fs 单调下降（PF 质量加载）
"""

from __future__ import annotations

import numpy as np

from app.mason_sim import (
    ALN1,
    MO,
    SIN,
    EPEStackParams,
    FBARParams,
    build_frequency_grid,
    find_te1_resonances,
    kt2_identity,
    nm_to_m,
    run_epe_stack_sweep,
    run_fbar_sweep,
    um2_to_m2,
)

AREA = um2_to_m2(2000.0)
ALPHA = 0.02


def _epe(
    t_piezo_nm: float,
    t_top_nm: float,
    t_bot_nm: float,
    t_pf_nm: float,
    alpha: float = ALPHA,
) -> EPEStackParams:
    return EPEStackParams(
        t_piezo=nm_to_m(t_piezo_nm),
        t_top=nm_to_m(t_top_nm),
        t_bot=nm_to_m(t_bot_nm),
        t_pf=nm_to_m(t_pf_nm),
        area=AREA,
        alpha=alpha,
    )


def test_sin_material_definition() -> None:
    """SIN 钝化层材料常数固定（v 取值不敏感，见 materials.py docstring）。"""
    assert SIN.name == "SiN"
    assert SIN.rho == 3170.0
    assert SIN.v == 11000.0
    assert SIN.alpha == 0.0


def test_epe_stack_bitwise_matches_fbar_when_symmetric_and_no_pf() -> None:
    """t_pf=0 且 t_top=t_bot=t 时，EPE 堆叠与 build_fbar_one_port 逐位一致。"""
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=5e6)
    fbar = run_fbar_sweep(
        freq,
        ALN1,
        MO,
        FBARParams(t_aln=nm_to_m(800.0), t_mo=nm_to_m(150.0), area=AREA, alpha=ALPHA),
    )
    epe = run_epe_stack_sweep(freq, ALN1, MO, SIN, _epe(800.0, 150.0, 150.0, 0.0))
    np.testing.assert_array_equal(epe.z11, fbar.z11)


def test_epe_stack_asymmetric_electrodes_sane_and_passive() -> None:
    """t_top≠t_bot：TE1 可检出、kt2 量级正常、无源；且相对对称电极谱形移动。"""
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=2e6)
    r_asym = run_epe_stack_sweep(freq, ALN1, MO, SIN, _epe(800.0, 100.0, 250.0, 0.0))

    # 无源性：有损耗器件 Re(Z11) 不得显著为负
    assert float(np.min(r_asym.z11.real)) >= -1e-9

    fs_asym, fp_asym = find_te1_resonances(freq, np.abs(r_asym.z11))
    assert 3e9 < fs_asym < fp_asym < 8e9
    kt2 = kt2_identity(fs_asym, fp_asym)
    assert 0.01 < kt2 < 0.15

    # 总 Mo 更厚（100+250=350nm > 300nm）→ fs 应低于对称 150/150 情形
    r_sym = run_epe_stack_sweep(freq, ALN1, MO, SIN, _epe(800.0, 150.0, 150.0, 0.0))
    fs_sym, _ = find_te1_resonances(freq, np.abs(r_sym.z11))
    assert fs_asym < fs_sym


def test_pf_loading_monotonically_lowers_fs() -> None:
    """同几何下 t_pf 增大 → fs 单调下降（质量加载效应）。"""
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=1e6)
    fs_list: list[float] = []
    for t_pf_nm in (0.0, 40.0, 80.0):
        r = run_epe_stack_sweep(freq, ALN1, MO, SIN, _epe(800.0, 150.0, 150.0, t_pf_nm))
        fs, fp = find_te1_resonances(freq, np.abs(r.z11))
        assert 3e9 < fs < fp < 8e9
        fs_list.append(fs)
    assert fs_list[0] > fs_list[1] > fs_list[2]


def test_pf_loaded_stack_is_passive() -> None:
    """含 PF 层的非对称堆叠同样满足无源性。"""
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=2e6)
    r = run_epe_stack_sweep(freq, ALN1, MO, SIN, _epe(700.0, 120.0, 180.0, 100.0))
    assert float(np.min(r.z11.real)) >= -1e-9
