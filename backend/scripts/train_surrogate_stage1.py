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

import numpy as np
import torch
from torch.utils.data import ConcatDataset, DataLoader, Dataset

# 把 backend 目录加入路径，以便 import app（同 train_sparse_reconstructor.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.surrogate.dataset import SurrogateDataset  # noqa: E402
from app.ml.surrogate.model import BaselineResidualMLP  # noqa: E402


class SimSupplementDataset(Dataset):
    """仿真补充集：校准伪标签（仅 fs/fp/kt2 有效）+ 样本权重 + 参数掩码。

    与 SurrogateDataset 输出同构（x/y_params/params_base/sparse 占位），
    另带 w（样本权重）与 pmask（参数有效掩码，Q 系全 0）。
    归一化统计与基准表直接复用实测数据目录，保证同一空间。
    """

    def __init__(self, sim_npz: str, real: SurrogateDataset, data_dir: str) -> None:
        from app.ml.surrogate.baseline import BaselineTable

        with np.load(sim_npz) as z:
            x_raw = z["x_struct"]  # (M,5) [eg,fl,ag,area,pf]
            p3 = z["params3"]  # (M,3) [fs,fp,kt2]（校准后）
        self.x = (torch.from_numpy(x_raw) - real.x_mean) / real.x_std
        y7 = torch.zeros(len(x_raw), 7)
        y7[:, :3] = torch.from_numpy(p3)
        self.y_params = (y7 - real.p_mean) / real.p_std
        baseline = BaselineTable.load(Path(data_dir) / "baseline.npz")
        pb = np.stack([baseline.lookup(float(r[3]), float(r[4]))[0] for r in x_raw])
        self.params_base = (torch.from_numpy(pb).float() - real.p_mean) / real.p_std
        self.k = real.k

    def __len__(self) -> int:
        return len(self.x)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        return {
            "x": self.x[idx],
            "y_params": self.y_params[idx],
            "params_base": self.params_base[idx],
            "y_sparse": torch.zeros(self.k),
            "sparse_base": torch.zeros(self.k),
            "group_idx": torch.tensor(-1),
            "w": torch.tensor(0.0),  # 占位，训练循环里对 sim 行统一乘 sim_weight
            "pmask": torch.tensor([1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
            "is_sim": torch.tensor(True),
        }


class _RealWithWeight(Dataset):
    """给 SurrogateDataset 补 w/pmask/is_sim 字段的薄包装。"""

    def __init__(self, ds: SurrogateDataset) -> None:
        self.ds = ds

    def __len__(self) -> int:
        return len(self.ds)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        item = dict(self.ds[idx])
        item["w"] = torch.tensor(1.0)
        item["pmask"] = torch.ones(7)
        item["is_sim"] = torch.tensor(False)
        return item


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
    sim_npz: str = "",
    sim_weight: float = 0.3,
    smoke: bool = False,
    seed: int | None = None,
) -> dict:
    if seed is not None:
        torch.manual_seed(seed)
        np.random.seed(seed)
    print(f"seed={seed}")
    dev = _device()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    train_ds = SurrogateDataset(data_dir, split="train")
    val_ds = SurrogateDataset(data_dir, split="val")
    if smoke:
        epochs = min(epochs, 2)
    train_mix: Dataset = _RealWithWeight(train_ds)
    if sim_npz:
        sim_ds = SimSupplementDataset(sim_npz, train_ds, data_dir)
        train_mix = ConcatDataset([train_mix, sim_ds])
        print(f"混合训练：实测 {len(train_ds)} + 仿真补充 {len(sim_ds)}（权重 {sim_weight}）")
    train_dl = DataLoader(train_mix, batch_size=batch_size, shuffle=True, drop_last=False)
    val_dl = DataLoader(_RealWithWeight(val_ds), batch_size=batch_size)

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
                w = batch["w"].to(dev)
                w = torch.where(batch["is_sim"].to(dev), w + sim_weight, w)  # sim 行权重
                pmask = batch["pmask"].to(dev)
                out_pred = model(x, pb, sb)
                dparams = (out_pred["params"] - yp) ** 2 * pmask
                loss_params = (dparams * w.unsqueeze(1)).sum() / (w.unsqueeze(1) * pmask).sum()
                dsparse = (out_pred["sparse"] - ys) ** 2
                w_sparse = torch.where(batch["is_sim"].to(dev), torch.zeros_like(w), w)
                loss_sparse = (dsparse * w_sparse.unsqueeze(1)).sum() / (
                    w_sparse.sum() * dsparse.shape[1] + 1e-12
                )
                loss = loss_params + lambda_sparse * loss_sparse
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
    ap.add_argument("--sim-npz", default="", help="仿真补充集（build_sim_supplement.py 产物）")
    ap.add_argument("--sim-weight", type=float, default=0.3, help="仿真样本权重（Q 已掩码）")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（默认每次随机）")
    args = ap.parse_args()
    train(
        args.data,
        args.out,
        args.epochs,
        args.batch_size,
        args.lr,
        args.lambda_sparse,
        args.patience,
        args.sim_npz,
        args.sim_weight,
        args.smoke,
        args.seed,
    )


if __name__ == "__main__":
    main()
