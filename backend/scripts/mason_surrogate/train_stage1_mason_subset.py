"""WP4b Stage 1 轻量子集训练入口：与 train_stage1_mason 同语义 + --init-from/--init-config。

为何存在：WP4b 数据效率曲线需要"EPE 预训练 → EPPE 子集微调"，而
train_stage1_mason.py 的 CLI 不支持从已有 checkpoint 初始化（该脚本正由另一
任务加固，不能改）。本入口复用 common.py 的稳定公共件（UnifiedMLP/归一化/稀疏
位置/掩码损失/参数指标），语义与 train_stage1_mason 对齐：
- loss = params 归一化 MSE + lambda_sparse × sparse 归一化 MSE（掩码外不计）
- 前 warmup_epochs 用 GT fp_ratio 稀疏位置，之后切自预测（detach）
- 早停用 val 总 loss；评估稀疏固定在 GT 位置
差异：
- --init-from：从已有 stage1.pt 加载模型权重（微调模式）
- --init-config：复用该 config 的归一化统计（x7/y7/db），不在子集上重拟合
  （迁移学习口径：预处理统计是模型资产的一部分，随预训练 checkpoint 迁移）
- 评估对空结构组鲁棒（子集可能只含单一结构）
- 落盘 dataset_sha256 绑定数据版本

用法：
    uv run python scripts/mason_surrogate/train_stage1_mason_subset.py \
        --data checkpoints_mason/dataeff/subsets/scratch_n10_s20260913.npz \
        --out checkpoints_mason/dataeff/s1_scratch_n10_s20260913
    # 微调：
    uv run python scripts/mason_surrogate/train_stage1_mason_subset.py \
        --data .../subset.npz --out .../s1_finetune_n10_s20260913 \
        --init-from checkpoints_mason/dataeff/s1_pretrain/stage1.pt \
        --init-config checkpoints_mason/dataeff/s1_pretrain/stage1_config.json --lr 1e-4
"""

from __future__ import annotations

import argparse
import hashlib
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
    NormStats,
    UnifiedMLP,
    apply_norm,
    batch_sparse_positions,
    fit_norm,
    invert_norm,
    masked_mse,
    param_metrics,
)

FP_RATIO_CLIP = (1.0005, 1.5)  # 与 train_stage1_mason 一致

DEFAULT_CFG = {
    "data": "surrogate_data_mason/unified_dataset.npz",
    "out_dir": "checkpoints_mason/dataeff/s1_default",
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
    "seed": 20260913,
    "device": "cpu",
    "init_from": None,
    "init_config": None,
}


def load_unified(path: str | Path) -> dict[str, NDArray]:
    with np.load(path) as d:
        return {k: d[k] for k in d.files}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def predict_fp_ratio(params_denorm: NDArray[np.float64]) -> NDArray[np.float64]:
    ratio = params_denorm[:, 1] / np.maximum(params_denorm[:, 0], 1e-9)
    return np.clip(ratio, FP_RATIO_CLIP[0], FP_RATIO_CLIP[1])


