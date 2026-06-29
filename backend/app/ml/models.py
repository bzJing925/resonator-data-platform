"""Spectral Residual PINN 模型定义。

包含三个核心组件：
- SpectralVAE: 频谱自编码器，将 S11/Z 频谱压缩到 latent space
- ResidualNet: 轻量 MLP，从器件几何参数预测 latent 偏移
- SmartSampler: 1D CNN 降采样头，学习视觉重要性掩码
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as f


class SpectralVAE(nn.Module):
    """频谱变分自编码器。

    输入: (batch, 1, n_freq) 的归一化频谱
    输出: 重建频谱 (B, 1, n_freq) + latent 均值/对数方差 (B, latent_dim)

    Args:
        n_freq: 频点数（默认 1001，与常见 VNA 扫描点数一致）。
        latent_dim: latent space 维度（默认 12）。
    """

    def __init__(self, n_freq: int = 1001, latent_dim: int = 12) -> None:
        super().__init__()
        self.n_freq = n_freq
        self.latent_dim = latent_dim

        # --- Encoder: 1D CNN 下采样 ---
        # 输入 (B, 1, n_freq)
        self.enc_conv = nn.Sequential(
            nn.Conv1d(1, 16, kernel_size=7, stride=2, padding=3),   # → n_freq//2
            nn.BatchNorm1d(16),
            nn.LeakyReLU(0.2, inplace=True),

            nn.Conv1d(16, 32, kernel_size=5, stride=2, padding=2),  # → n_freq//4
            nn.BatchNorm1d(32),
            nn.LeakyReLU(0.2, inplace=True),

            nn.Conv1d(32, 64, kernel_size=3, stride=2, padding=1),  # → n_freq//8
            nn.BatchNorm1d(64),
            nn.LeakyReLU(0.2, inplace=True),
        )

        # 计算 flatten 后的维度
        with torch.no_grad():
            dummy = torch.zeros(1, 1, n_freq)
            conv_out = self.enc_conv(dummy)
            self._flatten_dim = conv_out.view(1, -1).shape[1]

        self.enc_fc = nn.Sequential(
            nn.Flatten(),
            nn.Linear(self._flatten_dim, 128),
            nn.LeakyReLU(0.2, inplace=True),
        )
        self.fc_mu = nn.Linear(128, latent_dim)
        self.fc_logvar = nn.Linear(128, latent_dim)

        # --- Decoder: 全连接 + 上采样 ---
        self.dec_fc = nn.Sequential(
            nn.Linear(latent_dim, 128),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Linear(128, self._flatten_dim),
            nn.LeakyReLU(0.2, inplace=True),
        )

        # 计算反 flatten 后的 (C, L)
        with torch.no_grad():
            # 需要推断 (C, L)：从 enc_conv 最后一层输出推导
            dummy_conv = self.enc_conv(dummy)
            self._dec_channels = dummy_conv.shape[1]
            self._dec_length = dummy_conv.shape[2]

        self.dec_upsample = nn.Sequential(
            nn.ConvTranspose1d(self._dec_channels, 32, kernel_size=4, stride=2, padding=1),
            nn.BatchNorm1d(32),
            nn.LeakyReLU(0.2, inplace=True),

            nn.ConvTranspose1d(32, 16, kernel_size=4, stride=2, padding=1),
            nn.BatchNorm1d(16),
            nn.LeakyReLU(0.2, inplace=True),

            nn.ConvTranspose1d(16, 1, kernel_size=4, stride=2, padding=1),
        )

        # 如果上采样后长度不匹配，用线性插值对齐
        self._target_len = n_freq

    def encode(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """编码：返回 latent 的均值和对数方差。"""
        h = self.enc_conv(x)
        h = self.enc_fc(h)
        mu = self.fc_mu(h)
        logvar = self.fc_logvar(h)
        return mu, logvar

    def reparameterize(self, mu: torch.Tensor, logvar: torch.Tensor) -> torch.Tensor:
        """重参数化技巧采样 latent。"""
        std = torch.exp(0.5 * logvar)
        eps = torch.randn_like(std)
        return mu + eps * std

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        """解码：从 latent 重建频谱。"""
        h = self.dec_fc(z)
        h = h.view(-1, self._dec_channels, self._dec_length)
        out = self.dec_upsample(h)
        # 长度对齐
        if out.shape[-1] != self._target_len:
            out = f.interpolate(
                out, size=self._target_len, mode="linear", align_corners=False
            )
        return out

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """前向传播。

        Returns:
            (重建频谱, mu, logvar)
        """
        mu, logvar = self.encode(x)
        z = self.reparameterize(mu, logvar)
        recon = self.decode(z)
        return recon, mu, logvar

    def encode_deterministic(self, x: torch.Tensor) -> torch.Tensor:
        """确定性编码（推理时用均值，不加噪声）。"""
        mu, _ = self.encode(x)
        return mu


class ResidualNet(nn.Module):
    """轻量残差网络：从器件几何参数预测 latent 偏移。

    输入: [area_um2, x, y, eg, fl, ag]（需预先归一化）
    输出: Δz ∈ ℝ^{latent_dim}

    Args:
        latent_dim: latent space 维度。
    """

    def __init__(self, latent_dim: int = 12) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(6, 32),
            nn.LayerNorm(32),
            nn.GELU(),
            nn.Dropout(0.1),

            nn.Linear(32, 24),
            nn.LayerNorm(24),
            nn.GELU(),
            nn.Dropout(0.1),

            nn.Linear(24, latent_dim),
        )

    def forward(self, params: torch.Tensor) -> torch.Tensor:
        """
        Args:
            params: (B, 6) — [area_um2, x, y, eg, fl, ag]
        Returns:
            Δz: (B, latent_dim)
        """
        return self.net(params)


class SmartSampler(nn.Module):
    """智能降采样头：学习每个频点的视觉重要性。

    输入: 三通道 [S, dS/df, d²S/df²]，shape (B, 3, N)
    输出: 保留概率 p_i ∈ [0,1]，shape (B, N)

    实际使用时配合 enforce_critical_points_mask 强制保留 fs/fp/起止点。

    Args:
        n_freq: 频点数。
    """

    def __init__(self, n_freq: int = 1001) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(3, 16, kernel_size=7, padding=3),
            nn.BatchNorm1d(16),
            nn.GELU(),

            nn.Conv1d(16, 8, kernel_size=5, padding=2),
            nn.BatchNorm1d(8),
            nn.GELU(),

            nn.Conv1d(8, 1, kernel_size=3, padding=1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B, 3, N) — [S, dS, d2S]
        Returns:
            p: (B, N) — 每个频点的保留概率
        """
        return self.conv(x).squeeze(1)  # (B, N)


