"""WP3 Mason EPE ↔ 生产代理校准脚本（scripts/calibrate_mason_epe.py）测试。

- 可恢复性：Mason 自身在已知 θ（t_piezo=340, t_mo=150, c33×0.97, e33×1.03,
  eps×0.95, alpha×1.05=0.021）生成伪目标，同一套有界 LS（起点=名义值）恢复；
- 边界执行：伪目标材料越 ±10% 界时拟合值被钳在界内；
- 种子确定性：同种子两次拟合逐位一致；make_starts 可复现且首行为名义值；
- 谱残差计算：同曲线 RMSE=0；+3 dB 平移 RMSE=3；
- smoke：端到端 main() 在伪造 baseline.npz 上产出 3 个产物文件且 schema 键齐全。

测试拟合用窄带 4.6–5.8 GHz @1 MHz 控制耗时（伪目标 fs=4.977/fp=5.323 GHz 带内
余量 ≥270 MHz；标签管线的 bodeq/洛伦兹窗口均为点数相对口径，窄带行为与全带一致；
网格步长 1 MHz 与生产校准一致）。生产校准仍用 4–7 GHz @1 MHz。

可恢复性判据说明（实测证据见 WP3 汇报与校准报告）：7 参数目标存在物理简并——
fs 只约束 sqrt(c33)/t_piezo 组合、fp 只约束 kt2=e33²/(c33·eps) 组合、Q 主要钉
alpha；(t_piezo, c33) 与 (e33, eps) 的拆分方向在 1 MHz 标签量化 + Q 窗索引跳变
形成的微坑景观下不可局部识别（实测收敛端点：fs/fp/k²eff 残差精确为 0、kt2 偏差
0.26%、sqrt(c33)/t 偏差 0.32%、alpha 偏差 0.09%，而 eps 个体偏差 1.5%；不同网格
/求解器（TRF/Powell）落在 1.5–3.5% 的等价微坑，且 TRF 端点不随 max_nfev 增大
改善）。故个体材料参数用 <2% 回归护栏，可识别组合量用 <0.5% 严判据，标签层判据
（fs <1 MHz、k²eff <0.05 pct-pt）保持规格原文。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import calibrate_mason_epe as cal  # noqa: E402

from app.mason_sim import (  # noqa: E402
    ALSCN,
    MO,
    SIN,
    EPEStackParams,
    PiezoMaterial,
    build_frequency_grid,
    nm_to_m,
    run_epe_stack_sweep,
    um2_to_m2,
)
from app.mason_sim.prodlabels import PARAM_KEYS, extract_epe_production_labels  # noqa: E402

# 伪目标真值 θ = (t_piezo_nm, s_c33, s_e33, s_eps, s_alpha)；几何 t_mo=150 nm 对称、pf=0。
TRUE_THETA = np.array([340.0, 0.97, 1.03, 0.95, 1.05])
TRUE_T_MO_NM = 150.0
# 测试拟合窄带（生产校准为 4–7 GHz）：覆盖伪目标 fs=4.977/fp=5.323 GHz
TEST_BAND_HZ = (4.6e9, 5.8e9)


def _labels_vec(labels: dict) -> np.ndarray:
    return np.array([float(labels[k]) for k in PARAM_KEYS], dtype=float)


def _kt2_scale(theta: np.ndarray) -> float:
    """可识别组合量：kt2 标度 = s_e33²/(s_c33·s_eps)。"""
    return float(theta[2] ** 2 / (theta[1] * theta[3]))


def _fs_combo(theta: np.ndarray) -> float:
    """可识别组合量：fs 组合 = sqrt(s_c33)/t_piezo。"""
    return float(np.sqrt(theta[1]) / theta[0])


@pytest.fixture(scope="session")
def pseudo_target() -> tuple[np.ndarray, np.ndarray]:
    """Mason 自身在 TRUE_THETA 生成的 pf=0 伪目标（窄带 1 MHz 网格，7 参数向量）。"""
    freq = build_frequency_grid(*TEST_BAND_HZ, f_step_hz=1e6)
    labels = cal.simulate_labels(TRUE_THETA, TRUE_T_MO_NM, 0.0, 2000.0, freq)
    assert labels["valid"]
    return freq, _labels_vec(labels)


def test_recovers_known_stack(pseudo_target: tuple[np.ndarray, np.ndarray]) -> None:
    """同一套有界 LS（起点=名义值）恢复已知 θ 的标签层与可识别组合量。"""
    freq, target = pseudo_target
    starts = cal.make_starts(1, seed=123)  # 仅名义起点
    best = cal.fit_geometry(
        [(0.0, target)],
        TRUE_T_MO_NM,
        starts,
        cal.default_weights(),
        freq,
        max_nfev=120,
    )[0]
    labels = best.labels_by_state[0]
    assert labels["valid"]
    # 标签层恢复（规格判据）
    assert abs(labels["fs_ghz"] - target[0]) * 1000.0 < 1.0  # fs 残差 <1 MHz
    assert abs(labels["k2eff_pct"] - target[2]) < 0.05  # k²eff <0.05 pct-pt
    # 可识别组合量严判据（实测 0.26%/0.32%，余量 ~1.6×）
    assert abs(_kt2_scale(best.theta) / _kt2_scale(TRUE_THETA) - 1) < 0.005  # kt2
    assert abs(_fs_combo(best.theta) / _fs_combo(TRUE_THETA) - 1) < 0.005  # sqrt(c33)/t
    assert abs(best.theta[4] / TRUE_THETA[4] - 1) < 0.01  # alpha（Q 钉住，实测 0.09%）
    # 简并方向个体参数回归护栏（实测最差 eps 1.5%；不同配置 1.5–3.5% 等价微坑）
    for i, name in enumerate(cal.THETA_NAMES):
        rel_err = abs(best.theta[i] / TRUE_THETA[i] - 1)
        assert rel_err < 0.02, f"{name}: 恢复值 {best.theta[i]:.6g} vs 真值 {TRUE_THETA[i]:.6g}"


def test_bounds_enforced_when_truth_outside() -> None:
    """伪目标 c33×1.18（越 +10% 界）：拟合必须停在界内且 c33 被推向并停在上界附近。"""
    freq = build_frequency_grid(*TEST_BAND_HZ, f_step_hz=1e6)
    theta_true = np.array([340.0, 1.18, 1.0, 1.0, 1.0])
    labels = cal.simulate_labels(theta_true, TRUE_T_MO_NM, 0.0, 2000.0, freq)
    assert labels["valid"]
    target = _labels_vec(labels)
    starts = cal.make_starts(1, seed=321)
    best = cal.fit_geometry(
        [(0.0, target)],
        TRUE_T_MO_NM,
        starts,
        cal.default_weights(),
        freq,
        max_nfev=80,
    )[0]
    lo, hi = cal.theta_bounds()
    assert np.all(best.theta >= lo - 1e-12)
    assert np.all(best.theta <= hi + 1e-12)
    assert best.theta[1] > 1.05  # c33 逼近上界 1.1（真值 1.18 在界外）


def test_make_starts_deterministic_and_nominal_first() -> None:
    """同种子逐位一致、首行=名义值、全部在界内；不同种子给出不同界内起点。"""
    s1 = cal.make_starts(6, seed=999)
    s2 = cal.make_starts(6, seed=999)
    assert np.array_equal(s1, s2)
    assert s1.shape == (6, 5)
    assert np.array_equal(s1[0], cal.NOMINAL_THETA)
    lo, hi = cal.theta_bounds()
    assert np.all(s1 >= lo) and np.all(s1 <= hi)
    s3 = cal.make_starts(6, seed=1000)
    assert not np.array_equal(s1[1:], s3[1:])


def test_fit_deterministic_same_seed() -> None:
    """同种子同起点两次拟合：最优 θ 与 cost 逐位一致。"""
    freq = build_frequency_grid(*TEST_BAND_HZ, f_step_hz=1e6)
    labels = cal.simulate_labels(TRUE_THETA, TRUE_T_MO_NM, 0.0, 2000.0, freq)
    target = _labels_vec(labels)
    starts = cal.make_starts(1, seed=777)
    r1 = cal.fit_geometry(
        [(0.0, target)], TRUE_T_MO_NM, starts, cal.default_weights(), freq, max_nfev=25
    )
    r2 = cal.fit_geometry(
        [(0.0, target)], TRUE_T_MO_NM, starts, cal.default_weights(), freq, max_nfev=25
    )
    assert np.array_equal(r1[0].theta, r2[0].theta)
    assert r1[0].cost == r2[0].cost


def test_area_mean_targets() -> None:
    """面积均值目标 = params_base 沿面积轴取均值（不硬编码）。"""
    rng = np.arange(2 * 3 * 7, dtype=float).reshape(2, 3, 7)
    tgt = cal.area_mean_targets(rng)
    assert tgt.shape == (2, 7)
    assert np.allclose(tgt, rng.mean(axis=1))


def test_spectral_residuals_identical_and_shifted() -> None:
    """谱残差：同曲线 RMSE/max|Δ|/低频偏移=0；整体 +3 dB 平移 → 三者=3。"""
    freq = build_frequency_grid(4e9, 7e9, f_step_hz=3e6)
    base = 40.0 + np.linspace(0.0, 5.0, freq.size)
    r0 = cal.spectral_residuals(base, base, freq)
    assert r0["rmse_db"] == 0.0
    assert r0["max_abs_db"] == 0.0
    assert r0["low_level_offset_db"] == 0.0
    r3 = cal.spectral_residuals(base + 3.0, base, freq)
    assert r3["rmse_db"] == pytest.approx(3.0)
    assert r3["max_abs_db"] == pytest.approx(3.0)
    assert r3["low_level_offset_db"] == pytest.approx(3.0)


def test_zp_peak_db() -> None:
    """Zp = fp 邻域窗口内 |Z| dB 峰值。"""
    freq = build_frequency_grid(4e9, 7e9, f_step_hz=3e6)
    curve = 30.0 + 20.0 * np.exp(-0.5 * ((freq / 1e9 - 5.4) / 0.05) ** 2)
    peak = cal.zp_peak_db(curve, freq, fp_ghz=5.4)
    assert peak == pytest.approx(50.0, abs=0.05)


def _fabricate_baseline(path: Path) -> None:
    """伪造 2 面积 × 2 pf 的 baseline.npz（Mason 自洽假"生产真值"，键序与真 baseline 一致）。"""
    areas = np.array([2000.0, 3000.0])
    pfs = np.array([0.0, 1.0])
    mat = PiezoMaterial("fake", ALSCN.rho, ALSCN.c33 * 1.02, ALSCN.e33 * 0.98, ALSCN.eps * 1.01)
    freq_full = build_frequency_grid(4e9, 7e9, f_step_hz=3e6)
    params = np.zeros((2, 2, 7))
    z = np.zeros((2, 2, freq_full.size))
    for pi, t_pf in enumerate((0.0, 63.5)):
        for ai, area in enumerate(areas):
            stack = EPEStackParams(
                t_piezo=nm_to_m(352.0),
                t_top=nm_to_m(150.0),
                t_bot=nm_to_m(150.0),
                t_pf=nm_to_m(t_pf),
                area=um2_to_m2(float(area)),
                alpha=0.02,
            )
            res = run_epe_stack_sweep(freq_full, mat, MO, SIN, stack)
            z[pi, ai] = res.abs_z11_db
            labels = extract_epe_production_labels(freq_full, res.z11)
            assert labels["valid"]
            params[pi, ai] = _labels_vec(labels)
    np.savez_compressed(path, pfs=pfs, areas=areas, params_base=params, z_base_full=z)


def test_main_smoke_end_to_end(tmp_path: Path) -> None:
    """端到端 smoke：伪造 baseline 上 main() 产出 3 文件且 schema 必要键齐全。"""
    baseline = tmp_path / "baseline.npz"
    _fabricate_baseline(baseline)
    outdir = tmp_path / "calibration"
    rc = cal.main(
        [
            "--baseline",
            str(baseline),
            "--outdir",
            str(outdir),
            "--geometries",
            "150",
            "--n-starts",
            "1",
            "--max-nfev",
            "12",
            "--grid-step-mhz",
            "2.0",
            "--workers",
            "1",
            "--seed",
            "123",
        ]
    )
    assert rc == 0

    mats = json.loads((outdir / "calibrated_materials.json").read_text(encoding="utf-8"))
    for key in (
        "piezo",
        "alpha",
        "t_piezo_nm",
        "t_mo_nm",
        "t_pf_nm_pf1",
        "target",
        "method",
        "bounds_pct",
        "residuals_summary",
        "commit_sha",
        "calibrated_at",
    ):
        assert key in mats, key
    for key in ("name", "rho", "c33", "e33", "eps"):
        assert key in mats["piezo"], key
    assert mats["t_pf_nm_pf1"] == 63.5
    assert mats["bounds_pct"] == 10.0
    lo, hi = cal.theta_bounds()
    assert lo[0] - 1e-9 <= mats["t_piezo_nm"] <= hi[0] + 1e-9
    assert lo[1] - 1e-12 <= mats["piezo"]["c33"] / ALSCN.c33 <= hi[1] + 1e-12

    res = json.loads((outdir / "residuals.json").read_text(encoding="utf-8"))
    for key in ("pf0", "pf1", "spectra", "zp"):
        assert key in res, key

    report = (outdir / "calibration_report.md").read_text(encoding="utf-8")
    assert "校准后 Mason" in report
