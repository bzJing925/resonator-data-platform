"""WP-A：TQ06 14 GHz P3F 器件层归属判定与校准（scripts/tq06_analyze.py）测试。

覆盖关键纯函数与物理不变量：
- s1p 解析：``!``/``#``/空行跳过、dB∠° → 复数 S11、无数据报错；
- 文件名元数据解析：38 个真实命名模式（EG/FL/PF/CB/No AG/mark[2]/负坐标/S11|S22）；
- z11_from_s11 与 solver.s11_from_z11 往返一致；
- TE2 支路自适应窗：f2_pred = 1/Σ(t/v)，窗 (1.0, 1.13]×f2_pred 排除 TE3 误锁；
- 镜像简并（物理不变量，归属判定的核心结论）：归属 A(b,t) 与归属 B(t,b) 的
  1 端口 Z11 逐点一致（电极厚度自由时两种归属在 Z 数据上不可区分）；
- reverse 全局符号翻转简并：(+1,−1) ≡ (−1,+1)（P3F 极性序对调对照是平凡简并）；
- 残差统计 / 子集选择（|Δfs2| 最小前 n，确定性 tie-break，跳过 invalid）；
- 有界 LS 可恢复性（伪目标，窄带快速口径）；
- npz 产物 schema。

拟合测试用窄带 12–16 GHz @4 MHz 控制耗时（TE2 窗 (10,20] 内含 fs2；TE1 诊断窗
[3,8) 无覆盖 → te1 诊断 nan，不影响 valid——与全带行为一致）。
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

import tq06_analyze as tq  # noqa: E402

from app.mason_sim import (  # noqa: E402
    ALN1,
    MO,
    EPPEParams,
    build_frequency_grid,
    nm_to_m,
    run_eppe_sweep,
    s11_from_z11,
    um2_to_m2,
)
from app.mason_sim.prodlabels import PARAM_KEYS  # noqa: E402

GRID_TEST = build_frequency_grid(3e9, 22e9, f_step_hz=4e6)  # 测试粗网格（提速）
GRID_FIT = build_frequency_grid(12e9, 16e9, f_step_hz=4e6)  # 可恢复性窄带


# ============================================================
# s1p 解析
# ============================================================
def test_parse_s1p_skips_comments_and_reads_db_angle(tmp_path: Path) -> None:
    p = tmp_path / "t.s1p"
    p.write_text(
        "!Keysight Technologies,N5227B\n"
        "!Date: Thursday, July 10, 2025\n"
        "# Hz S  dB   R 50\n"
        "\n"
        "3000000000 0.0 90.0\n"
        "4000000000 -6.0205999 180.0\n"
        "5000000000 20.0 0.0\n",
        encoding="utf-8",
    )
    freq, s11 = tq.parse_s1p_db_angle(p)
    assert freq.tolist() == [3.0e9, 4.0e9, 5.0e9]
    # 0 dB ∠90° → j
    assert s11[0] == pytest.approx(1j, abs=1e-12)
    # -6.0206 dB ∠180° → -0.5
    assert s11[1] == pytest.approx(-0.5 + 0j, abs=1e-6)
    # +20 dB ∠0° → 10
    assert s11[2] == pytest.approx(10.0 + 0j, abs=1e-9)


def test_parse_s1p_no_data_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.s1p"
    p.write_text("! only comments\n# Hz S dB R 50\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="无数据"):
        tq.parse_s1p_db_angle(p)


def test_z11_from_s11_roundtrip() -> None:
    rng = np.random.default_rng(20260914)
    z = (rng.uniform(0.5, 500, 64) + 1j * rng.uniform(-500, 500, 64)).astype(complex)
    rt = tq.z11_from_s11(s11_from_z11(z, 50.0), 50.0)
    np.testing.assert_allclose(rt, z, rtol=1e-12, atol=1e-9)


# ============================================================
# 文件名元数据
# ============================================================
REAL_STEMS = [
    "1_EG0.75_FL0.75_No AG_E[2]_X0Y1N5_Fail_S11",
    "1_EG0.75_FL1_No AG_A_X0Y1N5_Fail_S11",
    "1_EG0.75_FL1_No AG_D_X0Y-2N19_Fail_S11",
    "1_EG0.75_FL1_No AG_E[2]_X0Y-2N19_Fail_S11",
    "1_EG0.75_FL1_No AG_E_X-1Y-1N16_Fail_S11",
    "1_EG0.75_FL1_No AG_F[2]_X1Y0N11_Fail_S11",
    "1_EG0.75_FL1_No AG_F_X1Y0N11_Fail_S22",
    "1_EG0.75_FL2_CB_B_X-1Y0N9_Fail_S11",
    "1_EG0.75_FL2_CB_B_X0Y0N10_Fail_S22",
    "1_EG0_FL0.75_PF_D_X1Y-1N14_Fail_S11",
    "1_EG0_FL1.5_B_X2Y-1N13_Fail_S11",
    "1_EG1.5_FL1_A_X1Y-1N14_Fail_S11",
    "1_EG1_FL0.75_D_X0Y-2N19_Fail_S22",
]


def test_parse_tq06_filename_real_stems() -> None:
    metas = [tq.parse_tq06_filename(s) for s in REAL_STEMS]
    m0 = metas[0]
    assert (m0.eg, m0.fl, m0.pf, m0.ag, m0.mark) == (0.75, 0.75, False, "No AG", "E[2]")
    assert (m0.x, m0.y, m0.n, m0.port) == (0, 1, 5, "S11")
    m6 = metas[6]
    assert (m6.mark, m6.port, m6.ag) == ("F", "S22", "No AG")
    m9 = metas[9]
    assert (m9.pf, m9.eg, m9.fl, m9.y) == (True, 0.0, 0.75, -1)
    m7 = metas[7]
    assert (m7.ag, m7.x, m7.y) == ("CB", -1, 0)
    assert metas[11].eg == pytest.approx(1.5)
    # 全部 38 个真实命名（数据目录在的时候）都能解析
    if tq.TQ06_DIR_DEFAULT.exists():
        for f in sorted(tq.TQ06_DIR_DEFAULT.glob("*.s1p")):
            tq.parse_tq06_filename(f.stem)


def test_parse_tq06_filename_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        tq.parse_tq06_filename("random_name_S11")


# ============================================================
# TE2 支路自适应窗与仿真标签
# ============================================================
def test_predict_te2_hz_scale_and_monotonicity() -> None:
    alscn = tq.load_calibrated_alscn()
    f_ref = tq.predict_te2_hz(170.0, 190.0, 130.0, 130.0, alscn)
    # 1/Σ(t/v) = 1/(170/11268 + 190/9064 + 2×130/6290) GHz ≈ 12.92 GHz
    assert f_ref == pytest.approx(12.92e9, abs=0.05e9)
    f_thick = tq.predict_te2_hz(170.0, 190.0, 160.0, 160.0, alscn)
    assert f_thick < f_ref  # 电极加厚 → 支路频率下降


def test_adaptive_window_tracks_f2_branch() -> None:
    """几何 (100,200)：宽窗 (10,20] 误锁 TE3(~17.9 GHz)；自适应窗锁定 f2 支路。"""
    alscn = tq.load_calibrated_alscn()
    wide = tq.simulate_te2_labels(
        "A", 100.0, 200.0, alscn=alscn, alpha=0.022, freq=GRID_TEST, adaptive=False
    )
    adap = tq.simulate_te2_labels(
        "A", 100.0, 200.0, alscn=alscn, alpha=0.022, freq=GRID_TEST, adaptive=True
    )
    assert wide["fs_ghz"] > 17.0  # 宽窗在该几何确实锁到 TE3
    assert adap["valid"] is True
    f2p = tq.predict_te2_hz(170.0, 190.0, 100.0, 200.0, alscn)
    assert f2p * 1.0 < adap["fs_ghz"] * 1e9 <= f2p * 1.13
    assert adap["fs_ghz"] == pytest.approx(12.6, abs=0.8)


def test_mirror_degeneracy_exact() -> None:
    """归属 A(mo_b,mo_t) 与归属 B(mo_t,mo_b) 的 Z11 逐点一致（镜像简并）。

    这是归属判定的核心物理结论：电极厚度为未知自由量时，两种层归属对任意
    1 端口 Z 数据给出相同预测，绝对归属不可由 Z 数据区分。
    """
    alscn = tq.load_calibrated_alscn()
    for mo_b, mo_t in ((100.0, 160.0), (130.0, 200.0)):
        pa = EPPEParams(
            t_piezo_bot=nm_to_m(170.0),
            t_piezo_top=nm_to_m(190.0),
            t_mo_bot=nm_to_m(mo_b),
            t_mo_top=nm_to_m(mo_t),
            area=um2_to_m2(2000.0),
            alpha=0.022,
        )
        pb = EPPEParams(
            t_piezo_bot=nm_to_m(190.0),
            t_piezo_top=nm_to_m(170.0),
            t_mo_bot=nm_to_m(mo_t),
            t_mo_top=nm_to_m(mo_b),
            area=um2_to_m2(2000.0),
            alpha=0.022,
        )
        za = run_eppe_sweep(GRID_TEST, ALN1, alscn, MO, pa).z11
        zb = run_eppe_sweep(GRID_TEST, alscn, ALN1, MO, pb).z11
        np.testing.assert_allclose(za, zb, rtol=1e-9, atol=1e-6)


def test_reverse_global_signflip_degenerate() -> None:
    """reverse (+1,−1) ≡ (−1,+1)：极性序对调对照实验是平凡简并（只需记录）。"""
    alscn = tq.load_calibrated_alscn()
    kw = dict(alscn=alscn, alpha=0.022, freq=GRID_TEST, adaptive=True)
    la = tq.simulate_te2_labels("A", 130.0, 160.0, reverse=(+1.0, -1.0), **kw)
    lb = tq.simulate_te2_labels("A", 130.0, 160.0, reverse=(-1.0, +1.0), **kw)
    assert la["fs_ghz"] == lb["fs_ghz"]
    assert la["fp_ghz"] == lb["fp_ghz"]
    assert la["k2eff_pct"] == lb["k2eff_pct"]


# ============================================================
# 统计与子集选择
# ============================================================
def _row(name: str, fs: float, valid: bool = True, port: str = "S11") -> dict:
    return {
        "file": name,
        "port": port,
        "valid": valid,
        "fs_ghz": fs,
        "fp_ghz": fs + 0.2,
        "k2eff_pct": 3.0,
        "qs": 150.0,
        "qp": 250.0,
        "qs_bodeq": 140.0,
        "qp_bodeq": 240.0,
    }


def test_summarize_rows_valid_only() -> None:
    rows = [
        _row("a", 14.10),
        _row("b", 14.20),
        _row("c", 14.30),
        _row("d", 14.40),
        _row("bad", float("nan"), valid=False),
    ]
    stats = tq.summarize_rows(rows)
    fs = stats["fs_ghz"]
    assert fs["n"] == 4
    assert fs["median"] == pytest.approx(14.25)
    assert fs["q25"] == pytest.approx(14.175)
    assert fs["q75"] == pytest.approx(14.325)
    assert fs["min"] == pytest.approx(14.10)
    assert fs["max"] == pytest.approx(14.40)
    assert set(stats.keys()) == set(PARAM_KEYS)


def test_select_subset_top_n_by_fs_residual() -> None:
    # 二进制精确值构造真实 tie（14.25/13.75 对 14.0 的 |Δ| 均为 0.25）
    rows = [
        _row("far", 15.00),
        _row("near1", 14.00),
        _row("near2", 13.875),
        _row("tie_b", 13.75),
        _row("tie_a", 14.25),  # 与 tie_b 同 |Δ|，按文件名确定性 tie-break
        _row("invalid", float("nan"), valid=False),
        _row("s22_only", 14.0625, port="S22"),
    ]
    idx = tq.select_subset(rows, sim_fs_ghz=14.00, n=3, port="S11")
    assert [rows[i]["file"] for i in idx] == ["near1", "near2", "tie_a"]
    # port=None 时纳入 S22
    idx_all = tq.select_subset(rows, sim_fs_ghz=14.00, n=4, port=None)
    assert [rows[i]["file"] for i in idx_all] == ["near1", "s22_only", "near2", "tie_a"]


# ============================================================
# 高 Q 子集选择（WP-A 续：仿真接近门内按 qs 降序）
# ============================================================
def _hq_row(
    name: str,
    fs: float,
    qs: float,
    *,
    valid: bool = True,
    port: str = "S11",
    pf: bool = False,
) -> dict:
    return {
        "file": name,
        "port": port,
        "valid": valid,
        "pf": pf,
        "fs_ghz": fs,
        "fp_ghz": fs + 0.15,
        "k2eff_pct": 2.5,
        "qs": qs,
        "qp": 200.0,
        "qs_bodeq": 150.0,
        "qp_bodeq": 190.0,
    }


def test_select_subset_highq_filters_gate_port_pf() -> None:
    """门控：|fs2 − sim| ≤ 60 MHz（含端点）+ S11 + pf=0 + valid + qs 有限。"""
    rows = [
        _hq_row("in_high", 14.20, 300.0),
        _hq_row("in_lowq", 14.20, 50.0),
        # 下端点：|14.14−14.20|×1e3 = 59.999… MHz（float 方向向下）→ 含端点入集
        _hq_row("edge_in", 14.14, 100.0),
        _hq_row("edge_out", 14.261, 400.0),  # 门外 1 MHz，Q 再高也不收
        _hq_row("pf_excluded", 14.20, 500.0, pf=True),  # _PF_ 质量加载态剔除
        _hq_row("s22_excluded", 14.20, 500.0, port="S22"),  # 端口不对称偏移，沿用排除
        _hq_row("invalid_excluded", 14.20, 500.0, valid=False),
        _hq_row("nan_qs", 14.20, float("nan")),
    ]
    idx = tq.select_subset_highq(rows, 14.20, gate_mhz=60.0, n=10, port="S11")
    assert [rows[i]["file"] for i in idx] == ["in_high", "edge_in", "in_lowq"]


def test_select_subset_highq_qs_desc_and_tiebreak() -> None:
    """排序：qs 降序；同 qs 按文件名升序确定性 tie-break；n 截断。"""
    rows = [
        _hq_row("b_tie", 14.20, 200.0),
        _hq_row("a_tie", 14.21, 200.0),
        _hq_row("top", 14.22, 300.0),
        _hq_row("low", 14.23, 100.0),
    ]
    idx = tq.select_subset_highq(rows, 14.20, gate_mhz=60.0, n=3, port="S11")
    assert [rows[i]["file"] for i in idx] == ["top", "a_tie", "b_tie"]
    # port=None 时纳入 S22（沿用 select_subset 的端口语义）
    rows_s22 = rows + [_hq_row("s22_hi", 14.20, 250.0, port="S22")]
    idx_all = tq.select_subset_highq(rows_s22, 14.20, gate_mhz=60.0, n=3, port=None)
    assert [rows_s22[i]["file"] for i in idx_all] == ["top", "s22_hi", "a_tie"]


EXPECTED_HIGHQ_FILES = [  # qs 降序：241.2/224.3/222.3/221.2/204.9/178.1/173.4/156.7/155.9/132.8
    "1_EG1.5_FL1_A_X1Y-1N14_Fail_S11.s1p",
    "1_EG0.75_FL2_No AG_C_X0Y-2N19_Fail_S11.s1p",
    "1_EG0.75_FL2_No AG_C_X1Y-1N14_Fail_S11.s1p",
    "1_EG0_FL1_A_X0Y-2N19_Fail_S11.s1p",
    "1_EG0_FL1.5_B_X2Y-1N13_Fail_S11.s1p",
    "1_EG0.75_FL2_CB_C_X1Y-1N14_Fail_S11.s1p",
    "1_EG0.75_FL1_No AG_A_X1Y0N11_Fail_S11.s1p",
    "1_EG0.75_FL1_No AG_E_X-1Y-1N16_Fail_S11.s1p",
    "1_EG0.75_FL2_No AG_B_X0Y-1N15_Fail_S11.s1p",
    "1_EG0.75_FL2_CB_B_X-1Y0N9_Fail_S11.s1p",
]


@pytest.mark.skipif(not tq.TQ06_DIR_DEFAULT.exists(), reason="TQ06 数据目录不在本机")
def test_select_subset_highq_real_data_membership_and_target() -> None:
    """真数据：A(130,100) 基准 fs2=14.252 GHz 门内 qs 降序前 10 + 子集中位目标。"""
    rows = [tq.extract_measured(p) for p in sorted(tq.TQ06_DIR_DEFAULT.glob("*.s1p"))]
    idx = tq.select_subset_highq(rows, 14.252, gate_mhz=60.0, n=10, port="S11")
    sub = [rows[i] for i in idx]
    assert [r["file"] for r in sub] == EXPECTED_HIGHQ_FILES
    # 全部成员确实在 ±60 MHz 门内、S11、pf=0
    for r in sub:
        assert r["port"] == "S11" and not r["pf"]
        assert abs(r["fs_ghz"] - 14.252) * 1e3 <= 60.0
    target = {k: float(np.median([float(r[k]) for r in sub])) for k in PARAM_KEYS}
    assert target["fs_ghz"] == pytest.approx(14.2516, abs=1e-4)
    assert target["fp_ghz"] == pytest.approx(14.39305, abs=1e-4)
    assert target["k2eff_pct"] == pytest.approx(2.49884, abs=1e-4)
    assert target["qs"] == pytest.approx(191.517, abs=1e-3)
    assert target["qp"] == pytest.approx(222.736, abs=1e-3)
    assert target["qs_bodeq"] == pytest.approx(177.578, abs=1e-3)
    assert target["qp_bodeq"] == pytest.approx(198.123, abs=1e-3)


# ============================================================
# 残差向量与有界 LS 可恢复性
# ============================================================
def test_residual_vector_penalty_and_values() -> None:
    w = tq.default_weights()
    target = np.array([14.3, 14.5, 3.0, 150.0, 250.0, 140.0, 240.0])
    bad = tq.residual_vector({"valid": False}, target, w)
    assert np.all(bad == tq.PENALTY)
    good = tq.residual_vector(
        {
            "valid": True,
            "fs_ghz": 14.3 + 0.010,  # +10 MHz
            "fp_ghz": 14.5,
            "k2eff_pct": 3.0 + 0.5,
            "qs": 150.0 * 1.10,  # +10%
            "qp": 250.0,
            "qs_bodeq": 140.0,
            "qp_bodeq": 240.0,
        },
        target,
        w,
    )
    assert good[0] == pytest.approx(10.0 * w[0])
    assert good[1] == pytest.approx(0.0, abs=1e-9)
    assert good[2] == pytest.approx(0.5 * w[2])
    assert good[3] == pytest.approx(10.0 * w[3])


def test_fit_recovers_params_on_pseudo_target() -> None:
    """伪目标可恢复性：真值 s_e33=0.97、t_mo_top=170 nm，窄带 2 自由度拟合恢复。

    判据说明（同 WP3 test_calibrate 的实证立场）：7 参数标签吸附频率网格形成
    阶梯景观，(e33, eps) 拆分方向近简并、xtol 收敛可能不可达（max_nfev 截断），
    故判据取**标签层恢复**（fs/fp 在频格量化内、k²eff 在 1 量化步内）+
    参数层宽松界（t_mo_top ±8 nm、s_e33 ±0.05）+ cost 大幅下降。
    """
    alscn = tq.load_calibrated_alscn()
    theta_true = np.array([1.0, 0.97, 1.0, 1.0, 150.0, 170.0])
    target_labels = tq.simulate_te2_from_theta(
        theta_true, attribution="A", alscn_base=alscn, alpha_base=0.022, freq=GRID_FIT
    )
    assert target_labels["valid"] is True
    target = np.array([float(target_labels[k]) for k in PARAM_KEYS])
    start = np.array([1.0, 1.0, 1.0, 1.0, 150.0, 140.0])
    w = tq.default_weights()
    r0 = tq.residual_vector(
        tq.simulate_te2_from_theta(
            start, attribution="A", alscn_base=alscn, alpha_base=0.022, freq=GRID_FIT
        ),
        target,
        w,
    )
    cost0 = 0.5 * float(r0 @ r0)
    fit = tq.fit_calibration(
        target,
        attribution="A",
        alscn_base=alscn,
        alpha_base=0.022,
        freq=GRID_FIT,
        starts=np.array([start]),
        free=("s_e33", "t_mo_top_nm"),
        max_nfev=120,
    )
    assert fit.cost < max(1.0, 0.2 * cost0)
    assert fit.theta[1] == pytest.approx(0.97, abs=0.05)
    assert fit.theta[5] == pytest.approx(170.0, abs=8.0)
    lab = tq.simulate_te2_from_theta(
        fit.theta, attribution="A", alscn_base=alscn, alpha_base=0.022, freq=GRID_FIT
    )
    assert abs(lab["fs_ghz"] - target[0]) * 1e3 <= 4.0  # 频格量化以内
    assert abs(lab["fp_ghz"] - target[1]) * 1e3 <= 4.0
    assert abs(lab["k2eff_pct"] - target[2]) < 0.3


# ============================================================
# npz 产物 schema
# ============================================================
def test_write_curves_npz_schema(tmp_path: Path) -> None:
    out = tmp_path / "tq06_curves.npz"
    meas = [
        ("dev1", np.array([3.0, 4.0, 5.0]), np.array([40.0, 41.0, 42.0])),
        ("dev2", np.array([3.0, 4.0, 5.0]), np.array([43.0, 44.0, 45.0])),
    ]
    sim_f = np.array([3.0, 4.0, 5.0])
    meta = {"verdict": "A", "subset": ["dev1", "dev2"]}
    tq.write_curves_npz(out, sim_f, np.zeros(3), np.ones(3), meas, meta)
    with np.load(out) as z:
        assert set(z.files) >= {
            "sim_freq_ghz",
            "mason_before_db",
            "mason_after_db",
            "meas_freq_ghz_0",
            "meas_z_db_0",
            "meas_freq_ghz_1",
            "meas_z_db_1",
        }
        np.testing.assert_array_equal(z["meas_z_db_1"], np.array([43.0, 44.0, 45.0]))
    meta_rt = json.loads((tmp_path / "tq06_curves.meta.json").read_text(encoding="utf-8"))
    assert meta_rt["verdict"] == "A"
    assert meta_rt["meas_names"] == ["dev1", "dev2"]


# ============================================================
# 真实数据 smoke（数据目录缺失时跳过）
# ============================================================
@pytest.mark.skipif(not tq.TQ06_DIR_DEFAULT.exists(), reason="TQ06 数据目录不在本机")
def test_real_data_extraction_smoke() -> None:
    rows = [tq.extract_measured(p) for p in sorted(tq.TQ06_DIR_DEFAULT.glob("*.s1p"))]
    assert len(rows) == 38
    valid = [r for r in rows if r["valid"]]
    assert len(valid) >= 35
    stats = tq.summarize_rows(valid)
    assert 14.0 < stats["fs_ghz"]["median"] < 14.6
    assert 2.0 < stats["k2eff_pct"]["median"] < 4.0
