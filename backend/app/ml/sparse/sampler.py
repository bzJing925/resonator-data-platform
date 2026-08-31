"""自适应稀疏采样策略网络（Gumbel-Softmax 版）。

输入 Z11(dB) + 物理分区 mask + fs/fp，输出每点采样概率 p_i。
通过 Gumbel-Softmax 实现可导的 soft top-k，替代 STE。
主模区内加入 fs/fp 高斯加权先验，使谐振峰附近采样更密集。
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as f


class AdaptiveSampler(nn.Module):
    """学习在频谱上选择 K 个采样点的策略网络。

    结构:
        Conv1d 特征提取 → 区域高斯先验加权 → 概率输出 → Gumbel-Softmax

    区域先验（硬编码）:
        主模区: β_main=2.0 + fs/fp 高斯加权
        杂模区: β_spur=3.0
        平滑区: β_smooth=0.3
    """

    def __init__(
        self,
        n_freq: int = 1001,
        beta_main: float = 2.0,
        beta_spur: float = 3.0,
        beta_smooth: float = 0.3,
        tau_init: float = 0.5,
        tau_min: float = 0.05,
    ):
        super().__init__()
        self.n_freq = n_freq
        self.beta = nn.Parameter(
            torch.tensor([beta_main, beta_spur, beta_smooth]),
            requires_grad=False,
        )
        self.tau_init = tau_init
        self.tau = tau_init
        self.tau_min = tau_min

        # 特征提取: 2 通道输入 (Z11, region_id)
        self.conv = nn.Sequential(
            nn.Conv1d(2, 16, kernel_size=7, padding=3),
            nn.ReLU(),
            nn.Conv1d(16, 32, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool1d(2),
        )

        feat_len = n_freq // 4
        self.mlp = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64 * feat_len, 128),
            nn.ReLU(),
        )
        self.prob_head = nn.Linear(128, n_freq)
        self.k_head = nn.Sequential(
            nn.Linear(128, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid(),  # 输出 [0, 1]，映射到 [k_min, k_max]
        )
        self.k_min = 50
        self.k_max = 500

    def _apply_region_prior(
        self,
        p_raw: torch.Tensor,       # (B, N)
        region_ids: torch.Tensor,  # (B, N)
        freq: torch.Tensor,        # (B, N) or (N,)
        fs: torch.Tensor | None = None,  # (B,)
        fp: torch.Tensor | None = None,  # (B,)
    ) -> torch.Tensor:
        """应用区域配额 + 主模区 fs/fp 高斯加权。"""
        b, n = p_raw.shape
        beta = self.beta.to(p_raw.device)

        # 区域配额
        p_weighted = p_raw * beta[region_ids.long()]  # (B, N)

        # 主模区 fs/fp 高斯加权
        if fs is not None and fp is not None:
            main_mask = (region_ids == 0).float()  # (B, N)
            if main_mask.sum() > 0:
                # 确保 freq 是 2D
                if freq.dim() == 1:
                    freq = freq.unsqueeze(0).expand(b, -1)

                bw = (fp - fs).clamp(min=1e-6).unsqueeze(1)  # (b, 1)
                d_fs = torch.abs(freq - fs.unsqueeze(1)) / (bw * 0.3)
                d_fp = torch.abs(freq - fp.unsqueeze(1)) / (bw * 0.3)
                gaussian = torch.exp(-torch.minimum(d_fs, d_fp) ** 2)  # (b, n)
                # 只在主模区应用高斯加权
                p_weighted = p_weighted + main_mask * gaussian * 2.0

        return p_weighted

    def forward(
        self,
        z_db: torch.Tensor,       # (B, N)
        region_ids: torch.Tensor,  # (B, N) int64
        freq: torch.Tensor,        # (B, N) or (N,)
        fs: torch.Tensor | None = None,  # (B,)
        fp: torch.Tensor | None = None,  # (B,)
        target_k: int | None = None,
        use_gumbel: bool = True,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            z_db: Z11(dB) 频谱
            region_ids: 每点的区域类别
            freq: 频率轴
            fs: 串联谐振频率 (GHz)，用于主模区高斯加权
            fp: 并联谐振频率 (GHz)
            target_k: 目标采样点数
            use_gumbel: 是否使用 Gumbel-Softmax（False=用 softmax）

        Returns:
            p_norm:  (B, N) 归一化采样概率
            y_soft:  (B, N) soft 采样分布（Gumbel-Softmax 输出）
        """
        if z_db.dim() == 2:
            z_db = z_db.unsqueeze(1)  # (b, 1, n)
        b, _, n = z_db.shape

        # 输入编码
        region_float = region_ids.float().unsqueeze(1) / 2.0
        x = torch.cat([z_db, region_float], dim=1)  # (B, 2, N)

        # CNN 特征提取
        feat = self.conv(x)  # (B, 64, N//4)

        # MLP → 特征
        h = self.mlp(feat)  # (B, 128)

        # 采样概率头
        p_raw = self.prob_head(h)  # (B, N)

        # 预测采样点数 k_pred
        k_ratio = self.k_head(h).squeeze(-1)  # (B,)
        k_pred = self.k_min + k_ratio * (self.k_max - self.k_min)

        # 区域先验加权
        p_weighted = self._apply_region_prior(p_raw, region_ids, freq, fs, fp)

        # Softmax 归一化
        p_norm = f.softmax(p_weighted / self.tau, dim=1)  # (b, n)

        if target_k is None:
            return p_norm, p_norm, k_pred

        if not self.training or not use_gumbel:
            return p_norm, p_norm, k_pred

        # Gumbel-Softmax
        log_p = torch.log(p_norm.clamp(min=1e-8))
        gumbel = -torch.log(-torch.log(torch.rand_like(log_p).clamp(min=1e-8)))
        y_soft = f.softmax((log_p + gumbel) / self.tau, dim=1)

        return p_norm, y_soft, k_pred

    def sample_points(
        self,
        p_norm: torch.Tensor,    # (B, N)
        k: int | None = None,
    ) -> tuple[torch.Tensor, int]:
        """推理时用的确定性采样（无 Gumbel 噪声，直接 top-k）。

        Returns:
            mask, k_actual
        """
        b, n = p_norm.shape
        if k is None:
            # 自适应：根据概率分布的熵决定采样点数
            entropy = -(p_norm * torch.log(p_norm.clamp(min=1e-8))).sum(dim=1)  # (b,)
            k = int(self.k_min + (entropy.mean().item() / math.log(n)) * (self.k_max - self.k_min))
            k = max(self.k_min, min(self.k_max, k))

        _, topk_idx = torch.topk(p_norm, min(k, n), dim=-1)
        mask = torch.zeros_like(p_norm)
        mask.scatter_(1, topk_idx, 1.0)
        return mask.bool(), k

    def update_tau(self, epoch: int, total_epochs: int) -> None:
        """退火策略：τ 从初始值逐渐降到 tau_min。"""
        progress = min(1.0, epoch / total_epochs)
        self.tau = self.tau_min + (self.tau_init - self.tau_min) * (1.0 - progress)
