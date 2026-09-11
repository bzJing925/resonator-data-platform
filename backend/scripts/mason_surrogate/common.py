"""WP4a 统一正向代理共享组件：模型 / 归一化 / 稀疏位置 / 掩码损失 / 评估指标。

口径说明：
- 稀疏位置：确定性采样 `deterministic_sample_positions(w_grid, 1.0, fp_ratio, k)`，
  只看 fs/fp（相对网格上 fs_w≡1.0）不看谱内容，训练/推理一致（生产规格 §5.3）。
- cond 列序沿用生产 SparseReconstructor：[fs, fp, Qs, Qp, kt2]；
  kt2 直接取 k2eff_pct 列（百分数，与生产 `row.k2eff_pct` 口径一致，见
  app/ml/sparse/dataset.py）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

import numpy as np
import torch
import torch.nn as nn
from numpy.typing import NDArray

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.sparse_sampler import (  # noqa: E402
    baseline_interpolate,
    deterministic_sample_positions,
)

X7_FLAG_COL = 0
X7_COLS = ("structure_flag", "t1_nm", "t2_nm", "t_top_nm", "t_bot_nm", "t_pf_nm", "area_um2")
PARAM_COLS = ("fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "qs_bodeq", "qp_bodeq")
COND_PARAM_IDX = (0, 1, 3, 4, 2)  # params(7) → cond(5) = [fs, fp, qs, qp, k2eff_pct]
Q_COLS = (3, 4, 5, 6)  # qs, qp, qs_bodeq, qp_bodeq


# ---------------------------------------------------------------- 归一化


class NormStats(NamedTuple):
    mean: NDArray[np.float64]
    std: NDArray[np.float64]


def fit_norm(x: NDArray, skip_cols: tuple[int, ...] = ()) -> NormStats:
    """逐列均值/标准差；skip_cols 列不归一化（mean=0,std=1，如结构标志列）。"""
    mean = x.mean(axis=0).astype(np.float64)
    std = x.std(axis=0).astype(np.float64)
    std = np.where(std < 1e-12, 1.0, std)  # 零方差守卫
    for c in skip_cols:
        mean[c] = 0.0
        std[c] = 1.0
    return NormStats(mean, std)


def apply_norm(x: NDArray, stats: NormStats) -> NDArray[np.float32]:
    return ((x - stats.mean) / stats.std).astype(np.float32)


def invert_norm(x: NDArray, stats: NormStats) -> NDArray[np.float64]:
    return np.asarray(x, dtype=np.float64) * stats.std + stats.mean


# ---------------------------------------------------------------- Stage 1 模型


class _ResBlock(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.net = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(x)


class UnifiedMLP(nn.Module):
    """x7 → 7 参数 + K 稀疏值的浅层残差 MLP（直接从输入预测，无 baseline 查表）。

    风格参考生产 BaselineResidualMLP（app/ml/surrogate/model.py），
    但输出为直接预测（统一语料无基准表可查）。
    """

    def __init__(
        self,
        in_dim: int = 7,
        hidden: int = 256,
        n_res_blocks: int = 2,
        n_params: int = 7,
        k: int = 256,
    ) -> None:
        super().__init__()
        self.n_params = n_params
        self.k = k
        layers: list[nn.Module] = [nn.Linear(in_dim, hidden), nn.GELU()]
        layers += [_ResBlock(hidden) for _ in range(n_res_blocks)]
        self.backbone = nn.Sequential(*layers)
        self.head_params = nn.Sequential(
            nn.Linear(hidden, hidden), nn.GELU(), nn.Linear(hidden, n_params)
        )
        self.head_sparse = nn.Sequential(nn.Linear(hidden, hidden), nn.GELU(), nn.Linear(hidden, k))

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        h = self.backbone(x)
        return {"params": self.head_params(h), "sparse": self.head_sparse(h)}


# ---------------------------------------------------------------- 稀疏位置


def sparse_positions(grid: NDArray[np.float64], fp_ratio: float, k: int = 256) -> NDArray[np.int64]:
    """相对网格上的确定性稀疏位置：fs_w≡1.0，fp_w=fp_ratio。"""
    return deterministic_sample_positions(np.asarray(grid, dtype=np.float64), 1.0, fp_ratio, k=k)


def batch_sparse_positions(
    grid: NDArray[np.float64], fp_ratios: NDArray[np.float64], k: int = 256
) -> NDArray[np.int64]:
    return np.stack([sparse_positions(grid, float(r), k=k) for r in fp_ratios], axis=0)


# ---------------------------------------------------------------- 损失


def masked_mse(pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """只在 mask=True 处计 MSE；全掩码返回 0（避免除零）。"""
    m = mask.to(pred.dtype)
    denom = m.sum().clamp_min(1.0)
    loss = (((pred - target) ** 2) * m).sum() / denom
    return torch.where(mask.any(), loss, torch.zeros((), device=pred.device))


# ---------------------------------------------------------------- 参数评估


def param_metrics(y_pred: NDArray, y_true: NDArray) -> dict[str, float]:
    """7 参数误差口径：fs/fp MAE(MHz)、k2eff MAE(pct-pt)、4×Q MAE(相对%)。"""
    d = np.asarray(y_pred, dtype=np.float64) - np.asarray(y_true, dtype=np.float64)
    out = {
        "fs_mae_mhz": float(np.abs(d[:, 0]).mean() * 1000.0),
        "fp_mae_mhz": float(np.abs(d[:, 1]).mean() * 1000.0),
        "k2eff_mae_pp": float(np.abs(d[:, 2]).mean()),
    }
    for c, name in zip(Q_COLS, ("qs", "qp", "qs_bodeq", "qp_bodeq"), strict=True):
        denom = np.maximum(np.abs(y_true[:, c]), 1e-9)
        out[f"{name}_mae_rel_pct"] = float((np.abs(d[:, c]) / denom).mean() * 100.0)
    return out


def build_cond(params: NDArray) -> NDArray[np.float64]:
    """params(M,7) → cond(M,5)=[fs, fp, Qs, Qp, kt2(=k2eff_pct 百分数，生产口径)]。"""
    return np.asarray(params, dtype=np.float64)[:, COND_PARAM_IDX]


# ---------------------------------------------------------------- 谱评估


def anchor_sensitivity_rmse_db(
    pred: NDArray,
    gt: NDArray,
    mask: NDArray[np.bool_],
    grid: NDArray[np.float64],
    s: NDArray[np.float64],
) -> tuple[float, float]:
    """乘性锚点扰动敏感度：pred 在查询 q = w·(1±s) 处取值后与 GT 算掩码内 RMSE(dB)。

    s: (M,) 每样本注入幅度（Stage 1 fs 相对误差 clip 后）。两个方向都报：
    返回 (rmse_plus, rmse_minus)。落网外或掩码外的点不计。

    口径说明：统一网格 w=f/f_a 下，锚点 f_a 的相对误差 ε 使绝对轴缩放 (1+ε)，
    等价于 w 轴乘性查询 q=w·(1+ε)——这是"锚点误差注入"探针而非对位修正
    （WP4a 复审裁决：原 aligned 加性平移口径对本级联不诚实，误差幅度主导）。
    """
    outs = []
    for sign in (1.0, -1.0):
        total, count = 0.0, 0
        for i in range(len(pred)):
            q = grid * (1.0 + sign * s[i])
            valid = mask[i] & (q >= grid[0]) & (q <= grid[-1])
            if not valid.any():
                continue
            p = np.interp(q[valid], grid, pred[i])
            total += float(((p - gt[i][valid]) ** 2).sum())
            count += int(valid.sum())
        outs.append(float(np.sqrt(total / count)) if count else float("nan"))
    return outs[0], outs[1]


def zp_peak_mae_db(
    pred: NDArray,
    gt: NDArray,
    mask: NDArray[np.bool_],
    grid: NDArray[np.float64],
    fp_ratios: NDArray[np.float64],
    half_win: float = 0.01,
) -> tuple[float, int]:
    """Zp 峰高 MAE(dB)：w ∈ [fp_ratio±half_win]（GT 口径）内峰值差。返回 (mae, n_used)。"""
    errs = []
    for i in range(len(pred)):
        win = mask[i] & (grid >= fp_ratios[i] - half_win) & (grid <= fp_ratios[i] + half_win)
        if not win.any():
            continue
        errs.append(abs(float(pred[i][win].max()) - float(gt[i][win].max())))
    if not errs:
        return float("nan"), 0
    return float(np.mean(errs)), len(errs)


# ---------------------------------------------------------------- Stage 2 输入构建


def build_sparse_samples(
    z_w_db: NDArray,
    w_mask: NDArray[np.bool_],
    positions: NDArray[np.int64],
    grid: NDArray[np.float64],
) -> tuple[NDArray[np.float32], NDArray[np.bool_]]:
    """按位置聚集稀疏样本：samples(M,K,2)=(w, z_db)，valid(M,K)=掩码聚集值。"""
    m, k = positions.shape
    rows = np.arange(m)[:, None]
    vals = z_w_db[rows, positions]
    valid = w_mask[rows, positions]
    samples = np.empty((m, k, 2), dtype=np.float32)
    samples[..., 0] = np.asarray(grid, dtype=np.float64)[positions]
    samples[..., 1] = vals
    return samples, valid


def precompute_baselines(
    positions: NDArray[np.int64],
    values: NDArray,
    valid: NDArray[np.bool_],
    grid: NDArray[np.float64],
    fill_value: float = 0.0,
) -> NDArray[np.float64]:
    """对每个样本用有效稀疏点做 cubic spline 基线（残差结构的先验）。

    <4 个有效点退化为线性插值（baseline_interpolate 内置）；0 个有效点填 fill_value。
    """
    m = positions.shape[0]
    base = np.full((m, len(grid)), fill_value, dtype=np.float64)
    for i in range(m):
        idx = positions[i][valid[i]]
        if len(idx) == 0:
            continue
        base[i] = baseline_interpolate(grid[idx], values[i][valid[i]], grid)
    return base
