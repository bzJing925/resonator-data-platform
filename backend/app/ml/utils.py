"""PINN 训练辅助工具：基准选择、频谱预处理、评估指标。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import torch
import torch.nn.functional as f

if TYPE_CHECKING:
    from numpy.typing import NDArray


def select_base_device(devices: list[dict]) -> dict:
    """从同 batch 器件中选基准器件：fs 中位数 + 面积中位数。

    Args:
        devices: 同 batch 的器件列表，每个元素含 "fs_ghz"、"area_um2" 等字段。

    Returns:
        被选中的基准器件字典。
    """
    if not devices:
        raise ValueError("devices 为空")

    # 先按 fs_ghz 排序取中位数
    fs_vals = [d.get("fs_ghz") for d in devices if d.get("fs_ghz") is not None]
    if not fs_vals:
        return devices[len(devices) // 2]

    fs_median = float(np.median(fs_vals))

    # 找 fs 最接近中位数的子集
    sorted_by_fs = sorted(
        devices,
        key=lambda d: abs((d.get("fs_ghz") or fs_median) - fs_median),
    )
    candidates = sorted_by_fs[: max(1, len(sorted_by_fs) // 5)]

    # 在候选集中找面积中位数
    area_vals = [d.get("area_um2") for d in candidates if d.get("area_um2") is not None]
    if area_vals:
        area_median = float(np.median(area_vals))
        base = min(
            candidates,
            key=lambda d: abs((d.get("area_um2") or area_median) - area_median),
        )
    else:
        base = candidates[0]

    return base


def preprocess_spectrum(
    raw: NDArray[np.float64],
    target_len: int = 1001,
) -> torch.Tensor:
    """将原始频谱预处理为统一长度、归一化的张量。

    步骤：
    1. 线性插值到 target_len 个频点
    2. 去均值（去直流）
    3. 除以标准差归一化

    Args:
        raw: 原始频谱向量，shape (N,)。
        target_len: 目标频点数。

    Returns:
        预处理后的张量，shape (1, target_len)。
    """
    if raw.ndim != 1:
        raise ValueError(f"raw 必须是一维数组， got shape {raw.shape}")

    # 插值到统一长度
    if len(raw) != target_len:
        old_x = np.linspace(0, 1, len(raw))
        new_x = np.linspace(0, 1, target_len)
        interp = np.interp(new_x, old_x, raw)
    else:
        interp = raw

    # 去均值 + 归一化
    mean = float(np.mean(interp))
    std = float(np.std(interp))
    if std < 1e-8:
        std = 1.0
    normed = (interp - mean) / std

    tensor = torch.from_numpy(normed).float().unsqueeze(0)  # (1, target_len)
    return tensor


def compute_ssim(
    s1: torch.Tensor,
    s2: torch.Tensor,
    window_size: int = 11,
) -> torch.Tensor:
    """计算一维频谱的 SSIM（结构相似性指数）。

    将 1D 频谱视为 1×L 的"图像"，使用高斯滑窗计算局部 SSIM。

    Args:
        s1, s2: 输入频谱，shape (B, 1, L)。
        window_size: 高斯滑窗大小。

    Returns:
        平均 SSIM 标量张量。
    """
    b, c, length = s1.shape
    if c != 1:
        raise ValueError("SSIM 目前只支持单通道")

    # 构造 1D 高斯核
    sigma = 1.5
    gauss = torch.tensor(
        [np.exp(-((x - window_size // 2) ** 2) / (2 * sigma**2)) for x in range(window_size)],
        dtype=torch.float32,
        device=s1.device,
    )
    gauss = gauss / gauss.sum()
    window = gauss.view(1, 1, -1)  # (1, 1, window_size)

    c1 = 0.01**2
    c2 = 0.03**2

    mu1 = f.conv1d(s1, window, padding=window_size // 2, groups=1)
    mu2 = f.conv1d(s2, window, padding=window_size // 2, groups=1)

    mu1_sq = mu1**2
    mu2_sq = mu2**2
    mu1_mu2 = mu1 * mu2

    sigma1_sq = f.conv1d(s1 * s1, window, padding=window_size // 2, groups=1) - mu1_sq
    sigma2_sq = f.conv1d(s2 * s2, window, padding=window_size // 2, groups=1) - mu2_sq
    sigma12 = f.conv1d(s1 * s2, window, padding=window_size // 2, groups=1) - mu1_mu2

    ssim_map = ((2 * mu1_mu2 + c1) * (2 * sigma12 + c2)) / (
        (mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2)
    )
    return ssim_map.mean()


def enforce_critical_points_mask(
    p: torch.Tensor,
    s: torch.Tensor,
    target_k: int,
) -> torch.Tensor:
    """在降采样概率上强制保留关键频点。

    强制保留：
    - 频谱起点和终点
    - S11 局部极小值（fs 附近）
    - S11 局部极大值（fp 附近）

    Args:
        p: 保留概率，shape (B, N)。
        s: 频谱值，shape (B, 1, N)。
        target_k: 目标保留点数。

    Returns:
        布尔掩码，shape (B, N)，True 表示保留。
    """
    b, n = p.shape
    mask = torch.zeros_like(p, dtype=torch.bool)

    # 强制保留首尾
    mask[:, 0] = True
    mask[:, -1] = True

    s_flat = s.squeeze(1)  # (B, N)

    for i in range(b):
        spec = s_flat[i].detach().cpu().numpy()

        # 找局部极小（fs）和局部极大（fp）
        # 用一阶差分符号变化检测
        diff = np.diff(spec)
        sign_change = np.diff(np.sign(diff))

        # 局部极小: diff 从负变正 → sign_change = +2
        local_min = np.where(sign_change > 0)[0] + 1
        # 局部极大: diff 从正变负 → sign_change = -2
        local_max = np.where(sign_change < 0)[0] + 1

        # 保留最重要的几个极值点
        if len(local_min) > 0:
            # 按深度排序，保留最深的 2 个
            depths = spec[local_min - 1] - spec[local_min]
            top_min = local_min[np.argsort(depths)[-2:]]
            mask[i, top_min] = True

        if len(local_max) > 0:
            heights = spec[local_max] - spec[local_max - 1]
            top_max = local_max[np.argsort(heights)[-2:]]
            mask[i, top_max] = True

    # 剩余配额按概率 Top-K 分配
    remaining = target_k - mask.sum(dim=1)  # (B,)
    for i in range(b):
        k = int(remaining[i].item())
        if k > 0:
            # 在未被强制的位置中选概率最高的
            exclude = mask[i]
            p_b = p[i].clone()
            p_b[exclude] = -1.0
            _, idx = torch.topk(p_b, k)
            mask[i, idx] = True

    return mask


def numerical_gradients(s: torch.Tensor) -> torch.Tensor:
    """计算频谱的一阶和二阶数值梯度。

    Args:
        s: 频谱张量，shape (B, 1, N)。

    Returns:
        三通道张量 [S, dS/df, d²S/df²]，shape (B, 3, N)。
    """
    b, _, n = s.shape
    # 一阶梯度（中心差分）
    ds = torch.zeros_like(s)
    ds[:, :, 1:-1] = (s[:, :, 2:] - s[:, :, :-2]) / 2.0
    ds[:, :, 0] = s[:, :, 1] - s[:, :, 0]
    ds[:, :, -1] = s[:, :, -1] - s[:, :, -2]

    # 二阶梯度
    d2s = torch.zeros_like(s)
    d2s[:, :, 1:-1] = s[:, :, 2:] - 2 * s[:, :, 1:-1] + s[:, :, :-2]
    d2s[:, :, 0] = d2s[:, :, 1]
    d2s[:, :, -1] = d2s[:, :, -2]

    return torch.cat([s, ds, d2s], dim=1)  # (B, 3, N)
