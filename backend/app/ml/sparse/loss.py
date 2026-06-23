"""稀疏采样重建损失函数（区域加权版）。

L_total = L_recon_weighted + λ_peak·L_peak + λ_smooth·L_smooth + λ_phys·L_phys + λ_count·L_count
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class SparseReconLoss(nn.Module):
    """稀疏采样重建综合损失（区域加权 + 峰感知）。

    Args:
        lambda_peak: 峰感知梯度匹配权重
        lambda_smooth: 频谱平滑性正则权重
        lambda_phys: 物理约束权重
        lambda_count: 采样点数偏离惩罚权重
        main_weight: 主模区重建权重（相对平滑区）
        spur_weight: 杂模区重建权重
    """

    def __init__(
        self,
        lambda_peak: float = 1.0,
        lambda_smooth: float = 0.01,
        lambda_phys: float = 0.5,
        lambda_count: float = 0.1,
        lambda_efficiency: float = 0.5,
        main_weight: float = 5.0,
        spur_weight: float = 3.0,
    ):
        super().__init__()
        self.lambda_peak = lambda_peak
        self.lambda_smooth = lambda_smooth
        self.lambda_phys = lambda_phys
        self.lambda_count = lambda_count
        self.lambda_efficiency = lambda_efficiency
        self.main_weight = main_weight
        self.spur_weight = spur_weight

    def forward(
        self,
        z_pred: torch.Tensor,      # (B, N)
        z_true: torch.Tensor,      # (B, N)
        region_ids: torch.Tensor,  # (B, N) 0=main, 1=spur, 2=smooth
        target_k: int,
        freq: torch.Tensor,        # (B, N) or (N,)
        fs_true: torch.Tensor | None = None,
        fp_true: torch.Tensor | None = None,
        k_actual: torch.Tensor | None = None,
        k_pred: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, dict[str, float]]:
        """
        Returns:
            total_loss, loss_dict
        """
        B, N = z_pred.shape

        # 1. 区域加权重建损失
        W = torch.ones_like(z_pred)
        W[region_ids == 0] = self.main_weight   # 主模区
        W[region_ids == 1] = self.spur_weight   # 杂模区
        # 平滑区保持 1.0

        diff_sq = (z_pred - z_true) ** 2
        recon_loss = (W * diff_sq).sum() / W.sum()

        # 2. 峰感知损失（主模区内的梯度匹配）
        peak_loss = torch.tensor(0.0, device=z_pred.device)
        if freq.dim() == 2:
            main_mask = (region_ids == 0).float()  # (B, N)
            if main_mask.sum() > 0:
                # 计算一阶梯度（沿频率轴）
                dz_pred = torch.zeros_like(z_pred)
                dz_true = torch.zeros_like(z_true)
                # 中心差分
                dz_pred[:, 1:-1] = (z_pred[:, 2:] - z_pred[:, :-2]) / 2.0
                dz_true[:, 1:-1] = (z_true[:, 2:] - z_true[:, :-2]) / 2.0
                # 只在主模区匹配梯度
                peak_loss = (main_mask * (dz_pred - dz_true) ** 2).sum() / main_mask.sum().clamp(min=1)

        # 3. 频谱平滑性
        smooth_loss = torch.mean((z_pred[:, 2:] - 2 * z_pred[:, 1:-1] + z_pred[:, :-2]) ** 2)

        # 4. 物理约束
        phys_loss = torch.tensor(0.0, device=z_pred.device)
        if fs_true is not None and fp_true is not None and freq.dim() == 2:
            fs_pred_idx = torch.argmin(z_pred, dim=1)
            fp_pred_idx = torch.argmax(z_pred, dim=1)
            fs_pred = torch.gather(freq, 1, fs_pred_idx.unsqueeze(1)).squeeze(1)
            fp_pred = torch.gather(freq, 1, fp_pred_idx.unsqueeze(1)).squeeze(1)

            order_loss = F.relu(fs_pred - fp_pred).mean()
            fs_mse = F.mse_loss(fs_pred, fs_true)
            fp_mse = F.mse_loss(fp_pred, fp_true)
            phys_loss = order_loss + fs_mse + fp_mse

        # 5. 采样点数偏离惩罚（用 k_pred 保证梯度）
        count_loss = torch.tensor(0.0, device=z_pred.device)
        if k_pred is not None:
            count_loss = torch.mean(torch.abs(k_pred - target_k)) / target_k

        # 6. 采样效率奖励（单位采样点的重建精度，用 k_pred 保证梯度）
        efficiency_loss = torch.tensor(0.0, device=z_pred.device)
        if k_pred is not None:
            # 效率 = 重建误差 / 预测采样点数，越小越好
            # 惩罚高采样点数但误差大的情况
            efficiency = recon_loss / (k_pred.mean() / 200.0)  # 归一化到 K=200 基准
            efficiency_loss = torch.log(1.0 + efficiency)

        total = (
            recon_loss
            + self.lambda_peak * peak_loss
            + self.lambda_smooth * smooth_loss
            + self.lambda_phys * phys_loss
            + self.lambda_count * count_loss
            + self.lambda_efficiency * efficiency_loss
        )

        loss_dict = {
            "total": total.item(),
            "recon": recon_loss.item(),
            "peak": peak_loss.item(),
            "smooth": smooth_loss.item(),
            "phys": phys_loss.item(),
            "count": count_loss.item(),
            "efficiency": efficiency_loss.item(),
        }

        return total, loss_dict
