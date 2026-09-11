"""WP4b Stage 2 轻量子集训练入口：与 train_stage2_mason 同语义 + --init-from/--init-config。

为何存在：数据效率锚点组需要"EPE-only Stage 2 预训练 → EPPE 100 样本微调"，而
train_stage2_mason.py 的 CLI 不支持 checkpoint 初始化（该脚本正由另一任务加固，
不能改）。本入口复用 common.py 稳定公共件与生产 SparseReconstructor，语义对齐：
- cond=[fs,fp,Qs,Qp,kt2] 取 Stage 1 预测值（推理口径）；kt2=k2eff_pct 百分数
- 稀疏位置由 Stage 1 预测 fp_ratio 确定性算出；值取 w 网格 dB（归一化）
- 样条基线一次性预计算；loss=掩码内归一化 MSE（训练/val 用隔点子网格提速）
差异：
- --init-from/--init-config：加载 Stage 2 权重并复用 cond/db 归一化统计
- --max-freq 直接指定（默认 1.0，沿用 WP4a compare 结论），不再跑候选对比
- 评估对空结构组鲁棒；落盘 dataset_sha256

用法：
    uv run python scripts/mason_surrogate/train_stage2_mason_subset.py \
        --data checkpoints_mason/dataeff/subsets/pretrain.npz \
        --stage1-ckpt checkpoints_mason/dataeff/s1_pretrain/stage1.pt \
        --stage1-config checkpoints_mason/dataeff/s1_pretrain/stage1_config.json \
        --out checkpoints_mason/dataeff/s2_pretrain --epochs 40
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

from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402
from mason_surrogate.common import (  # noqa: E402
    NormStats,
    UnifiedMLP,
    apply_norm,
    batch_sparse_positions,
    build_cond,
    build_sparse_samples,
    fit_norm,
    masked_mse,
    precompute_baselines,
)

FP_RATIO_CLIP = (1.0005, 1.5)

DEFAULT_CFG = {
    "data": "surrogate_data_mason/unified_dataset.npz",
    "stage1_ckpt": "checkpoints_mason/stage1.pt",
    "stage1_config": "checkpoints_mason/stage1_config.json",
    "out_dir": "checkpoints_mason/dataeff/s2_default",
    "epochs": 40,
    "batch_size": 256,
    "lr": 1e-3,
    "weight_decay": 1e-4,
    "patience": 20,
    "k": 256,
    "d_model": 64,
    "n_layers": 4,
    "n_heads": 4,
    "dim_feedforward": 256,
    "dropout": 0.1,
    "max_freq": 1.0,  # 沿用 WP4a compare 结论（w 轴 1.0 优于 0.1）
    "seed": 20260913,
    "device": "cpu",
    "num_threads": 10,
    "train_target_stride": 2,
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


@torch.no_grad()
def stage1_predict(cfg: dict, data: dict[str, NDArray]) -> tuple[NDArray, NDArray]:
    """Stage 1 checkpoint 前向：去归一化 7 参数 + 归一化 dB 稀疏值（K 个）。"""
    s1_cfg = json.loads(Path(cfg["stage1_config"]).read_text(encoding="utf-8"))
    ckpt = torch.load(cfg["stage1_ckpt"], map_location="cpu", weights_only=True)
    model = UnifiedMLP(in_dim=7, hidden=ckpt["hidden"], n_res_blocks=ckpt["n_res_blocks"],
                       n_params=7, k=ckpt["k"])
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    x_stats = NormStats(np.array(s1_cfg["x7_mean"]), np.array(s1_cfg["x7_std"]))
    y_stats = NormStats(np.array(s1_cfg["y7_mean"]), np.array(s1_cfg["y7_std"]))
    x_n = torch.from_numpy(apply_norm(data["x7"], x_stats))
    params, sparse = [], []
    for i0 in range(0, len(x_n), 1024):
        out = model(x_n[i0 : i0 + 1024])
        params.append(out["params"].numpy())
        sparse.append(out["sparse"].numpy())
    params_pred = np.concatenate(params, axis=0) * y_stats.std + y_stats.mean
    return params_pred, np.concatenate(sparse, axis=0)


def prepare(cfg: dict) -> dict:
    """加载数据 + Stage 1 预测，构建 Stage 2 全部输入张量（一次性预计算）。"""
    t0 = time.time()
    data = load_unified(cfg["data"])
    grid = data["w_grid"].astype(np.float64)
    split = data["split"]
    idx = np.arange(len(split))
    tr_np, va_np, te_np = (idx[split == s] for s in (0, 1, 2))

    s1_cfg = json.loads(Path(cfg["stage1_config"]).read_text(encoding="utf-8"))
    db_mean, db_std = float(s1_cfg["db_mean"]), float(s1_cfg["db_std"])

    params_pred, sparse_pred_norm = stage1_predict(cfg, data)
    fp_ratio_pred = predict_fp_ratio(params_pred)

    cond_raw = build_cond(params_pred)
    if cfg["init_config"]:
        ic = json.loads(Path(cfg["init_config"]).read_text(encoding="utf-8"))
        cond_stats = NormStats(np.array(ic["cond_mean"]), np.array(ic["cond_std"]))
        norm_source = "init_config"
    else:
        cond_stats = fit_norm(cond_raw[tr_np])
        norm_source = "fit_train"

    pos = batch_sparse_positions(grid, fp_ratio_pred, k=cfg["k"])
    samples_raw, valid = build_sparse_samples(data["z_w_db"], data["w_mask"], pos, grid)
    samples = samples_raw.copy()
    samples[..., 1] = (samples[..., 1] - db_mean) / db_std
    samples_e2e = samples_raw.copy()
    samples_e2e[..., 1] = sparse_pred_norm.astype(np.float32)

    t_base = time.time()
    baseline = precompute_baselines(pos, samples[..., 1], valid, grid, fill_value=0.0)
    baseline_e2e = precompute_baselines(pos, samples_e2e[..., 1], valid, grid, fill_value=0.0)
    print(f"baseline precompute: {time.time() - t_base:.1f}s", flush=True)

    arrays = {
        "grid": grid,
        "grid_t": torch.from_numpy(grid.astype(np.float32)),
        "cond": torch.from_numpy(apply_norm(cond_raw, cond_stats)),
        "samples": torch.from_numpy(samples),
        "sample_pad": torch.from_numpy(~valid),
        "baseline": torch.from_numpy(baseline.astype(np.float32)),
        "samples_e2e": torch.from_numpy(samples_e2e),
        "baseline_e2e": torch.from_numpy(baseline_e2e.astype(np.float32)),
        "target": torch.from_numpy(((data["z_w_db"] - db_mean) / db_std).astype(np.float32)),
        "target_mask": torch.from_numpy(data["w_mask"]),
        "tr": torch.from_numpy(tr_np), "va": torch.from_numpy(va_np),
        "te": torch.from_numpy(te_np),
        "data": data,
        "cond_stats": cond_stats,
        "norm_source": norm_source,
        "db_mean": db_mean, "db_std": db_std,
    }
    print(f"stage2 prepare: {time.time() - t0:.1f}s "
          f"(n_train={len(tr_np)} n_val={len(va_np)} n_test={len(te_np)})", flush=True)
    return arrays


def train_reconstructor(cfg: dict, a: dict) -> dict:
    dev = torch.device(cfg["device"])
    torch.manual_seed(cfg["seed"])
    model = SparseReconstructor(
        d_model=cfg["d_model"], n_encoder_layers=cfg["n_layers"], n_heads=cfg["n_heads"],
        dim_feedforward=cfg["dim_feedforward"], dropout=cfg["dropout"],
        max_freq=float(cfg["max_freq"]),
    ).to(dev)
    if cfg["init_from"]:
        init = torch.load(cfg["init_from"], map_location="cpu", weights_only=True)
        model.load_state_dict(init["model_state"])
        print(f"init from {cfg['init_from']} (best_epoch={init.get('best_epoch')})", flush=True)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=cfg["epochs"])
    gen = torch.Generator().manual_seed(cfg["seed"])
    stride = int(cfg["train_target_stride"])
    grid_tr = a["grid_t"][::stride].to(dev)
    base_tr = a["baseline"][:, ::stride]
    tgt_tr = a["target"][:, ::stride]
    tmsk_tr = a["target_mask"][:, ::stride]
    tr, va = a["tr"], a["va"]

    def run_epoch(idx: torch.Tensor, train_mode: bool) -> float:
        model.train(train_mode)
        total, n = 0.0, 0
        order = torch.randperm(len(idx), generator=gen) if train_mode else torch.arange(len(idx))
        with torch.set_grad_enabled(train_mode):
            for i0 in range(0, len(idx), cfg["batch_size"]):
                bi = idx[order[i0 : i0 + cfg["batch_size"]]]
                pred = model(
                    cond=a["cond"][bi].to(dev),
                    samples=a["samples"][bi].to(dev),
                    freq_target=grid_tr,
                    z_baseline=base_tr[bi].to(dev),
                    sample_mask=a["sample_pad"][bi].to(dev),
                )
                loss = masked_mse(pred, tgt_tr[bi].to(dev), tmsk_tr[bi].to(dev))
                if train_mode:
                    opt.zero_grad()
                    loss.backward()
                    opt.step()
                total += loss.item() * len(bi)
                n += len(bi)
        return total / max(n, 1)

    best_val, best_state, best_epoch, bad = float("inf"), None, 0, 0
    history = []
    for epoch in range(1, cfg["epochs"] + 1):
        t_ep = time.time()
        tr_l = run_epoch(tr, True)
        va_l = run_epoch(va, False)
        sched.step()
        history.append({"epoch": epoch, "train": tr_l, "val": va_l})
        if va_l < best_val - 1e-6:
            best_val, best_epoch, bad = va_l, epoch, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        if epoch == 1 or epoch % 5 == 0:
            print(f"epoch {epoch}: train={tr_l:.5f} val={va_l:.5f} "
                  f"best={best_val:.5f}@{best_epoch} [{time.time() - t_ep:.1f}s]", flush=True)
        if bad >= cfg["patience"]:
            print(f"early stop at epoch {epoch}", flush=True)
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    return {"model": model, "best_val": best_val, "best_epoch": best_epoch, "history": history}


@torch.no_grad()
def evaluate(cfg: dict, a: dict, model: SparseReconstructor) -> dict:
    """test 集按结构评估（空组跳过）：e2e full RMSE + GT 稀疏诊断 full RMSE（dB）。"""
    dev = torch.device(cfg["device"])
    model.eval()
    grid_t = a["grid_t"].to(dev)
    te = a["te"]
    pred_gt, pred_e2e = [], []
    for i0 in range(0, len(te), 1024):
        bi = te[i0 : i0 + 1024]
        pred_gt.append(
            model(cond=a["cond"][bi].to(dev), samples=a["samples"][bi].to(dev),
                  freq_target=grid_t, z_baseline=a["baseline"][bi].to(dev),
                  sample_mask=a["sample_pad"][bi].to(dev)).cpu().numpy()
        )
        pred_e2e.append(
            model(cond=a["cond"][bi].to(dev), samples=a["samples_e2e"][bi].to(dev),
                  freq_target=grid_t, z_baseline=a["baseline_e2e"][bi].to(dev),
                  sample_mask=a["sample_pad"][bi].to(dev)).cpu().numpy()
        )
    gt_input_db = np.concatenate(pred_gt, axis=0) * a["db_std"] + a["db_mean"]
    e2e_db = np.concatenate(pred_e2e, axis=0) * a["db_std"] + a["db_mean"]

    data = a["data"]
    te_np = te.numpy()
    gt_db = data["z_w_db"][te_np].astype(np.float64)
    mask = data["w_mask"][te_np]
    struct = data["x7"][te_np, 0].astype(np.int64)

    out = {}
    for name, sel in [("epe", struct == 0), ("eppe", struct == 1)]:
        if not sel.any():
            continue
        m = mask[sel]
        d2_e2e = (e2e_db[sel] - gt_db[sel]) ** 2
        d2_gt = (gt_input_db[sel] - gt_db[sel]) ** 2
        out[name] = {
            "full_rmse_db": float(np.sqrt(d2_e2e[m].mean())) if m.any() else float("nan"),
            "gt_sparse_full_rmse_db": float(np.sqrt(d2_gt[m].mean())) if m.any() else float("nan"),
            "n_test": int(sel.sum()),
        }
    return out


def train(cfg: dict) -> dict:
    t_start = time.time()
    torch.set_num_threads(int(cfg["num_threads"]))
    out_dir = Path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    a = prepare(cfg)

    r = train_reconstructor(cfg, a)
    model = r["model"]
    ev = evaluate(cfg, a, model)
    train_seconds = time.time() - t_start

    torch.save(
        {"model_state": model.state_dict(), "d_model": cfg["d_model"],
         "n_layers": cfg["n_layers"], "n_heads": cfg["n_heads"],
         "dim_feedforward": cfg["dim_feedforward"], "dropout": cfg["dropout"],
         "max_freq": float(cfg["max_freq"]), "k": cfg["k"], "best_epoch": r["best_epoch"]},
        out_dir / "stage2.pt",
    )
    config = {
        **{k_: cfg[k_] for k_ in ("epochs", "batch_size", "lr", "weight_decay", "patience",
                                  "k", "d_model", "n_layers", "n_heads", "dim_feedforward",
                                  "dropout", "seed", "num_threads", "train_target_stride")},
        "epochs_run": len(r["history"]),
        "best_epoch": r["best_epoch"],
        "best_val": r["best_val"],
        "max_freq": float(cfg["max_freq"]),
        "max_freq_note": "固定 1.0（沿用 WP4a compare 结论；不再跑候选对比）",
        "cond_mean": a["cond_stats"].mean.tolist(),
        "cond_std": a["cond_stats"].std.tolist(),
        "cond_cols": ["fs", "fp", "Qs", "Qp", "kt2=k2eff_pct(百分数)"],
        "cond_source": "stage1 预测值",
        "norm_source": a["norm_source"],
        "init_from": cfg["init_from"],
        "init_config": cfg["init_config"],
        "db_mean": a["db_mean"], "db_std": a["db_std"],
        "stage1_ckpt": cfg["stage1_ckpt"],
        "stage1_config": cfg["stage1_config"],
        "dataset_sha256": sha256_file(cfg["data"]),
        "train_seconds": train_seconds,
    }
    (out_dir / "stage2_config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "stage2_history.json").write_text(json.dumps(r["history"], indent=2),
                                                 encoding="utf-8")
    (out_dir / "stage2_eval.json").write_text(
        json.dumps({"eval": ev, "best_epoch": r["best_epoch"], "best_val": r["best_val"],
                    "epochs_run": len(r["history"]), "train_seconds": train_seconds,
                    "max_freq": float(cfg["max_freq"])},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"saved: {out_dir / 'stage2.pt'} (best val={r['best_val']:.6f} "
          f"@epoch {r['best_epoch']}, {train_seconds / 60:.1f}min)", flush=True)
    for name, m in ev.items():
        print(f"  [{name}] e2e_full={m['full_rmse_db']:.3f}dB "
              f"gt_sparse={m['gt_sparse_full_rmse_db']:.4f}dB n_test={m['n_test']}", flush=True)
    return {"best_val": r["best_val"], "best_epoch": r["best_epoch"],
            "epochs_run": len(r["history"]), "eval": ev, "train_seconds": train_seconds}


def main() -> None:
    ap = argparse.ArgumentParser(description="WP4b Stage 2 子集训练（支持预训练初始化）")
    ap.add_argument("--data", default=DEFAULT_CFG["data"])
    ap.add_argument("--out", dest="out_dir", default=DEFAULT_CFG["out_dir"])
    ap.add_argument("--stage1-ckpt", default=DEFAULT_CFG["stage1_ckpt"])
    ap.add_argument("--stage1-config", default=DEFAULT_CFG["stage1_config"])
    for key in ("epochs", "batch_size", "patience", "k", "d_model", "n_layers", "n_heads",
                "dim_feedforward", "seed", "num_threads", "train_target_stride"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=int, default=DEFAULT_CFG[key])
    for key in ("lr", "weight_decay", "dropout", "max_freq"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=DEFAULT_CFG[key])
    ap.add_argument("--device", default=DEFAULT_CFG["device"])
    ap.add_argument("--init-from", default=None, help="预训练 stage2.pt 路径（微调初始化）")
    ap.add_argument("--init-config", default=None,
                    help="预训练 stage2_config.json（复用 cond 归一化统计）")
    args = ap.parse_args()
    train({**DEFAULT_CFG, **vars(args)})


if __name__ == "__main__":
    main()
