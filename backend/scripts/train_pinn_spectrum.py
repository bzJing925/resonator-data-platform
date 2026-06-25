"""Spectral Residual PINN + 智能降采样 训练脚本。

用法:
    python scripts/train_pinn_spectrum.py --mode synthetic --epochs 200
    python scripts/train_pinn_spectrum.py --mode db --db-url postgresql+psycopg://aln:aln@localhost:5432/aln

三阶段训练:
    Phase A (前 20 epochs): 冻结 ResidualNet，只训练 VAE 编码器/解码器
    Phase B (中 100 epochs): 联合训练 VAE + ResidualNet + PINN Loss
    Phase C (后 80 epochs):  加入 SmartSampler，联合优化重建 + 降采样 SSIM
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

# 把 backend 目录加入路径，以便 import app.ml
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.dataset import RealS1PBatchDataset, SyntheticSpectrumDataset
from app.ml.losses import PINNSpectralLoss
from app.ml.models import ResidualNet, SmartSampler, SpectralVAE
from app.ml.utils import (
    compute_ssim,
    enforce_critical_points_mask,
    numerical_gradients,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="训练 Spectral Residual PINN")
    parser.add_argument(
        "--mode",
        choices=["synthetic", "db", "s1p-batch"],
        default="synthetic",
        help="数据来源：synthetic=合成数据，db=数据库真实数据，s1p-batch=文件系统s1p批量",
    )
    parser.add_argument("--n-freq", type=int, default=1001, help="频点数")
    parser.add_argument("--latent-dim", type=int, default=12, help="latent space 维度")
    parser.add_argument("--n-batches", type=int, default=10, help="batch 数量（合成模式）")
    parser.add_argument(
        "--n-devices-per-batch", type=int, default=200, help="每 batch 器件数（合成模式）"
    )
    parser.add_argument("--epochs", type=int, default=200, help="总训练轮数")
    parser.add_argument("--batch-size", type=int, default=64, help="训练 batch size")
    parser.add_argument("--lr", type=float, default=1e-3, help="学习率")
    parser.add_argument("--weight-decay", type=float, default=1e-5, help="权重衰减")
    parser.add_argument("--validate-every", type=int, default=1, help="每 N 个 epoch 验证一次")
    parser.add_argument("--early-stop-patience", type=int, default=30, help="早停耐心值（epoch）")
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        help="计算设备：auto | cuda | cpu | mps",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./pinn_outputs",
        help="模型和日志输出目录",
    )
    parser.add_argument(
        "--phase-a-epochs",
        type=int,
        default=20,
        help="Phase A（仅 VAE）epoch 数",
    )
    parser.add_argument(
        "--phase-b-epochs",
        type=int,
        default=100,
        help="Phase B（VAE + ResidualNet）epoch 数",
    )
    parser.add_argument(
        "--phase-c-epochs",
        type=int,
        default=80,
        help="Phase C（加入 SmartSampler）epoch 数",
    )
    parser.add_argument(
        "--target-k",
        type=int,
        default=500,
        help="智能降采样目标保留点数",
    )
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument(
        "--pretrained-vae",
        type=str,
        default="",
        help="预训练 VAE 权重路径（.pt），加载后 Phase A 可跳过或缩短",
    )
    parser.add_argument(
        "--pretrained-residual",
        type=str,
        default="",
        help="预训练 ResidualNet 权重路径（.pt）",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default="postgresql+psycopg://aln:aln@localhost:5432/aln",
        help="数据库连接字符串（db 模式）",
    )
    parser.add_argument(
        "--data-root",
        type=str,
        default="/data3/aln",
        help="数据根目录（db 模式）",
    )
    parser.add_argument(
        "--s1p-dir",
        type=str,
        default="",
        help="s1p 文件目录（s1p-batch 模式）",
    )
    parser.add_argument(
        "--noise-std",
        type=float,
        default=0.0,
        help="数据增强噪声标准差（s1p-batch 模式）",
    )
    parser.add_argument(
        "--group-by-area",
        action="store_true",
        help="按 area 字母拆分 sub-batch（s1p-batch 模式）",
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


def precompute_base_latents(
    dataset: SyntheticSpectrumDataset,
    vae: SpectralVAE,
    device: torch.device,
) -> dict[int, dict[str, torch.Tensor]]:
    """为每个 batch 预计算基准频谱的 latent 编码。"""
    vae.eval()
    base_latents: dict[int, dict[str, torch.Tensor]] = {}
    with torch.no_grad():
        for batch_id, meta in dataset.batch_meta.items():
            base_spectrum = torch.from_numpy(meta["base_spectrum"]).float().unsqueeze(0).unsqueeze(0)
            base_spectrum = base_spectrum.to(device)
            z_base = vae.encode_deterministic(base_spectrum)
            base_latents[batch_id] = {
                "z_base": z_base.cpu().squeeze(0),
                "base_spectrum": base_spectrum.cpu().squeeze(0),
            }
    return base_latents


def train_epoch(
    dataloader: DataLoader,
    vae: SpectralVAE,
    residual_net: ResidualNet,
    smart_sampler: SmartSampler | None,
    pinn_loss: PINNSpectralLoss,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
    base_latents: dict[int, dict[str, torch.Tensor]],
    phase: str,
    target_k: int,
    epoch: int,
) -> dict[str, float]:
    """单 epoch 训练。"""
    vae.train()
    residual_net.train()
    if smart_sampler is not None:
        smart_sampler.train()

    total_loss = 0.0
    total_recon = 0.0
    total_coherence = 0.0
    total_smooth = 0.0
    total_order = 0.0
    total_far = 0.0
    total_kl = 0.0
    n_batches = 0

    for batch in dataloader:
        spectrum = batch["spectrum"].to(device)  # (B, 1, N)
        params = batch["params"].to(device)      # (B, 6)
        fs = batch["fs"].to(device)              # (B,)
        fp = batch["fp"].to(device)              # (B,)
        batch_ids = batch["batch_id"].cpu().numpy()

        # 收集该 batch 的基准 latent 和基准频谱
        z_base_list = []
        S_base_list = []
        for bid in batch_ids:
            z_base_list.append(base_latents[int(bid)]["z_base"])
            S_base_list.append(base_latents[int(bid)]["base_spectrum"])
        z_base = torch.stack(z_base_list, dim=0).to(device)      # (B, D)
        S_base = torch.stack(S_base_list, dim=0).to(device)      # (B, N)
        S_base = S_base.unsqueeze(1)  # (B, 1, N)

        optimizer.zero_grad()

        # --- VAE 前向 ---
        recon_vae, mu, logvar = vae(spectrum)

        if phase == "A":
            # Phase A: 只训 VAE，不用 ResidualNet
            delta_z = torch.zeros_like(mu)
            z = mu
        else:
            # Phase B/C: ResidualNet 预测偏移
            delta_z = residual_net(params)
            z = z_base + delta_z
            recon_vae = vae.decode(z)

        # --- SmartSampler（仅 Phase C）---
        sampler_loss = torch.tensor(0.0, device=device)
        if phase == "C" and smart_sampler is not None:
            grads = numerical_gradients(recon_vae)  # (B, 3, N)
            p = smart_sampler(grads)                # (B, N)
            mask = enforce_critical_points_mask(p, recon_vae, target_k)

            # 降采样后的重建损失（只对保留点计算 MSE）
            sampled_pred = recon_vae * mask.unsqueeze(1).float()
            sampled_true = spectrum * mask.unsqueeze(1).float()
            sampler_loss = F.mse_loss(sampled_pred, sampled_true)

            # 同时优化 SSIM
            # 降采样后插值回全长度，计算与原始的全局 SSIM
            sampled_flat = sampled_pred.squeeze(1)  # (B, N)
            # 简单的 SSIM 近似：只对保留区域计算
            ssim_val = compute_ssim(sampled_pred, spectrum)
            sampler_loss = sampler_loss - 0.1 * ssim_val  # 最大化 SSIM

        # --- PINN Loss ---
        loss, loss_dict = pinn_loss(
            S_pred=recon_vae,
            S_true=spectrum,
            z=z,
            z_base=z_base,
            fs_pred=fs,
            fp_pred=fp,
            S_base=S_base,
            freq=torch.linspace(1.0, 3.0, spectrum.shape[-1], device=device),
            mu=mu,
            logvar=logvar,
        )

        if phase == "C" and smart_sampler is not None:
            loss = loss + 0.5 * sampler_loss

        loss.backward()
        torch.nn.utils.clip_grad_norm_(
            list(vae.parameters()) + list(residual_net.parameters()), max_norm=1.0
        )
        if smart_sampler is not None:
            torch.nn.utils.clip_grad_norm_(smart_sampler.parameters(), max_norm=1.0)
        optimizer.step()

        total_loss += loss_dict["total"]
        total_recon += loss_dict["recon"]
        total_coherence += loss_dict.get("coherence", 0.0)
        total_smooth += loss_dict.get("smoothness", 0.0)
        total_order += loss_dict.get("fs_fp_order", 0.0)
        total_far += loss_dict.get("far_band", 0.0)
        total_kl += loss_dict.get("kl", 0.0)
        n_batches += 1

    return {
        "loss": total_loss / n_batches,
        "recon": total_recon / n_batches,
        "coherence": total_coherence / n_batches,
        "smooth": total_smooth / n_batches,
        "order": total_order / n_batches,
        "far": total_far / n_batches,
        "kl": total_kl / n_batches,
    }


@torch.no_grad()
def validate(
    dataloader: DataLoader,
    vae: SpectralVAE,
    residual_net: ResidualNet,
    smart_sampler: SmartSampler | None,
    device: torch.device,
    base_latents: dict[int, dict[str, torch.Tensor]],
    target_k: int,
) -> dict[str, float]:
    """验证集评估。"""
    vae.eval()
    residual_net.eval()
    if smart_sampler is not None:
        smart_sampler.eval()

    total_mse = 0.0
    total_ssim = 0.0
    total_sampler_mse = 0.0
    n_batches = 0

    for batch in dataloader:
        spectrum = batch["spectrum"].to(device)  # (B, 1, N)
        params = batch["params"].to(device)
        batch_ids = batch["batch_id"].cpu().numpy()

        z_base_list = []
        for bid in batch_ids:
            z_base_list.append(base_latents[int(bid)]["z_base"])
        z_base = torch.stack(z_base_list, dim=0).to(device)

        delta_z = residual_net(params)
        z = z_base + delta_z
        recon = vae.decode(z)

        mse = F.mse_loss(recon, spectrum).item()
        ssim = compute_ssim(recon, spectrum).item()

        total_mse += mse
        total_ssim += ssim

        if smart_sampler is not None:
            grads = numerical_gradients(recon)
            p = smart_sampler(grads)
            mask = enforce_critical_points_mask(p, recon, target_k)
            sampled = recon * mask.unsqueeze(1).float()
            sampler_mse = F.mse_loss(sampled, spectrum).item()
            total_sampler_mse += sampler_mse

        n_batches += 1

    result: dict[str, float] = {
        "mse": total_mse / n_batches,
        "ssim": total_ssim / n_batches,
    }
    if smart_sampler is not None:
        result["sampler_mse"] = total_sampler_mse / n_batches
    return result


def save_training_curves(
    history: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    """保存损失曲线为 JSON，并尝试画简单的 ASCII/文本图。"""
    json_path = output_dir / "training_curves.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    # 文本格式的损失摘要
    summary_path = output_dir / "training_summary.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("Epoch | Loss     | Recon    | Coherence | Smooth   | Order    | Far      | KL       | Val MSE  | Val SSIM\n")
        f.write("-" * 110 + "\n")
        for h in history:
            f.write(
                f"{h['epoch']:5d} | {h['loss']:.6f} | {h['recon']:.6f} | "
                f"{h['coherence']:.6f} | {h['smooth']:.6f} | {h['order']:.6f} | "
                f"{h['far']:.6f} | {h['kl']:.6f} | {h.get('val_mse', -1):.6f} | "
                f"{h.get('val_ssim', -1):.6f}\n"
            )


def main() -> None:
    args = parse_args()
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    device = get_device(args.device)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"设备: {device}")
    print(f"模式: {args.mode}")
    print(f"输出目录: {output_dir.absolute()}")

    # --- 数据集 ---
    if args.mode == "synthetic":
        print(f"生成合成数据: {args.n_batches} batches × {args.n_devices_per_batch} devices")
        dataset = SyntheticSpectrumDataset(
            n_batches=args.n_batches,
            n_devices_per_batch=args.n_devices_per_batch,
            n_freq=args.n_freq,
            latent_dim=args.latent_dim,
        )
    elif args.mode == "s1p-batch":
        if not args.s1p_dir:
            print("错误: --s1p-batch 模式需要指定 --s1p-dir")
            sys.exit(1)
        print(f"加载 S1P 批量数据: {args.s1p_dir}")
        dataset = RealS1PBatchDataset(
            s1p_dir=args.s1p_dir,
            n_freq=args.n_freq,
            latent_dim=args.latent_dim,
            noise_std=args.noise_std,
            group_by_area=args.group_by_area,
        )
    else:
        print("加载真实数据（预留接口，当前项目无现成数据）")
        from app.ml.dataset import RealSpectrumDataset

        dataset = RealSpectrumDataset(
            db_url=args.db_url,
            data_root=args.data_root,
            n_freq=args.n_freq,
        )
        if len(dataset) == 0:
            print("错误: 未加载到任何真实数据。请检查数据库连接和文件路径。")
            sys.exit(1)

    # 训练/验证划分 (90/10)
    n_total = len(dataset)
    n_train = int(0.9 * n_total)
    n_val = n_total - n_train
    train_set, val_set = torch.utils.data.random_split(
        dataset, [n_train, n_val],
        generator=torch.Generator().manual_seed(args.seed),
    )

    train_loader = DataLoader(
        train_set,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=0,
        drop_last=True,
    )
    val_loader = DataLoader(
        val_set,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=0,
    )

    print(f"训练集: {len(train_set)}, 验证集: {len(val_set)}")

    # --- 模型 ---
    vae = SpectralVAE(n_freq=args.n_freq, latent_dim=args.latent_dim).to(device)
    residual_net = ResidualNet(latent_dim=args.latent_dim).to(device)
    smart_sampler = SmartSampler(n_freq=args.n_freq).to(device)

    # 加载预训练权重
    if args.pretrained_vae:
        print(f"加载预训练 VAE: {args.pretrained_vae}")
        vae.load_state_dict(torch.load(args.pretrained_vae, map_location=device, weights_only=True))
    if args.pretrained_residual:
        print(f"加载预训练 ResidualNet: {args.pretrained_residual}")
        residual_net.load_state_dict(torch.load(args.pretrained_residual, map_location=device, weights_only=True))

    # 根据计划调整 PINN Loss 权重，给 ResidualNet 更大自由度
    pinn_loss = PINNSpectralLoss(
        lambda_coherence=0.05,
        lambda_order=5.0,
    ).to(device)

    # 先预计算基准 latent（用当前 VAE）
    print("预计算基准 latent...")
    base_latents = precompute_base_latents(dataset, vae, device)

    # --- 三阶段训练（含 Cosine Annealing + 早停） ---
    history: list[dict[str, Any]] = []
    global_epoch = 0
    best_val_mse = float("inf")
    patience_counter = 0
    best_state: dict[str, Any] | None = None

    def _train_phase(
        phase_name: str,
        phase_epochs: int,
        models_to_train: list,
        phase_key: str,
        smart_samp: SmartSampler | None,
        lr_mult: float = 1.0,
        bl: dict[int, dict[str, torch.Tensor]] | None = None,
    ) -> dict[int, dict[str, torch.Tensor]]:
        nonlocal global_epoch, best_val_mse, patience_counter, best_state
        if phase_epochs <= 0:
            return bl if bl is not None else {}

        _bl = bl if bl is not None else base_latents
        print(f"\n=== {phase_name} ({phase_epochs} epochs) ===")
        opt = torch.optim.Adam(
            models_to_train,
            lr=args.lr * lr_mult,
            weight_decay=args.weight_decay,
        )
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=phase_epochs)

        for epoch in range(1, phase_epochs + 1):
            t0 = time.time()
            train_metrics = train_epoch(
                train_loader, vae, residual_net, smart_samp, pinn_loss,
                opt, device, _bl, phase_key, args.target_k, epoch,
            )
            scheduler.step()

            # 按频率验证 + 早停检查
            do_validate = (epoch % args.validate_every == 0) or (epoch == phase_epochs)
            if do_validate:
                val_metrics = validate(
                    val_loader, vae, residual_net, smart_samp, device, _bl, args.target_k,
                )
                _bl = precompute_base_latents(dataset, vae, device)

                global_epoch += 1
                record = {
                    "epoch": global_epoch,
                    "phase": phase_key,
                    **train_metrics,
                    "val_mse": val_metrics["mse"],
                    "val_ssim": val_metrics["ssim"],
                }
                if smart_samp is not None:
                    record["val_sampler_mse"] = val_metrics.get("sampler_mse", -1)
                history.append(record)

                # 早停
                if val_metrics["mse"] < best_val_mse:
                    best_val_mse = val_metrics["mse"]
                    patience_counter = 0
                    # 保存最佳状态
                    best_state = {
                        "vae": vae.state_dict(),
                        "residual_net": residual_net.state_dict(),
                        "smart_sampler": smart_sampler.state_dict(),
                        "base_latents": _bl,
                        "epoch": global_epoch,
                    }
                else:
                    patience_counter += 1

                tag = "*" if patience_counter == 0 else f"({patience_counter})"
                print(
                    f"Epoch {global_epoch:03d} | loss={train_metrics['loss']:.4f} "
                    f"val_mse={val_metrics['mse']:.6f} val_ssim={val_metrics['ssim']:.4f} "
                    f"lr={scheduler.get_last_lr()[0]:.6f} {tag} ({time.time()-t0:.1f}s)"
                )

                if patience_counter >= args.early_stop_patience:
                    print(f"早停触发！已 {patience_counter} 个验证周期无改善。")
                    return _bl
            else:
                global_epoch += 1

        return _bl

    # 计算各阶段 epoch 数
    phase_a_epochs = min(args.phase_a_epochs, args.epochs)

    # 冻结 ResidualNet（Phase A）
    for p in residual_net.parameters():
        p.requires_grad = False

    base_latents = _train_phase("Phase A: 仅 VAE", phase_a_epochs, list(vae.parameters()), "A", None, lr_mult=1.0, bl=base_latents)

    # 解冻 ResidualNet（Phase B）
    for p in residual_net.parameters():
        p.requires_grad = True

    base_latents = _train_phase(
        "Phase B: VAE + ResidualNet",
        min(args.phase_b_epochs, args.epochs - args.phase_a_epochs),
        list(vae.parameters()) + list(residual_net.parameters()),
        "B", None, lr_mult=0.5, bl=base_latents,
    )

    base_latents = _train_phase(
        "Phase C: + SmartSampler",
        min(args.phase_c_epochs, args.epochs - args.phase_a_epochs - args.phase_b_epochs),
        list(vae.parameters()) + list(residual_net.parameters()) + list(smart_sampler.parameters()),
        "C", smart_sampler, lr_mult=0.3, bl=base_latents,
    )

    # 恢复最佳状态
    if best_state is not None:
        print(f"\n恢复最佳模型（epoch {best_state['epoch']}, val_mse={best_val_mse:.6f}）")
        vae.load_state_dict(best_state["vae"])
        residual_net.load_state_dict(best_state["residual_net"])
        smart_sampler.load_state_dict(best_state["smart_sampler"])
        base_latents = best_state["base_latents"]

    # --- 保存模型 ---
    print("\n保存模型...")
    torch.save(vae.state_dict(), output_dir / "vae.pt")
    torch.save(residual_net.state_dict(), output_dir / "residual_net.pt")
    torch.save(smart_sampler.state_dict(), output_dir / "smart_sampler.pt")

    # 保存基准频谱元数据
    base_meta = {}
    for bid, meta in base_latents.items():
        base_meta[str(bid)] = {
            "z_base": meta["z_base"].cpu().numpy().tolist(),
            "base_spectrum": meta["base_spectrum"].cpu().numpy().tolist(),
        }
    with open(output_dir / "base_latents.json", "w", encoding="utf-8") as f:
        json.dump(base_meta, f, indent=2)

    # 保存训练历史
    save_training_curves(history, output_dir)

    # 保存参数归一化统计
    if hasattr(dataset, "params_mean") and hasattr(dataset, "params_std"):
        norm_stats = {
            "mean": dataset.params_mean.cpu().numpy().tolist(),
            "std": dataset.params_std.cpu().numpy().tolist(),
        }
        with open(output_dir / "params_norm.json", "w", encoding="utf-8") as f:
            json.dump(norm_stats, f, indent=2)

    print(f"\n训练完成。产物保存在: {output_dir.absolute()}")
    print("  - vae.pt")
    print("  - residual_net.pt")
    print("  - smart_sampler.pt")
    print("  - base_latents.json")
    print("  - training_curves.json / .txt")
    print("  - params_norm.json")


if __name__ == "__main__":
    main()
