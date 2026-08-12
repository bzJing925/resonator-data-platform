"""Stage 2 训练：稀疏点 + cond → 全谱（规格 §7，主路径=从头训练）。

用法：
    uv run python scripts/train_stage2_surrogate.py --data ./surrogate_data \
        --out ./checkpoints_surrogate --epochs 200
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

# 把 backend 目录加入路径，以便 import app（同 train_sparse_reconstructor.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.sparse_sampler import baseline_interpolate  # noqa: E402
from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402

_COND_IDX = [0, 1, 3, 4, 2]  # y_params 中 [fs, fp, Qs, Qp, kt2] 的列索引


class Stage2Dataset(Dataset):
    """npz → reconstructor 训练对（z 用原始 dB，与 reconstructor 既有设计一致）。"""

    def __init__(self, data_dir: str | Path, split: str = "train") -> None:
        data_dir = Path(data_dir)
        meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
        keep = {i for i, g in enumerate(meta["groups"]) if g["split"] == split}
        cond_idx = np.asarray(_COND_IDX)
        cond_mean = np.asarray(meta["params_mean"], dtype=np.float32)[cond_idx]
        cond_std = np.asarray(meta["params_std"], dtype=np.float32)[cond_idx]
        z_mean = float(meta["z_mean"][0])
        z_std = float(meta["z_std"][0])
        with np.load(data_dir / "dataset.npz") as data:
            mask = np.array([ci in keep for ci in data["group_idx"]])
            # cond/z 归一化（O(1) 激活；原始 dB/百位 Q 值会压垮 LayerNorm 的位置分辨）
            self.cond = torch.from_numpy(
                (data["y_params"][mask][:, _COND_IDX] - cond_mean) / cond_std
            ).float()
            self.samples = np.stack(
                [
                    np.asarray(meta["freq_ghz"], dtype=np.float32)[data["sample_pos"][mask]],
                    (data["y_sparse"][mask] - z_mean) / z_std,
                ],
                axis=-1,
            ).astype(np.float32)  # (M, K, 2)
            self.target = torch.from_numpy((data["full_z"][mask] - z_mean) / z_std).float()
        self.freq = np.asarray(meta["freq_ghz"], dtype=np.float32)
        self.z_baseline = torch.from_numpy(
            np.stack([baseline_interpolate(s[:, 0], s[:, 1], self.freq) for s in self.samples])
        ).float()

    def __len__(self) -> int:
        return len(self.cond)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        return {
            "cond": self.cond[idx],
            "samples": torch.from_numpy(self.samples[idx]),
            "freq_target": torch.from_numpy(self.freq),
            "z_baseline": self.z_baseline[idx],
            "target": self.target[idx],
        }


def train(
    data_dir: str,
    out_dir: str,
    epochs: int = 200,
    batch_size: int = 256,
    lr: float = 1e-3,
    patience: int = 30,
    smoke: bool = False,
) -> dict:
    dev = (
        torch.device("mps")
        if torch.backends.mps.is_available()
        else torch.device("cuda")
        if torch.cuda.is_available()
        else torch.device("cpu")
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    train_dl = DataLoader(Stage2Dataset(data_dir, "train"), batch_size=batch_size, shuffle=True)
    val_dl = DataLoader(Stage2Dataset(data_dir, "val"), batch_size=batch_size)
    if smoke:
        epochs = min(epochs, 2)

    # max_freq=0.1 必须与保存的 stage2_config.json 一致（频率标尺，勿改默认 10.0）
    model = SparseReconstructor(
        d_model=64, n_encoder_layers=4, n_heads=4, dropout=0.1, max_freq=0.1
    ).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)

    def run_epoch(dl, train_mode: bool) -> float:
        model.train(train_mode)
        total, n = 0.0, 0
        with torch.set_grad_enabled(train_mode):
            for b in dl:
                cond = b["cond"].to(dev)
                samples = b["samples"].to(dev)
                freq_t = b["freq_target"].to(dev)
                zb = b["z_baseline"].to(dev)
                target = b["target"].to(dev)
                pred = model(cond, samples, freq_t, z_baseline=zb)
                loss = torch.nn.functional.mse_loss(pred, target)
                if train_mode:
                    opt.zero_grad()
                    loss.backward()
                    opt.step()
                total += loss.item() * len(cond)
                n += len(cond)
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
            print(f"epoch {epoch}: train={tr:.4f} val={va:.4f} best={best_val:.4f}")
        if bad >= patience:
            print(f"early stop at epoch {epoch}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    torch.save(model.state_dict(), out / "stage2.pt")
    (out / "stage2_config.json").write_text(
        json.dumps(
            {
                "d_model": 64,
                "n_encoder_layers": 4,
                "n_heads": 4,
                "dropout": 0.1,
                "max_freq": 0.1,
            },  # 频率标尺：4–7 GHz 映射到 40–70，正弦位置编码可分辨
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "stage2_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"saved: {out / 'stage2.pt'} (best val={best_val:.4f})")
    return {"best_val": best_val, "epochs_run": len(history)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="./checkpoints_surrogate")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    train(args.data, args.out, args.epochs, args.batch_size, args.lr, args.patience, args.smoke)


if __name__ == "__main__":
    main()
