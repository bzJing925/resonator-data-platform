"""EPE 回归测试：vendored 求解器必须与原始 vendor 求解器逐点一致。

golden 文件 ``fixtures/epe_golden.npz`` 由【原始 vendor 求解器】生成（2026-09-10），
生成方式（在 ADSML train-export 工作区执行，勿修改 vendor 目录任何文件）::

    cd /Users/jingbozuo/00.PROJECTS/ADSML/.worktrees/train-export
    PYTHONPATH=. <master venv python> -c "
    from src.mason_solver import (
        run_fbar_sweep, FBARParams, build_frequency_grid, nm_to_m, um2_to_m2,
    )
    from src.materials import ALSCN, MO
    import numpy as np
    params = FBARParams(t_aln=nm_to_m(800.0), t_mo=nm_to_m(150.0),
                        area=um2_to_m2(1000.0), alpha=0.02)
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=10e6)
    r = run_fbar_sweep(freq, ALSCN, MO, params)
    np.savez('/tmp/epe_golden.npz', freq=freq, z11=r.z11,
             t_aln_nm=800.0, t_mo_nm=150.0, area_um2=1000.0, alpha=0.02)
    "
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.mason_sim import (
    ALSCN,
    MO,
    FBARParams,
    MNACircuit,
    add_piezo_layer_four_port,
    build_frequency_grid,
    nm_to_m,
    piezo_intermediates,
    run_fbar_sweep,
    um2_to_m2,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "epe_golden.npz"
MAX_REL_TOL = 1e-6


def test_vendored_fbar_sweep_matches_golden() -> None:
    """vendored run_fbar_sweep 复数 Z 全频点与 golden 的相对误差 < 1e-6。"""
    golden = np.load(GOLDEN_PATH)
    freq_g = golden["freq"]
    z11_g = golden["z11"]

    params = FBARParams(
        t_aln=nm_to_m(float(golden["t_aln_nm"])),
        t_mo=nm_to_m(float(golden["t_mo_nm"])),
        area=um2_to_m2(float(golden["area_um2"])),
        alpha=float(golden["alpha"]),
    )
    freq = build_frequency_grid(float(freq_g[0]), float(freq_g[-1]), f_step_hz=10e6)
    result = run_fbar_sweep(freq, ALSCN, MO, params)

    np.testing.assert_array_equal(freq, freq_g)
    rel_err = np.abs(result.z11 - z11_g) / np.maximum(np.abs(z11_g), 1e-300)
    assert float(np.max(rel_err)) < MAX_REL_TOL


def test_reverse_default_keeps_legacy_turns_ratio() -> None:
    """reverse 默认 +1.0 保持旧行为；reverse=-1.0 只翻转变压器变比符号。"""
    freq = 5e9
    area = um2_to_m2(1000.0)
    thk = nm_to_m(800.0)
    n_default = piezo_intermediates(ALSCN, area, thk, freq, 0.02)["N"]
    n_plus = piezo_intermediates(ALSCN, area, thk, freq, 0.02, reverse=+1.0)["N"]
    n_minus = piezo_intermediates(ALSCN, area, thk, freq, 0.02, reverse=-1.0)["N"]

    assert n_default == n_plus
    assert n_minus == -n_plus


def test_reverse_validation_rejects_invalid_values() -> None:
    """reverse 只允许 ±1（极性符号）；其他取值应抛 ValueError。"""
    freq = 5e9
    area = um2_to_m2(1000.0)
    thk = nm_to_m(800.0)

    for bad in (0.0, 0.5, +2.0, -1.5):
        with pytest.raises(ValueError, match="reverse"):
            piezo_intermediates(ALSCN, area, thk, freq, 0.02, reverse=bad)

    circ = MNACircuit()
    with pytest.raises(ValueError, match="reverse"):
        add_piezo_layer_four_port(
            circ,
            prefix="BAD",
            port1="IN",
            port2="0",
            port3="0",
            port4="0",
            mat=ALSCN,
            area=area,
            thk=thk,
            alpha=0.02,
            freq=freq,
            reverse=0.0,
        )
