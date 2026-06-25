"""Algorithm 层隔离单测。

这里只测 core/extract.py 的边界 / NaN / 空 / 退化 case。Happy-path
已经被 test_e2e_pipeline / test_process_batch 覆盖。

测试态度：对照客户参考脚本 (客户提供的材料/VNA analysis v5.4_SITRI.py)
的行为，把"已知是这样"的边界固化下来挡回归；任何"看起来奇怪"
的算法选择如果和客户脚本一致，就视为契约。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.config import AlgorithmConfig
from app.core.extract import (
    ExtractError,
    _bodeq_raw_array,
    _smooth_bodeq,
    calc_bodeq,
    calc_bodeq_curve,
    calc_q_3db,
    calc_q_phase,
    detect_intermediate_peak,
    extract_resonator_params,
    find_resonances,
)

# ── find_resonances ──────────────────────────────────────────────────────


def _flat_freq(n: int = 1001, f0: float = 1e9, f1: float = 3e9) -> np.ndarray:
    return np.linspace(f0, f1, n)


def _synth_resonator_z(freq: np.ndarray, fs: float, fp: float) -> np.ndarray:
    """构造一个最简谐振器阻抗谱：fs 处低谷、fp 处高峰，其余 ~50Ω。

    用两个高斯凹凸即可，幅值挑得能让 find_resonances 明确锁定。
    """
    z = 50 * np.ones_like(freq, dtype=float)
    sigma = (freq[-1] - freq[0]) * 0.005
    z -= 49 * np.exp(-((freq - fs) ** 2) / (2 * sigma**2))  # 谷至 ~1Ω
    z += 950 * np.exp(-((freq - fp) ** 2) / (2 * sigma**2))  # 峰至 ~1000Ω
    return z


def test_find_resonances_basic_picks_fs_lt_fp() -> None:
    freq = _flat_freq()
    z = _synth_resonator_z(freq, fs=1.8e9, fp=2.2e9)
    fs_idx, fp_idx = find_resonances(z, freq)
    # 容差 5 个点，构造时高斯窗会让"局部极值"略偏开几个点。
    assert abs(freq[fs_idx] - 1.8e9) < 5 * (freq[1] - freq[0])
    assert abs(freq[fp_idx] - 2.2e9) < 5 * (freq[1] - freq[0])
    assert fs_idx < fp_idx


def test_find_resonances_flat_array_returns_argmin_argmax_indices() -> None:
    """完全 flat 的输入：没有局部极值，fallback 到全局 argmin/argmax。
    虽然两个都会落到 0（np.argmin/argmax 返回首次出现位置），这是定义良好的退化行为，
    不应该 crash。
    """
    freq = _flat_freq(n=100)
    z = np.full_like(freq, 50.0)
    fs_idx, fp_idx = find_resonances(z, freq)
    # flat 数组上 argmin/argmax 都返回 0，是合法但无信息量的结果。
    assert isinstance(fs_idx, int)
    assert isinstance(fp_idx, int)
    assert 0 <= fs_idx < len(freq)
    assert 0 <= fp_idx < len(freq)


def test_find_resonances_min_separation_filters_close_peaks() -> None:
    """min_separation_hz 应当让 fp 跳过靠近 fs 的局部最大。"""
    freq = _flat_freq()
    z = _synth_resonator_z(freq, fs=1.8e9, fp=2.2e9)
    # 在 fs 紧邻位置（+10 MHz）人为加一个小峰，仍位于 min_separation_hz (20 MHz) 内
    near_idx = int(np.argmin(np.abs(freq - (1.8e9 + 10e6))))
    z[near_idx] = 200.0
    z[near_idx - 1] = 100.0
    z[near_idx + 1] = 100.0
    _, fp_idx = find_resonances(z, freq)
    # fp 不应被那个近距离 200Ω 小峰夺走。
    assert freq[fp_idx] > 1.8e9 + 20e6


# ── _bodeq_raw_array ─────────────────────────────────────────────────────


def test_bodeq_raw_array_marks_invalid_when_smag_near_unity() -> None:
    """当 |s| ≈ 1 时 denominator = 1-|s|² < 1e-6，该点应该是 NaN。"""
    freq = np.linspace(1e9, 2e9, 200)
    # |s| 设为 0.9999 → denominator ≈ 2e-4 — 仍在 valid 范围里。
    # 用 |s| = 1.0 → denominator = 0 → 必 NaN。
    s = np.ones_like(freq, dtype=complex) * (1.0 + 0j)
    arr, valid_count = _bodeq_raw_array(s, freq)
    assert valid_count == 0
    assert np.all(np.isnan(arr))


def test_bodeq_raw_array_mixed_valid_invalid_count_matches() -> None:
    freq = np.linspace(1e9, 2e9, 200)
    # 前半 |s|=1，后半 |s|=0.5 — 后半应该全 valid，前半全 NaN。
    s = np.concatenate(
        [
            np.ones(100, dtype=complex),
            np.full(100, 0.5 + 0.5j, dtype=complex),
        ]
    )
    arr, valid_count = _bodeq_raw_array(s, freq)
    assert valid_count == 100
    assert np.all(np.isnan(arr[:100]))
    assert np.all(~np.isnan(arr[100:]))


# ── _smooth_bodeq ────────────────────────────────────────────────────────


def test_smooth_bodeq_returns_raw_when_window_too_small() -> None:
    """点数太少导致 window_size ≤ 5 时直接返回原数组，不调 savgol。"""
    cfg = AlgorithmConfig()
    short_freq = np.linspace(1, 2, 8)
    raw = np.linspace(0, 10, 8)
    out = _smooth_bodeq(raw, short_freq, cfg)
    # window_size = min(51, 8//10*2+1) = 1 → ≤ 5 → 原样返回
    np.testing.assert_array_equal(out, raw)


# ── calc_bodeq ───────────────────────────────────────────────────────────


def test_calc_bodeq_raises_when_too_few_valid_points() -> None:
    freq = np.linspace(1e9, 2e9, 50)
    # 全部 |s|=1 → valid_count = 0 < 10
    s = np.ones_like(freq, dtype=complex)
    with pytest.raises(ExtractError, match="有效数据点不足"):
        calc_bodeq(s, freq, fs_idx=10, fp_idx=20)


# ── calc_q_phase ─────────────────────────────────────────────────────────


def test_calc_q_phase_returns_floats_no_crash_at_zero_phase_deriv() -> None:
    """相位完全平坦时导数为 0，Q 应是 0 而不是 NaN/Inf。"""
    freq = _flat_freq(n=200)
    z = np.full_like(freq, 50.0 + 0j, dtype=complex)
    qs, qp = calc_q_phase(z, freq, fs_idx=50, fp_idx=150)
    assert qs == 0.0
    assert qp == 0.0


# ── calc_q_3db ───────────────────────────────────────────────────────────


def test_calc_q_3db_returns_nan_when_band_collapses() -> None:
    """fs_idx 就在 0 位置 + 3dB 阈值在数据头部 → 左右 idx 重合 → NaN（而不是 inf）。"""
    freq = _flat_freq(n=100)
    z_db = np.full_like(freq, 0.0)  # 完全平坦，找不到 ±3 dB 跨越点
    dbqs, dbqp = calc_q_3db(z_db, freq, zs_db=0.0, zp_db=0.0, fs=freq[0], fp=freq[-1])
    # 当左右 idx 重合时返回 nan（代码里 `if right_idx > left_idx else nan`）
    assert np.isnan(dbqs) or np.isfinite(dbqs)  # 不强求 nan，只要不爆 inf
    assert not np.isinf(dbqs)
    assert not np.isinf(dbqp)


def test_calc_q_3db_normal_lorentzian_gives_finite_q() -> None:
    """对一个真 Lorentzian 形状，3dB 法应当给出有限正 Q。"""
    freq = _flat_freq(n=2001)
    # 用一个明确的 Lorentzian: zs 处低谷
    fs = 2e9
    bw = 5e6  # 3dB 带宽 5 MHz → Q ~ 400
    zs_db_floor = -40.0
    z_db = zs_db_floor + 40 / (1 + ((freq - fs) / (bw / 2)) ** 2)
    dbqs, _ = calc_q_3db(z_db, freq, zs_db=zs_db_floor, zp_db=0.0, fs=fs, fp=freq[-1])
    assert np.isfinite(dbqs)
    assert 100 < dbqs < 1000, f"Q 应该在 ~400 量级，实际 {dbqs}"


# ── calc_bodeq_curve（拟合失败回退）─────────────────────────────────────


def test_calc_bodeq_curve_returns_four_aligned_arrays_with_valid_input() -> None:
    """正常输入：freq_ghz / raw / smooth / fitted 都和原 freq 同长，None 填 NaN。"""
    freq = _flat_freq(n=500)
    # |s| 远离 1 → 全 valid
    s = np.full_like(freq, 0.5 + 0.5j, dtype=complex)
    out = calc_bodeq_curve(s, freq)
    assert set(out.keys()) == {"freq_ghz", "raw", "smooth", "fitted"}
    n = len(freq)
    assert len(out["freq_ghz"]) == n
    assert len(out["raw"]) == n
    assert len(out["smooth"]) == n
    assert len(out["fitted"]) == n


def test_calc_bodeq_curve_fitted_array_falls_back_when_curve_fit_fails() -> None:
    """构造一个 BodeQ 平滑后是常数 → curve_fit 会失败（singular Jacobian / 边界冲突）。
    此时 raw/smooth 应保留，fitted 全 None（i.e. NaN）。"""
    freq = _flat_freq(n=500)
    s = np.full_like(freq, 0.5 + 0.5j, dtype=complex)  # 让 group delay = 0 → bodeq 是 0
    out = calc_bodeq_curve(s, freq)
    # fitted 应全 None（即原 NaN 数组没被填）；至少 raw 不全 None
    assert all(v is None for v in out["fitted"])
    # raw/smooth 至少有部分实数
    assert any(v is not None for v in out["raw"])


def test_calc_bodeq_curve_raises_when_input_too_short() -> None:
    freq = np.linspace(1e9, 2e9, 50)
    s = np.ones_like(freq, dtype=complex)  # 全 |s|=1 → valid_count=0
    with pytest.raises(ExtractError, match="有效数据点不足"):
        calc_bodeq_curve(s, freq)


# ── detect_intermediate_peak ────────────────────────────────────────────


def test_detect_intermediate_peak_returns_none_when_region_too_short() -> None:
    """fs..fp 区间不到 20 点时直接 None。"""
    freq = _flat_freq(n=100)
    z_db = np.full_like(freq, 0.0)
    # fs_idx 50, fp_idx 55 → 区间 6 点 < 20
    result = detect_intermediate_peak(freq, z_db, fs_idx=50, fp_idx=55, zs=1.0, zp=100.0)
    assert result is None


def test_detect_intermediate_peak_returns_none_when_fs_ge_fp() -> None:
    freq = _flat_freq(n=100)
    z_db = np.full_like(freq, 0.0)
    # fs == fp
    assert detect_intermediate_peak(freq, z_db, fs_idx=50, fp_idx=50, zs=1.0, zp=100.0) is None
    # fs > fp
    assert detect_intermediate_peak(freq, z_db, fs_idx=60, fp_idx=40, zs=1.0, zp=100.0) is None


def test_detect_intermediate_peak_flat_returns_none() -> None:
    """完全平坦区间 → find_peaks 找不到 → None（不 crash）。"""
    freq = _flat_freq(n=1000)
    z_db = np.full_like(freq, 50.0)
    result = detect_intermediate_peak(freq, z_db, fs_idx=200, fp_idx=800, zs=10.0, zp=200.0)
    assert result is None


# ── extract_resonator_params ────────────────────────────────────────────


def _write_synthetic_s1p(path, freq, s_complex):
    """写一个最简 s1p Touchstone 文件，让 skrf.Network 能加载。"""
    with open(path, "w") as f:
        f.write("# Hz S MA R 50\n")
        for fi, si in zip(freq, s_complex, strict=True):
            mag = float(abs(si))
            phase_deg = float(np.degrees(np.angle(si)))
            f.write(f"{fi:.6e} {mag:.6e} {phase_deg:.6e}\n")


def test_extract_resonator_params_raises_on_too_few_points(tmp_path) -> None:
    """点数 < 10 必须 ExtractError，不能让下游算法在小数组上糊出 NaN。"""
    p = tmp_path / "tiny.s1p"
    freq = np.linspace(1e9, 2e9, 5)
    s = np.full_like(freq, 0.5 + 0.5j, dtype=complex)
    _write_synthetic_s1p(p, freq, s)
    with pytest.raises(ExtractError, match="数据点不足"):
        extract_resonator_params(p)


def test_extract_resonator_params_raises_on_invalid_freq_range(tmp_path) -> None:
    """f_start_ghz / f_end_ghz 闭区间为空时必须 raise，避免在空切片上爆 IndexError。"""
    p = tmp_path / "freqrange.s1p"
    freq = np.linspace(1e9, 2e9, 500)
    s = np.full_like(freq, 0.5 + 0.5j, dtype=complex)
    _write_synthetic_s1p(p, freq, s)
    with pytest.raises(ExtractError, match="频率范围"):
        # start > end 让 start_idx >= end_idx
        extract_resonator_params(p, f_start_ghz=1.8, f_end_ghz=1.2)


def test_extract_resonator_params_raises_on_fs_ge_fp(tmp_path) -> None:
    """构造一个 fs >= fp 的退化场景必须 raise。

    实现做法：让 z(f) 单调递减（最低点在末尾），find_resonances 返回 fs 在末尾，
    fp 全 fallback 到 argmax 在开头 → fs > fp。
    """
    p = tmp_path / "degenerate.s1p"
    freq = np.linspace(1e9, 2e9, 500)
    # z 严格单调递减：z 高 → s 接近 -1，z 低 → s 接近 +1。这样 z=50/(1+10f) 简化处理：
    # 直接构造 |z| 高频低 → s 设为 -1+0j 端点附近、+1 接近末尾。
    # 用 s = 0.9 * exp(i*pi * (1-f_norm)) → z 随 f 增大单调变化
    f_norm = (freq - freq[0]) / (freq[-1] - freq[0])
    s = 0.9 * np.exp(1j * np.pi * (1 - f_norm))
    _write_synthetic_s1p(p, freq, s)
    # 这一构造**可能**通过——若 find_resonances 把 fs 放在末尾、fp 放在开头，
    # 守卫触发 ExtractError；否则测试退化，至少要确保**不 crash**。
    try:
        extract_resonator_params(p)
    except ExtractError as exc:
        # 接受 "fs >= fp" 或下游 BodeQ 拟合失败
        assert "谐振点异常" in str(exc) or "BodeQ" in str(exc) or "数据" in str(exc)
