"""Phase 2 专项训练：冻结 reconstructor，只训练 AdaptiveSampler。

用法:
    python scripts/train_phase2.py \
      --ckpt-dir ./sparse_recon_v2 \
      --s1p-dir ../#3 \
      --piezo-thickness 308 \
      --epochs 30 \
      --device mps
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.sparse.dataset import SparseReconDataset, collate_fn
from app.ml.sparse.loss import SparseReconLoss
from app.ml.sparse.reconstructor import SparseReconstructor
from app.ml.sparse.sampler import AdaptiveSampler


def get_device(prefer: str = "auto") -> torch.device:
    if prefer == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    if prefer == "cuda" and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt-dir", type=str, required=True, help="Phase 1 checkpoint 目录")
    parser.add_argument("--s1p-dir", type=str, required=True)
    parser.add_argument("--piezo-thickness", type=str, default="308")
    parser.add_argument("--target-k", type=int, default=300)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=5e-4)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--output-dir", type=str, default="./sparse_phase2")
    parser.add_argument("--steps-per-run", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def train_epoch(dataloader, recon, sampler, criterion, optimizer, device, target_k):
    recon.eval()
    sampler.train()

    total_loss = 0.0
    total_recon = 0.0
    n_batches = 0

    for batch in dataloader:
        cond = batch["cond"].to(device)
        target_freq = batch["target_freq"].to(device)
        target_z = batch["target_z"].to(device)
        z_baseline = batch.get("z_baseline")
        if z_baseline is not None:
            z_baseline = z_baseline.to(device)
        region_ids = batch["region_ids"].to(device)

        # AdaptiveSampler 重新采样
        B, N = target_z.shape
        fs_true = cond[:, 0]
        fp_true = cond[:, 1]
        p_norm, y_soft, k_pred = sampler(target_z, region_ids, freq=target_freq,
                                          fs=fs_true, fp=fp_true, target_k=target_k, use_gumbel=True)

        sampled_list = []
        k_actual_list = []
        for b in range(B):
            k_actual_b = int(k_pred[b].item())
            k_actual_b = max(sampler.k_min, min(sampler.k_max, k_actual_b))
            k_actual_list.append(k_actual_b)
            idx = torch.where(y_soft[b] > 1.0 / N)[0]
            if len(idx) == 0:
                _, topk_idx = torch.topk(p_norm[b], min(k_actual_b, N))
                idx = topk_idx
            if len(idx) > k_actual_b:
                idx = idx[:k_actual_b]
            sf = target_freq[b, idx]
            sz = target_z[b, idx]
            pad_len = target_k - len(idx)
            if pad_len > 0:
                sf = torch.cat([sf, torch.full((pad_len,), target_freq[b, -1].item(), device=device)])
                sz = torch.cat([sz, torch.full((pad_len,), target_z[b, -1].item(), device=device)])
            sampled_list.append(torch.stack([sf, sz], dim=1))

        samples = torch.stack(sampled_list, dim=0)
        sample_mask = torch.zeros(B, target_k, dtype=torch.bool, device=device)
        k_actual = torch.tensor(k_actual_list, dtype=torch.float32, device=device)

        z_pred = recon(cond, samples, target_freq, z_baseline=z_baseline, sample_mask=sample_mask)

        loss, loss_dict = criterion(
            z_pred, target_z, region_ids, target_k,
            freq=target_freq, fs_true=fs_true, fp_true=fp_true,
            k_actual=k_actual, k_pred=k_pred,
        )

        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(sampler.parameters(), max_norm=1.0)
        optimizer.step()

        total_loss += loss_dict["total"]
        total_recon += loss_dict["recon"]
        n_batches += 1

    return {"loss": total_loss / max(n_batches, 1), "recon": total_recon / max(n_batches, 1)}


@torch.no_grad()
def validate(dataloader, recon, sampler, criterion, device, target_k):
    recon.eval()
    sampler.eval()

    total_loss = 0.0
    total_recon = 0.0
    n_batches = 0

    for batch in dataloader:
        cond = batch["cond"].to(device)
        target_freq = batch["target_freq"].to(device)
        target_z = batch["target_z"].to(device)
        z_baseline = batch.get("z_baseline")
        if z_baseline is not None:
            z_baseline = z_baseline.to(device)
        region_ids = batch["region_ids"].to(device)

        B, N = target_z.shape
        fs_true = cond[:, 0]
        fp_true = cond[:, 1]
        p_norm, y_soft, k_pred = sampler(target_z, region_ids, freq=target_freq,
                                          fs=fs_true, fp=fp_true, target_k=target_k, use_gumbel=False)

        sampled_list = []
        k_actual_list = []
        for b in range(B):
            k_actual_b = int(k_pred[b].item())
            k_actual_b = max(sampler.k_min, min(sampler.k_max, k_actual_b))
            k_actual_list.append(k_actual_b)
            idx = torch.where(y_soft[b] > 1.0 / N)[0]
            if len(idx) == 0:
                _, topk_idx = torch.topk(p_norm[b], min(k_actual_b, N))
                idx = topk_idx
            if len(idx) > k_actual_b:
                idx = idx[:k_actual_b]
            sf = target_freq[b, idx]
            sz = target_z[b, idx]
            pad_len = target_k - len(idx)
            if pad_len > 0:
                sf = torch.cat([sf, torch.full((pad_len,), target_freq[b, -1].item(), device=device)])
                sz = torch.cat([sz, torch.full((pad_len,), target_z[b, -1].item(), device=device)])
            else:
                sf = sf[:target_k]
                sz = sz[:target_k]
            sampled_list.append(torch.stack([sf, sz], dim=1))

        samples = torch.stack(sampled_list, dim=0)
        sample_mask = torch.zeros(B, target_k, dtype=torch.bool, device=device)
        k_actual = torch.tensor(k_actual_list, dtype=torch.float32, device=device)

        z_pred = recon(cond, samples, target_freq, z_baseline=z_baseline, sample_mask=sample_mask)

        loss, loss_dict = criterion(
            z_pred, target_z, region_ids, target_k,
            freq=target_freq, fs_true=fs_true, fp_true=fp_true,
            k_actual=k_actual, k_pred=k_pred,
        )
        total_loss += loss_dict["total"]
        total_recon += loss_dict["recon"]
        n_batches += 1

    return {"loss": total_loss / max(n_batches, 1), "recon": total_recon / max(n_batches, 1)}


def main():
    args = parse_args()
    torch.manual_seed(42)
    np.random.seed(42)

    device = get_device(args.device)
    ckpt_dir = Path(args.ckpt_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 加载配置
    config = {"d_model": 64, "n_encoder_layers": 4, "n_heads": 4, "n_freq": 1001}
    config_path = ckpt_dir / "config.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            config.update(json.load(f))

    # 数据集
    dataset = SparseReconDataset(s1p_dir=args.s1p_dir, target_k=args.target_k, noise_std=0.05)
    n_val = max(1, int(len(dataset) * 0.2))
    n_train = len(dataset) - n_val
    from torch.utils.data import random_split
    train_set, val_set = random_split(dataset, [n_train, n_val])
    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True, collate_fn=collate_fn)
    val_loader = DataLoader(val_set, batch_size=args.batch_size, shuffle=False, collate_fn=collate_fn)

    # 加载 reconstructor（冻结）
    recon = SparseReconstructor(
        d_model=config.get("d_model", 64),
        n_encoder_layers=config.get("n_encoder_layers", 4),
        n_heads=config.get("n_heads", 4),
    ).to(device)
    recon.load_state_dict(torch.load(ckpt_dir / "reconstructor.pt", map_location=device, weights_only=True))
    for p in recon.parameters():
        p.requires_grad = False
    recon.eval()

    # 加载或初始化 sampler
    sampler = AdaptiveSampler(n_freq=config.get("n_freq", 1001), tau_init=0.5, tau_min=0.05).to(device)
    sampler_ckpt = output_dir / "checkpoint.pt"
    state_path = output_dir / "train_state.json"

    start_epoch = 1
    best_val_recon = float("inf")
    patience_counter = 0
    history = []

    if args.resume and sampler_ckpt.exists() and state_path.exists():
        sampler.load_state_dict(torch.load(sampler_ckpt, map_location=device)["sampler"])
        with open(state_path, "r") as f:
            saved = json.load(f)
        start_epoch = saved.get("epoch", 1) + 1
        best_val_recon = saved.get("best_val_recon", float("inf"))
        patience_counter = saved.get("patience_counter", 0)
        history = saved.get("history", [])
        print(f"续训 epoch {start_epoch}/{args.epochs}, best_val_recon={best_val_recon:.6f}")
    else:
        # 从 Phase 1 的 sampler 初始化（如果存在）
        phase1_sampler = ckpt_dir / "sampler.pt"
        if phase1_sampler.exists():
            sampler.load_state_dict(torch.load(phase1_sampler, map_location=device, weights_only=True))
            print("从 Phase 1 sampler 初始化")

    criterion = SparseReconLoss(lambda_count=0.1, lambda_smooth=0.01, lambda_phys=0.5, lambda_efficiency=0.5).to(device)

    end_epoch = min(start_epoch + args.steps_per_run - 1, args.epochs)
    optimizer = torch.optim.AdamW(sampler.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    for epoch in range(start_epoch, end_epoch + 1):
        sampler.update_tau(epoch, args.epochs)

        train_metrics = train_epoch(train_loader, recon, sampler, criterion, optimizer, device, args.target_k)
        val_metrics = validate(val_loader, recon, sampler, criterion, device, args.target_k)
        scheduler.step()

        print(f"Epoch {epoch:3d}/{args.epochs} | train_loss={train_metrics['loss']:.6f} val_loss={val_metrics['loss']:.6f} val_recon={val_metrics['recon']:.6f}")

        history.append({"epoch": epoch, "train_loss": train_metrics["loss"], "val_loss": val_metrics["loss"], "val_recon": val_metrics["recon"]})

        if val_metrics["recon"] < best_val_recon:
            best_val_recon = val_metrics["recon"]
            patience_counter = 0
            torch.save(sampler.state_dict(), output_dir / "best_sampler.pt")
        else:
            patience_counter += 1
            if patience_counter >= 15:
                print(f"\n早停于 epoch {epoch}，最佳 val_recon={best_val_recon:.6f}")
                break

    torch.save({"sampler": sampler.state_dict()}, sampler_ckpt)
    with open(state_path, "w") as f:
        json.dump({"epoch": end_epoch, "best_val_recon": best_val_recon, "patience_counter": patience_counter, "history": history}, f, indent=2)

    if end_epoch >= args.epochs or patience_counter >= 15:
        if (output_dir / "best_sampler.pt").exists():
            sampler.load_state_dict(torch.load(output_dir / "best_sampler.pt", map_location=device))
        torch.save(sampler.state_dict(), output_dir / "sampler.pt")
        with open(output_dir / "config.json", "w") as f:
            json.dump({"piezo_thickness": args.piezo_thickness, "target_k": args.target_k, **config}, f, indent=2)
        print(f"\nPhase 2 训练完成。产物: {output_dir.absolute()}")
    else:
        print(f"\n本轮完成 epoch {end_epoch}/{args.epochs}。请再次启动以继续训练（加 --resume）")


if __name__ == "__main__":
    main()
