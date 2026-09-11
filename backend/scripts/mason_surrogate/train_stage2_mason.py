"""WP4a Stage 2：复用生产 SparseReconstructor 的掩码谱重建（统一 w 网格）。

- 架构原样实例化（生产配置 d_model=64/4 层/4 头/dropout=0.1；max_freq 需按 w 轴复核，
  见 compare_max_freq：w∈[0.8,1.2] 与生产 GHz 轴尺度不同，30 epoch 对比 0.1 vs 1.0）。
- cond=[fs,fp,Qs,Qp,kt2] 取 Stage 1 预测值；kt2 直接取 k2eff_pct 预测列（百分数，
  与生产 app/ml/sparse/dataset.py 的 row.k2eff_pct 口径一致）。
- 稀疏点 (w, dB_norm)：位置由 Stage 1 预测 fp_ratio 确定性算出（推理一致性——
  推理时无 GT fp_ratio 可用）；值取 w 网格 dB（归一化）；掩码外点走 sample_mask。
- 样条基线：baseline_interpolate 在 (w, sparse_norm) 上预计算（位置/值固定，一次性）。
- loss：掩码内归一化 MSE。

用法：
    uv run python scripts/mason_surrogate/train_stage2_mason.py            # 先对比 max_freq 再全量
    uv run python scripts/mason_surrogate/train_stage2_mason.py --max-freq 0.1  # 跳过对比
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

from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402
from mason_surrogate.build_unified_dataset import file_sha256  # noqa: E402
from mason_surrogate.common import (  # noqa: E402
    NormStats,
    UnifiedMLP,
    anchor_sensitivity_rmse_db,
    apply_norm,
    batch_sparse_positions,
    build_cond,
    build_sparse_samples,
    fit_norm,
    masked_mse,
    precompute_baselines,
    zp_peak_mae_db,
)
from mason_surrogate.train_stage1_mason import load_unified, predict_fp_ratio  # noqa: E402

DEFAULT_CFG = {
    "data": "surrogate_data_mason/unified_dataset.npz",
    "stage1_ckpt": "checkpoints_mason/stage1.pt",
    "stage1_config": "checkpoints_mason/stage1_config.json",
    "out_dir": "checkpoints_mason",
    "epochs": 200,
    "batch_size": 256,
    "lr": 1e-3,
    "weight_decay": 1e-4,
    "patience": 20,
    # k（稀疏点数）不在这里配：一律从 Stage 1 ckpt 读（复审重要项 1）
    "d_model": 64,
    "n_layers": 4,
    "n_heads": 4,
    "dim_feedforward": 256,
    "dropout": 0.1,
    "max_freq": None,  # None → 先跑 compare_max_freq((0.1, 1.0)) 选 val loss 更优者
    "compare_candidates": (0.1, 1.0),
    "compare_epochs": 30,
    "seed": 20260911,
    "device": "cpu",
    "time_budget_h": 3.0,  # ETA 超过则降上限到 cap_epochs_on_budget
    "cap_epochs_on_budget": 120,
    "num_threads": 10,  # 本机 10 核；torch 默认 4 严重欠载（实测 4.4s→2.8s/batch）
    "train_target_stride": 2,  # 训练/val loss 用隔点目标子网格（501 点）提速；eval 仍全网格
}

ANCHOR_PERTURB_MAX = 0.01  # 锚点敏感度注入幅度上限（Stage1 fs 相对误差 clip，w 相对单位）


# ---------------------------------------------------------------- 数据准备


@torch.no_grad()
def _stage1_predict(cfg: dict, data: dict[str, NDArray]) -> tuple[NDArray, NDArray, int]:
    """加载 Stage 1 checkpoint，预测全数据集 7 参数（去归一化）+ 稀疏值（归一化 dB）。

    稀疏头的 K 个输出对应"由 fp_ratio_pred 确定性算出的位置"（推理口径）。
    返回 (params_pred, sparse_pred_norm, k)：k 以 ckpt 为准（复审重要项 1）。
    """
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
    return params_pred, np.concatenate(sparse, axis=0), int(ckpt["k"])


def _prepare(cfg: dict) -> dict:
    """加载数据 + Stage 1 预测，构建 Stage 2 全部输入张量（一次性预计算）。"""
    t0 = time.time()
    data = load_unified(cfg["data"])
    grid = data["w_grid"].astype(np.float64)
    split = data["split"]
    idx = np.arange(len(split))
    tr_np, va_np, te_np = (idx[split == s] for s in (0, 1, 2))

    s1_cfg = json.loads(Path(cfg["stage1_config"]).read_text(encoding="utf-8"))
    db_mean, db_std = float(s1_cfg["db_mean"]), float(s1_cfg["db_std"])

    params_pred, sparse_pred_norm, k = _stage1_predict(cfg, data)
    fp_ratio_pred = predict_fp_ratio(params_pred)

    # cond = [fs, fp, Qs, Qp, kt2=k2eff_pct]（Stage 1 预测值，生产口径），train 统计归一化
    cond_raw = build_cond(params_pred)
    cond_stats = fit_norm(cond_raw[tr_np])

    # 稀疏点：位置来自预测 fp_ratio（训练/推理一致）；k 以 Stage 1 ckpt 为准
    pos = batch_sparse_positions(grid, fp_ratio_pred, k=k)
    samples_raw, valid = build_sparse_samples(data["z_w_db"], data["w_mask"], pos, grid)
    samples = samples_raw.copy()
    samples[..., 1] = (samples[..., 1] - db_mean) / db_std  # dB 归一化（沿用 Stage 1 统计）

    n_valid_sparse_min = int(valid.sum(1).min())
    # 全掩码样本会让 attention 全 pad → NaN 静默污染；构建器已丢弃零覆盖，此处兜底断言
    assert n_valid_sparse_min >= 1, (
        f"存在 n_valid_sparse=0 的样本（min={n_valid_sparse_min}）："
        "稀疏点全无效会污染 attention，检查数据集构建"
    )

    # 端到端（e2e）评估输入：稀疏值用 Stage 1 稀疏头预测（同位置），重建基线另算
    samples_e2e = samples_raw.copy()
    samples_e2e[..., 1] = sparse_pred_norm.astype(np.float32)

    # 样条基线（归一化空间；一次性）：GT 稀疏值（训练）与 e2e 预测稀疏值（评估）各一份
    t_base = time.time()
    baseline = precompute_baselines(
        pos, samples[..., 1], valid, grid, fill_value=0.0
    ).astype(np.float32)
    baseline_e2e = precompute_baselines(
        pos, samples_e2e[..., 1], valid, grid, fill_value=0.0
    ).astype(np.float32)
    print(f"baseline precompute: {time.time() - t_base:.1f}s", flush=True)

    arrays = {
        "grid": grid,
        "grid_t": torch.from_numpy(grid.astype(np.float32)),
        "cond": torch.from_numpy(apply_norm(cond_raw, cond_stats)),
        "samples": torch.from_numpy(samples),
        # SparseReconstructor 的 src_key_padding_mask：True=忽略 → 取反
        "sample_pad": torch.from_numpy(~valid),
        "baseline": torch.from_numpy(baseline),
        "samples_e2e": torch.from_numpy(samples_e2e),
        "baseline_e2e": torch.from_numpy(baseline_e2e),
        "target": torch.from_numpy(((data["z_w_db"] - db_mean) / db_std).astype(np.float32)),
        "target_mask": torch.from_numpy(data["w_mask"]),
        "tr": torch.from_numpy(tr_np), "va": torch.from_numpy(va_np), "te": torch.from_numpy(te_np),
        "data": data,
        "params_pred": params_pred,
        "cond_stats": cond_stats,
        "db_mean": db_mean, "db_std": db_std,
        "k": k,
        "n_valid_sparse_min": n_valid_sparse_min,
    }
    print(f"stage2 prepare: {time.time() - t0:.1f}s", flush=True)
    return arrays


# ---------------------------------------------------------------- 训练


def _train_reconstructor(cfg: dict, a: dict, max_freq: float, epochs: int,
                         patience: int, tag: str) -> dict:
    """训练单个 SparseReconstructor，返回 {model, best_val, best_epoch, history}。"""
    dev = torch.device(cfg["device"])
    torch.manual_seed(cfg["seed"])
    model = SparseReconstructor(
        d_model=cfg["d_model"], n_encoder_layers=cfg["n_layers"], n_heads=cfg["n_heads"],
        dim_feedforward=cfg["dim_feedforward"], dropout=cfg["dropout"], max_freq=max_freq,
    ).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    gen = torch.Generator().manual_seed(cfg["seed"])
    # 训练/val 在隔点子网格上算 loss（提速 ~2×）；eval 另用全网格
    stride = int(cfg.get("train_target_stride", 1))
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
    for epoch in range(1, epochs + 1):
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
        if epoch == 1 or epoch % 10 == 0:
            print(f"[{tag}] epoch {epoch}: train={tr_l:.5f} val={va_l:.5f} "
                  f"best={best_val:.5f}@{best_epoch} [{time.time() - t_ep:.1f}s]", flush=True)
        if bad >= patience:
            print(f"[{tag}] early stop at epoch {epoch}", flush=True)
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    return {"model": model, "best_val": best_val, "best_epoch": best_epoch, "history": history}


def compare_max_freq(cfg: dict, candidates=(0.1, 1.0), epochs: int = 30) -> dict[float, float]:
    """max_freq 对比实验：w∈[0.8,1.2] 与生产 GHz 轴尺度不同，选 val loss 更优者。"""
    torch.set_num_threads(int(cfg["num_threads"]))
    a = _prepare(cfg)
    scores = {}
    for cand in candidates:
        r = _train_reconstructor(cfg, a, float(cand), epochs, patience=epochs + 1,
                                 tag=f"max_freq={cand}")
        scores[float(cand)] = r["best_val"]
        print(f"[compare] max_freq={cand}: best val={r['best_val']:.5f}", flush=True)
    return scores


def _estimate_epoch_seconds(cfg: dict, a: dict, max_freq: float, n_batches: int = 8) -> float:
    """用少量 batch 估算每 epoch 秒数（训练模式，含反传）。"""
    dev = torch.device(cfg["device"])
    torch.manual_seed(cfg["seed"])
    model = SparseReconstructor(
        d_model=cfg["d_model"], n_encoder_layers=cfg["n_layers"], n_heads=cfg["n_heads"],
        dim_feedforward=cfg["dim_feedforward"], dropout=cfg["dropout"], max_freq=max_freq,
    ).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"])
    stride = int(cfg.get("train_target_stride", 1))
    grid_t = a["grid_t"][::stride].to(dev)
    tgt = a["target"][:, ::stride]
    tmsk = a["target_mask"][:, ::stride]
    base = a["baseline"][:, ::stride]
    tr = a["tr"]
    t0 = time.time()
    model.train()
    n_done = 0
    for i0 in range(0, min(n_batches * cfg["batch_size"], len(tr)), cfg["batch_size"]):
        bi = tr[i0 : i0 + cfg["batch_size"]]
        pred = model(cond=a["cond"][bi].to(dev), samples=a["samples"][bi].to(dev),
                     freq_target=grid_t, z_baseline=base[bi].to(dev),
                     sample_mask=a["sample_pad"][bi].to(dev))
        loss = masked_mse(pred, tgt[bi].to(dev), tmsk[bi].to(dev))
        opt.zero_grad()
        loss.backward()
        opt.step()
        n_done += 1
    per_batch = (time.time() - t0) / max(n_done, 1)
    n_epoch_batches = (len(tr) + cfg["batch_size"] - 1) // cfg["batch_size"]
    # val 前向约为训练 batch 耗时的 1/3，按 len(va)/len(tr) 比例折算
    val_frac = len(a["va"]) / max(len(tr), 1) / 3.0
    return per_batch * n_epoch_batches * (1.0 + val_frac)


# ---------------------------------------------------------------- 评估


@torch.no_grad()
def evaluate(cfg: dict, a: dict, model: SparseReconstructor) -> dict:
    """test 集按结构分开评估（均 dB，掩码内）。

    两套输入：
    - e2e（论文口径）：cond/稀疏值/位置全部来自 Stage 1 预测 → full / 锚点敏感度 / Zp。
    - GT 稀疏值（teacher-forcing 诊断）：只报 full RMSE，隔离 Stage 2 自身误差。
    """
    dev = torch.device(cfg["device"])
    model.eval()
    grid = a["grid"]
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
    fs_pred = a["params_pred"][te_np, 0]
    f_a = data["f_a"][te_np]
    # 锚点注入幅度 = Stage 1 fs 相对误差（clip 到 ±ANCHOR_PERTURB_MAX）
    s_inj = np.clip(fs_pred / f_a - 1.0, -ANCHOR_PERTURB_MAX, ANCHOR_PERTURB_MAX)
    fp_ratio_gt = data["fp_ratio"][te_np]

    out = {}
    for name, sel in [("epe", struct == 0), ("eppe", struct == 1),
                      ("all", np.ones(len(te_np), bool))]:
        m = mask[sel]
        d2_gt = (gt_input_db[sel] - gt_db[sel]) ** 2
        d2_e2e = (e2e_db[sel] - gt_db[sel]) ** 2
        sens_p, sens_m = anchor_sensitivity_rmse_db(
            e2e_db[sel], gt_db[sel], m, grid, s_inj[sel]
        )
        zp, zp_n = zp_peak_mae_db(e2e_db[sel], gt_db[sel], m, grid, fp_ratio_gt[sel])
        out[name] = {
            "full_rmse_db": float(np.sqrt(d2_e2e[m].mean())) if m.any() else float("nan"),
            "anchor_sensitivity_rmse_db": {"plus": sens_p, "minus": sens_m},
            "zp_peak_mae_db": zp,
            "zp_n_used": zp_n,
            "gt_sparse_full_rmse_db": float(np.sqrt(d2_gt[m].mean())) if m.any() else float("nan"),
            "mean_abs_shift": float(np.abs(s_inj[sel]).mean()),
            "n_test": int(sel.sum()),
        }
    return out


def print_eval_stage2(ev: dict) -> None:
    print("\n== Stage 2 test 评估（按结构；e2e=Stage1 预测稀疏输入，gt=GT 稀疏诊断）==")
    print("{:<7}{:>11}{:>10}{:>10}{:>12}{:>10}{:>11}{:>8}".format(
        "struct", "e2e_full", "anchor+", "anchor-", "e2e_Zp_MAE", "gt_full",
        "mean_pert", "n_test"))
    for name, m in ev.items():
        sens = m["anchor_sensitivity_rmse_db"]
        print(f"{name:<7}{m['full_rmse_db']:>11.3f}{sens['plus']:>10.3f}{sens['minus']:>10.3f}"
              f"{m['zp_peak_mae_db']:>12.3f}{m['gt_sparse_full_rmse_db']:>10.3f}"
              f"{m['mean_abs_shift']:>11.5f}{m['n_test']:>8d}")


# ---------------------------------------------------------------- 主流程


def train(cfg: dict) -> dict:
    t_start = time.time()
    torch.set_num_threads(int(cfg["num_threads"]))
    out_dir = Path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    a = _prepare(cfg)
    print(f"n_train={len(a['tr'])} n_val={len(a['va'])} n_test={len(a['te'])} "
          f"min_valid_sparse={a['n_valid_sparse_min']}", flush=True)

    # ---- max_freq 选择 ----
    compare_scores = None
    if cfg["max_freq"] is None:
        compare_scores = compare_max_freq_prepared(cfg, a)
        max_freq = min(compare_scores, key=compare_scores.get)
        print(f"[compare] 选定 max_freq={max_freq}", flush=True)
    else:
        max_freq = float(cfg["max_freq"])

    # ---- 时间预算 ----
    epochs = cfg["epochs"]
    ep_sec = _estimate_epoch_seconds(cfg, a, max_freq)
    eta_h = ep_sec * epochs / 3600.0
    print(f"估算每 epoch {ep_sec:.1f}s → {epochs} epochs ETA {eta_h:.2f}h", flush=True)
    if eta_h > cfg["time_budget_h"]:
        epochs = min(epochs, cfg["cap_epochs_on_budget"])
        print(f"ETA 超预算 {cfg['time_budget_h']}h → 降为 {epochs} epochs "
              f"(ETA {ep_sec * epochs / 3600.0:.2f}h)", flush=True)
    epochs_effective = epochs  # 截断后的实际上限（复审次要项 3：config 与执行一致）

    r = _train_reconstructor(cfg, a, max_freq, epochs, cfg["patience"], tag=f"mf={max_freq}")
    model = r["model"]

    ev = evaluate(cfg, a, model)
    train_seconds = time.time() - t_start

    torch.save(
        {"model_state": model.state_dict(), "d_model": cfg["d_model"],
         "n_layers": cfg["n_layers"], "n_heads": cfg["n_heads"],
         "dim_feedforward": cfg["dim_feedforward"], "dropout": cfg["dropout"],
         "max_freq": max_freq, "k": a["k"], "best_epoch": r["best_epoch"]},
        out_dir / "stage2.pt",
    )
    config = {
        **{k_: cfg[k_] for k_ in ("epochs", "batch_size", "lr", "weight_decay", "patience",
                                  "d_model", "n_layers", "n_heads", "dim_feedforward",
                                  "dropout", "seed", "num_threads", "train_target_stride",
                                  "compare_epochs")},
        "epochs_effective": epochs_effective,
        "epochs_run": len(r["history"]),
        "best_epoch": r["best_epoch"],
        "best_val": r["best_val"],
        "k": a["k"],
        "k_source": "stage1 ckpt（复审重要项 1）",
        "max_freq": max_freq,
        "max_freq_note": "w∈[0.8,1.2] 轴；由 compare_max_freq 按 val loss 选出"
        "（生产 GHz 轴用 0.1，w 轴尺度不同需实测）",
        "max_freq_compare": compare_scores,
        "cond_mean": a["cond_stats"].mean.tolist(),
        "cond_std": a["cond_stats"].std.tolist(),
        "cond_cols": ["fs", "fp", "Qs", "Qp", "kt2=k2eff_pct(百分数，生产口径)"],
        "cond_source": "stage1 预测值",
        "db_mean": a["db_mean"], "db_std": a["db_std"],
        "sparse_positions_source": "stage1 预测 fp_ratio 的确定性采样（推理一致）",
        # checkpoint↔数据集版本绑定（复审重要项 2）
        "dataset": str(cfg["data"]),
        "dataset_sha256": file_sha256(Path(cfg["data"])),
        "anchor_perturb_max": ANCHOR_PERTURB_MAX,
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
                    "max_freq": max_freq, "max_freq_compare": compare_scores},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"saved: {out_dir / 'stage2.pt'} (best val={r['best_val']:.5f} "
          f"@epoch {r['best_epoch']}, {train_seconds / 60:.1f}min)")
    print_eval_stage2(ev)
    return {"best_val": r["best_val"], "best_epoch": r["best_epoch"],
            "epochs_run": len(r["history"]), "history": r["history"], "eval": ev,
            "train_seconds": train_seconds, "max_freq": max_freq,
            "max_freq_compare": compare_scores}


def compare_max_freq_prepared(cfg: dict, a: dict) -> dict[float, float]:
    """在已准备的数组上跑 max_freq 对比（train 主流程用）。"""
    scores = {}
    for cand in cfg["compare_candidates"]:
        r = _train_reconstructor(cfg, a, float(cand), cfg["compare_epochs"],
                                 patience=cfg["compare_epochs"] + 1, tag=f"max_freq={cand}")
        scores[float(cand)] = r["best_val"]
        print(f"[compare] max_freq={cand}: best val={r['best_val']:.5f}", flush=True)
    return scores


def main() -> None:
    ap = argparse.ArgumentParser(description="WP4a Stage 2 稀疏重建训练")
    ap.add_argument("--data", default=DEFAULT_CFG["data"])
    ap.add_argument("--out", dest="out_dir", default=DEFAULT_CFG["out_dir"])
    ap.add_argument("--stage1-ckpt", default=DEFAULT_CFG["stage1_ckpt"])
    ap.add_argument("--stage1-config", default=DEFAULT_CFG["stage1_config"])
    for key in ("epochs", "batch_size", "patience", "d_model", "n_layers", "n_heads",
                "dim_feedforward", "compare_epochs", "seed", "cap_epochs_on_budget"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=int, default=DEFAULT_CFG[key])
    for key in ("lr", "weight_decay", "dropout", "time_budget_h"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=DEFAULT_CFG[key])
    ap.add_argument("--max-freq", type=float, default=None,
                    help="缺省先跑 30 epoch 对比 {0.1, 1.0} 选优")
    ap.add_argument("--device", default=DEFAULT_CFG["device"])
    args = ap.parse_args()
    train({**DEFAULT_CFG, **vars(args)})


if __name__ == "__main__":
    main()
