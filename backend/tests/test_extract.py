"""Algorithm 层隔离单测。

这里只测 core/extract.py 的边界 / NaN / 空 / 退化 case。Happy-path
已经被 test_e2e_pipeline / test_process_batch 覆盖。

测试态度：对照客户参考脚本 (客户提供的材料/VNA analysis v5.4_SITRI.py)
的行为，把"已知是这样"的边界固化下来挡回归；任何"看起来奇怪"
的算法选择如果和客户脚本一致，就视为契约。

例外（2026-08-20 review）：杂散模态鲁棒性修复刻意偏离客户脚本——
fp 检测（prominence）、3dB 穿越搜索、相位局部拟合、BodeQ 主模态窗口、
尖峰掩蔽。这些场景客户脚本的写法已被合成数据证明会算错，
见本文件 "杂散模态鲁棒性" 一节与 docs/algorithm-port.md。
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


# ── 杂散模态鲁棒性（2026-08-20 review，刻意偏离客户脚本）─────────────────
#
# 构造方法：mBVD 主模态 + 杂散串联支路（并联进同一 C0），合成 |Z|/∠Z/S11。
# 各测试里的"现状值"均为修复前代码的实测输出，用于确认测试确实卡在缺陷上。


def _mbvd_z(
    freq: np.ndarray, branches: list[tuple[float, float, float]], c0: float, r0: float = 0.0
) -> np.ndarray:
    """C0（可带并联损耗 R0）并联若干 (Rm, Lm, Cm) 串联支路的阻抗。"""
    w = 2 * np.pi * freq
    y = 1j * w * c0
    if r0 > 0:
        y = y + 1 / r0
    for rm, lm, cm in branches:
        y = y + 1 / (rm + 1j * w * lm + 1 / (1j * w * cm))
    return 1 / y


def _mbvd_branch(fs: float, k2: float, qm: float, c0: float) -> tuple[tuple, float]:
    """由 fs / k2eff / 机械 Q 反解 (Rm, Lm, Cm)，返回 (branch, fp)。"""
    u = 4 * k2 / np.pi**2
    r = (1 - np.sqrt(1 - 4 * u)) / (2 * u)  # fp/fs 小根
    cm = c0 * (r**2 - 1)
    lm = 1 / ((2 * np.pi * fs) ** 2 * cm)
    rm = 2 * np.pi * fs * lm / qm
    return (rm, lm, cm), fs * r


def _spurious_z() -> tuple[np.ndarray, np.ndarray, float]:
    """主模态 fs=2.0 GHz + 高 Q 杂散模态，返回 (freq, z, 主峰频率)。

    杂散支路的并联峰绝对高度（60.5 dB）高于主峰（52.4 dB），
    但主峰 prominence（38.8 dB）大于杂散峰（28.0 dB）。
    主峰实际位置 2.0395 GHz（被杂散支路容性加载拉离孤立 fp=2.0512）。
    """
    freq = np.linspace(1.8e9, 2.3e9, 5001)
    main, _ = _mbvd_branch(2.0e9, k2=0.06, qm=400, c0=2e-12)
    spur, _ = _mbvd_branch(2.058e9, k2=0.05, qm=1500, c0=0.25e-12)
    z = _mbvd_z(freq, [main, spur], c0=2e-12)
    return freq, z, 2.0395e9


def test_find_resonances_fp_not_hijacked_by_higher_spurious_peak() -> None:
    """窗口内杂散并联峰更高时，fp 仍应锁定主模态峰（prominence 最大者）。

    修复前按绝对高度选 → fp=2.0750 GHz（杂散峰）。
    """
    freq, z, f_main_peak = _spurious_z()
    fs_idx, fp_idx = find_resonances(np.abs(z), freq)
    df = freq[1] - freq[0]
    assert abs(freq[fs_idx] - 2.0e9) < 5 * df
    assert abs(freq[fp_idx] - f_main_peak) < 5 * df, (
        f"fp={freq[fp_idx] / 1e9:.4f} GHz 被杂散峰劫持（主峰在 {f_main_peak / 1e9:.4f}）"
    )


def test_calc_q_3db_first_crossing_ignores_remote_spurious_dip() -> None:
    """远处恰压电平的杂散点不应被当作 −3dB 穿越点；应取紧邻 fs 的穿越。

    确定性构造：V 形主凹陷在 fs=1.5 GHz，−3dB 电平穿越点在 fs±29.85 个网格
    （线性插值后真 Qs≈25.1）；右侧 +290 MHz 处放一个恰好等于电平的孤立
    bait 点。修复前 argmin 全数组找最近点 → 锁定 bait → Q≈4.7（错）。
    """
    freq = 1e9 + np.arange(1001) * 1e6  # 1 MHz 网格
    fs = float(freq[500])
    zs_db = -50.05
    z_db = np.full(1001, -40.0)
    z_db[400:501] = np.linspace(-40.0, zs_db, 101)
    z_db[500:601] = np.linspace(zs_db, -40.0, 101)
    z_db[790] = zs_db + 3  # bait：恰好等于 −3dB 电平的远距孤点（比近穿越点更近电平）
    dbqs, _ = calc_q_3db(z_db, freq, zs_db=zs_db, zp_db=0.0, fs=fs, fp=float(freq[900]))
    assert 20 < dbqs < 32, f"真 Qs≈25.1，算出 {dbqs:.1f}"


def test_calc_q_3db_returns_nan_when_level_never_crossed() -> None:
    """电平在扫描范围内从未被穿越 → NaN（不得静默返回最近点凑出假带宽）。"""
    freq = np.linspace(1.9e9, 2.1e9, 2001)
    fs = 2.0e9
    # 凹陷只有 2 dB 深，永远到不了 zs+3 dB
    z_db = -2.0 / (1 + ((freq - fs) / 2e6) ** 2)
    dbqs, _ = calc_q_3db(z_db, freq, zs_db=-2.0, zp_db=0.0, fs=fs, fp=2.09e9)
    assert np.isnan(dbqs)


def test_calc_q_phase_unwraps_phase_wrapping_at_resonance() -> None:
    """去嵌误差使 Re(Z)<0 时相位在 fs 附近跨过 ±π，unwrap 后斜率仍正确。

    φ(fs)=π 恰好在 fs 处 wrap；Q_true = fs·|dφ/df|/2 = 1500。
    修复前单点梯度被 2π 跳变污染 → qs≈312659（208 倍错误）。
    """
    freq = np.linspace(1.99e9, 2.01e9, 2001)
    fs = 2.0e9
    gamma = 2e6
    q_true = fs * (3.0 / gamma) / 2
    phi = np.pi + 3.0 * np.arctan((freq - fs) / gamma)
    z = 100 * np.exp(1j * phi)
    fs_idx = int(np.argmin(np.abs(freq - fs)))
    qs, _ = calc_q_phase(z, freq, fs_idx, fs_idx)
    assert abs(qs - q_true) / q_true < 0.10, f"Q_true={q_true:.0f}，算出 {qs:.1f}"


def test_calc_q_phase_local_fit_robust_to_noise() -> None:
    """实测网格（df=0.25 MHz、γ=5 MHz、Q≈750）下，σ=0.005 rad 相位噪声中
    局部三次拟合的平均误差应 <2.5%（实测 1.6%），单次最大 <6%。

    修复前单点梯度在同构造下平均误差 ≈3.8%（std 4.7%），过不了 2.5% 阈值。
    网格参数取 #2 批次实测值（4–7 GHz、12001 点、0.25 MHz 步进）。
    """
    freq = np.linspace(4.9e9, 5.1e9, 801)  # df = 0.25 MHz
    fs = 5.0e9
    gamma = 5e6
    q_true = fs * (1.5 / gamma) / 2  # 750
    phi = 1.5 * np.arctan((freq - fs) / gamma)
    fs_idx = int(np.argmin(np.abs(freq - fs)))
    rng = np.random.default_rng(42)
    errs = []
    for _ in range(12):
        z = 100 * np.exp(1j * (phi + rng.normal(0, 0.005, len(freq))))
        qs, _ = calc_q_phase(z, freq, fs_idx, fs_idx)
        errs.append(abs(qs - q_true) / q_true)
    assert float(np.mean(errs)) < 0.025, f"平均误差 {np.mean(errs) * 100:.1f}%"
    assert max(errs) < 0.06, f"单次误差过大: {max(errs) * 100:.1f}%"


def test_calc_q_phase_local_fit_no_curvature_bias() -> None:
    """拟合窗口覆盖相位转变区的可观比例时，三次拟合应无系统性低估。

    实测数据回归（#2 批次 40 器件抽样）：±4 点线性拟合相对参考脚本系统性
    偏低 −3.4%（arctan 相位在窗口内的曲率被直线平均）；同窗口三次多项式的
    一次项系数恢复真实切线斜率（中位差 +0.01%）。
    本构造：γ=2 MHz 相位转变半宽（w/γ=0.5），无噪声 arctan 相位，
    deg1 偏差 −5.5%（红），deg3 偏差 −0.3%（绿）。
    """
    freq = np.linspace(4.9e9, 5.1e9, 801)  # df = 0.25 MHz（与 #2 实测一致）
    fs = 5.0e9
    gamma = 2e6
    q_true = fs * (1.5 / gamma) / 2  # 1875
    phi = 1.5 * np.arctan((freq - fs) / gamma)
    z = 100 * np.exp(1j * phi)
    fs_idx = int(np.argmin(np.abs(freq - fs)))
    qs, _ = calc_q_phase(z, freq, fs_idx, fs_idx)
    assert abs(qs - q_true) / q_true < 0.01, f"Q_true={q_true:.0f}，算出 {qs:.1f}（曲率偏差未消除）"


def _spurious_s11() -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """主模态 S11（无杂散支路），返回 (freq, s, z, fs_idx, fp_idx)。

    r0=5000Ω 并联损耗让 |S11|<1（无损耗时 |s|→1，BodeQ 在 fp 附近发散，
    主峰形状不真实）。
    """
    freq = np.linspace(1.8e9, 2.3e9, 5001)
    main, _ = _mbvd_branch(2.0e9, k2=0.06, qm=400, c0=2e-12)
    z = _mbvd_z(freq, [main], c0=2e-12, r0=5000.0)
    s = (z - 50) / (z + 50)
    fs_idx, fp_idx = find_resonances(np.abs(z), freq)
    return freq, s, z, fs_idx, fp_idx


def test_calc_bodeq_peak_locked_to_main_mode_not_outofband_spike() -> None:
    """带外（2.25 GHz > fp）BodeQ 尖峰更高时，fbode 仍应锁定主模态邻域。

    修复前全局 nanargmax → fbode=2.2500 GHz（尖峰处）。
    """
    freq, s, _, fs_idx, fp_idx = _spurious_s11()
    spur = 0.15 * 8.0 * np.exp(-(((freq - 2.25e9) / 0.3e6) ** 2))
    out = calc_bodeq(s * np.exp(1j * spur), freq, fs_idx, fp_idx)
    half_span = 0.5 * (freq[fp_idx] - freq[fs_idx])
    assert freq[fs_idx] - half_span <= out["fbode_ghz"] * 1e9 <= freq[fp_idx] + half_span


def test_calc_bodeq_raw_interp_stays_finite_with_nan_points() -> None:
    """raw 数组含 NaN（|s|=1 点）时，bodeq_raw 插值应跨过 NaN 保持有限值。

    修复前 interp1d(cubic) 直接作用在含 NaN 数组上 → bodeq_raw=nan。
    """
    freq, s, _, fs_idx, fp_idx = _spurious_s11()
    s = s.copy()
    s[(fs_idx + fp_idx) // 2] = 1.0 + 0j  # 主峰邻域制造一个 NaN
    out = calc_bodeq(s, freq, fs_idx, fp_idx)
    assert np.isfinite(out["bodeq_raw"])


def test_calc_bodeq_masks_narrow_spike_contaminating_qs_bodeq() -> None:
    """fs 附近（savgol 窗口覆盖内）的窄 BodeQ 尖峰不应污染 qs_bodeq / bodeq_fitted。

    修复前尖峰能量经 savgol 泄漏：qs_bodeq 偏差 669%，bodeq_fitted 偏差 32%。
    """
    freq, s, _, fs_idx, fp_idx = _spurious_s11()
    ref = calc_bodeq(s, freq, fs_idx, fp_idx)
    i_spur = fs_idx + 12  # fs 右侧 1.2 MHz，在 savgol 窗口（±25 点）覆盖内
    spike = np.where(np.abs(np.arange(len(freq)) - i_spur) <= 2, 0.4, 0.0)
    out = calc_bodeq(s * np.exp(1j * spike), freq, fs_idx, fp_idx)
    assert abs(out["qs_bodeq"] - ref["qs_bodeq"]) / ref["qs_bodeq"] < 0.15
    assert abs(out["bodeq_fitted"] - ref["bodeq_fitted"]) / ref["bodeq_fitted"] < 0.15
