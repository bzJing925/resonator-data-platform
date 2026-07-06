"""稀疏采样重建网络训练脚本。

两阶段训练:
  Phase 1: 固定规则采样，只训练 SparseReconstructor
  Phase 2: 联合训练 AdaptiveSampler + SparseReconstructor

用法:
    python scripts/train_sparse_reconstructor.py \
      --s1p-dir ../#3 \
      --piezo-thickness 308 \
      --target-k 300 \
      --epochs 200 \
      --device mps \
      --output-dir ./sparse_recon_308
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, random_split

# 把 backend 目录加入路径，以便 import app.ml
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.sparse.dataset import SparseReconDataset, collate_fn  # noqa: E402
from app.ml.sparse.loss import SparseReconLoss  # noqa: E402
from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402
from app.ml.sparse.sampler import AdaptiveSampler  # noqa: E402

log = logging.getLogger("aln")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def get_device(prefer: str = "auto") -> torch.device:
    if prefer == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    if prefer == "cuda" and torch.cuda.is_available():
        return torch.device("cuda")
    if prefer == "auto":
        if torch.backends.mps.is_available():
            return torch.device("mps")
        if torch.cuda.is_available():
            return torch.device("cuda")
    return torch.device("cpu")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="训练稀疏采样重建网络")
    parser.add_argument(
        "--s1p-dir", type=str, nargs="+", required=True, help="S1P/S2P 文件目录（可多个）"
    )
    parser.add_argument("--piezo-thickness", type=str, default="308", help="压电层厚度标识")
    parser.add_argument("--target-k", type=int, default=300, help="目标采样点数")
    parser.add_argument("--d-model", type=int, default=64, help="Transformer hidden dim")
    parser.add_argument("--n-encoder-layers", type=int, default=4, help="Transformer encoder 层数")
    parser.add_argument("--n-heads", type=int, default=4, help="注意力头数")
    parser.add_argument("--epochs", type=int, default=200, help="总 epoch 数")
    parser.add_argument(
        "--phase1-epochs", type=int, default=50, help="Phase 1 epoch 数（固定采样）"
    )
    parser.add_argument("--batch-size", type=int, default=8, help="batch size")
    parser.add_argument("--lr", type=float, default=1e-3, help="学习率")
    parser.add_argument("--weight-decay", type=float, default=1e-4, help="权重衰减")
    parser.add_argument("--early-stop-patience", type=int, default=20, help="早停耐心值")
    parser.add_argument("--device", type=str, default="auto", help="计算设备")
    parser.add_argument("--output-dir", type=str, default="./sparse_recon_output", help="输出目录")
    parser.add_argument("--noise-std", type=float, default=0.05, help="数据增强噪声")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--resume", action="store_true", help="从输出目录的 checkpoint 续训")
    parser.add_argument(
        "--steps-per-run", type=int, default=5, help="每轮跑的 epoch 数（用于分轮续跑）"
    )
    return parser.parse_args()


def train_epoch(
    dataloader: DataLoader,
    recon: SparseReconstructor,
    sampler: AdaptiveSampler | None,
    criterion: SparseReconLoss,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
    target_k: int,
    phase: int,  # 1 or 2
) -> dict[str, float]:
    recon.train()
    if sampler is not None:
        sampler.train() if phase == 2 else sampler.eval()

    total_loss = 0.0
    total_recon = 0.0
    n_batches = 0

    for batch in dataloader:
        cond = batch["cond"].to(device)  # (B, 5)
        samples = batch["samples"].to(device)  # (B, K, 2)
        sample_mask = batch["sample_mask"].to(device)  # (B, K)
        target_freq = batch["target_freq"].to(device)  # (B, N)
        target_z = batch["target_z"].to(device)  # (B, N)
        z_baseline = batch.get("z_baseline")
        if z_baseline is not None:
            z_baseline = z_baseline.to(device)  # (B, N)
        region_ids = batch["region_ids"].to(device)  # (B, N)

        # Phase 2: 使用 AdaptiveSampler 重新采样
        if phase == 2 and sampler is not None:
            z_t = target_z  # (B, N)
            fs_true = cond[:, 0]
            fp_true = cond[:, 1]
            p_norm, y_soft, k_pred = sampler(
                z_t,
                region_ids,
                freq=target_freq,
                fs=fs_true,
                fp=fp_true,
                target_k=target_k,
                use_gumbel=True,
            )
            # 使用 k_pred 作为实际采样点数（自适应）
            b, n = target_z.shape
            sampled_list = []
            k_actual_list = []
            for batch_idx in range(b):
                # 自适应采样点数
                k_actual_b = int(k_pred[batch_idx].item())
                k_actual_b = max(sampler.k_min, min(sampler.k_max, k_actual_b))
                k_actual_list.append(k_actual_b)

                idx = torch.where(y_soft[batch_idx] > 1.0 / n)[0]
                if len(idx) == 0:
                    _, topk_idx = torch.topk(p_norm[batch_idx], min(k_actual_b, n))
                    idx = topk_idx
                if len(idx) > k_actual_b:
                    idx = idx[:k_actual_b]
                sf = target_freq[batch_idx, idx]
                sz = target_z[batch_idx, idx]
                pad_len = target_k - len(idx)
                if pad_len > 0:
                    sf = torch.cat(
                        [
                            sf,
                            torch.full(
                                (pad_len,), target_freq[batch_idx, -1].item(), device=device
                            ),
                        ]
                    )
                    sz = torch.cat(
                        [sz, torch.full((pad_len,), target_z[batch_idx, -1].item(), device=device)]
                    )
                sampled_list.append(torch.stack([sf, sz], dim=1))
            samples = torch.stack(sampled_list, dim=0)
            sample_mask = torch.zeros(b, target_k, dtype=torch.bool, device=device)
            _k_actual = torch.tensor(k_actual_list, dtype=torch.float32, device=device)

        # 前向（传入基线插值）
        z_pred = recon(cond, samples, target_freq, z_baseline=z_baseline, sample_mask=sample_mask)

        # 物理参数
        fs_true = cond[:, 0]
        fp_true = cond[:, 1]

        loss, loss_dict = criterion(
            z_pred,
            target_z,
            region_ids,
            target_k,
            freq=target_freq,
            fs_true=fs_true,
            fp_true=fp_true,
        )

        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(
            list(recon.parameters())
            + (list(sampler.parameters()) if sampler and phase == 2 else []),
            max_norm=1.0,
        )
        optimizer.step()

        total_loss += loss_dict["total"]
        total_recon += loss_dict["recon"]
        n_batches += 1

    return {
        "loss": total_loss / max(n_batches, 1),
        "recon": total_recon / max(n_batches, 1),
    }


@torch.no_grad()
def validate(
    dataloader: DataLoader,
    recon: SparseReconstructor,
    sampler: AdaptiveSampler | None,
    criterion: SparseReconLoss,
    device: torch.device,
    target_k: int,
    phase: int,
) -> dict[str, float]:
    recon.eval()
    if sampler is not None:
        sampler.eval()

    total_loss = 0.0
    total_recon = 0.0
    n_batches = 0

    for batch in dataloader:
        cond = batch["cond"].to(device)
        samples = batch["samples"].to(device)
        sample_mask = batch["sample_mask"].to(device)
        target_freq = batch["target_freq"].to(device)
        target_z = batch["target_z"].to(device)
        z_baseline = batch.get("z_baseline")
        if z_baseline is not None:
            z_baseline = z_baseline.to(device)
        region_ids = batch["region_ids"].to(device)

        k_actual = None
        if phase == 2 and sampler is not None:
            z_t = target_z
            fs_true = cond[:, 0]
            fp_true = cond[:, 1]
            p_norm, y_soft, k_pred = sampler(
                z_t,
                region_ids,
                freq=target_freq,
                fs=fs_true,
                fp=fp_true,
                target_k=target_k,
                use_gumbel=False,
            )
            b, n = target_z.shape
            sampled_list = []
            k_actual_list = []
            for batch_idx in range(b):
                k_actual_b = int(k_pred[batch_idx].item())
                k_actual_b = max(sampler.k_min, min(sampler.k_max, k_actual_b))
                k_actual_list.append(k_actual_b)

                idx = torch.where(y_soft[batch_idx] > 1.0 / n)[0]
                if len(idx) == 0:
                    _, topk_idx = torch.topk(p_norm[batch_idx], min(k_actual_b, n))
                    idx = topk_idx
                if len(idx) > k_actual_b:
                    idx = idx[:k_actual_b]
                sf = target_freq[batch_idx, idx]
                sz = target_z[batch_idx, idx]
                pad_len = target_k - len(idx)
                if pad_len > 0:
                    sf = torch.cat(
                        [
                            sf,
                            torch.full(
                                (pad_len,), target_freq[batch_idx, -1].item(), device=device
                            ),
                        ]
                    )
                    sz = torch.cat(
                        [sz, torch.full((pad_len,), target_z[batch_idx, -1].item(), device=device)]
                    )
                else:
                    sf = sf[:target_k]
                    sz = sz[:target_k]
                sampled_list.append(torch.stack([sf, sz], dim=1))
            samples = torch.stack(sampled_list, dim=0)
            sample_mask = torch.zeros(b, target_k, dtype=torch.bool, device=device)
            k_actual = torch.tensor(k_actual_list, dtype=torch.float32, device=device)

        z_pred = recon(cond, samples, target_freq, z_baseline=z_baseline, sample_mask=sample_mask)
        fs_true = cond[:, 0]
        fp_true = cond[:, 1]

        loss, loss_dict = criterion(
            z_pred,
            target_z,
            region_ids,
            target_k,
            freq=target_freq,
            fs_true=fs_true,
            fp_true=fp_true,
            k_actual=k_actual,
        )
        total_loss += loss_dict["total"]
        total_recon += loss_dict["recon"]
        n_batches += 1

    return {
        "loss": total_loss / max(n_batches, 1),
        "recon": total_recon / max(n_batches, 1),
    }


def main() -> None:
    args = parse_args()
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    device = get_device(args.device)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"设备: {device}")
    print(f"压电层厚度: {args.piezo_thickness}nm")
    print(f"目标采样点数: {args.target_k}")
    print(f"输出目录: {output_dir.absolute()}")

    # --- 数据集路径处理（支持从脚本目录或项目根目录解析） ---
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent
    s1p_dirs: list[str] = []
    for d in args.s1p_dir:
        p = Path(d).resolve()
        if not p.exists():
            # 用户可能基于脚本位置传了相对路径（如 ../#frame），
            # 尝试以项目根目录为基准，用目录名直接拼接
            alt = (project_root / Path(d).name).resolve()
            if alt.exists():
                p = alt
        s1p_dirs.append(str(p))

    dataset = SparseReconDataset(
        s1p_dir=s1p_dirs,
        target_k=args.target_k,
        noise_std=args.noise_std,
    )

    if len(dataset) == 0:
        raise RuntimeError("没有加载到任何样本")

    n_val = max(1, int(len(dataset) * 0.2))
    n_train = len(dataset) - n_val
    train_set, val_set = random_split(dataset, [n_train, n_val])

    train_loader = DataLoader(
        train_set, batch_size=args.batch_size, shuffle=True, collate_fn=collate_fn
    )
    val_loader = DataLoader(
        val_set, batch_size=args.batch_size, shuffle=False, collate_fn=collate_fn
    )

    print(f"训练集: {len(train_set)}, 验证集: {len(val_set)}")

    # --- 模型 ---
    recon = SparseReconstructor(
        d_model=args.d_model,
        n_encoder_layers=args.n_encoder_layers,
        n_heads=args.n_heads,
    ).to(device)

    sampler = AdaptiveSampler(n_freq=train_set.dataset.samples[0]["target_z"].shape[0]).to(device)

    criterion = SparseReconLoss(lambda_count=0.1, lambda_smooth=0.01, lambda_phys=0.5).to(device)

    # --- 恢复状态 ---
    ckpt_path = output_dir / "checkpoint.pt"
    state_path = output_dir / "train_state.json"
    best_ckpt_path = output_dir / "best_model.pt"

    start_epoch = 1
    best_val_metric = float("inf")
    patience_counter = 0
    best_state: dict | None = None
    history: list[dict] = []

    if args.resume and ckpt_path.exists() and state_path.exists():
        print(f"\n=== 从 checkpoint 续训: {ckpt_path} ===")
        ckpt = torch.load(ckpt_path, map_location=device)
        recon.load_state_dict(ckpt["recon"])
        sampler.load_state_dict(ckpt["sampler"])
        with open(state_path, encoding="utf-8") as f:
            saved = json.load(f)
        start_epoch = saved.get("epoch", 1) + 1
        best_val_metric = saved.get("best_val_metric", float("inf"))
        patience_counter = saved.get("patience_counter", 0)
        history = saved.get("history", [])
        best_epoch = saved.get("best_epoch", None)
        if best_epoch is not None and best_ckpt_path.exists():
            best_ckpt = torch.load(best_ckpt_path, map_location=device)
            best_state = {
                "epoch": best_epoch,
                "recon": best_ckpt["recon"],
                "sampler": best_ckpt["sampler"],
            }
        print(
            f"续训 epoch {start_epoch}/{args.epochs}, "
            f"best_val_recon={best_val_metric:.6f}, patience={patience_counter}"
        )

    # --- 训练循环 ---
    end_epoch = min(start_epoch + args.steps_per_run - 1, args.epochs)
    optimizer = None
    scheduler = None
    current_phase = None

    for epoch in range(start_epoch, end_epoch + 1):
        phase = 1 if epoch <= args.phase1_epochs else 2

        # Sampler 温度退火
        if sampler is not None:
            sampler.update_tau(epoch, args.epochs)

        # Phase 切换时才创建新的 optimizer
        if phase != current_phase:
            current_phase = phase
            if phase == 1:
                for p in sampler.parameters():
                    p.requires_grad = False
                params = list(recon.parameters())
            else:
                for p in sampler.parameters():
                    p.requires_grad = True
                params = list(recon.parameters()) + list(sampler.parameters())
            lr = args.lr * (0.5 if phase == 2 else 1.0)
            optimizer = torch.optim.AdamW(params, lr=lr, weight_decay=args.weight_decay)
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

        train_metrics = train_epoch(
            train_loader, recon, sampler, criterion, optimizer, device, args.target_k, phase
        )
        val_metrics = validate(val_loader, recon, sampler, criterion, device, args.target_k, phase)
        scheduler.step()

        print(
            f"Epoch {epoch:3d}/{args.epochs} | Phase {phase} | "
            f"train_loss={train_metrics['loss']:.6f} val_loss={val_metrics['loss']:.6f} "
            f"val_recon={val_metrics['recon']:.6f}"
        )

        history.append(
            {
                "epoch": epoch,
                "phase": phase,
                "train_loss": train_metrics["loss"],
                "val_loss": val_metrics["loss"],
                "val_recon": val_metrics["recon"],
            }
        )

        # 早停（基于 val_recon，避免 Phase2 效率损失干扰）
        if val_metrics["recon"] < best_val_metric:
            best_val_metric = val_metrics["recon"]
            patience_counter = 0
            best_state = {
                "epoch": epoch,
                "recon": recon.state_dict(),
                "sampler": sampler.state_dict(),
            }
            torch.save(
                {"recon": best_state["recon"], "sampler": best_state["sampler"]},
                best_ckpt_path,
            )
        else:
            patience_counter += 1
            if patience_counter >= args.early_stop_patience:
                print(f"\n早停于 epoch {epoch}，最佳 val_recon={best_val_metric:.6f}")
                break

    # --- 保存 checkpoint ---
    print("\n保存 checkpoint...")
    torch.save({"recon": recon.state_dict(), "sampler": sampler.state_dict()}, ckpt_path)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "epoch": end_epoch,
                "best_val_metric": best_val_metric,
                "patience_counter": patience_counter,
                "best_epoch": best_state["epoch"] if best_state else None,
                "history": history,
            },
            f,
            indent=2,
        )

    # --- 如果是最后一轮或早停，保存最终模型 ---
    if end_epoch >= args.epochs or patience_counter >= args.early_stop_patience:
        if best_state is not None:
            print(f"\n恢复最佳模型（epoch {best_state['epoch']}, val_recon={best_val_metric:.6f}）")
            recon.load_state_dict(best_state["recon"])
            sampler.load_state_dict(best_state["sampler"])
        print("\n保存最终模型...")
        torch.save(recon.state_dict(), output_dir / "reconstructor.pt")
        torch.save(sampler.state_dict(), output_dir / "sampler.pt")

        config = {
            "piezo_thickness": args.piezo_thickness,
            "target_k": args.target_k,
            "d_model": args.d_model,
            "n_encoder_layers": args.n_encoder_layers,
            "n_heads": args.n_heads,
            "n_freq": train_set.dataset.samples[0]["target_z"].shape[0],
        }
        with open(output_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        with open(output_dir / "history.json", "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
        print(f"\n训练完成。产物保存在: {output_dir.absolute()}")
        print("  - reconstructor.pt")
        print("  - sampler.pt")
        print("  - config.json")
    else:
        print(f"\n本轮完成 epoch {end_epoch}/{args.epochs}。请再次启动以继续训练（加 --resume）")


if __name__ == "__main__":
    main()
