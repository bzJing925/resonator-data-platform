"""PINN 推理模块：为已训练模型提供快速 S11_dB 预测。

用法:
    from app.ml.inference import predict_s11_db
    freq_ghz, s11_db = predict_s11_db(device)

模型产物（由 scripts/train_pinn_spectrum.py 生成）:
    - app/ml/checkpoints/vae.pt
    - app/ml/checkpoints/residual_net.pt
    - app/ml/checkpoints/base_latents.json
    - app/ml/checkpoints/params_norm.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import torch
    from app.ml.models import ResidualNet, SpectralVAE
    from app.models import Device

log = logging.getLogger("aln")

# ---------------------------------------------------------------------------
# 全局懒加载状态
# ---------------------------------------------------------------------------
_MODELS_LOADED = False
_VAE: SpectralVAE | None = None
_RESIDUAL_NET: ResidualNet | None = None
_DEVICE: torch.device | None = None

# 元数据
_Z_BASE: torch.Tensor | None = None      # (latent_dim,)
_PARAMS_MEAN: torch.Tensor | None = None  # (6,)
_PARAMS_STD: torch.Tensor | None = None   # (6,)
_N_FREQ: int = 1001
_LATENT_DIM: int = 8


def _get_checkpoint_dir() -> Path:
    """返回模型检查点目录。"""
    return Path(__file__).parent / "checkpoints"


def _init_device() -> "torch.device":
    """选择最优推理设备。"""
    import torch
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def load_models(force: bool = False) -> bool:
    """懒加载 VAE 和 ResidualNet。

    Returns:
        True 表示加载成功，False 表示检查点不存在或加载失败。
    """
    global _MODELS_LOADED, _VAE, _RESIDUAL_NET, _DEVICE
    global _Z_BASE, _PARAMS_MEAN, _PARAMS_STD, _N_FREQ, _LATENT_DIM

    if _MODELS_LOADED and not force:
        return True

    ckpt_dir = _get_checkpoint_dir()
    vae_path = ckpt_dir / "vae.pt"
    residual_path = ckpt_dir / "residual_net.pt"
    base_latents_path = ckpt_dir / "base_latents.json"
    params_norm_path = ckpt_dir / "params_norm.json"

    if not vae_path.exists() or not residual_path.exists():
        log.warning("PINN 检查点不存在，跳过加载")
        return False

    _DEVICE = _init_device()

    # 读取元数据推断维度
    if base_latents_path.exists():
        with open(base_latents_path, "r", encoding="utf-8") as f:
            base_meta = json.load(f)
        # 取第一个 batch 的 z_base 长度作为 latent_dim
        first_key = next(iter(base_meta))
        _LATENT_DIM = len(base_meta[first_key]["z_base"])
        _Z_BASE = torch.tensor(base_meta[first_key]["z_base"], dtype=torch.float32)

    if params_norm_path.exists():
        with open(params_norm_path, "r", encoding="utf-8") as f:
            norm_stats = json.load(f)
        _PARAMS_MEAN = torch.tensor(norm_stats["mean"], dtype=torch.float32)
        _PARAMS_STD = torch.tensor(norm_stats["std"], dtype=torch.float32)

    import torch
    import torch.nn.functional as F
    from app.ml.models import ResidualNet, SpectralVAE

    # 构造模型并加载权重
    _VAE = SpectralVAE(n_freq=_N_FREQ, latent_dim=_LATENT_DIM).to(_DEVICE)
    _RESIDUAL_NET = ResidualNet(latent_dim=_LATENT_DIM).to(_DEVICE)

    _VAE.load_state_dict(torch.load(vae_path, map_location=_DEVICE, weights_only=True))
    _RESIDUAL_NET.load_state_dict(
        torch.load(residual_path, map_location=_DEVICE, weights_only=True)
    )

    _VAE.eval()
    _RESIDUAL_NET.eval()

    if _Z_BASE is not None:
        _Z_BASE = _Z_BASE.to(_DEVICE)
    if _PARAMS_MEAN is not None:
        _PARAMS_MEAN = _PARAMS_MEAN.to(_DEVICE)
    if _PARAMS_STD is not None:
        _PARAMS_STD = _PARAMS_STD.to(_DEVICE)

    _MODELS_LOADED = True
    log.info(
        "PINN 模型加载完成: device=%s, latent_dim=%d, n_freq=%d",
        _DEVICE, _LATENT_DIM, _N_FREQ,
    )
    return True


def _device_to_param_tensor(device: "Device") -> "torch.Tensor | None":
    """将 ORM Device 转换为 ResidualNet 输入张量 (6,)。

    需要字段: area_um2, x, y, eg, fl, ag。
    任一字段缺失则返回 None（触发 fallback）。
    """
    import torch
    vals = [
        device.area_um2,
        device.x,
        device.y,
        device.eg,
        device.fl,
        device.ag,
    ]
    if any(v is None for v in vals):
        return None
    return torch.tensor([float(v) for v in vals], dtype=torch.float32)


def predict_s11_db(device: "Device") -> tuple[list[float], list[float]] | None:
    """通过 PINN 快速预测器件的 S11_dB 频谱。

    Args:
        device: SQLAlchemy Device ORM 对象。

    Returns:
        (freq_ghz, s11_db) 或 None（模型未加载 / 参数缺失 / 推理失败）。
    """
    if not load_models():
        return None

    assert _VAE is not None
    assert _RESIDUAL_NET is not None
    assert _Z_BASE is not None
    assert _PARAMS_MEAN is not None
    assert _PARAMS_STD is not None
    assert _DEVICE is not None

    params = _device_to_param_tensor(device)
    if params is None:
        return None

    # 推断频率范围
    f_start = device.batch.f_start_ghz if (device.batch and device.batch.f_start_ghz) else 4.0
    f_end = device.batch.f_end_ghz if (device.batch and device.batch.f_end_ghz) else 7.0
    freq_ghz = np.linspace(f_start, f_end, _N_FREQ).tolist()

    try:
        import torch
        with torch.no_grad():
            params_norm = (params.to(_DEVICE) - _PARAMS_MEAN) / _PARAMS_STD
            delta_z = _RESIDUAL_NET(params_norm.unsqueeze(0))  # (1, latent_dim)
            z = _Z_BASE.unsqueeze(0) + delta_z                 # (1, latent_dim)
            recon = _VAE.decode(z)                             # (1, 1, N)
            s11_db = recon.squeeze().cpu().numpy().tolist()    # (N,)
        return freq_ghz, s11_db
    except Exception:
        log.exception("PINN 推理失败: device_id=%s", device.id)
        return None
