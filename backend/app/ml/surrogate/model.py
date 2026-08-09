"""Stage 1：基准锚定残差 MLP（规格 §6）。

输入归一化结构参数，输出 = 基准 + 残差（归一化空间）。
双头最后一层零初始化 → 训练起点等价于纯基准模型（B0），
残差从零开始逐步学习，避免初期输出被随机头破坏。
"""

from __future__ import annotations

import torch
import torch.nn as nn


class _ResBlock(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.net = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(x)


class BaselineResidualMLP(nn.Module):
    """(eg, fl, ag, area, pf) → Δparams(7) + Δsparse(K)，输出基准+残差。"""

    def __init__(
        self,
        in_dim: int = 5,
        hidden: int = 256,
        n_res_blocks: int = 2,
        n_params: int = 7,
        k: int = 256,
    ) -> None:
        super().__init__()
        self.in_dim = in_dim
        self.n_params = n_params
        self.k = k

        layers: list[nn.Module] = [nn.Linear(in_dim, hidden), nn.GELU()]
        layers += [_ResBlock(hidden) for _ in range(n_res_blocks)]
        self.backbone = nn.Sequential(*layers)

        self.head_params = nn.Sequential(
            nn.Linear(hidden, hidden), nn.GELU(), nn.Linear(hidden, n_params)
        )
        self.head_sparse = nn.Sequential(
            nn.Linear(hidden, hidden), nn.GELU(), nn.Linear(hidden, k)
        )
        # 零初始化双头最后一层：零残差 → 输出恒等于基准
        for head in (self.head_params, self.head_sparse):
            nn.init.zeros_(head[-1].weight)
            nn.init.zeros_(head[-1].bias)

    def forward(
        self,
        x: torch.Tensor,  # (B, in_dim) 归一化
        params_base: torch.Tensor,  # (B, n_params) 归一化基准
        sparse_base: torch.Tensor,  # (B, k) 归一化基准
    ) -> dict[str, torch.Tensor]:
        h = self.backbone(x)
        return {
            "params": params_base + self.head_params(h),
            "sparse": sparse_base + self.head_sparse(h),
        }
