"""EPE Mason 语料生成脚本（scripts/build_mason_epe_corpus.py）测试。

- 12 样本端到端：npz 形状/dtype/有限性 + meta.json 字段
- 断点续跑：已存在 shard 不重算
- PF 单调性：同几何 t_pf=0 vs 80nm，fs 下降（走完整求解+标签管线）
- 产量率：12 样本有效 ≥ 11
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_mason_epe_corpus as corpus  # noqa: E402

# 端到端测试用脚本默认种子（= 生产配置，先于测试存在，非挑种子）：2026-09-10 实测
# 该种子 12 样本全有效（ALN1 与 ALSCN 口径均 12/12）。注：靠窗沿（fs<3.1 或 fp>7.9 GHz，
# 在 find_te1_resonances 的 edge_skip=100 点之外）或越窗角点样本被丢弃是【正确行为】
# （ALN1 口径宽网格真值验证：seed=12345 的 2 个丢弃样本真 TE1 为 (7.814,7.992) 与
# (3.042,3.118) GHz，峰贴窗沿，强行保留会得到有偏标签）；语料级真实产量率以
# --smoke 200 实测为准（ALSCN 口径约 91.5%）。
TEST_SEED = corpus.SEED_DEFAULT
EXPECTED_PARAM_COLS = ["fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "qs_bodeq", "qp_bodeq"]


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


def test_lhc_sampling_deterministic_and_in_box() -> None:
    """同种子 LHS 逐位可复现；4 维几何落在采样盒内；面积 5 档轮转。"""
    g1 = corpus.sample_geometry(16, seed=123)
    g2 = corpus.sample_geometry(16, seed=123)
    np.testing.assert_array_equal(g1, g2)
    assert g1.shape == (16, 5)
    assert g1.dtype == np.float64

    lo, hi = corpus.T_PIEZO_RANGE_NM
    assert float(g1[:, 0].min()) >= lo
    assert float(g1[:, 0].max()) <= hi
    for col, (c_lo, c_hi) in ((1, (50.0, 300.0)), (2, (50.0, 300.0)), (3, (0.0, 100.0))):
        assert float(g1[:, col].min()) >= c_lo
        assert float(g1[:, col].max()) <= c_hi
    assert set(np.unique(g1[:, 4]).tolist()) <= set(corpus.AREA_LEVELS_UM2)
    # 轮转分配：i 与 i+5 面积相同
    assert g1[0, 4] == g1[5, 4] == corpus.AREA_LEVELS_UM2[0]


def test_z_dense_grid_alignment() -> None:
    """z_dense 索引精确对应 4.000–7.000 GHz @ 3 MHz（1 MHz 网格整点子集）。"""
    idx = corpus.Z_DENSE_IDX
    assert len(idx) == 1001
    freq_ghz = corpus.SOLVE_FREQ_HZ[idx] / 1e9
    np.testing.assert_allclose(freq_ghz, 4.0 + 0.003 * np.arange(1001), rtol=0.0, atol=1e-12)


def test_end_to_end_12_samples(tmp_path: Path) -> None:
    """12 样本端到端：形状/dtype/有限性/物理范围/meta 字段；产量率 ≥ 11/12。"""
    out, _shards = _run_12(tmp_path)

    data = np.load(out)
    x = data["x_geom"]
    y = data["y_params"]
    z = data["z_dense"]
    f = data["freq_ghz"]

    m = x.shape[0]
    assert 11 <= m <= 12, f"产量率不足：{m}/12"
    assert x.shape == (m, 5) and x.dtype == np.float64
    assert y.shape == (m, 7) and y.dtype == np.float64
    assert z.shape == (m, 1001) and z.dtype == np.complex64
    assert f.shape == (1001,)

    assert np.isfinite(x).all()
    assert np.isfinite(y).all()
    assert np.isfinite(z.real).all() and np.isfinite(z.imag).all()
    np.testing.assert_allclose(f, 4.0 + 0.003 * np.arange(1001), rtol=0.0, atol=1e-12)

    # 物理范围：fs/fp 在求解窗内（含边距），fp>fs，k2eff 合理
    assert float(y[:, 0].min()) > 3.0
    assert float(y[:, 1].max()) < 8.0
    assert (y[:, 1] > y[:, 0]).all()
    # k²eff 上限 sanity（ALSCN 口径）：材料 kt2=19.44% 是物理天花板，电极质量
    # 加载把有效 k²eff 压到 ~15-16% 量级（2026-09-10 ALSCN 冒烟 200 实测 max=15.69%）；
    # 上限取 20%——超过材料 kt2 必为标签管线 bug
    assert ((y[:, 2] > 0.0) & (y[:, 2] < 20.0)).all()

    meta = json.loads((tmp_path / "corpus.meta.json").read_text())
    assert meta["seed"] == TEST_SEED
    assert meta["material"] == "ALSCN"  # 默认材料口径（生产 AlScN 晶圆）
    assert meta["materials"]["piezo"] == "AlScN"
    assert meta["n_requested"] == 12
    assert meta["n_produced"] == m
    assert meta["n_dropped"] == 12 - m
    assert sum(meta["drop_reasons"].values()) == meta["n_dropped"]
    assert set(meta["drop_reasons"]) == {"no_resonance", "label_nan", "edge_margin"}
    assert meta["label_provenance"] == "production extract.py k2eff512+q_phase+calc_bodeq"
    assert meta["param_cols"] == EXPECTED_PARAM_COLS
    assert meta["geom_cols"] == ["t_piezo_nm", "t_top_nm", "t_bot_nm", "t_pf_nm", "area_um2"]
    assert isinstance(meta["mason_sim_commit_sha"], str) and len(meta["mason_sim_commit_sha"]) == 40
    assert meta["wall_time_sec"] > 0.0
    assert meta["ranges_nm"]["t_piezo"] == list(corpus.T_PIEZO_RANGE_NM)
    assert meta["area_levels_um2"] == list(corpus.AREA_LEVELS_UM2)
    assert meta["solve_grid"] == {"f_start_ghz": 3.0, "f_stop_ghz": 8.0, "f_step_mhz": 1.0}
    assert meta["z_dense_grid"] == {"f_start_ghz": 4.0, "f_stop_ghz": 7.0, "f_step_mhz": 3.0}


def test_resume_skips_existing_shards(tmp_path: Path) -> None:
    """断点续跑：已存在 shard 不重算（mtime 不被触动），产物一致。"""
    out, shards = _run_12(tmp_path)
    shard_files = sorted(shards.glob("shard_*.npz"))
    assert len(shard_files) == 3  # 12 样本 / shard_size 5 → 3 shards

    first = np.load(out)
    x1, y1 = first["x_geom"].copy(), first["y_params"].copy()

    # 把 shard mtime 回拨到 epoch；若续跑重写 shard，mtime 会变成当前时间
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


def test_material_selection_aln1_control() -> None:
    """--material ALN1 对照：t_piezo 用 ALN1 标定范围；同几何两材料 fs 不同。"""
    from app.mason_sim import ALN1, ALSCN

    g_aln = corpus.sample_geometry(16, seed=123, material="ALN1")
    lo_aln, hi_aln = corpus.T_PIEZO_RANGE_NM_BY_MATERIAL["ALN1"]
    assert float(g_aln[:, 0].min()) >= lo_aln
    assert float(g_aln[:, 0].max()) <= hi_aln
    # 与默认 ALSCN 同一 u 序列 → t_top/t_bot/t_pf 相同，t_piezo 范围不同
    g_scn = corpus.sample_geometry(16, seed=123, material="ALSCN")
    np.testing.assert_array_equal(g_aln[:, 1:], g_scn[:, 1:])
    assert float(g_aln[:, 0].min()) > float(g_scn[:, 0].min())  # ALN1 v 更高 → 更厚

    row = (450.0, 150.0, 150.0, 0.0, 2000.0)
    r_scn = corpus.solve_extract_one(row, piezo=ALSCN)
    r_aln = corpus.solve_extract_one(row, piezo=ALN1)
    assert r_scn["valid"] and r_aln["valid"]
    assert r_scn["y"][0] != r_aln["y"][0]  # 材料影响 fs
    # AlScN 的 k²eff 显著高于 AlN（材料 kt2 19.4% vs 7.2%）
    assert r_scn["y"][2] > r_aln["y"][2]


def test_pf_monotonicity_through_pipeline() -> None:
    """同几何 t_pf=0 vs 80nm：fs 下降（走 solve_extract_one 完整管线）。"""
    row0 = (450.0, 150.0, 150.0, 0.0, 2000.0)
    row80 = (450.0, 150.0, 150.0, 80.0, 2000.0)
    r0 = corpus.solve_extract_one(row0)
    r80 = corpus.solve_extract_one(row80)

    assert r0["valid"], r0["reason"]
    assert r80["valid"], r80["reason"]
    fs0 = r0["y"][0]
    fs80 = r80["y"][0]
    assert fs80 < fs0
