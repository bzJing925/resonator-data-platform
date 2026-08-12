"""Stage 1 训练（规格 §6）。

用法：
    uv run python scripts/train_surrogate_stage1.py --data ./surrogate_data \
        --out ./checkpoints_surrogate --epochs 300
冒烟：
    uv run python scripts/train_surrogate_stage1.py --data ./surrogate_data \
        --out /tmp/surr_smoke --epochs 2 --smoke
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from torch.utils.data import DataLoader

# 把 backend 目录加入路径，以便 import app（同 train_sparse_reconstructor.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.surrogate.dataset import SurrogateDataset  # noqa: E402
from app.ml.surrogate.model import BaselineResidualMLP  # noqa: E402


def _device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def train(
    data_dir: str,
    out_dir: str,
    epochs: int = 500,
    batch_size: int = 64,
    lr: float = 1e-3,
    lambda_sparse: float = 0.1,
    patience: int = 30,
    smoke: bool = False,
) -> dict:
    dev = _device()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    train_ds = SurrogateDataset(data_dir, split="train")
    val_ds = SurrogateDataset(data_dir, split="val")
    if smoke:
        epochs = min(epochs, 2)
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=False)
    val_dl = DataLoader(val_ds, batch_size=batch_size)

    model = BaselineResidualMLP(in_dim=5, hidden=256, n_res_blocks=2, n_params=7, k=train_ds.k).to(
        dev
    )
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)

    def run_epoch(dl, train_mode: bool) -> float:
        model.train(train_mode)
        total, n = 0.0, 0
        with torch.set_grad_enabled(train_mode):
            for batch in dl:
                x = batch["x"].to(dev)
                yp = batch["y_params"].to(dev)
                ys = batch["y_sparse"].to(dev)
                pb = batch["params_base"].to(dev)
                sb = batch["sparse_base"].to(dev)
                out_pred = model(x, pb, sb)
                loss = torch.nn.functional.mse_loss(out_pred["params"], yp) + (
                    lambda_sparse * torch.nn.functional.mse_loss(out_pred["sparse"], ys)
                )
                if train_mode:
                    opt.zero_grad()
                    loss.backward()
                    opt.step()
                total += loss.item() * len(x)
                n += len(x)
        return total / max(n, 1)

    best_val, best_state, bad = float("inf"), None, 0
    history = []
    for epoch in range(1, epochs + 1):
        tr = run_epoch(train_dl, True)
        va = run_epoch(val_dl, False)
        sched.step()
        history.append({"epoch": epoch, "train": tr, "val": va})
        if va < best_val - 1e-6:
            best_val, bad = va, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        if epoch % 10 == 0 or epoch == 1:
            print(f"epoch {epoch}: train={tr:.5f} val={va:.5f} best={best_val:.5f}")
        if bad >= patience:
            print(f"early stop at epoch {epoch}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    torch.save(
        {
            "model_state": model.state_dict(),
            "in_dim": 5,
            "hidden": 256,
            "n_res_blocks": 2,
            "n_params": 7,
            "k": train_ds.k,
            "lambda_sparse": lambda_sparse,
        },
        out / "stage1.pt",
    )
    (out / "stage1_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"saved: {out / 'stage1.pt'} (best val={best_val:.5f})")
    return {"best_val": best_val, "epochs_run": len(history)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="./checkpoints_surrogate")
    ap.add_argument("--epochs", type=int, default=500)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--lambda-sparse", type=float, default=0.1)
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    train(
        args.data,
        args.out,
        args.epochs,
        args.batch_size,
        args.lr,
        args.lambda_sparse,
        args.patience,
        args.smoke,
    )


if __name__ == "__main__":
    main()