class PINNReconstructor(nn.Module):
    """端到端重建器：ResidualNet + VAE Decoder + SmartSampler。

    推理时使用：
        S_recon = decoder(z_base + residual_net(params))
        mask = smart_sampler([S_recon, grad, hess])
        S_sampled = S_recon[:, mask]
    """

    def __init__(self, vae: SpectralVAE, residual_net: ResidualNet) -> None:
        super().__init__()
        self.vae = vae
        self.residual_net = residual_net
        # 冻结 VAE encoder，推理时不需要
        for p in vae.enc_conv.parameters():
            p.requires_grad = False
        for p in vae.enc_fc.parameters():
            p.requires_grad = False
        for p in vae.fc_mu.parameters():
            p.requires_grad = False
        for p in vae.fc_logvar.parameters():
            p.requires_grad = False

    def forward(
        self,
        z_base: torch.Tensor,
        params: torch.Tensor,
    ) -> torch.Tensor:
        """从基准 latent + 器件参数重建频谱。

        Args:
            z_base: (B, latent_dim) 或 (latent_dim,)
            params: (B, 6) 器件参数
        Returns:
            S_recon: (B, 1, N)
        """
        if z_base.dim() == 1:
            z_base = z_base.unsqueeze(0)
        delta_z = self.residual_net(params)
        z = z_base + delta_z
        return self.vae.decode(z)
