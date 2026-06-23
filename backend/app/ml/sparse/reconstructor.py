"""稀疏采样重建网络：从稀疏点 + 5 性能指标 → 完整 Z11 频谱。

架构: Transformer Encoder (稀疏点序列) → Cross-Attention → 残差修正
输出 = 基线插值(CubicSpline) + 网络残差 * scale
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F


class SinusoidalEmbedding(nn.Module):
    """正弦位置编码，用于将频率值编码为向量。"""

    def __init__(self, dim: int = 32):
        super().__init__()
        self.dim = dim

    def forward(self, freq: torch.Tensor) -> torch.Tensor:
        """Args: freq (...,) 任意形状的频率值 (GHz)。Returns: (..., dim)"""
        half = self.dim // 2
        emb = math.log(10000.0) / (half - 1)
        emb = torch.exp(torch.arange(half, device=freq.device) * -emb)
        emb = freq.unsqueeze(-1) * emb.unsqueeze(0)  # (..., half)
        return torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)


class SparseReconstructor(nn.Module):
    """从稀疏采样点重建完整频谱（残差结构）。

    输入:
        - 全局条件: [fs, fp, Qs, Qp, kt2] (B, 5)
        - 稀疏点: [(freq_j, z_j)] (B, K, 2)
        - 目标频率: freq_target (B, N) or (N,)
        - 基线插值: z_baseline (B, N) — 从稀疏点 cubic spline 插值

    输出:
        - z_pred (B, N): 完整频谱 = z_baseline + residual * scale
    """

    def __init__(
        self,
        d_model: int = 64,
        n_encoder_layers: int = 4,
        n_heads: int = 4,
        dim_feedforward: int = 256,
        dropout: float = 0.2,
        max_freq: float = 10.0,
    ):
        super().__init__()
        self.d_model = d_model
        self.max_freq = max_freq

        # 全局条件投影
        self.cond_proj = nn.Sequential(
            nn.Linear(5, d_model),
            nn.ReLU(),
            nn.Linear(d_model, d_model),
        )

        # 稀疏点编码
        self.freq_embed = SinusoidalEmbedding(dim=d_model // 2)
        self.val_proj = nn.Linear(1, d_model // 2)
        self.sample_norm = nn.LayerNorm(d_model)

        # Transformer Encoder
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_encoder_layers)

        # Cross-Attention
        self.query_norm = nn.LayerNorm(d_model)
        self.cross_attn = nn.MultiheadAttention(
            embed_dim=d_model,
            num_heads=n_heads,
            dropout=dropout,
            batch_first=True,
        )

        # 残差 MLP
        self.residual_mlp = nn.Sequential(
            nn.Linear(d_model, dim_feedforward),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(dim_feedforward, d_model),
            nn.Dropout(dropout),
        )
        self.output_norm = nn.LayerNorm(d_model)

        # 输出头：预测残差（而非完整频谱）
        self.residual_head = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.ReLU(),
            nn.Linear(d_model // 2, 1),
        )

        # 可学习的残差缩放因子（初始化很小）
        self.residual_scale = nn.Parameter(torch.tensor(0.01))

    def _encode_samples(
        self,
        samples: torch.Tensor,
        cond: torch.Tensor,
        mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        B, K, _ = samples.shape
        freq = samples[..., 0]
        zval = samples[..., 1:2]

        freq_norm = freq / self.max_freq
        pos_emb = self.freq_embed(freq_norm)
        val_emb = self.val_proj(zval)

        x = torch.cat([pos_emb, val_emb], dim=-1)
        x = x + cond.unsqueeze(1)
        x = self.sample_norm(x)

        h = self.encoder(x, src_key_padding_mask=mask)
        return h

    def forward(
        self,
        cond: torch.Tensor,
        samples: torch.Tensor,
        freq_target: torch.Tensor,
        z_baseline: torch.Tensor | None = None,
        sample_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        B = cond.shape[0]

        c = self.cond_proj(cond)
        h = self._encode_samples(samples, c, sample_mask)

        if freq_target.dim() == 1:
            freq_target = freq_target.unsqueeze(0).expand(B, -1)
        N = freq_target.shape[1]

        freq_t_norm = freq_target / self.max_freq
        q_pos = self.freq_embed(freq_t_norm)
        q = torch.cat([
            q_pos,
            torch.zeros(B, N, self.d_model // 2, device=q_pos.device),
        ], dim=-1)
        q = q + c.unsqueeze(1)
        q = self.query_norm(q)

        attn_out, _ = self.cross_attn(
            query=q, key=h, value=h,
            key_padding_mask=sample_mask,
        )

        out = attn_out + q
        out = out + self.residual_mlp(out)
        out = self.output_norm(out)

        # 预测残差
        z_residual = self.residual_head(out).squeeze(-1)  # (B, N)

        # 残差缩放
        scale = torch.sigmoid(self.residual_scale)
        z_pred = z_residual * scale

        # 加上基线插值（如果有）
        if z_baseline is not None:
            if z_baseline.dim() == 1:
                z_baseline = z_baseline.unsqueeze(0).expand(B, -1)
            z_pred = z_pred + z_baseline

        return z_pred
