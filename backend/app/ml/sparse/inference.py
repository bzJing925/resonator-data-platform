"""稀疏采样重建推理入口。

提供从 S1P 文件 → 分区 → 采样 → 重建的完整 pipeline。
支持按压电层厚度路由到对应模型。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import torch

    from app.ml.sparse.reconstructor import SparseReconstructor
    from app.ml.sparse.sampler import AdaptiveSampler

log = logging.getLogger("aln")

# ---------------------------------------------------------------------------
# 全局懒加载状态
# ---------------------------------------------------------------------------
_MODELS_LOADED: dict[str, bool] = {}
_RECONSTRUCTOR: dict[str, SparseReconstructor | None] = {}
_SAMPLER: dict[str, AdaptiveSampler | None] = {}
_DEVICE: torch.device | None = None


def _get_checkpoint_dir(piezo: str) -> Path:
    """返回指定压电层厚度的模型检查点目录。"""
    return Path(__file__).parent.parent / "checkpoints" / "sparse" / f"{piezo}nm"


def _init_device() -> torch.device:
    import torch

    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def load_models(piezo: str = "308", force: bool = False) -> bool:
    """懒加载指定压电层厚度的模型。

    Args:
        piezo: 压电层厚度，如 "308" 或 "325"
        force: 强制重新加载

    Returns:
        True 表示加载成功
    """
    global _DEVICE

    if piezo in _MODELS_LOADED and _MODELS_LOADED[piezo] and not force:
        return True

    ckpt_dir = _get_checkpoint_dir(piezo)
    recon_path = ckpt_dir / "reconstructor.pt"
    sampler_path = ckpt_dir / "sampler.pt"
    config_path = ckpt_dir / "config.json"

    if not recon_path.exists():
        log.warning(f"稀疏重建模型不存在: {recon_path}")
        return False

    import torch

    from app.ml.sparse.reconstructor import SparseReconstructor
    from app.ml.sparse.sampler import AdaptiveSampler

    if _DEVICE is None:
        _DEVICE = _init_device()

    # 读取配置
    config = {"d_model": 64, "n_encoder_layers": 4, "n_heads": 4}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            config.update(json.load(f))

    # 加载重建网络
    recon = SparseReconstructor(
        d_model=config.get("d_model", 64),
        n_encoder_layers=config.get("n_encoder_layers", 4),
        n_heads=config.get("n_heads", 4),
    ).to(_DEVICE)
    recon.load_state_dict(torch.load(recon_path, map_location=_DEVICE, weights_only=True))
    recon.eval()
    _RECONSTRUCTOR[piezo] = recon

    # 加载采样器（可选）
    if sampler_path.exists():
        sampler = AdaptiveSampler(n_freq=config.get("n_freq", 1001)).to(_DEVICE)
        sampler.load_state_dict(torch.load(sampler_path, map_location=_DEVICE, weights_only=True))
        sampler.eval()
        _SAMPLER[piezo] = sampler
    else:
        _SAMPLER[piezo] = None

    _MODELS_LOADED[piezo] = True
    log.info(f"稀疏重建模型加载完成: piezo={piezo}nm, device={_DEVICE}")
    return True


def _s1p_to_z11_db(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """读取 S1P，返回插值到 1001 点的 (freq_ghz, z11_db)。

    必须与训练时 dataset.py 中的 s1p_to_z11_db 保持一致（统一 1001 频点）。
    """
    from app.ml.sparse.dataset import s1p_to_z11_db as _dataset_s1p_to_z11

    return _dataset_s1p_to_z11(path)


def predict_z11_sparse(
    s1p_path: str | Path,
    cond: dict[str, float] | None = None,
    piezo: str = "308",
    target_k: int = 300,
    use_learned_sampler: bool = False,
) -> dict[str, object] | None:
    """从 S1P 文件预测完整 Z11 频谱（稀疏采样重建）。

    Args:
        s1p_path: S1P 文件路径
        cond: 预计算的 {fs, fp, Qs, Qp, kt2}，None 时从 S1P 自动提取
        piezo: 压电层厚度模型选择
        target_k: 目标采样点数；传 0 表示使用自适应采样，此时强制启用 learned sampler
        use_learned_sampler: 是否使用训练好的 AdaptiveSampler（False=用固定规则）

    Returns:
        {
            "freq_ghz": [...],
            "z_pred": [...],
            "z_true": [...],
            "sample_points": [{"freq_ghz": ..., "z_db": ..., "region": ...}, ...],
            "regions": {"main": [...], "spurious": [...], "smooth": [...]},
            "cond": {"fs": ..., "fp": ..., "Qs": ..., "Qp": ..., "kt2": ...},
        }
        或 None（模型未加载 / 失败）
    """
    # target_k=0 表示使用自适应采样，必须启用 learned sampler
    if target_k == 0:
        use_learned_sampler = True
        target_k = 300  # 内部用 300 作为最大 padding 长度

    if not load_models(piezo):
        return None

    recon = _RECONSTRUCTOR.get(piezo)
    if recon is None or _DEVICE is None:
        return None

    try:
        # 1. 读取 S1P
        freq, z_db = _s1p_to_z11_db(s1p_path)

        # 2. 提取/准备条件参数
        if cond is None:
            from app.ml.sparse.dataset import extract_five_params

            cond = extract_five_params(s1p_path)

        import torch

        cond_vec = (
            torch.tensor(
                [cond["fs"], cond["fp"], cond["Qs"], cond["Qp"], cond["kt2"]],
                dtype=torch.float32,
            )
            .unsqueeze(0)
            .to(_DEVICE)
        )  # (1, 5)

        # 3. 分区
        from app.ml.sparse.region_partition import partition_regions

        region_mask = partition_regions(z_db, freq)
        region_ids = np.zeros(len(freq), dtype=np.int64)
        region_ids[region_mask["main"]] = 0
        region_ids[region_mask["spurious"]] = 1
        region_ids[region_mask["smooth"]] = 2

        # 4. 采样
        if use_learned_sampler and _SAMPLER.get(piezo) is not None:
            sampler = _SAMPLER[piezo]
            z_t = torch.from_numpy(z_db).float().unsqueeze(0).to(_DEVICE)
            rid_t = torch.from_numpy(region_ids).long().unsqueeze(0).to(_DEVICE)
            freq_t = torch.from_numpy(freq).float().unsqueeze(0).to(_DEVICE)
            fs_t = torch.tensor([cond["fs"]], dtype=torch.float32, device=_DEVICE)
            fp_t = torch.tensor([cond["fp"]], dtype=torch.float32, device=_DEVICE)
            with torch.no_grad():
                p_norm, _, k_pred = sampler(
                    z_t,
                    rid_t,
                    freq=freq_t,
                    fs=fs_t,
                    fp=fp_t,
                    target_k=target_k,
                    use_gumbel=False,
                )
                k_val = int(k_pred[0].item())
                mask, k_actual = sampler.sample_points(p_norm, k=k_val)
                sample_idx = torch.where(mask[0])[0].cpu().numpy()
            print(f"[采样] 自适应 K={k_actual}")
        else:
            # 固定规则采样
            from app.ml.sparse.dataset import fixed_rule_sample

            sf, sz = fixed_rule_sample(
                freq,
                z_db,
                region_mask,
                target_k,
                fs=cond["fs"],
                fp=cond["fp"],
            )
            sample_idx = np.searchsorted(freq, sf)
            sample_idx = np.clip(sample_idx, 0, len(freq) - 1)

        # 5. 构建稀疏点输入 + 基线插值
        if len(sample_idx) == 0:
            log.warning(f"采样点为空，回退到固定规则采样: {s1p_path}")
            from app.ml.sparse.dataset import fixed_rule_sample

            sf, sz = fixed_rule_sample(
                freq,
                z_db,
                region_mask,
                target_k if target_k > 0 else 300,
                fs=cond["fs"],
                fp=cond["fp"],
            )
            sample_idx = np.searchsorted(freq, sf)
            sample_idx = np.clip(sample_idx, 0, len(freq) - 1)

        sample_freq = torch.from_numpy(freq[sample_idx]).float().unsqueeze(0).to(_DEVICE)
        sample_z = torch.from_numpy(z_db[sample_idx]).float().unsqueeze(0).to(_DEVICE)
        samples = torch.stack([sample_freq, sample_z], dim=-1)

        freq_target = torch.from_numpy(freq).float().to(_DEVICE)

        # 基线插值（cubic spline）
        from app.ml.sparse.dataset import baseline_interpolate

        z_baseline_np = baseline_interpolate(freq[sample_idx], z_db[sample_idx], freq)
        z_baseline = torch.from_numpy(z_baseline_np).float().unsqueeze(0).to(_DEVICE)

        # 6. 重建（残差结构）
        with torch.no_grad():
            z_pred = recon(cond_vec, samples, freq_target, z_baseline=z_baseline)
            z_pred = z_pred.squeeze(0).cpu().numpy()

        # 7. 构造采样点详情
        sample_points = []
        for idx in sample_idx:
            r = "main"
            if region_mask["spurious"][idx]:
                r = "spurious"
            elif region_mask["smooth"][idx]:
                r = "smooth"
            sample_points.append(
                {
                    "freq_ghz": float(freq[idx]),
                    "z_db": float(z_db[idx]),
                    "region": r,
                }
            )

        return {
            "freq_ghz": freq.tolist(),
            "z_pred": z_pred.tolist(),
            "z_true": z_db.tolist(),
            "sample_points": sample_points,
            "regions": {
                "main": np.where(region_mask["main"])[0].tolist(),
                "spurious": np.where(region_mask["spurious"])[0].tolist(),
                "smooth": np.where(region_mask["smooth"])[0].tolist(),
            },
            "cond": cond,
        }

    except Exception:
        log.exception("稀疏重建推理失败: %s", s1p_path)
        return None
