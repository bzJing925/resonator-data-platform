"""EPPE PF（SiN 顶层质量加载）扩展测试（WP2b）。

- t_pf=0（默认）退化：与旧行为逐位一致（默认构造 vs 显式 t_pf=0.0）
- t_pf 增大 → TE2 fs2 单调下降（质量加载物理）
- 含 PF 的有损耗 EPPE 无源性
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mason_sim import (
    ALN1,
    ALSCN,
    MO,
    SIN,
    EPPEParams,
    build_frequency_grid,
    nm_to_m,
    run_eppe_sweep,
    um2_to_m2,
)
from app.mason_sim.labels import find_te2_resonances

# 几何选取说明（2026-09-11 探测）：AlN 100 / AlScN 300 / Mo 50+50 在 10–20 GHz 窗内
# 只有单一反极性支路（f2≈16.5 GHz，窗内无竞争高阶模），PF 0→100nm 全程不跳支路，
# 单调性断言针对同一支路成立。厚电极几何（如 225/225/Mo150）在 pf=100nm 时
# prominence 最强对会跳到 ~16.2 GHz 高阶支路——该现象由语料层支路核验处理，
# 不属于本求解器测试的断言对象。
T_ALN_NM = 100.0
T_ALSCN_NM = 300.0
T_MO_NM = 50.0
AREA_UM2 = 2000.0
ALPHA = 0.02
TE2_WIN_HZ = (10e9, 20e9)


def _eppe_params(t_pf_nm: float | None = None, alpha: float = ALPHA) -> EPPEParams:
    kwargs: dict[str, object] = {}
    if t_pf_nm is not None:
        kwargs["t_pf"] = nm_to_m(t_pf_nm)
    return EPPEParams(
        t_piezo_bot=nm_to_m(T_ALN_NM),
        t_piezo_top=nm_to_m(T_ALSCN_NM),
        t_mo_bot=nm_to_m(T_MO_NM),
        t_mo_top=nm_to_m(T_MO_NM),
        area=um2_to_m2(AREA_UM2),
        alpha=alpha,
        reverse_bot=+1.0,
        reverse_top=-1.0,
        **kwargs,
    )


def _sweep(params: EPPEParams, f_step_hz: float = 2e6):
    freq = build_frequency_grid(3e9, 22e9, f_step_hz=f_step_hz)
    return run_eppe_sweep(freq, ALN1, ALSCN, MO, params, pf_material=SIN)


def test_t_pf_default_is_zero() -> None:
    """EPPEParams 默认 t_pf=0.0（不加 PF 层）。"""
    assert _eppe_params().t_pf == 0.0


def test_t_pf_negative_or_nan_rejected() -> None:
    """t_pf 负值/NaN/inf 一律 ValueError（不得静默退化为无 PF 层）。"""
    with pytest.raises(ValueError, match="t_pf"):
        _eppe_params(-1.0)
    with pytest.raises(ValueError, match="t_pf"):
        _eppe_params(float("nan"))
    with pytest.raises(ValueError, match="t_pf"):
        _eppe_params(float("inf"))


def test_t_pf_zero_sweep_bitwise_identical_to_default() -> None:
    """显式 t_pf=0.0 与默认构造的扫频结果逐位一致（退化情形不改动既有电路）。"""
    freq = build_frequency_grid(3e9, 22e9, f_step_hz=2e6)
    r_default = run_eppe_sweep(freq, ALN1, ALSCN, MO, _eppe_params())
    r_zero = run_eppe_sweep(freq, ALN1, ALSCN, MO, _eppe_params(0.0), pf_material=SIN)
    np.testing.assert_array_equal(r_default.z11, r_zero.z11)
    np.testing.assert_array_equal(r_default.s11, r_zero.s11)


def test_t_pf_requires_pf_material() -> None:
    """t_pf>0 但未提供 pf_material → ValueError（不允许静默用错材料）。"""
    with pytest.raises(ValueError, match="pf_material"):
        run_eppe_sweep(
            build_frequency_grid(3e9, 22e9, f_step_hz=20e6),
            ALN1,
            ALSCN,
            MO,
            _eppe_params(50.0),
        )


def test_te2_fs_decreases_monotonically_with_t_pf() -> None:
    """PF 质量加载单调压低 TE2 fs2（SiN 顶层叠在顶 Mo 之上）。"""
    t_pf_values = [0.0, 25.0, 50.0, 75.0, 100.0]
    fs2_values: list[float] = []
    for t_pf in t_pf_values:
        result = _sweep(_eppe_params(t_pf))
        fs2, fp2 = find_te2_resonances(
            result.freq, np.abs(result.z11), TE2_WIN_HZ[0], TE2_WIN_HZ[1]
        )
        assert np.isfinite(fs2) and np.isfinite(fp2), f"t_pf={t_pf} 时 TE2 未检出"
        assert fs2 > TE2_WIN_HZ[0], f"t_pf={t_pf} 时 fs2 越出窗口下界"
        fs2_values.append(fs2)

    for prev, cur in zip(fs2_values, fs2_values[1:], strict=False):
        assert cur < prev, f"fs2 未随 t_pf 单调下降：{fs2_values}"
    # 100 nm SiN 的下移量 sanity：2026-09-11 实测约 0.2–0.6 GHz 量级（防"无效果"假通过）
    assert fs2_values[0] - fs2_values[-1] > 50e6


def test_eppe_with_pf_is_passive() -> None:
    """含 PF（t_pf=80nm）有损耗 EPPE：全频点 Re(Z11) >= -1e-9（无源器件不产能）。"""
    result = _sweep(_eppe_params(80.0), f_step_hz=10e6)
    assert float(np.min(result.z11.real)) >= -1e-9
