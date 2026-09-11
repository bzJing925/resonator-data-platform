"""mason_surrogate.common 的单元测试（TDD 先于实现）。

覆盖：归一化（结构标志列跳过）、UnifiedMLP 形状、确定性稀疏位置、
masked MSE、7 参数误差口径（fs/fp MHz、k2eff pct-pt、Q 相对%）、
Stage2 对齐 RMSE 平移方向、Zp 峰高 MAE、cond 列序、稀疏样本与样条基线。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import torch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
for p in (ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mason_surrogate import build_unified_dataset as bud  # noqa: E402
from mason_surrogate import common as mc  # noqa: E402

# ---------------------------------------------------------------- 归一化


def test_fit_norm_skips_flag_column():
    x = np.array([[0.0, 10.0, 100.0], [1.0, 20.0, 200.0], [0.0, 30.0, 300.0]])
    stats = mc.fit_norm(x, skip_cols=(0,))
    np.testing.assert_allclose(stats.mean[0], 0.0)
    np.testing.assert_allclose(stats.std[0], 1.0)
    np.testing.assert_allclose(stats.mean[1:], [20.0, 200.0])
    # np.std 默认 ddof=0：sqrt(200/3)
    np.testing.assert_allclose(stats.std[1:], [np.sqrt(200 / 3), np.sqrt(20000 / 3)])
    xn = mc.apply_norm(x, stats)
    np.testing.assert_allclose(xn[:, 0], [0.0, 1.0, 0.0])  # 标志列原样
    np.testing.assert_allclose(xn[:, 1], [-10 / np.sqrt(200 / 3), 0.0, 10 / np.sqrt(200 / 3)])


def test_norm_roundtrip_and_zero_std_guard():
    x = np.array([[5.0, 1.0], [5.0, 2.0], [5.0, 3.0]])
    stats = mc.fit_norm(x)
    assert stats.std[0] == 1.0  # 零方差列守卫
    xn = mc.apply_norm(x, stats)
    assert (xn[:, 0] == 0).all()
    np.testing.assert_allclose(mc.invert_norm(xn, stats), x, rtol=1e-6)


# ---------------------------------------------------------------- UnifiedMLP


def test_unified_mlp_forward_shapes():
    torch.manual_seed(0)
    model = mc.UnifiedMLP(in_dim=7, hidden=32, n_res_blocks=2, n_params=7, k=256)
    out = model(torch.zeros(4, 7))
    assert out["params"].shape == (4, 7)
    assert out["sparse"].shape == (4, 256)


# ---------------------------------------------------------------- 稀疏位置


def test_sparse_positions_deterministic_dense_near_anchor():
    grid = bud.w_grid()
    p1 = mc.sparse_positions(grid, 1.05, k=256)
    p2 = mc.sparse_positions(grid, 1.05, k=256)
    np.testing.assert_array_equal(p1, p2)
    assert len(p1) == 256
    assert p1.min() >= 0 and p1.max() <= 1000
    assert (np.diff(p1) > 0).all()  # 严格升序
    # 密集区 [1-0.5Δ, 1.05+0.5Δ]，Δ=0.05 → [0.975, 1.075]，配额 60%
    in_dense = ((grid[p1] >= 0.975 - 1e-9) & (grid[p1] <= 1.075 + 1e-9)).mean()
    assert in_dense >= 0.55


def test_sparse_positions_invalid_ratio_uniform_fallback():
    grid = bud.w_grid()
    pos = mc.sparse_positions(grid, 0.9, k=256)  # fp<=fs → 全轴均匀
    assert pos[0] <= 2
    assert pos[-1] >= 998
    assert len(pos) == 256


def test_batch_sparse_positions_shape():
    grid = bud.w_grid()
    pos = mc.batch_sparse_positions(grid, np.array([1.05, 1.02, 1.10]), k=256)
    assert pos.shape == (3, 256)
    np.testing.assert_array_equal(pos[0], mc.sparse_positions(grid, 1.05, k=256))


# ---------------------------------------------------------------- masked MSE


def test_masked_mse_excludes_masked_entries():
    pred = torch.tensor([[1.0, 2.0, 999.0]])
    target = torch.tensor([[1.0, 2.0, -999.0]])
    mask = torch.tensor([[True, True, False]])
    assert mc.masked_mse(pred, target, mask).item() == pytest.approx(0.0)
    full = ((pred - target) ** 2).mean()
    assert mc.masked_mse(pred, target, mask) < full


def test_masked_mse_all_masked_returns_zero():
    pred = torch.ones(2, 3)
    mask = torch.zeros(2, 3, dtype=torch.bool)
    assert mc.masked_mse(pred, torch.zeros(2, 3), mask).item() == 0.0


# ---------------------------------------------------------------- 参数误差口径


def test_param_metrics_units():
    y_true = np.array([[5.0, 5.2, 12.0, 500.0, 600.0, 550.0, 620.0]])
    y_pred = np.array([[5.001, 5.198, 12.3, 550.0, 540.0, 550.0, 682.0]])
    m = mc.param_metrics(y_pred, y_true)
    assert m["fs_mae_mhz"] == pytest.approx(1.0)
    assert m["fp_mae_mhz"] == pytest.approx(2.0)
    assert m["k2eff_mae_pp"] == pytest.approx(0.3)
    assert m["qs_mae_rel_pct"] == pytest.approx(10.0)
    assert m["qp_mae_rel_pct"] == pytest.approx(10.0)
    assert m["qs_bodeq_mae_rel_pct"] == pytest.approx(0.0)
    assert m["qp_bodeq_mae_rel_pct"] == pytest.approx(10.0)


# ---------------------------------------------------------------- cond 列序


def test_build_cond_selects_production_order():
    params = np.array([[5.0, 5.2, 12.0, 500.0, 600.0, 550.0, 620.0]])
    cond = mc.build_cond(params)
    # 生产口径 [fs, fp, Qs, Qp, kt2]，kt2 直接取 k2eff_pct 列（百分数）
    np.testing.assert_allclose(cond[0], [5.0, 5.2, 500.0, 600.0, 12.0])


# ---------------------------------------------------------------- 锚点敏感度（乘性）


def test_anchor_sensitivity_zero_perturb_identity():
    grid = bud.w_grid()
    rng = np.random.default_rng(3)
    gt = np.cumsum(rng.normal(0, 1, 1001)) + 30.0
    mask = np.ones_like(grid, dtype=bool)
    plus, minus = mc.anchor_sensitivity_rmse_db(
        gt[None], gt[None], mask[None], grid, np.array([0.0])
    )
    assert plus == pytest.approx(0.0)
    assert minus == pytest.approx(0.0)


def test_anchor_sensitivity_multiplicative_linear_analytic():
    """线性谱 gt=aw+b：乘性查询 q=w(1+s) 的解析误差 = |a·w·s|（落网外点不计）。"""
    grid = bud.w_grid()
    a_, b_ = 30.0, 5.0
    gt = a_ * grid + b_
    s = np.array([0.005])
    mask = np.ones((1, 1001), dtype=bool)
    plus, minus = mc.anchor_sensitivity_rmse_db(gt[None], gt[None], mask, grid, s)
    q = grid * (1 + s[0])
    valid = q <= grid[-1]
    expect_plus = float(np.sqrt(np.mean((a_ * q[valid] - a_ * grid[valid]) ** 2)))
    assert plus == pytest.approx(expect_plus, rel=1e-9)
    q2 = grid * (1 - s[0])
    valid2 = q2 >= grid[0]
    expect_minus = float(np.sqrt(np.mean((a_ * q2[valid2] - a_ * grid[valid2]) ** 2)))
    assert minus == pytest.approx(expect_minus, rel=1e-9)
    assert plus > 0.05 and minus > 0.05  # 确实注入了误差


def test_anchor_sensitivity_respects_mask():
    grid = bud.w_grid()
    gt = np.zeros_like(grid)
    pred = np.zeros_like(grid)
    pred[grid > 1.1] = 100.0  # 只在被掩码区有差异
    mask = (grid <= 1.1)[None, :]
    plus, minus = mc.anchor_sensitivity_rmse_db(
        pred[None], gt[None], mask, grid, np.array([0.005])
    )
    # 掩码区不计；乘性查询 q=w·(1+s) 会把 w>1.1 处的大值带入 w≤1.1 的查询点
    # → 手工重算对照（np.interp 语义）
    q = grid * 1.005
    valid = mask[0] & (q <= grid[-1])
    manual = np.sqrt(np.mean((np.interp(q[valid], grid, pred) - gt[valid]) ** 2))
    assert plus == pytest.approx(manual, rel=1e-9)


# ---------------------------------------------------------------- Zp 峰高


def test_zp_peak_mae_db():
    grid = bud.w_grid()
    fp_ratio = 1.05
    gt = np.full_like(grid, 10.0)
    gt[(grid >= 1.04) & (grid <= 1.06)] = 60.0  # GT 峰 60 dB
    pred = np.full_like(grid, 10.0)
    pred[(grid >= 1.04) & (grid <= 1.06)] = 63.0  # 预测峰 63 dB
    mask = np.ones_like(grid, dtype=bool)
    mae, n_used = mc.zp_peak_mae_db(pred[None], gt[None], mask[None], grid, np.array([fp_ratio]))
    assert n_used == 1
    assert mae == pytest.approx(3.0)


def test_zp_peak_mae_skips_fully_masked_window():
    grid = bud.w_grid()
    mask = np.ones_like(grid, dtype=bool)
    mask[(grid >= 1.04) & (grid <= 1.06)] = False  # fp 窗全掩码
    mae, n_used = mc.zp_peak_mae_db(
        np.zeros((1, 1001)), np.zeros((1, 1001)), mask[None], grid, np.array([1.05])
    )
    assert n_used == 0
    assert np.isnan(mae)


# ---------------------------------------------------------------- 稀疏样本与基线


def test_build_sparse_samples_values_and_validity():
    grid = bud.w_grid()
    z_w_db = np.arange(1001, dtype=np.float32)[None, :]  # 值=索引，便于对照
    w_mask = np.ones((1, 1001), dtype=bool)
    w_mask[0, 900:] = False
    pos = np.array([[0, 500, 950]])
    samples, valid = mc.build_sparse_samples(z_w_db, w_mask, pos, grid)
    assert samples.shape == (1, 3, 2)
    np.testing.assert_allclose(samples[0, :, 0], grid[pos[0]])
    np.testing.assert_allclose(samples[0, :, 1], [0.0, 500.0, 950.0])
    assert valid.tolist() == [[True, True, False]]


def test_precompute_baselines_linear_exact_and_mask():
    grid = bud.w_grid()
    pos = np.array([100, 300, 500, 700, 900])
    vals = 3.0 * grid[pos] + 1.0  # 线性谱 → 三次样条精确还原
    valid = np.array([True, True, False, True, True])  # 剔除 500 处点
    base = mc.precompute_baselines(pos[None], vals[None], valid[None], grid, fill_value=0.0)
    assert base.shape == (1, 1001)
    expect = 3.0 * grid + 1.0
    interior = (grid > grid[pos][0]) & (grid < grid[pos][-1])
    np.testing.assert_allclose(base[0][interior], expect[interior], atol=1e-6)


def test_precompute_baselines_degenerate_fill():
    grid = bud.w_grid()
    pos = np.array([100, 300, 500])
    vals = np.array([1.0, 2.0, 3.0])
    valid = np.array([False, False, False])  # 无有效点
    base = mc.precompute_baselines(pos[None], vals[None], valid[None], grid, fill_value=-7.0)
    assert (base[0] == -7.0).all()
