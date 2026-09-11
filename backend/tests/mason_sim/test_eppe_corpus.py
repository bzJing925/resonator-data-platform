"""EPPE Mason 语料生成脚本（scripts/build_mason_eppe_corpus.py）测试（WP2b）。

- 材料冻结：校准 AlScN 从 calibrated_materials.json 加载；改 json 值求解器输出变
- 采样：同种子逐位可复现、盒内、t_aln/area 5 档正交轮转
- 支路核验：已知几何通过 / 人为跳支案例拦下（branch_ambiguous）
- 丢弃分支：no_resonance / te2_out_of_window / label_nan / solver_error 各至少一例
- 12 样本端到端：npz 形状/dtype/有限性 + diag + meta.json 字段
- 断点续跑：已存在 shard 不重算；异配置续跑报错
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_mason_eppe_corpus as corpus  # noqa: E402

from app.mason_sim import ALN1  # noqa: E402
from app.mason_sim.prodlabels import PARAM_KEYS  # noqa: E402

TEST_SEED = corpus.SEED_DEFAULT

# 支路核验通过几何（校准 AlScN，2026-09-11 探测）：
# f1_twin=6.432 GHz，窗内最强对 fs2=13.198 GHz，f2/f1=2.052 ∈ [1.9, 2.8]
ROW_PASS = (100.0, 400.0, 50.0, 50.0, 0.0, 2000.0)
# 人为跳支几何（brief 探测案例口径）：t_alscn=400 厚电极，
# 窗内最强对 16.098 GHz 为 f4 支路（f4/f1_twin=3.92 > 2.8）→ branch_ambiguous
ROW_BRANCH_JUMP = (100.0, 400.0, 150.0, 150.0, 0.0, 2000.0)


@pytest.fixture(scope="module")
def cal_materials():
    return corpus.load_calibrated_piezo()


def _solve_row(row, mats):
    piezo_top, alpha, _info = mats
    return corpus.solve_extract_one(row, piezo_bot=ALN1, piezo_top=piezo_top, alpha=alpha)


# ============================================================
# 材料冻结
# ============================================================
def test_material_freeze_loading(cal_materials) -> None:
    """校准材料加载：值与 calibrated_materials.json 逐字段一致；alpha=0.022；溯源完整。"""
    piezo, alpha, info = cal_materials
    payload = json.loads(corpus.CALIBRATION_JSON.read_text())
    assert piezo.rho == payload["piezo"]["rho"]
    assert piezo.c33 == payload["piezo"]["c33"]
    assert piezo.e33 == payload["piezo"]["e33"]
    assert piezo.eps == payload["piezo"]["eps"]
    assert alpha == payload["alpha"]
    assert alpha == pytest.approx(0.022, abs=1e-9)
    assert info["commit_sha"] == payload["commit_sha"]
    assert len(info["sha256"]) == 64


def test_material_freeze_changes_solver_output(tmp_path: Path, cal_materials) -> None:
    """改 json 的 c33（×1.1）→ 加载后同一几何 fs2 改变（材料确实从文件冻结加载）。"""
    payload = json.loads(corpus.CALIBRATION_JSON.read_text())
    payload["piezo"]["c33"] *= 1.1
    modified = tmp_path / "modified_materials.json"
    modified.write_text(json.dumps(payload))
    piezo_mod, alpha_mod, _ = corpus.load_calibrated_piezo(modified)

    r_ref = _solve_row(ROW_PASS, cal_materials)
    r_mod = corpus.solve_extract_one(ROW_PASS, piezo_bot=ALN1, piezo_top=piezo_mod, alpha=alpha_mod)
    assert r_ref["valid"] and r_mod["valid"]
    assert r_mod["y"][0] != r_ref["y"][0]  # c33 改变 → fs2 改变


# ============================================================
# 采样
# ============================================================
def test_sampling_deterministic_in_box_orthogonal_rotation() -> None:
    """同种子逐位可复现；LHS 4 维在盒内；t_aln/area 5 档正交轮转（前 25 样本覆盖 25 组合）。"""
    g1 = corpus.sample_geometry(50, seed=123)
    g2 = corpus.sample_geometry(50, seed=123)
    np.testing.assert_array_equal(g1, g2)
    assert g1.shape == (50, 6) and g1.dtype == np.float64

    assert set(np.unique(g1[:, 0]).tolist()) <= set(corpus.T_ALN_LEVELS_NM)
    assert float(g1[:, 1].min()) >= corpus.T_ALSCN_MIN_NM
    assert float(g1[:, 1].max()) <= corpus.T_ALSCN_MAX_NM
    for col, (lo, hi) in ((2, corpus.T_TOP_RANGE_NM), (3, corpus.T_BOT_RANGE_NM)):
        assert float(g1[:, col].min()) >= lo and float(g1[:, col].max()) <= hi
    assert float(g1[:, 4].min()) >= 0.0 and float(g1[:, 4].max()) <= corpus.T_PF_RANGE_NM[1]
    assert set(np.unique(g1[:, 5]).tolist()) <= set(corpus.AREA_LEVELS_UM2)

    # 正交轮转：前 25 个样本的 (t_aln, area) 组合恰好 25 种各一次
    combos = {(float(g1[i, 0]), float(g1[i, 5])) for i in range(25)}
    assert len(combos) == 25


def test_solve_and_dense_grids() -> None:
    """求解网格 3–22 GHz @2 MHz（9501 点）；密集网格 8–22 GHz @5 MHz（2801 点）。"""
    assert len(corpus.SOLVE_FREQ_HZ) == 9501
    np.testing.assert_allclose(
        corpus.SOLVE_FREQ_HZ / 1e9, 3.0 + 0.002 * np.arange(9501), rtol=0.0, atol=1e-12
    )
    assert len(corpus.DENSE_FREQ_HZ) == 2801
    np.testing.assert_allclose(
        corpus.DENSE_FREQ_HZ / 1e9, 8.0 + 0.005 * np.arange(2801), rtol=0.0, atol=1e-12
    )


# ============================================================
# 支路核验
# ============================================================
def test_branch_anchor_ok_unit() -> None:
    """支路核验谓词：f2/f1 比值窗口 [1.9, 2.8]（含端点）；nan/非正 f1 一律拒绝。"""
    ok = corpus.branch_anchor_ok
    # 窗口内：实测通过几何（比值 2.052）与 WP1 黄金对称案例（比值 2.41）
    assert ok(13.198e9, 6.432e9)  # ratio=2.052
    assert ok(11.15e9, 4.632e9)  # ratio=2.408（AlN/AlN 250/250/Mo150 黄金案例）
    # 边界含端点
    assert ok(1.9e9, 1.0e9)  # ratio=1.9 → 通过
    assert ok(2.8e9, 1.0e9)  # ratio=2.8 → 通过
    # 边界外（含 f3 支路量级 3.2+ 与跳支案例 3.92）
    assert not ok(1.899e9, 1.0e9)
    assert not ok(2.801e9, 1.0e9)
    assert not ok(3.2e9, 1.0e9)  # f3 支路下界
    assert not ok(16.098e9, 4.108e9)  # ratio=3.92（跳支案例）
    # nan / 非正 f1 拒绝
    assert not ok(13.0e9, float("nan"))
    assert not ok(float("nan"), 6.5e9)
    assert not ok(13.0e9, 0.0)


def test_branch_guard_accepts_golden_symmetric_case() -> None:
    """WP1 黄金案例（AlN/AlN 250/250/Mo150 对称反极性，比值 2.41）必须放行。

    该几何在旧 15%-of-2f1 守卫下被误杀（err=0.41）；比值窗口（控制者拍板）
    接受全部偶模 f2 支路。同材料双层不经校准材料，直接传 ALN1/ALN1。
    """
    r = corpus.solve_extract_one(
        (250.0, 250.0, 150.0, 150.0, 0.0, 2000.0),
        piezo_bot=ALN1,
        piezo_top=ALN1,
        alpha=0.022,
    )
    assert r["valid"], r["reason"]
    fs2_ghz, f1_twin_ghz = r["y"][0], r["diag"][3]
    ratio = fs2_ghz / f1_twin_ghz
    assert ratio == pytest.approx(2.41, abs=0.05)
    assert corpus.BRANCH_RATIO_LO <= ratio <= corpus.BRANCH_RATIO_HI
    # 对称反极性：TE1 诊断抑制为 nan
    assert np.isnan(r["diag"][0])


def test_branch_guard_pass_geometry(cal_materials) -> None:
    """近谐波薄电极几何（比值 2.05）：支路核验通过，valid=True，f1_twin 入诊断列。"""
    r = _solve_row(ROW_PASS, cal_materials)
    assert r["valid"], r["reason"]
    fs2_ghz, f1_twin_ghz = r["y"][0], r["diag"][3]
    assert fs2_ghz == pytest.approx(13.198, abs=0.05)
    assert f1_twin_ghz == pytest.approx(6.432, abs=0.05)
    ratio = fs2_ghz / f1_twin_ghz
    assert corpus.BRANCH_RATIO_LO <= ratio <= corpus.BRANCH_RATIO_HI
    assert 10.0 < fs2_ghz <= 20.0
    # TE1 诊断：残余 ~6.6 GHz 在 [3,8) 窗内 → 有限值
    assert np.isfinite(r["diag"][0]) and np.isfinite(r["diag"][2])
    # z_dense 形状与 dtype
    assert r["z"].shape == (2801,) and r["z"].dtype == np.complex64


def test_branch_guard_blocks_branch_jump_case(cal_materials) -> None:
    """跳支案例（厚电极 t_alscn=400）：窗内最强对为 f4 支路（比值 3.92）→ branch_ambiguous。"""
    r = _solve_row(ROW_BRANCH_JUMP, cal_materials)
    assert not r["valid"]
    assert r["reason"] == "branch_ambiguous"


# ============================================================
# 丢弃分支（stub 标签路径）
# ============================================================
def _stub_labels(reason: str) -> dict:
    d: dict = {k: float("nan") for k in PARAM_KEYS}
    d.update(
        {
            "fs1_ghz": float("nan"),
            "fp1_ghz": float("nan"),
            "k2eff_te1_pct": float("nan"),
            "valid": False,
            "reason": reason,
        }
    )
    return d


@pytest.mark.parametrize("reason", ["no_resonance", "te2_out_of_window", "label_nan"])
def test_invalid_label_reasons_propagate(
    monkeypatch: pytest.MonkeyPatch, cal_materials, reason: str
) -> None:
    """标签无效的三种原因（no_resonance/te2_out_of_window/label_nan）原样传播为丢弃原因。"""
    monkeypatch.setattr(
        corpus, "extract_eppe_production_labels", lambda *a, **k: _stub_labels(reason)
    )
    r = _solve_row(ROW_PASS, cal_materials)
    assert not r["valid"]
    assert r["reason"] == reason


def test_solver_error_fallback(monkeypatch: pytest.MonkeyPatch, cal_materials) -> None:
    """求解器异常（如 LinAlgError）兜底为 solver_error，不炸整轮。"""

    def _boom(*args: object, **kwargs: object) -> None:
        raise np.linalg.LinAlgError("Singular matrix")

    monkeypatch.setattr(corpus, "run_eppe_sweep", _boom)
    r = _solve_row(ROW_PASS, cal_materials)
    assert not r["valid"]
    assert r["reason"] == "solver_error"
    assert "LinAlgError" in r["error"]


# ============================================================
# 端到端 / 断点续跑
# ============================================================
def _run_12(tmp_path: Path) -> tuple[Path, Path]:
    out = tmp_path / "corpus.npz"
    shards = tmp_path / "shards"
    rc = corpus.main(
        [
            "--n",
            "12",
            "--out",
            str(out),
            "--shard-dir",
            str(shards),
            "--shard-size",
            "5",
            "--nprocs",
            "2",
            "--seed",
            str(TEST_SEED),
        ]
    )
    assert rc == 0
    return out, shards


def test_end_to_end_12_samples(tmp_path: Path) -> None:
    """12 样本端到端：形状/dtype/有限性/物理范围/meta 字段；丢弃计数闭合。"""
    out, _shards = _run_12(tmp_path)

    data = np.load(out)
    x = data["x_geom"]
    y = data["y_params"]
    z = data["z_dense"]
    f = data["freq_ghz"]
    diag = data["diag"]

    m = x.shape[0]
    assert 0 <= m <= 12
    assert x.shape == (m, 6) and x.dtype == np.float64
    assert y.shape == (m, 7) and y.dtype == np.float64
    assert z.shape == (m, 2801) and z.dtype == np.complex64
    assert f.shape == (2801,)
    assert diag.shape == (m, 4) and diag.dtype == np.float64
    np.testing.assert_allclose(f, 8.0 + 0.005 * np.arange(2801), rtol=0.0, atol=1e-12)

    if m > 0:
        assert np.isfinite(x).all()
        assert np.isfinite(y).all()
        assert np.isfinite(z.real).all() and np.isfinite(z.imag).all()
        # TE2 窗约束与 fp>fs 在产物上成立
        assert ((y[:, 0] > 10.0) & (y[:, 0] <= 20.0)).all()
        assert (y[:, 1] > y[:, 0]).all()
        assert ((y[:, 2] > 0.0) & (y[:, 2] < 20.0)).all()  # AlScN kt2≈19% 物理天花板
        # 支路核验在产物上成立：f2/f1_twin ∈ [1.9, 2.8]
        ratio = y[:, 0] / diag[:, 3]
        assert ((ratio >= corpus.BRANCH_RATIO_LO) & (ratio <= corpus.BRANCH_RATIO_HI)).all()

    meta = json.loads((tmp_path / "corpus.meta.json").read_text())
    assert meta["seed"] == TEST_SEED
    assert meta["n_requested"] == 12
    assert meta["n_attempted"] == 12
    assert meta["n_produced"] == m
    assert meta["n_dropped"] == 12 - m
    assert sum(meta["drop_reasons"].values()) == meta["n_dropped"]
    assert set(meta["drop_reasons"]) == {
        "no_resonance",
        "te2_out_of_window",
        "label_nan",
        "branch_ambiguous",
        "solver_error",
    }
    # 材料冻结溯源
    assert meta["materials"]["piezo_bot"]["name"] == "AlN"
    assert "literature" in meta["materials"]["piezo_bot"]["source"]
    assert meta["materials"]["piezo_top"]["name"] == "AlScN_cal"
    assert meta["materials"]["piezo_top"]["source_sha256"]
    assert meta["alpha"] == pytest.approx(0.022, abs=1e-9)
    assert meta["branch_guard"]["ratio_lo"] == corpus.BRANCH_RATIO_LO
    assert meta["branch_guard"]["ratio_hi"] == corpus.BRANCH_RATIO_HI
    assert meta["te2_window_ghz"] == [10.0, 20.0]
    assert meta["geom_cols"] == [
        "t_aln_nm",
        "t_alscn_nm",
        "t_top_nm",
        "t_bot_nm",
        "t_pf_nm",
        "area_um2",
    ]
    assert meta["param_cols"] == list(PARAM_KEYS)
    assert meta["diag_cols"] == ["fs1_ghz", "fp1_ghz", "k2eff_te1_pct", "f1_twin_ghz"]
    sha_base = meta["mason_sim_commit_sha"].removesuffix("-dirty")
    assert len(sha_base) == 40
    assert meta["wall_time_sec"] > 0.0


def test_resume_skips_existing_shards(tmp_path: Path) -> None:
    """断点续跑：已存在 shard 不重算（mtime 不被触动），产物一致。"""
    out, shards = _run_12(tmp_path)
    shard_files = sorted(shards.glob("shard_*.npz"))
    assert len(shard_files) == 3

    first = np.load(out)
    x1, y1 = first["x_geom"].copy(), first["y_params"].copy()
    for p in shard_files:
        os.utime(p, (0, 0))

    rc = corpus.main(
        [
            "--n",
            "12",
            "--out",
            str(out),
            "--shard-dir",
            str(shards),
            "--shard-size",
            "5",
            "--nprocs",
            "2",
            "--seed",
            str(TEST_SEED),
        ]
    )
    assert rc == 0
    for p in shard_files:
        assert p.stat().st_mtime == 0, f"shard 被重算：{p}"
    second = np.load(out)
    np.testing.assert_array_equal(second["x_geom"], x1)
    np.testing.assert_array_equal(second["y_params"], y1)


def test_resume_with_mismatched_config_errors(tmp_path: Path) -> None:
    """shard 配置指纹不匹配（不同 seed）续跑必须报错退出，不得静默混料。"""
    out, shards = _run_12(tmp_path)

    def _args(seed: str) -> list[str]:
        return [
            "--n",
            "12",
            "--out",
            str(out),
            "--shard-dir",
            str(shards),
            "--shard-size",
            "5",
            "--nprocs",
            "2",
            "--seed",
            seed,
        ]

    with pytest.raises(SystemExit, match="配置指纹不匹配"):
        corpus.main(_args("999"))
    assert corpus.main(_args(str(TEST_SEED))) == 0


def test_cli_rejects_non_positive_numbers() -> None:
    """--n/--nprocs/--shard-size/--smoke 非正数 → 人类可读报错（exit code 2）。"""
    for flag in ("--n", "--nprocs", "--shard-size", "--smoke"):
        with pytest.raises(SystemExit) as exc_info:
            corpus.main([flag, "0"])
        assert exc_info.value.code == 2, flag
