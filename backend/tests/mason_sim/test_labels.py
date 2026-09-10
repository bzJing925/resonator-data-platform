"""labels.py 单元测试（合成数据，确定性，不依赖求解器）。"""

from __future__ import annotations

import numpy as np
import pytest

from app.mason_sim.labels import (
    compute_q_bode_phase,
    estimate_q_factor,
    find_te1_resonances,
    find_te2_resonances,
    kt2_identity,
)


def test_kt2_identity() -> None:
    fs, fp = 10.0e9, 10.5e9
    assert kt2_identity(fs, fp) == pytest.approx((np.pi**2 / 4.0) * (fp - fs) / fp)


def _synthetic_dual_mode_spectrum() -> tuple[np.ndarray, np.ndarray]:
    """合成 |Z|：平坦基底 + TE1 谐振对（5.0/5.3 GHz）+ TE2 谐振对（15.0/15.3 GHz）。

    在 dB 域叠加高斯谷/峰，保证 prominence 足够被检出。
    """
    freq = np.linspace(3e9, 22e9, int((22 - 3) / 0.005) + 1)  # 5 MHz 步长
    z_db = np.full_like(freq, 100.0)

    def _bump(f0_ghz: float, depth_db: float, sigma_mhz: float = 10.0) -> np.ndarray:
        return depth_db * np.exp(-(((freq - f0_ghz * 1e9) / (sigma_mhz * 1e6)) ** 2))

    z_db -= _bump(5.0, 40.0)  # fs1 谷
    z_db += _bump(5.3, 30.0)  # fp1 峰
    z_db -= _bump(15.0, 40.0)  # fs2 谷
    z_db += _bump(15.3, 30.0)  # fp2 峰
    return freq, 10.0 ** (z_db / 20.0)


def test_find_te1_resonances_picks_lowest_mode() -> None:
    freq, z_mag = _synthetic_dual_mode_spectrum()
    fs, fp = find_te1_resonances(freq, z_mag)
    assert fs == pytest.approx(5.0e9, abs=5e6)
    assert fp == pytest.approx(5.3e9, abs=5e6)


def test_find_te2_resonances_in_window() -> None:
    freq, z_mag = _synthetic_dual_mode_spectrum()
    fs2, fp2 = find_te2_resonances(freq, z_mag, f_min_hz=10e9)
    assert fs2 == pytest.approx(15.0e9, abs=5e6)
    assert fp2 == pytest.approx(15.3e9, abs=5e6)


def test_find_te2_resonances_dead_window_returns_nan() -> None:
    """窗内无显著谐振（纹波低于 prominence 阈值）时返回 (nan, nan)。"""
    freq = np.linspace(10e9, 22e9, 2401)
    z_db = 60.0 - 10.0 * np.log10(freq / 10e9)  # 平滑电容斜坡
    z_db = z_db + 1.0 * np.sin(freq / 1e9 * 2.0 * np.pi / 0.5)  # 1 dB 纹波
    z_mag = 10.0 ** (z_db / 20.0)
    fs2, fp2 = find_te2_resonances(freq, z_mag, f_min_hz=10e9)
    assert np.isnan(fs2)
    assert np.isnan(fp2)


def test_estimate_q_factor_series_synthetic() -> None:
    """串联谐振 |Z| = z0*sqrt(1+x^2)，x=2Q(f-f0)/f0：-3dB 带宽精确等于 f0/Q。"""
    f0, q, z0 = 15.0e9, 500.0, 2.0
    freq = np.linspace(14.9e9, 15.1e9, 41)  # 5 MHz 步长，f0 与 ±f0/(2Q) 都落在格点上
    x = 2.0 * q * (freq - f0) / f0
    z_mag = z0 * np.sqrt(1.0 + x**2)
    assert estimate_q_factor(freq, z_mag, f0, resonance_type="series") == pytest.approx(q, rel=1e-9)


def test_estimate_q_factor_parallel_synthetic() -> None:
    """并联谐振 |Z| = z0/sqrt(1+x^2)：-3dB 带宽同样等于 f0/Q。"""
    f0, q, z0 = 15.0e9, 500.0, 2.0e5
    freq = np.linspace(14.9e9, 15.1e9, 41)
    x = 2.0 * q * (freq - f0) / f0
    z_mag = z0 / np.sqrt(1.0 + x**2)
    assert estimate_q_factor(freq, z_mag, f0, resonance_type="parallel") == pytest.approx(
        q, rel=1e-9
    )


def test_compute_q_bode_phase_synthetic() -> None:
    """Bode-Q：Z = z0*(1+j*x)、x=2Q(f-f0)/f0 的近谐振模型有解析解。

    S11 = (Z-R)/(Z+R) 在 f0 处：Q_bode = 2*R*z0*Q / (R^2 - z0^2)。
    """
    f0, q, z0, zref = 12.0e9, 100.0, 2.0, 50.0
    freq = np.linspace(f0 - 100e6, f0 + 100e6, 201)  # 1 MHz 步长
    x = 2.0 * q * (freq - f0) / f0
    z11 = z0 * (1.0 + 1j * x)

    qbode = compute_q_bode_phase(freq, z11, f0, zref=zref)
    expected = 2.0 * zref * z0 * q / (zref**2 - z0**2)
    assert qbode == pytest.approx(expected, rel=1e-3)
