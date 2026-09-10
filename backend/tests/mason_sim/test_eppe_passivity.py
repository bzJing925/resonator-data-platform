"""EPPE 无源性 / 能量一致性测试。

- 有损耗（alpha>0）：无源网络输入阻抗实部不得显著为负（数值噪声容差 -1e-9 Ω）
- 无损耗（alpha=0）：Z 应接近纯电抗（本 MNA 下数值上精确为 0）
"""

from __future__ import annotations

import numpy as np

from app.mason_sim import (
    ALN1,
    ALSCN,
    MO,
    EPPEParams,
    build_frequency_grid,
    nm_to_m,
    run_eppe_sweep,
    um2_to_m2,
)


def _eppe_params(alpha: float) -> EPPEParams:
    return EPPEParams(
        t_piezo_bot=nm_to_m(100.0),
        t_piezo_top=nm_to_m(200.0),
        t_mo_bot=nm_to_m(150.0),
        t_mo_top=nm_to_m(150.0),
        area=um2_to_m2(1000.0),
        alpha=alpha,
        reverse_bot=+1.0,
        reverse_top=-1.0,
    )


def test_lossy_eppe_is_passive() -> None:
    """alpha=0.02 时全频点 Re(Z11) >= -1e-9（无源器件不产能）。"""
    freq = build_frequency_grid(3e9, 22e9, f_step_hz=10e6)
    result = run_eppe_sweep(freq, ALN1, ALSCN, MO, _eppe_params(alpha=0.02))
    assert float(np.min(result.z11.real)) >= -1e-9


def test_lossless_eppe_is_purely_reactive() -> None:
    """alpha=0 时 Re(Z11) 相对 |Z11| 接近 0（纯数值噪声量级）。"""
    freq = build_frequency_grid(3e9, 22e9, f_step_hz=10e6)
    result = run_eppe_sweep(freq, ALN1, ALSCN, MO, _eppe_params(alpha=0.0))
    rel_real = np.abs(result.z11.real) / np.maximum(np.abs(result.z11), 1e-300)
    assert float(np.max(rel_real)) < 1e-9
