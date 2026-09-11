"""train_stage1_mason / train_stage2_mason 的冒烟与逻辑测试（TDD 先于实现）。

用合成统一数据集（M=96）跑 2 epoch 验证：
- 训练循环可运行、loss 有限、早停/历史记录结构正确
- warmup→自预测位置切换路径被覆盖（warmup_epochs=1, epochs=2）
- checkpoint + config json 落盘且含归一化统计
- test 集评估按结构拆分、指标键齐全
- Stage2 max_freq 对比函数返回各候选 val loss
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
for p in (ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mason_surrogate import build_unified_dataset as bud  # noqa: E402
from mason_surrogate import train_stage1_mason as ts1  # noqa: E402
from mason_surrogate import train_stage2_mason as ts2  # noqa: E402


def _make_tiny_unified(path: Path, m_epe: int = 64, m_eppe: int = 32) -> Path:
    """合成统一数据集：带共振形状的平滑谱，参数与几何有单调关系（可学）。"""
    rng = np.random.default_rng(7)
    grid = bud.w_grid()
    m = m_epe + m_eppe
    x7 = np.zeros((m, 7), dtype=np.float32)
    x7[:m_epe, 0] = 0.0
    x7[m_epe:, 0] = 1.0
    x7[:, 1] = rng.uniform(150, 500, m)  # t1
    x7[m_epe:, 2] = rng.uniform(100, 380, m - m_epe)  # t2 (EPPE)
    x7[:, 3] = rng.uniform(50, 300, m)
    x7[:, 4] = rng.uniform(50, 300, m)
    x7[:, 5] = rng.uniform(0, 100, m)
    x7[:, 6] = rng.choice([700.0, 1200.0, 2000.0, 3500.0, 5500.0], m)
    y7 = np.zeros((m, 7), dtype=np.float32)
    fs = np.where(x7[:, 0] == 0, 6.5 - x7[:, 1] / 200.0, 18.0 - x7[:, 1] / 50.0)
    fp = fs * rng.uniform(1.01, 1.05, m)
    y7[:, 0] = fs
    y7[:, 1] = fp
    y7[:, 2] = np.where(x7[:, 0] == 0, 12.0, 2.5) + rng.normal(0, 0.2, m)
    y7[:, 3:7] = rng.uniform(200, 900, (m, 4))
    # 谱：log 域洛伦兹形 fs 谷 + fp 峰
    z_w_db = np.zeros((m, 1001), dtype=np.float32)
    for i in range(m):
        depth = 20 + 10 * rng.random()
        peak = 50 + 10 * rng.random()
        z_w_db[i] = (
            30
            - depth / (1 + ((grid - 1.0) / 0.005) ** 2)
            + peak / (1 + ((grid - fp[i] / fs[i]) / 0.004) ** 2)
        )
    w_mask = np.ones((m, 1001), dtype=bool)
    # 制造部分掩码：前 10 个 EPE 样本高 w 端掩码
    w_mask[:10, grid > 1.1] = False
    z_w_db[~w_mask] = 0.0
    split = np.zeros(m, dtype=np.int8)
    for rows in (np.arange(0, m_epe), np.arange(m_epe, m)):  # 每结构各自 85/7.5/7.5
        n = len(rows)
        split[rows[int(0.85 * n) : int(0.925 * n)]] = 1
        split[rows[int(0.925 * n) :]] = 2
    np.savez(
        path,
        x7=x7,
        y7=y7,
        z_w_db=z_w_db,
        w_mask=w_mask,
        w_grid=grid,
        f_a=fs.astype(np.float64),
        fp_ratio=(fp / fs).astype(np.float64),
        split=split,
    )
    return path


@pytest.fixture(scope="module")
def tiny_data(tmp_path_factory) -> Path:
    return _make_tiny_unified(tmp_path_factory.mktemp("data") / "unified.npz")


def _stage1_cfg(data: Path, out: Path) -> dict:
    cfg = dict(ts1.DEFAULT_CFG)
    cfg.update(
        data=str(data),
        out_dir=str(out),
        epochs=2,
        warmup_epochs=1,  # epoch1 用 GT 位置，epoch2 切自预测 → 两条路径都覆盖
        batch_size=32,
        hidden=32,
        n_res_blocks=1,
        patience=10,
        device="cpu",
        seed=20260911,
    )
    return cfg


def test_stage1_train_smoke(tiny_data, tmp_path):
    out = tmp_path / "ckpt1"
    result = ts1.train(_stage1_cfg(tiny_data, out))
    assert result["epochs_run"] == 2
    assert len(result["history"]) == 2
    assert np.isfinite(result["history"][-1]["val"])
    assert result["best_epoch"] >= 1
    ckpt = out / "stage1.pt"
    cfg_json = out / "stage1_config.json"
    assert ckpt.exists() and cfg_json.exists()
    saved = json.loads(cfg_json.read_text())
    for key in ["x7_mean", "x7_std", "y7_mean", "y7_std", "db_mean", "db_std", "seed", "k"]:
        assert key in saved
    # 评估结构
    ev = result["eval"]
    for struct in ["epe", "eppe", "all"]:
        assert struct in ev
        for key in ["fs_mae_mhz", "fp_mae_mhz", "k2eff_mae_pp", "sparse_rmse_db", "n_test"]:
            assert key in ev[struct], (struct, key)
    assert ev["epe"]["n_test"] > 0 and ev["eppe"]["n_test"] > 0


@pytest.fixture(scope="module")
def stage1_ckpt(tiny_data, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("ckpt") / "stage1"
    ts1.train(_stage1_cfg(tiny_data, out))
    return out


def _stage2_cfg(data: Path, s1: Path, out: Path) -> dict:
    cfg = dict(ts2.DEFAULT_CFG)
    cfg.update(
        data=str(data),
        stage1_ckpt=str(s1 / "stage1.pt"),
        stage1_config=str(s1 / "stage1_config.json"),
        out_dir=str(out),
        epochs=2,
        batch_size=32,
        d_model=16,
        n_layers=1,
        n_heads=2,
        dim_feedforward=32,
        patience=10,
        max_freq=0.1,
        seed=20260911,
        device="cpu",
    )
    return cfg


def test_stage2_train_smoke(tiny_data, stage1_ckpt, tmp_path):
    out = tmp_path / "ckpt2"
    result = ts2.train(_stage2_cfg(tiny_data, stage1_ckpt, out))
    assert result["epochs_run"] == 2
    assert np.isfinite(result["history"][-1]["val"])
    assert (out / "stage2.pt").exists() and (out / "stage2_config.json").exists()
    ev = result["eval"]
    for struct in ["epe", "eppe", "all"]:
        for key in ["full_rmse_db", "aligned_rmse_db", "zp_peak_mae_db", "n_test"]:
            assert key in ev[struct], (struct, key)


def test_stage2_max_freq_compare(tiny_data, stage1_ckpt, tmp_path):
    cfg = _stage2_cfg(tiny_data, stage1_ckpt, tmp_path / "ckpt_cmp")
    scores = ts2.compare_max_freq(cfg, candidates=(0.1, 1.0), epochs=1)
    assert set(scores) == {0.1, 1.0}
    assert all(np.isfinite(v) and v > 0 for v in scores.values())
