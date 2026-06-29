"""PINN 重建效果可视化。

随机抽取 4 个器件，对比：
- 原始频谱 vs PINN 重建频谱
- SmartSampler 降采样后保留的点
- 残差曲线

用法:
    python scripts/visualize_pinn.py \
        --checkpoint-dir ./pinn_outputs \
        --s1p-dir /Users/jingbozuo/Desktop/aln-data-master/#3 \
        --n-samples 4 \
        --output ./recon_samples.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

# 把 backend 目录加入路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.dataset import RealS1PBatchDataset  # noqa: E402
from app.ml.models import ResidualNet, SmartSampler, SpectralVAE  # noqa: E402
from app.ml.utils import enforce_critical_points_mask, numerical_gradients  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PINN 重建效果可视化")
    parser.add_argument(
        "--checkpoint-dir",
        type=str,
        required=True,
        help="训练输出目录（含 vae.pt / residual_net.pt / smart_sampler.pt / params_norm.json）",
    )
    parser.add_argument(
        "--s1p-dir",
        type=str,
        required=True,
        help="s1p 文件目录",
    )
    parser.add_argument("--n-samples", type=int, default=4, help="可视化样本数")
    parser.add_argument("--n-freq", type=int, default=1001, help="频点数")
    parser.add_argument("--latent-dim", type=int, default=8, help="latent 维度")
    parser.add_argument("--target-k", type=int, default=500, help="降采样目标点数")
    parser.add_argument(
        "--output",
        type=str,
        default="./recon_samples.png",
        help="输出 PNG 路径",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        help="计算设备：auto | cuda | cpu | mps",
    )
    return parser.parse_args()


def get_device(preference: str) -> torch.device:
    if preference == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        elif torch.backends.mps.is_available():
            return torch.device("mps")
        else:
            return torch.device("cpu")
    return torch.device(preference)


@torch.no_grad()
def main() -> None:
    args = parse_args()
    device = get_device(args.device)
    ckpt_dir = Path(args.checkpoint_dir)

    # 加载模型
    print("加载模型...")
    vae = SpectralVAE(n_freq=args.n_freq, latent_dim=args.latent_dim).to(device)
    residual_net = ResidualNet(latent_dim=args.latent_dim).to(device)
    smart_sampler = SmartSampler(n_freq=args.n_freq).to(device)

    vae.load_state_dict(torch.load(ckpt_dir / "vae.pt", map_location=device))
    residual_net.load_state_dict(
        torch.load(ckpt_dir / "residual_net.pt", map_location=device)
    )
    smart_sampler.load_state_dict(
        torch.load(ckpt_dir / "smart_sampler.pt", map_location=device)
    )
    vae.eval()
    residual_net.eval()
    smart_sampler.eval()

    # 加载归一化统计
    with open(ckpt_dir / "params_norm.json", encoding="utf-8") as f:
        norm_stats = json.load(f)
    params_mean = torch.tensor(norm_stats["mean"], dtype=torch.float32, device=device)
    params_std = torch.tensor(norm_stats["std"], dtype=torch.float32, device=device)

    # 加载基准 latent
    with open(ckpt_dir / "base_latents.json", encoding="utf-8") as f:
        base_meta = json.load(f)
    z_base = torch.tensor(base_meta["0"]["z_base"], dtype=torch.float32, device=device)

    # 加载数据集
    dataset = RealS1PBatchDataset(
        s1p_dir=args.s1p_dir,
        n_freq=args.n_freq,
        latent_dim=args.latent_dim,
    )

    # 随机选样本
    rng = np.random.default_rng(42)
    indices = rng.choice(len(dataset), size=min(args.n_samples, len(dataset)), replace=False)

    # 准备绘图数据
    samples: list[dict] = []
    for idx in indices:
        item = dataset[idx]
        spectrum = item["spectrum"].unsqueeze(0).to(device)  # (1, 1, N)
        params = item["params"].unsqueeze(0).to(device)      # (1, 6)
        params_orig = params * params_std + params_mean      # 反归一化

        # 重建
        delta_z = residual_net(params)
        z = z_base + delta_z
        recon = vae.decode(z)  # (1, 1, N)

        # 降采样
        grads = numerical_gradients(recon)
        p = smart_sampler(grads)
        mask = enforce_critical_points_mask(p, recon, args.target_k)

        samples.append({
            "filename": dataset.devices[idx]["filename"],
            "original": spectrum.squeeze().cpu().numpy(),
            "recon": recon.squeeze().cpu().numpy(),
            "mask": mask.squeeze().cpu().numpy(),
            "params": params_orig.squeeze().cpu().numpy(),
        })

    # 尝试用 matplotlib 绘图；若不可用则输出文本报告
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(args.n_samples, 2, figsize=(14, 3 * args.n_samples))
        if args.n_samples == 1:
            axes = axes.reshape(1, -1)

        freq_ghz = np.linspace(4.0, 7.0, args.n_freq)

        for i, s in enumerate(samples):
            ax_orig = axes[i, 0]
            ax_resid = axes[i, 1]

            # 左图：原始 vs 重建
            ax_orig.plot(freq_ghz, s["original"], "b-", alpha=0.7, label="Original")
            ax_orig.plot(freq_ghz, s["recon"], "orange", lw=2, label="PINN Recon")
            # 标记降采样保留点
            keep_idx = np.where(s["mask"])[0]
            ax_orig.scatter(
                freq_ghz[keep_idx],
                s["original"][keep_idx],
                c="red",
                s=8,
                zorder=5,
                label=f"Kept {len(keep_idx)} pts",
            )
            ax_orig.set_title(f"{s['filename']}")
            ax_orig.set_xlabel("Freq (GHz)")
            ax_orig.set_ylabel("S11 (dB)")
            ax_orig.legend(loc="upper right", fontsize=7)
            ax_orig.grid(True, alpha=0.3)

            # 右图：残差
            residual = s["original"] - s["recon"]
            ax_resid.plot(freq_ghz, residual, "g-", alpha=0.7)
            ax_resid.axhline(0, color="k", ls="--", lw=0.5)
            rmse = float(np.sqrt(np.mean(residual**2)))
            ax_resid.set_title(f"Residual (RMSE={rmse:.3f} dB)")
            ax_resid.set_xlabel("Freq (GHz)")
            ax_resid.set_ylabel("ΔS11 (dB)")
            ax_resid.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(args.output, dpi=150)
        print(f"可视化结果已保存: {args.output}")

    except ImportError:
        print("matplotlib 未安装，输出文本报告：")
        for s in samples:
            residual = s["original"] - s["recon"]
            rmse = float(np.sqrt(np.mean(residual**2)))
            print(f"  {s['filename']}: RMSE={rmse:.3f} dB, kept={s['mask'].sum()}/{len(s['mask'])}")


if __name__ == "__main__":
    main()