def resolve_norm_stats(cfg: dict, data: dict[str, NDArray], tr_np: NDArray) -> tuple:
    """归一化统计：--init-config 时复用预训练统计（迁移口径），否则由本数据集 train 拟合。"""
    if cfg["init_config"]:
        ic = json.loads(Path(cfg["init_config"]).read_text(encoding="utf-8"))
        x_stats = NormStats(np.array(ic["x7_mean"]), np.array(ic["x7_std"]))
        y_stats = NormStats(np.array(ic["y7_mean"]), np.array(ic["y7_std"]))
        return x_stats, y_stats, float(ic["db_mean"]), float(ic["db_std"]), "init_config"
    x_stats = fit_norm(data["x7"][tr_np], skip_cols=(0,))
    y_stats = fit_norm(data["y7"][tr_np])
    tr_valid_db = data["z_w_db"][tr_np][data["w_mask"][tr_np]]
    db_mean = float(tr_valid_db.mean())
    db_std = float(max(tr_valid_db.std(), 1e-9))
    return x_stats, y_stats, db_mean, db_std, "fit_train"


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
    tr_np, va_np = idx_all[split == 0], idx_all[split == 1]

    x_stats, y_stats, db_mean, db_std, norm_source = resolve_norm_stats(cfg, data, tr_np)

    x_n = torch.from_numpy(apply_norm(data["x7"], x_stats))
    y_n = torch.from_numpy(apply_norm(data["y7"], y_stats))
    z_n = torch.from_numpy(((data["z_w_db"] - db_mean) / db_std).astype(np.float32))
    w_m = torch.from_numpy(data["w_mask"])
    pos_gt = batch_sparse_positions(grid, data["fp_ratio"].astype(np.float64), k=k)

    model = UnifiedMLP(
        in_dim=7, hidden=cfg["hidden"], n_res_blocks=cfg["n_res_blocks"], n_params=7, k=k
    ).to(dev)
    if cfg["init_from"]:
        init = torch.load(cfg["init_from"], map_location="cpu", weights_only=True)
        model.load_state_dict(init["model_state"])
        print(f"init from {cfg['init_from']} (best_epoch={init.get('best_epoch')})", flush=True)
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
                bi = idx[order[i0 : i0 + cfg["batch_size"]]]
                xb = x_n[bi].to(dev)
                yb = y_n[bi].to(dev)
                out = model(xb)
                if train_mode and epoch > cfg["warmup_epochs"]:
                    with torch.no_grad():
                        p_den = invert_norm(out["params"].detach().cpu().numpy(), y_stats)
                    pos = batch_sparse_positions(grid, predict_fp_ratio(p_den), k=k)
                else:
                    pos = pos_gt[bi.numpy()]
                pos_t = torch.from_numpy(pos)
                rows = torch.arange(len(bi))
                tgt = z_n[bi][rows[:, None], pos_t].to(dev)
                valid = w_m[bi][rows[:, None], pos_t].to(dev)
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
        va_l, va_p, va_s = run_epoch(va, epoch, False)
        sched.step()
        history.append({"epoch": epoch, "train": tr_l, "val": va_l,
                        "train_params": tr_p, "train_sparse": tr_s,
                        "val_params": va_p, "val_sparse": va_s})
        if va_l < best_val - 1e-6:
            best_val, best_epoch, bad = va_l, epoch, 0
            best_state = {k_: v.detach().cpu().clone() for k_, v in model.state_dict().items()}
        else:
            bad += 1
        if epoch == 1 or epoch % 10 == 0:
            print(f"epoch {epoch}: train={tr_l:.5f} val={va_l:.5f} "
                  f"best={best_val:.5f}@{best_epoch} [{time.time() - t_ep:.1f}s]", flush=True)
        if bad >= cfg["patience"]:
            print(f"early stop at epoch {epoch}", flush=True)
            break
    if best_state is not None:
        model.load_state_dict(best_state)

    ev = evaluate(model, data, pos_gt, x_stats, y_stats, db_mean, db_std, dev)
    train_seconds = time.time() - t_start

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
        "epochs_run": len(history),
        "train_seconds": train_seconds,
        "x7_mean": x_stats.mean.tolist(), "x7_std": x_stats.std.tolist(),
        "y7_mean": y_stats.mean.tolist(), "y7_std": y_stats.std.tolist(),
        "db_mean": db_mean, "db_std": db_std,
        "fp_ratio_clip": list(FP_RATIO_CLIP),
        "norm_source": norm_source,
        "init_from": cfg["init_from"],
        "init_config": cfg["init_config"],
        "dataset_sha256": sha256_file(cfg["data"]),
    }
    (out_dir / "stage1_config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "stage1_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    (out_dir / "stage1_eval.json").write_text(
        json.dumps({"eval": ev, "best_epoch": best_epoch, "best_val": best_val,
                    "epochs_run": len(history), "train_seconds": train_seconds},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"saved: {out_dir / 'stage1.pt'} (best val={best_val:.5f} @epoch {best_epoch}, "
          f"{train_seconds:.0f}s)", flush=True)
    for name, m in ev.items():
        print(f"  [{name}] fs={m['fs_mae_mhz']:.2f}MHz fp={m['fp_mae_mhz']:.2f}MHz "
              f"k2={m['k2eff_mae_pp']:.3f}pp sparse={m['sparse_rmse_db']:.3f}dB "
              f"n_test={m['n_test']}", flush=True)
    return {"best_val": best_val, "best_epoch": best_epoch, "epochs_run": len(history),
            "eval": ev, "train_seconds": train_seconds}


@torch.no_grad()
def evaluate(
    model: UnifiedMLP,
    data: dict[str, NDArray],
    pos_gt: NDArray[np.int64],
    x_stats: NormStats,
    y_stats: NormStats,
    db_mean: float,
    db_std: float,
    dev: torch.device,
) -> dict:
    """test 集按结构评估（空结构组跳过，子集鲁棒）。"""
    model.eval()
    x_n = torch.from_numpy(apply_norm(data["x7"], x_stats)).to(dev)
    preds, sparse_preds = [], []
    for i0 in range(0, len(x_n), 1024):
        out = model(x_n[i0 : i0 + 1024])
        preds.append(out["params"].cpu().numpy())
        sparse_preds.append(out["sparse"].cpu().numpy())
    params_pred = invert_norm(np.concatenate(preds, axis=0), y_stats)
    sparse_pred = np.concatenate(sparse_preds, axis=0) * db_std + db_mean

    rows = np.arange(len(data["split"]))[:, None]
    sparse_tgt = data["z_w_db"][rows, pos_gt]
    sparse_val = data["w_mask"][rows, pos_gt]

    split = data["split"]
    struct = data["x7"][:, 0].astype(np.int64)
    out = {}
    for name, sel in [("epe", struct == 0), ("eppe", struct == 1)]:
        te = sel & (split == 2)
        if not te.any():
            continue
        m = param_metrics(params_pred[te], data["y7"][te].astype(np.float64))
        d2 = (sparse_pred[te] - sparse_tgt[te]) ** 2
        v = sparse_val[te]
        m["sparse_rmse_db"] = float(np.sqrt(d2[v].mean())) if v.any() else float("nan")
        m["n_train"] = int((sel & (split == 0)).sum())
        m["n_val"] = int((sel & (split == 1)).sum())
        m["n_test"] = int(te.sum())
        out[name] = m
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="WP4b Stage 1 子集训练（支持预训练初始化）")
    ap.add_argument("--data", default=DEFAULT_CFG["data"])
    ap.add_argument("--out", dest="out_dir", default=DEFAULT_CFG["out_dir"])
    for key in ("epochs", "batch_size", "patience", "warmup_epochs", "hidden", "n_res_blocks",
                "k", "seed"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=int, default=DEFAULT_CFG[key])
    for key in ("lr", "weight_decay", "lambda_sparse"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=DEFAULT_CFG[key])
    ap.add_argument("--device", default=DEFAULT_CFG["device"])
    ap.add_argument("--init-from", default=None, help="预训练 stage1.pt 路径（微调初始化）")
    ap.add_argument("--init-config", default=None,
                    help="预训练 stage1_config.json（复用归一化统计）")
    args = ap.parse_args()
    train({**DEFAULT_CFG, **vars(args)})


if __name__ == "__main__":
    main()
