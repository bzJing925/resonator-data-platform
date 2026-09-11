"""WP4b 反向模型（train_inverse_mason）的单元与冒烟测试（TDD 先于实现）。

覆盖：
- routed_loss 按 structure_flag 路由：EPE 批次只反传 t1 头；EPPE 批次只反传 cls/t2 头
- confusion_matrix 计数正确
- sensitivity_check：理想物理模型（t ∝ 1/fs）pearson≈1、符号一致率=1；
  反物理模型（t ∝ fs）pearson≈-1、符号一致率=0
- 合成小数据集（M=128）2 epoch 冒烟：checkpoint/config/eval json 落盘且键齐全；
  抖动评估确定性（同种子两次一致）
"""

from __future__ import annotations

import json
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

from mason_surrogate import train_inverse_mason as tim  # noqa: E402

T_ALN = tim.T_ALN_CLASSES


def _head_grads(model: tim.InverseMLP) -> dict[str, torch.Tensor | None]:
    return {
        "t1": model.head_t1[-1].weight.grad,
        "cls": model.head_cls[-1].weight.grad,
        "t2": model.head_t2[-1].weight.grad,
    }


def _grad_zero(g: torch.Tensor | None) -> bool:
    return g is None or bool((g == 0).all())


def test_routed_loss_epe_only_backprop():
    torch.manual_seed(0)
    model = tim.InverseMLP(in_dim=8, hidden=32, n_res_blocks=1)
    x = torch.randn(8, 8)
    flag = torch.zeros(8, dtype=torch.long)  # 全 EPE
    out = model(x)
    loss = tim.routed_loss(
        out,
        flag,
        t1_tgt=torch.randn(8),
        cls_tgt=torch.zeros(8, dtype=torch.long),
        t2_tgt=torch.randn(8),
    )
    loss.backward()
    grads = _head_grads(model)
    assert not _grad_zero(grads["t1"]), "EPE 批次应反传 t1 头"
    assert _grad_zero(grads["cls"]), "EPE 批次不应触碰 cls 头"
    assert _grad_zero(grads["t2"]), "EPE 批次不应触碰 t2 头"


def test_routed_loss_eppe_only_backprop():
    torch.manual_seed(0)
    model = tim.InverseMLP(in_dim=8, hidden=32, n_res_blocks=1)
    x = torch.randn(8, 8)
    flag = torch.ones(8, dtype=torch.long)  # 全 EPPE
    out = model(x)
    loss = tim.routed_loss(
        out,
        flag,
        t1_tgt=torch.randn(8),
        cls_tgt=torch.randint(0, 5, (8,)),
        t2_tgt=torch.randn(8),
    )
    loss.backward()
    grads = _head_grads(model)
    assert _grad_zero(grads["t1"]), "EPPE 批次不应触碰 t1 头"
    assert not _grad_zero(grads["cls"]), "EPPE 批次应反传 cls 头"
    assert not _grad_zero(grads["t2"]), "EPPE 批次应反传 t2 头"


def test_confusion_matrix_counts():
    cm = tim.confusion_matrix(
        np.array([0, 0, 1, 2, 2, 2]), np.array([0, 1, 1, 2, 0, 2]), n_classes=3
    )
    assert cm.tolist() == [[1, 1, 0], [0, 1, 0], [1, 0, 2]]


def _fs_physics_predict(fs_mean: float, fs_std: float, k: float = 1000.0):
    """理想物理模型：t = K/fs（fs 为输入第 0 列，去归一化后 GHz）。"""

    def predict(x_norm: np.ndarray) -> np.ndarray:
        fs = x_norm[:, 0] * fs_std + fs_mean
        return k / fs

    return predict


def test_sensitivity_check_perfect_physics():
    rng = np.random.default_rng(0)
    fs = rng.uniform(3.5, 7.5, 200)
    fs_mean, fs_std = 5.5, 1.2
    x = np.stack([(fs - fs_mean) / fs_std, np.zeros(len(fs))], axis=1).astype(np.float32)
    r = tim.sensitivity_check(
        _fs_physics_predict(fs_mean, fs_std), x, fs, fs_std=fs_std, delta_mhz=10.0
    )
    assert r["pearson"] > 0.99
    assert r["sign_rate"] == pytest.approx(1.0)
    assert 0.5 < r["median_mag_ratio"] < 2.0
    assert r["n_points"] == 2 * len(fs)


def test_sensitivity_check_anti_physics():
    rng = np.random.default_rng(1)
    fs = rng.uniform(3.5, 7.5, 100)
    fs_mean, fs_std = 5.5, 1.2
    x = np.stack([(fs - fs_mean) / fs_std, np.zeros(len(fs))], axis=1).astype(np.float32)

    def anti(x_norm: np.ndarray) -> np.ndarray:  # t ∝ fs：与物理相反
        return 300.0 * (x_norm[:, 0] * fs_std + fs_mean)

    r = tim.sensitivity_check(anti, x, fs, fs_std=fs_std, delta_mhz=10.0)
    assert r["pearson"] < -0.99
    assert r["sign_rate"] == pytest.approx(0.0)


