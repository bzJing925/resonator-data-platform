"""统一数据集构建（build_unified_dataset）的单元测试。

TDD：先于实现编写。覆盖：
- w 网格端点精确
- x7 映射（EPE/EPPE 两种结构）
- 单行手工重算对照（插值 + 掩码）
- 零覆盖样本丢弃
- 分层划分比例与种子确定性
- 真实语料全量构建的形状/dtype/掩码率/已知行重算
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]  # backend/
SCRIPTS_DIR = ROOT / "scripts"
for p in (ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mason_surrogate import build_unified_dataset as bud  # noqa: E402

DATA_DIR = ROOT / "surrogate_data_mason"


# ---------------------------------------------------------------- w 网格


def test_w_grid_exact_endpoints():
    g = bud.w_grid()
    assert g.dtype == np.float64
    assert len(g) == 1001
    assert g[0] == 0.80
    assert g[-1] == 1.20
    assert np.allclose(np.diff(g), 4e-4)


# ---------------------------------------------------------------- x7 映射


def test_build_x7_epe_pads_t2_with_zero():
    x_geom = np.array([[308.0, 150.0, 120.0, 30.0, 2000.0]])
    x7 = bud.build_x7(0, x_geom)
    assert x7.shape == (1, 7)
    np.testing.assert_allclose(x7[0], [0.0, 308.0, 0.0, 150.0, 120.0, 30.0, 2000.0])


def test_build_x7_eppe_two_piezo_layers():
    x_geom = np.array([[100.0, 250.0, 150.0, 120.0, 20.0, 3500.0]])
    x7 = bud.build_x7(1, x_geom)
    assert x7.shape == (1, 7)
    np.testing.assert_allclose(x7[0], [1.0, 100.0, 250.0, 150.0, 120.0, 20.0, 3500.0])


def test_build_x7_rejects_wrong_width():
    with pytest.raises(ValueError):
        bud.build_x7(0, np.zeros((3, 6)))  # EPE 期望 5 列
    with pytest.raises(ValueError):
        bud.build_x7(1, np.zeros((3, 5)))  # EPPE 期望 6 列


# ---------------------------------------------------------------- 插值 + 掩码


def _grid():
    return bud.w_grid()


def test_interp_to_w_manual_row():
    """手工重算：|Z| 分段线性，f_a=5 GHz，窗口 [4,7] 全覆盖。"""
    freq = np.array([4.0, 5.0, 6.0, 7.0])
    z = np.array([1.0, 100.0, 100.0, 1.0], dtype=np.complex128)
    z_db, mask = bud.interp_to_w(freq, z, f_a=5.0, grid=_grid())
    # dB: 0, 40, 40, 0；w=0.8→f=4.0；w=1.0→f=5.0；w=1.2→f=6.0
    assert mask.all()
    assert z_db[0] == pytest.approx(0.0, abs=1e-9)
    assert z_db[500] == pytest.approx(40.0, abs=1e-9)  # w=1.0
    assert z_db[-1] == pytest.approx(40.0, abs=1e-9)  # w=1.2 → f=6.0
    assert z_db[250] == pytest.approx(20.0, abs=1e-9)  # w=0.9 → f=4.5 线性中点


def test_interp_to_w_masks_outside_window():
    """f_a=6 GHz → 查询窗 [4.8, 7.2]；f>7.0 的网格点必须 mask=0 且值填 0。"""
    freq = np.linspace(4.0, 7.0, 301)
    z = np.full(301, 10.0, dtype=np.complex128)  # 20 dB 常数谱
    z_db, mask = bud.interp_to_w(freq, z, f_a=6.0, grid=_grid())
    w_hi = 7.0 / 6.0  # ≈1.16667
    grid = _grid()
    expect_valid = grid * 6.0 <= 7.0 + 1e-12
    np.testing.assert_array_equal(mask, expect_valid)
    assert z_db[expect_valid] == pytest.approx(20.0, abs=1e-6)
    assert (z_db[~expect_valid] == 0.0).all()
    n_masked = int((grid > w_hi).sum())
    assert (~mask).sum() == n_masked


# ---------------------------------------------------------------- 分层划分


def test_stratified_split_ratios_and_determinism():
    rng = np.random.default_rng(0)
    flags = np.concatenate([np.zeros(1000, dtype=np.int64), np.ones(500, dtype=np.int64)])
    flags = flags[rng.permutation(len(flags))]  # 打乱，检验分层与顺序无关
    s1 = bud.stratified_split(flags, seed=20260911)
    s2 = bud.stratified_split(flags, seed=20260911)
    np.testing.assert_array_equal(s1, s2)  # 种子确定性
    assert s1.dtype == np.int8
    assert set(np.unique(s1)) == {0, 1, 2}
    for flag, _n in [(0, 1000), (1, 500)]:
        sub = s1[flags == flag]
        assert abs((sub == 0).mean() - 0.85) < 0.01
        assert abs((sub == 1).mean() - 0.075) <= 0.011
        assert abs((sub == 2).mean() - 0.075) <= 0.011


def test_stratified_split_seed_changes_assignment():
    flags = np.zeros(200, dtype=np.int64)
    a = bud.stratified_split(flags, seed=1)
    b = bud.stratified_split(flags, seed=2)
    assert not np.array_equal(a, b)


# ---------------------------------------------------------------- 全量构建（合成语料）


def _write_synthetic_corpora(tmp: Path) -> tuple[Path, Path]:
    """EPE 2 样本（一个 fs=2 GHz 零覆盖应被丢弃），EPPE 1 样本。"""
    freq_e = np.linspace(4.0, 7.0, 1001)
    x_e = np.array([[308.0, 150.0, 150.0, 0.0, 2000.0], [500.0, 50.0, 50.0, 10.0, 700.0]])
    y_e = np.array(
        [
            [5.0, 5.25, 12.0, 500.0, 600.0, 550.0, 620.0],
            [2.0, 2.1, 10.0, 400.0, 500.0, 450.0, 520.0],  # 窗 [1.6,2.4] ⊄ [4,7]
        ]
    )
    z_e = (10 + 1j * np.linspace(-5, 5, 1001))[None, :].repeat(2, axis=0).astype(np.complex64)
    epe = tmp / "epe.npz"
    np.savez(epe, x_geom=x_e, y_params=y_e, z_dense=z_e, freq_ghz=freq_e)

    freq_p = np.linspace(8.0, 22.0, 2801)
    x_p = np.array([[100.0, 250.0, 150.0, 150.0, 20.0, 3500.0]])
    y_p = np.array([[14.0, 14.2, 2.5, 300.0, 350.0, 320.0, 360.0]])
    z_p = (20 + 0j * np.linspace(8, 22, 2801))[None, :].astype(np.complex64)
    eppe = tmp / "eppe.npz"
    np.savez(eppe, x_geom=x_p, y_params=y_p, z_dense=z_p, freq_ghz=freq_p, diag=np.zeros((1, 4)))
    return epe, eppe


def test_build_unified_drops_zero_coverage(tmp_path):
    epe, eppe = _write_synthetic_corpora(tmp_path)
    out = bud.build_unified(epe, eppe)
    # 3 输入样本，零覆盖的 fs=2 GHz 样本被丢弃 → 2 保留
    assert out["x7"].shape == (2, 7)
    assert out["src_structure"].tolist() == [0, 1]
    assert out["src_index"].tolist() == [0, 0]  # EPE 仅保留第 0 行
    assert out["n_dropped"] == {0: 1, 1: 0}
    # 保留的 EPE 行：f_a=5 → 查询窗 [4,6] ⊂ [4,7]，全掩码有效
    assert out["w_mask"][0].all()
    np.testing.assert_allclose(out["f_a"], [5.0, 14.0])
    np.testing.assert_allclose(out["fp_ratio"], [5.25 / 5.0, 14.2 / 14.0])


def test_build_and_save_persists_src_keys(tmp_path):
    """src_structure/src_index 持久化进 npz（按行回溯语料），spec 键不受影响。"""
    epe, eppe = _write_synthetic_corpora(tmp_path)
    out_npz, out_meta = tmp_path / "u.npz", tmp_path / "u.meta.json"
    bud.build_and_save(epe, eppe, out_npz, out_meta)
    with np.load(out_npz) as d:
        for key in ["x7", "y7", "z_w_db", "w_mask", "w_grid", "f_a", "fp_ratio", "split"]:
            assert key in d.files
        np.testing.assert_array_equal(d["src_structure"], [0, 1])
        np.testing.assert_array_equal(d["src_index"], [0, 0])


# ---------------------------------------------------------------- 全量构建（真实语料）


@pytest.mark.skipif(
    not (DATA_DIR / "epe_corpus.npz").exists() or not (DATA_DIR / "eppe_corpus.npz").exists(),
    reason="Mason 语料不在本地",
)
def test_build_unified_real_corpora():
    out = bud.build_unified(DATA_DIR / "epe_corpus.npz", DATA_DIR / "eppe_corpus.npz")
    epe = np.load(DATA_DIR / "epe_corpus.npz")
    eppe = np.load(DATA_DIR / "eppe_corpus.npz")
    n_epe_in, n_eppe_in = len(epe["y_params"]), len(eppe["y_params"])

    # 独立重算 EPE 覆盖率，验证丢弃数
    fs = epe["y_params"][:, 0]
    f0, f1 = epe["freq_ghz"][0], epe["freq_ghz"][-1]
    cov = (np.minimum(1.2 * fs, f1) - np.maximum(0.8 * fs, f0)) / (0.4 * fs)
    n_epe_kept = int((cov > 0).sum())

    m = n_epe_kept + n_eppe_in
    assert out["x7"].shape == (m, 7) and out["x7"].dtype == np.float32
    assert out["y7"].shape == (m, 7) and out["y7"].dtype == np.float32
    assert out["z_w_db"].shape == (m, 1001) and out["z_w_db"].dtype == np.float32
    assert out["w_mask"].shape == (m, 1001) and out["w_mask"].dtype == bool
    assert out["w_grid"].dtype == np.float64
    assert out["f_a"].dtype == np.float64 and out["fp_ratio"].dtype == np.float64
    assert out["split"].dtype == np.int8
    assert out["n_dropped"] == {0: n_epe_in - n_epe_kept, 1: 0}

    # 结构顺序：先 EPE 后 EPPE；flag 列正确
    assert (out["x7"][:n_epe_kept, 0] == 0).all()
    assert (out["x7"][n_epe_kept:, 0] == 1).all()
    assert (out["x7"][:n_epe_kept, 2] == 0).all()  # EPE t2=0
    assert (out["x7"][n_epe_kept:, 2] > 0).all()  # EPPE t2=t_alscn>0

    # f_a / fp_ratio 与 y7 一致
    np.testing.assert_allclose(out["f_a"], out["y7"][:, 0], rtol=1e-6)
    np.testing.assert_allclose(out["fp_ratio"], out["y7"][:, 1] / out["y7"][:, 0], rtol=1e-6)

    # 掩码率：EPE 中段样本（fs∈[5,5.8]，窗 ⊂ [4,7]）全 1；EPPE 掩码率低
    fs_kept = out["y7"][:n_epe_kept, 0]
    mid = np.where((fs_kept >= 5.0) & (fs_kept <= 5.8))[0]
    assert len(mid) > 100
    assert out["w_mask"][mid].all()
    epe_mask_rate = 1.0 - out["w_mask"][:n_epe_kept].mean()
    eppe_mask_rate = 1.0 - out["w_mask"][n_epe_kept:].mean()
    assert 0.2 < epe_mask_rate < 0.6  # 边缘 fs 样本大量掩码
    assert eppe_mask_rate < 0.10

    # 已知行手工重算对照（取 EPE 第 0 保留行与某 EPPE 行）
    grid = out["w_grid"]
    for row, src_struct, src_i in [(0, 0, int(out["src_index"][0])), (m - 1, 1, n_eppe_in - 1)]:
        corpus = epe if src_struct == 0 else eppe
        freq = corpus["freq_ghz"]
        z = corpus["z_dense"][src_i]
        f_a = float(corpus["y_params"][src_i, 0])
        ref_db, ref_mask = bud.interp_to_w(freq, z, f_a, grid)
        np.testing.assert_array_equal(out["w_mask"][row], ref_mask)
        np.testing.assert_allclose(
            out["z_w_db"][row][ref_mask], ref_db[ref_mask].astype(np.float32), rtol=1e-5
        )

    # 分层比例
    for flag, n_struct in [(0, n_epe_kept), (1, n_eppe_in)]:
        sub = out["split"][out["x7"][:, 0] == flag]
        assert abs((sub == 0).mean() - 0.85) < 0.02
        assert len(sub) == n_struct
