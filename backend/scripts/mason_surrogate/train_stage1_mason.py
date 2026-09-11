"""WP4a Stage 1：统一浅层 MLP（x7 → 7 参数 + 256 稀疏值），EPE+EPPE 共训。

- 直接从 x7 预测（无 baseline 查表）；归一化统计只从 train 计算（结构标志列除外）。
- 稀疏位置：前 warmup_epochs 轮用 GT fp_ratio（热身）；之后切换到模型自预测
  （detach：fp_ratio_pred = fp_pred/fs_pred，位置计算不参与反传）。
- loss = params 归一化 MSE + lambda_sparse × sparse 归一化 MSE（掩码外不计）。
- 早停用 val 总 loss；val/test 稀疏评估固定在 GT 位置（统一标尺）。

用法：
    uv run python scripts/mason_surrogate/train_stage1_mason.py --epochs 300
冒烟：
    uv run python scripts/mason_surrogate/train_stage1_mason.py --epochs 2 --warmup-epochs 1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from numpy.typing import NDArray

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
for p in (PROJECT_ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mason_surrogate.common import (  # noqa: E402
    UnifiedMLP,
    apply_norm,
    batch_sparse_positions,
    fit_norm,
    invert_norm,
    masked_mse,
    param_metrics,
)

FP_RATIO_CLIP = (1.0005, 1.5)  # 自预测 fp_ratio 物理合法域（fp>fs）

DEFAULT_CFG = {
    "data": "surrogate_data_mason/unified_dataset.npz",
    "out_dir": "checkpoints_mason",
    "epochs": 300,
    "batch_size": 256,
    "lr": 1e-3,
    "weight_decay": 1e-4,
    "lambda_sparse": 0.1,
    "patience": 30,
    "warmup_epochs": 20,
    "hidden": 256,
    "n_res_blocks": 2,
    "k": 256,
    "seed": 20260911,
    "device": "cpu",
}


def load_unified(path: str | Path) -> dict[str, NDArray]:
    with np.load(path) as d:
        return {k: d[k] for k in d.files}


def predict_fp_ratio(params_denorm: NDArray[np.float64]) -> NDArray[np.float64]:
    """由（去归一化）预测参数算 fp_ratio 并裁剪到物理合法域。"""
    ratio = params_denorm[:, 1] / np.maximum(params_denorm[:, 0], 1e-9)
    return np.clip(ratio, FP_RATIO_CLIP[0], FP_RATIO_CLIP[1])


def _gather_at_positions(
    z_norm: torch.Tensor, mask: torch.Tensor, idx: torch.Tensor, pos: NDArray[np.int64]
) -> tuple[torch.Tensor, torch.Tensor]:
    """z_norm(M,N)/mask(M,N) 在 idx 行、pos(B,K) 列聚集 → (B,K)。"""
    pos_t = torch.from_numpy(pos)
    rows = torch.arange(len(idx))
    return z_norm[idx][rows[:, None], pos_t], mask[idx][rows[:, None], pos_t]


def train(cfg: dict) -> dict:
    t_start = time.time()
    torch.manual_seed(cfg["seed"])
    np.random.seed(cfg["seed"])
    dev = torch.device(cfg["device"])
    out_dir = Path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    data = load_unified(cfg["data"])
    grid = data["w_grid"].astype(np.float64)
    k = cfg["k"]
    split = data["split"]
    idx_all = np.arange(len(split))
    tr_np, va_np, te_np = (idx_all[split == s] for s in (0, 1, 2))

    # ---- 归一化统计（只用 train；结构标志列不归一化）----
    x_stats = fit_norm(data["x7"][tr_np], skip_cols=(0,))
    y_stats = fit_norm(data["y7"][tr_np])
    tr_valid_db = data["z_w_db"][tr_np][data["w_mask"][tr_np]]
    db_mean, db_std = float(tr_valid_db.mean()), float(max(tr_valid_db.std(), 1e-9))

    x_n = torch.from_numpy(apply_norm(data["x7"], x_stats))
    y_n = torch.from_numpy(apply_norm(data["y7"], y_stats))
    z_n = torch.from_numpy(((data["z_w_db"] - db_mean) / db_std).astype(np.float32))
    w_m = torch.from_numpy(data["w_mask"])

    # GT 稀疏位置（热身期 + val/test 评估的统一标尺）
    pos_gt = batch_sparse_positions(grid, data["fp_ratio"].astype(np.float64), k=k)

    model = UnifiedMLP(
        in_dim=7, hidden=cfg["hidden"], n_res_blocks=cfg["n_res_blocks"], n_params=7, k=k
    ).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=cfg["epochs"])
    gen = torch.Generator().manual_seed(cfg["seed"])

    tr = torch.from_numpy(tr_np)
    va = torch.from_numpy(va_np)

    def run_epoch(idx: torch.Tensor, epoch: int, train_mode: bool) -> tuple[float, float, float]:
        model.train(train_mode)
        total, tot_p, tot_s, n = 0.0, 0.0, 0.0, 0
        order = torch.randperm(len(idx), generator=gen) if train_mode else torch.arange(len(idx))
        with torch.set_grad_enabled(train_mode):
            for i0 in range(0, len(idx), cfg["batch_size"]):
                bi = idx[order[i0 : i0 + cfg["batch_size"]]]  # CPU 索引
                xb = x_n[bi].to(dev)
                yb = y_n[bi].to(dev)
                out = model(xb)
                if train_mode and epoch > cfg["warmup_epochs"]:
                    # 自预测位置（detach）：fp_ratio_pred 换算后算确定性位置
                    with torch.no_grad():
                        p_den = invert_norm(out["params"].detach().cpu().numpy(), y_stats)
                    pos = batch_sparse_positions(grid, predict_fp_ratio(p_den), k=k)
                else:
                    pos = pos_gt[bi.numpy()]
                tgt, valid = _gather_at_positions(z_n, w_m, bi, pos)
                tgt, valid = tgt.to(dev), valid.to(dev)
                loss_p = ((out["params"] - yb) ** 2).mean()
                loss_s = masked_mse(out["sparse"], tgt, valid)
                loss = loss_p + cfg["lambda_sparse"] * loss_s
                if train_mode:
                    opt.zero_grad()
                    loss.backward()
                    opt.step()
                b = len(bi)
                total += loss.item() * b
                tot_p += loss_p.item() * b
                tot_s += loss_s.item() * b
                n += b
        return total / max(n, 1), tot_p / max(n, 1), tot_s / max(n, 1)

    best_val, best_state, best_epoch, bad = float("inf"), None, 0, 0
    history = []
    for epoch in range(1, cfg["epochs"] + 1):
        t_ep = time.time()
        tr_l, tr_p, tr_s = run_epoch(tr, epoch, True)
        va_l, va_p, va_s = run_epoch(va, epoch, False)  # val 恒用 GT 位置
        sched.step()
        history.append(
            {"epoch": epoch, "train": tr_l, "val": va_l, "train_params": tr_p,
             "train_sparse": tr_s, "val_params": va_p, "val_sparse": va_s}
        )
        if va_l < best_val - 1e-6:
            best_val, best_epoch, bad = va_l, epoch, 0
            best_state = {k_: v.detach().cpu().clone() for k_, v in model.state_dict().items()}
        else:
            bad += 1
        if epoch == 1 or epoch % 10 == 0:
            print(
                f"epoch {epoch}: train={tr_l:.5f} (p={tr_p:.5f} s={tr_s:.5f}) "
                f"val={va_l:.5f} best={best_val:.5f}@{best_epoch} [{time.time() - t_ep:.1f}s]",
                flush=True,
            )
        if bad >= cfg["patience"]:
            print(f"early stop at epoch {epoch}", flush=True)
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    # ---- 评估（test，按结构分开；稀疏在 GT 位置、去归一化到 dB）----
    ev = evaluate(model, data, pos_gt, x_stats, y_stats, db_mean, db_std, dev)

    torch.save(
        {"model_state": model.state_dict(), "hidden": cfg["hidden"],
         "n_res_blocks": cfg["n_res_blocks"], "k": k, "best_epoch": best_epoch},
        out_dir / "stage1.pt",
    )
    config = {
        **{k_: cfg[k_] for k_ in ("epochs", "batch_size", "lr", "weight_decay", "lambda_sparse",
                                  "patience", "warmup_epochs", "hidden", "n_res_blocks", "k",
                                  "seed")},
        "best_epoch": best_epoch,
        "best_val": best_val,
        "train_seconds": time.time() - t_start,
        "x7_mean": x_stats.mean.tolist(), "x7_std": x_stats.std.tolist(),
        "y7_mean": y_stats.mean.tolist(), "y7_std": y_stats.std.tolist(),
        "db_mean": db_mean, "db_std": db_std,
        "fp_ratio_clip": list(FP_RATIO_CLIP),
        "norm_note": "归一化统计仅由 train 计算；x7 第 0 列（structure_flag）不归一化",
    }
    (out_dir / "stage1_config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "stage1_history.json").write_text(
        json.dumps(history, indent=2), encoding="utf-8"
    )
    train_seconds = time.time() - t_start
    (out_dir / "stage1_eval.json").write_text(
        json.dumps({"eval": ev, "best_epoch": best_epoch, "best_val": best_val,
                    "epochs_run": len(history), "train_seconds": train_seconds},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"saved: {out_dir / 'stage1.pt'} (best val={best_val:.5f} @epoch {best_epoch})")
    print_eval_stage1(ev)
    return {"best_val": best_val, "best_epoch": best_epoch, "epochs_run": len(history),
            "history": history, "eval": ev, "train_seconds": train_seconds}


@torch.no_grad()
def evaluate(
    model: UnifiedMLP,
    data: dict[str, NDArray],
    pos_gt: NDArray[np.int64],
    x_stats,
    y_stats,
    db_mean: float,
    db_std: float,
    dev: torch.device,
) -> dict:
    """test 集按结构分开：7 参数 MAE + sparse RMSE(dB，GT 位置、掩码内）。"""
    model.eval()
    x_n = torch.from_numpy(apply_norm(data["x7"], x_stats)).to(dev)
    preds = []
    for i0 in range(0, len(x_n), 1024):
        preds.append(model(x_n[i0 : i0 + 1024])["params"].cpu().numpy())
    params_pred = invert_norm(np.concatenate(preds, axis=0), y_stats)

    # 稀疏：GT 位置处预测（去归一化回 dB）
    sparse_pred = []
    for i0 in range(0, len(x_n), 1024):
        sparse_pred.append(model(x_n[i0 : i0 + 1024])["sparse"].cpu().numpy())
    sparse_pred = np.concatenate(sparse_pred, axis=0) * db_std + db_mean

    rows = np.arange(len(data["split"]))[:, None]
    sparse_tgt = data["z_w_db"][rows, pos_gt]
    sparse_val = data["w_mask"][rows, pos_gt]

    split = data["split"]
    struct = data["x7"][:, 0].astype(np.int64)
    out = {}
    for name, sel in [
        ("epe", struct == 0), ("eppe", struct == 1), ("all", np.ones(len(split), bool))
    ]:
        te = sel & (split == 2)
        m = param_metrics(params_pred[te], data["y7"][te].astype(np.float64))
        d2 = (sparse_pred[te] - sparse_tgt[te]) ** 2
        v = sparse_val[te]
        m["sparse_rmse_db"] = float(np.sqrt(d2[v].mean())) if v.any() else float("nan")
        m["n_train"] = int((sel & (split == 0)).sum())
        m["n_val"] = int((sel & (split == 1)).sum())
        m["n_test"] = int(te.sum())
        out[name] = m
    return out


def print_eval_stage1(ev: dict) -> None:
    print("\n== Stage 1 test 评估（按结构）==")
    hdr = ("struct", "fs_MHz", "fp_MHz", "k2_pp", "Qs_%", "Qp_%", "Qsb_%", "Qpb_%",
           "sparse_dB", "n_test")
    print("{:<7}{:>9}{:>9}{:>8}{:>8}{:>8}{:>8}{:>8}{:>11}{:>8}".format(*hdr))
    for name, m in ev.items():
        print(
            f"{name:<7}{m['fs_mae_mhz']:>9.2f}{m['fp_mae_mhz']:>9.2f}{m['k2eff_mae_pp']:>8.3f}"
            f"{m['qs_mae_rel_pct']:>8.2f}{m['qp_mae_rel_pct']:>8.2f}"
            f"{m['qs_bodeq_mae_rel_pct']:>8.2f}{m['qp_bodeq_mae_rel_pct']:>8.2f}"
            f"{m['sparse_rmse_db']:>11.3f}{m['n_test']:>8d}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="WP4a Stage 1 统一 MLP 训练")
    ap.add_argument("--data", default=DEFAULT_CFG["data"])
    ap.add_argument("--out", dest="out_dir", default=DEFAULT_CFG["out_dir"])
    for key in ("epochs", "batch_size", "patience", "warmup_epochs", "hidden", "n_res_blocks",
                "k", "seed"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=int, default=DEFAULT_CFG[key])
    for key in ("lr", "weight_decay", "lambda_sparse"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=DEFAULT_CFG[key])
    ap.add_argument("--device", default=DEFAULT_CFG["device"])
    args = ap.parse_args()
    train({**DEFAULT_CFG, **vars(args)})


if __name__ == "__main__":
    main()