def _make_tiny_unified(path: Path, m_epe: int = 64, m_eppe: int = 64) -> Path:
    """合成数据集：fs≈1000/t1（EPE）、fs≈1800/(t1+t2)（EPPE），可学。"""
    rng = np.random.default_rng(11)
    m = m_epe + m_eppe
    x7 = np.zeros((m, 7), dtype=np.float32)
    x7[m_epe:, 0] = 1.0
    x7[:m_epe, 1] = rng.uniform(140, 550, m_epe)
    x7[m_epe:, 1] = rng.choice(np.array(T_ALN, dtype=np.float32), m_eppe)
    x7[m_epe:, 2] = rng.uniform(100, 380, m_eppe)
    x7[:, 3] = rng.uniform(50, 300, m)
    x7[:, 4] = rng.uniform(50, 300, m)
    x7[:, 5] = rng.uniform(0, 100, m)
    x7[:, 6] = rng.choice([700.0, 1200.0, 2000.0, 3500.0, 5500.0], m)
    y7 = np.zeros((m, 7), dtype=np.float32)
    fs = np.where(
        x7[:, 0] == 0,
        1000.0 / x7[:, 1] + rng.normal(0, 0.01, m),
        1800.0 / (x7[:, 1] + x7[:, 2]) + rng.normal(0, 0.01, m),
    )
    y7[:, 0] = fs
    y7[:, 1] = fs * rng.uniform(1.01, 1.05, m)
    y7[:, 2] = np.where(x7[:, 0] == 0, 12.0, 2.5) + rng.normal(0, 0.2, m)
    y7[:, 3:7] = rng.uniform(200, 900, (m, 4))
    grid = np.linspace(0.8, 1.2, 1001)
    z_w_db = np.zeros((m, 1001), dtype=np.float32)
    z_w_db[:] = 30.0
    w_mask = np.ones((m, 1001), dtype=bool)
    split = np.zeros(m, dtype=np.int8)
    for rows in (np.arange(0, m_epe), np.arange(m_epe, m)):
        n = len(rows)
        split[rows[int(0.75 * n) : int(0.875 * n)]] = 1
        split[rows[int(0.875 * n) :]] = 2
    np.savez(
        path,
        x7=x7,
        y7=y7,
        z_w_db=z_w_db,
        w_mask=w_mask,
        w_grid=grid,
        f_a=fs.astype(np.float64),
        fp_ratio=(y7[:, 1] / fs).astype(np.float64),
        split=split,
    )
    return path


def _cfg(data: Path, out: Path, eval_out: Path) -> dict:
    cfg = dict(tim.DEFAULT_CFG)
    cfg.update(
        data=str(data),
        out_dir=str(out),
        eval_out=str(eval_out),
        epochs=2,
        batch_size=32,
        hidden=32,
        n_res_blocks=1,
        patience=5,
        seed=20260912,
    )
    return cfg


def test_inverse_train_smoke(tmp_path):
    data = _make_tiny_unified(tmp_path / "u.npz")
    out = tmp_path / "ckpt"
    eval_out = tmp_path / "evals" / "inverse_eval.json"
    result = tim.train(_cfg(data, out, eval_out))
    assert result["epochs_run"] == 2
    assert (out / "inverse.pt").exists() and (out / "inverse_config.json").exists()
    cfg = json.loads((out / "inverse_config.json").read_text())
    for key in ["y7_mean", "y7_std", "t1_mean", "t1_std", "t2_mean", "t2_std",
                "t_aln_classes", "dataset_sha256", "seed"]:
        assert key in cfg, key
    assert len(cfg["dataset_sha256"]) == 64
    ev = json.loads(eval_out.read_text())
    clean = ev["test"]["clean"]
    assert clean["epe"]["n_test"] > 0 and clean["eppe"]["n_test"] > 0
    assert np.isfinite(clean["epe"]["t_piezo_mae_nm"])
    sens = clean["epe"]["sensitivity"]
    for key in ["pearson", "sign_rate", "median_mag_ratio", "n_points", "delta_mhz"]:
        assert key in sens, key
    eppe = clean["eppe"]
    assert 0.0 <= eppe["t_aln_acc"] <= 1.0
    cm = np.array(eppe["t_aln_confusion"])
    assert cm.shape == (5, 5)
    # 混淆矩阵行和 = 各类 test 样本数
    assert int(cm.sum()) == eppe["n_test"]
    assert np.isfinite(eppe["t_alscn_mae_nm"])
    jit = ev["test"]["fs_jitter"]
    assert jit["delta_mhz"] == pytest.approx(10.0)
    assert np.isfinite(jit["epe"]["t_piezo_mae_nm"])
    assert 0.0 <= jit["eppe"]["t_aln_acc"] <= 1.0
    deg = ev["test"]["degradation"]
    assert deg["epe"]["t_piezo_mae_nm_delta"] == pytest.approx(
        jit["epe"]["t_piezo_mae_nm"] - clean["epe"]["t_piezo_mae_nm"]
    )


def test_inverse_jitter_deterministic(tmp_path):
    data = _make_tiny_unified(tmp_path / "u.npz")
    out = tmp_path / "ckpt"
    tim.train(_cfg(data, out, tmp_path / "e1.json"))
    d = tim.load_unified(data)
    cfg = json.loads((out / "inverse_config.json").read_text())
    m1 = tim.evaluate_from_checkpoint(out, d, cfg, jitter_mhz=10.0, jitter_seed=123)
    m2 = tim.evaluate_from_checkpoint(out, d, cfg, jitter_mhz=10.0, jitter_seed=123)
    assert m1["eppe"]["t_aln_acc"] == m2["eppe"]["t_aln_acc"]
    assert m1["epe"]["t_piezo_mae_nm"] == m2["epe"]["t_piezo_mae_nm"]
