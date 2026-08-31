"""PINN 物理约束损失函数。

结合数据驱动重建损失与谐振器物理先验（同 batch 一致性、频谱平滑性、
fs/fp 顺序约束、远带回归基准）。
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as f


class PINNSpectralLoss(nn.Module):
    """Spectral Residual PINN 综合损失。

    Args:
        lambda_coherence: 同 batch latent 一致性权重。
        lambda_smooth: 频谱平滑性权重。
        lambda_order: fs < fp 顺序约束权重。
        lambda_far_band: 远带回归基准权重。
        lambda_kl: VAE KL 散度权重。
    """

    def __init__(
        self,
        lambda_coherence: float = 0.1,
        lambda_smooth: float = 0.01,
        lambda_order: float = 10.0,
        lambda_far_band: float = 0.05,
        lambda_kl: float = 0.001,
    ) -> None:
        super().__init__()
        self.lambda_coherence = lambda_coherence
        self.lambda_smooth = lambda_smooth
        self.lambda_order = lambda_order
        self.lambda_far_band = lambda_far_band
        self.lambda_kl = lambda_kl

    def forward(
        self,
        s_pred: torch.Tensor,
        s_true: torch.Tensor,
        z: torch.Tensor,
        z_base: torch.Tensor,
        fs_pred: torch.Tensor | None = None,
        fp_pred: torch.Tensor | None = None,
        s_base: torch.Tensor | None = None,
        freq: torch.Tensor | None = None,
        mu: torch.Tensor | None = None,
        logvar: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, dict[str, float]]:
        """计算综合损失。

        Args:
            S_pred: 重建频谱 (B, 1, N)。
            S_true: 真实频谱 (B, 1, N)。
            z: 编码 latent (B, D)。
            z_base: 基准 latent (B, D) 或 (D,)。
            fs_pred: 预测的串联谐振频率 (B,)，单位 GHz。
            fp_pred: 预测的并联谐振频率 (B,)，单位 GHz。
            S_base: 基准频谱 (B, 1, N) 或 (1, N)。
            freq: 频率轴 (N,)，单位 GHz。
            mu: VAE 编码均值 (B, D)。
            logvar: VAE 编码对数方差 (B, D)。

        Returns:
            (total_loss, loss_dict)
        """
        losses: dict[str, torch.Tensor] = {}

        # 1. 重建损失
        losses["recon"] = f.mse_loss(s_pred, s_true)

        # 2. 同 batch latent 一致性（压电层厚度相同 → 频谱整体相似）
        if z_base is not None:
            if z_base.dim() == 1:
                z_base = z_base.unsqueeze(0).expand(z.shape[0], -1)
            losses["coherence"] = f.mse_loss(z, z_base)
        else:
            losses["coherence"] = torch.tensor(0.0, device=z.device)

        # 3. 频谱平滑性（S参数是解析函数，二阶导应小）
        d2s = s_pred[:, :, 2:] - 2 * s_pred[:, :, 1:-1] + s_pred[:, :, :-2]
        losses["smoothness"] = torch.mean(d2s**2)

        # 4. fs < fp 顺序约束（物理合法性）
        if fs_pred is not None and fp_pred is not None:
            # ReLU(fs - fp) > 0 时惩罚
            violations = f.relu(fs_pred - fp_pred)
            losses["fs_fp_order"] = violations.mean()
        else:
            losses["fs_fp_order"] = torch.tensor(0.0, device=z.device)

        # 5. 远带回归基准（远离谐振点处应接近基准频谱）
        if s_base is not None and freq is not None and fs_pred is not None and fp_pred is not None:
            losses["far_band"] = self._far_band_loss(s_pred, s_base, freq, fs_pred, fp_pred)
        else:
            losses["far_band"] = torch.tensor(0.0, device=z.device)

        # 6. VAE KL 散度
        if mu is not None and logvar is not None:
            losses["kl"] = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp()) / mu.shape[0]
        else:
            losses["kl"] = torch.tensor(0.0, device=z.device)

        # 加权求和
        total = (
            losses["recon"]
            + self.lambda_coherence * losses["coherence"]
            + self.lambda_smooth * losses["smoothness"]
            + self.lambda_order * losses["fs_fp_order"]
            + self.lambda_far_band * losses["far_band"]
            + self.lambda_kl * losses["kl"]
        )

        loss_dict = {k: float(v.item()) for k, v in losses.items()}
        loss_dict["total"] = float(total.item())

        return total, loss_dict

    def _far_band_loss(
        self,
        s_pred: torch.Tensor,
        s_base: torch.Tensor,
        freq: torch.Tensor,
        fs_pred: torch.Tensor,
        fp_pred: torch.Tensor,
    ) -> torch.Tensor:
        """远带约束：远离谐振点处，预测频谱应接近基准频谱。

        定义远带为 freq < 0.8*fs 或 freq > 1.2*fp 的区域。
        """
        b, _, n = s_pred.shape
        freq = freq.to(s_pred.device)

        # 扩展 fs/fp 到 (B, 1, 1)
        fs = fs_pred.view(b, 1, 1)
        fp = fp_pred.view(b, 1, 1)
        freq_expanded = freq.view(1, 1, n)

        # 远带掩码
        far_mask = (freq_expanded < 0.8 * fs) | (freq_expanded > 1.2 * fp)  # (B, 1, N)

        if s_base.dim() == 2:
            s_base = s_base.unsqueeze(0).expand(b, -1, -1)

        diff = (s_pred - s_base) * far_mask.float()
        # 只对远带区域求 MSE
        count = far_mask.sum().clamp(min=1.0)
        return (diff**2).sum() / count
