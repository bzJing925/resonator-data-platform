"""WP4b 反向模型：统一 MLP（y7 + structure_flag → 几何厚度），EPE/EPPE 双结构三头路由。

- 输入 8 维 = y7 归一化（7 列）+ structure_flag（0/1，不归一化）；归一化统计只用 train split。
- 三个输出头：
    t1_reg（回归）：EPE → t_piezo_nm；EPPE 不使用此头。
    t_aln_cls（5 类分类，{50,80,100,120,150}）：仅 EPPE。
    t2_reg（回归）：仅 EPPE → t_alscn_nm。
- loss 按 flag 路由：EPE 样本只算 t1_reg MSE；EPPE 样本算 cls CE + t2_reg MSE。
- 评估（test split，按结构分开）：
    EPE：t_piezo MAE(nm) + 厚度-频率灵敏度一致性（fs ±10MHz 抖动，预测位移 vs
         解析 δt≈−t·δf/fs，报告皮尔逊相关/符号一致率/幅度比中位数）；
    EPPE：t_aln exact-match 准确率 + 混淆矩阵；t_alscn MAE(nm)；
    鲁棒性：fs 列加 ±10MHz 均匀抖动后重复上述指标评估（报告衰减幅度）。

用法：
    uv run python scripts/mason_surrogate/train_inverse_mason.py --epochs 300
冒烟：
    uv run python scripts/mason_surrogate/train_inverse_mason.py --epochs 2 --hidden 32
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from numpy.typing import NDArray

_F = nn.functional  # mse_loss / cross_entropy（避免 N812 非小写别名）

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
for p in (PROJECT_ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# 归一化/IO 复用包内既有件（common.py / build_unified_dataset / train_stage1_mason）
from mason_surrogate.build_unified_dataset import file_sha256  # noqa: E402
from mason_surrogate.common import (  # noqa: E402
    NormStats,
    apply_norm,
    fit_norm,
    invert_norm,
    json_safe,
)
from mason_surrogate.train_stage1_mason import load_unified  # noqa: E402

Y_FS_COL = 0  # y7 第 0 列 = fs_ghz（输入前 7 列即 y7 归一化）
FLAG_COL = 7  # 输入第 8 列 = structure_flag
T_ALN_CLASSES = (50.0, 80.0, 100.0, 120.0, 150.0)
X7_T1_COL = 1  # x7 第 1 列 = t1_nm（EPE: t_piezo；EPPE: t_aln）
X7_T2_COL = 2  # x7 第 2 列 = t2_nm（EPPE: t_alscn）

DEFAULT_CFG = {
    "data": "surrogate_data_mason/unified_dataset.npz",
    "out_dir": "checkpoints_mason",
    "eval_out": "surrogate_data_mason/evals/inverse_eval.json",
    "epochs": 300,
    "batch_size": 256,
    "lr": 1e-3,
    "weight_decay": 1e-4,
    "patience": 30,
    "hidden": 256,
    "n_res_blocks": 3,
    "seed": 20260912,
    "device": "cpu",
    "sensitivity_mhz": 10.0,
    "jitter_mhz": 10.0,
}


# --------------------------------------------- 模型（反向三头为本文件新写）


class _ResBlock(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.net = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(x)


class InverseMLP(nn.Module):
    """y7_norm+flag (8) → {t1_reg, t_aln_logits(5), t2_reg} 三头残差 MLP。"""

    def __init__(
        self, in_dim: int = 8, hidden: int = 256, n_res_blocks: int = 3, n_classes: int = 5
    ) -> None:
        super().__init__()
        layers: list[nn.Module] = [nn.Linear(in_dim, hidden), nn.GELU()]
        layers += [_ResBlock(hidden) for _ in range(n_res_blocks)]
        self.backbone = nn.Sequential(*layers)

        def _head(out_dim: int) -> nn.Sequential:
            return nn.Sequential(
                nn.Linear(hidden, hidden // 4), nn.GELU(), nn.Linear(hidden // 4, out_dim)
            )

        self.head_t1 = _head(1)
        self.head_cls = _head(n_classes)
        self.head_t2 = _head(1)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        h = self.backbone(x)
        return {
            "t1": self.head_t1(h).squeeze(-1),
            "cls_logits": self.head_cls(h),
            "t2": self.head_t2(h).squeeze(-1),
        }


def routed_loss(
    out: dict[str, torch.Tensor],
    flag: torch.Tensor,
    t1_tgt: torch.Tensor,
    cls_tgt: torch.Tensor,
    t2_tgt: torch.Tensor,
) -> torch.Tensor:
    """按 structure_flag 路由：EPE(flag=0) 只算 t1 MSE；EPPE(flag=1) 算 cls CE + t2 MSE。

    注意损失是项级等权（t1 / cls / t2 各项取各自子集均值后直接相加），
    而非样本级等权：批内 EPE/EPPE 比例不影响各项权重。
    """
    epe = flag == 0
    eppe = ~epe
    losses: list[torch.Tensor] = []
    if epe.any():
        losses.append(_F.mse_loss(out["t1"][epe], t1_tgt[epe]))
    if eppe.any():
        losses.append(_F.cross_entropy(out["cls_logits"][eppe], cls_tgt[eppe]))
        losses.append(_F.mse_loss(out["t2"][eppe], t2_tgt[eppe]))
    if not losses:
        return torch.zeros((), device=out["t1"].device)
    return sum(losses)


# ---------------------------------------------------------------- 评估辅助


def confusion_matrix(y_true: NDArray, y_pred: NDArray, n_classes: int) -> NDArray[np.int64]:
    cm = np.zeros((n_classes, n_classes), dtype=np.int64)
    for t, p in zip(y_true.astype(np.int64), y_pred.astype(np.int64), strict=True):
        cm[t, p] += 1
    return cm


def sensitivity_check(
    predict_t1: Callable[[NDArray[np.float32]], NDArray[np.float64]],
    x_norm: NDArray[np.float32],
    fs_ghz: NDArray[np.float64],
    fs_std: float,
    delta_mhz: float = 10.0,
) -> dict:
    """厚度-频率灵敏度一致性：fs ±delta_mhz 抖动，预测位移 vs 解析 δt=−t·δf/fs。

    predict_t1 接受归一化输入 (N,8) 返回去归一化 t_piezo(nm)；fs_std 用于把
    GHz 抖动换算到归一化空间。返回皮尔逊相关 / 符号一致率 / 幅度比中位数。
    """
    delta_norm = (delta_mhz / 1000.0) / fs_std
    t_base = predict_t1(x_norm)
    x_plus = x_norm.copy()
    x_plus[:, Y_FS_COL] += delta_norm
    x_minus = x_norm.copy()
    x_minus[:, Y_FS_COL] -= delta_norm
    dt_pred = np.concatenate([predict_t1(x_plus) - t_base, predict_t1(x_minus) - t_base])
    fs_mhz = np.asarray(fs_ghz, dtype=np.float64) * 1000.0
    dt_analytic = np.concatenate(
        [-t_base * delta_mhz / fs_mhz, +t_base * delta_mhz / fs_mhz]
    )
    pearson = float(np.corrcoef(dt_pred, dt_analytic)[0, 1])
    sign_rate = float(np.mean(np.sign(dt_pred) == np.sign(dt_analytic)))
    mag_ratio = float(
        np.median(np.abs(dt_pred) / np.maximum(np.abs(dt_analytic), 1e-12))
    )
    return {
        "delta_mhz": float(delta_mhz),
        "pearson": pearson,
        "sign_rate": sign_rate,
        "median_mag_ratio": mag_ratio,
        "n_points": int(len(dt_pred)),
    }


@torch.no_grad()
def predict_all(
    model: InverseMLP,
    x_n: NDArray[np.float32],
    t1_stats: NormStats,
    t2_stats: NormStats,
    dev: torch.device,
    batch: int = 4096,
) -> dict[str, NDArray[np.float64]]:
    """批量前向，输出去归一化：t1_nm (M,), cls_idx (M,), t2_nm (M,)。"""
    model.eval()
    t1, cls_, t2 = [], [], []
    xt = torch.from_numpy(np.asarray(x_n, dtype=np.float32))
    for i0 in range(0, len(xt), batch):
        out = model(xt[i0 : i0 + batch].to(dev))
        t1.append(out["t1"].cpu().numpy())
        cls_.append(out["cls_logits"].argmax(dim=1).cpu().numpy())
        t2.append(out["t2"].cpu().numpy())
    return {
        "t1_nm": invert_norm(np.concatenate(t1), t1_stats),
        "cls_idx": np.concatenate(cls_).astype(np.int64),
        "t2_nm": invert_norm(np.concatenate(t2), t2_stats),
    }


def per_structure_metrics(
    pred: dict[str, NDArray[np.float64]], data: dict[str, NDArray]
) -> dict[str, dict]:
    """test split 按结构评估：EPE t_piezo MAE；EPPE t_aln acc+混淆矩阵、t_alscn MAE。"""
    split = data["split"]
    flag = data["x7"][:, 0].astype(np.int64)
    x7 = data["x7"].astype(np.float64)
    cls_true_all = np.array(
        [int(np.argmin(np.abs(np.asarray(T_ALN_CLASSES) - v))) for v in x7[:, X7_T1_COL]]
    )
    out: dict[str, dict] = {}
    te_epe = (split == 2) & (flag == 0)
    if te_epe.any():
        out["epe"] = {
            "n_test": int(te_epe.sum()),
            "t_piezo_mae_nm": float(
                np.abs(pred["t1_nm"][te_epe] - x7[te_epe, X7_T1_COL]).mean()
            ),
        }
    te_eppe = (split == 2) & (flag == 1)
    if te_eppe.any():
        cm = confusion_matrix(cls_true_all[te_eppe], pred["cls_idx"][te_eppe], len(T_ALN_CLASSES))
        out["eppe"] = {
            "n_test": int(te_eppe.sum()),
            "t_aln_acc": float((pred["cls_idx"][te_eppe] == cls_true_all[te_eppe]).mean()),
            "t_aln_confusion": cm.tolist(),
            "t_aln_classes": list(T_ALN_CLASSES),
            "t_alscn_mae_nm": float(
                np.abs(pred["t2_nm"][te_eppe] - x7[te_eppe, X7_T2_COL]).mean()
            ),
        }
    return out


def evaluate_from_checkpoint(
    ckpt_dir: str | Path,
    data: dict[str, NDArray],
    cfg: dict,
    jitter_mhz: float = 0.0,
    jitter_seed: int = 0,
) -> dict[str, dict]:
    """从 checkpoint + config 重建模型并评估 test split；jitter_mhz>0 时 fs 列加均匀抖动。"""
    dev = torch.device("cpu")
    ckpt = torch.load(Path(ckpt_dir) / "inverse.pt", map_location="cpu", weights_only=True)
    model = InverseMLP(
        in_dim=8, hidden=ckpt["hidden"], n_res_blocks=ckpt["n_res_blocks"],
        n_classes=ckpt["n_classes"],
    )
    model.load_state_dict(ckpt["model_state"])
    y_stats = NormStats(np.array(cfg["y7_mean"]), np.array(cfg["y7_std"]))
    t1_stats = NormStats(np.array(cfg["t1_mean"]), np.array(cfg["t1_std"]))
    t2_stats = NormStats(np.array(cfg["t2_mean"]), np.array(cfg["t2_std"]))

    y7 = data["y7"].astype(np.float64).copy()
    if jitter_mhz > 0:
        rng = np.random.default_rng(jitter_seed)
        te = data["split"] == 2
        y7[te, Y_FS_COL] += rng.uniform(-jitter_mhz / 1000.0, jitter_mhz / 1000.0, int(te.sum()))
    x_n = np.concatenate(
        [apply_norm(y7, y_stats), data["x7"][:, [0]].astype(np.float32)], axis=1
    )
    pred = predict_all(model, x_n, t1_stats, t2_stats, dev)
    return per_structure_metrics(pred, data)


# ---------------------------------------------------------------- 训练


def train(cfg: dict) -> dict:
    t_start = time.time()
    torch.manual_seed(cfg["seed"])
    np.random.seed(cfg["seed"])
    dev = torch.device(cfg["device"])
    out_dir = Path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    data = load_unified(cfg["data"])
    split = data["split"]
    flag = data["x7"][:, 0].astype(np.int64)
    idx_all = np.arange(len(split))
    tr_np, va_np = idx_all[split == 0], idx_all[split == 1]

    # ---- 归一化统计（只用 train split；flag 列不归一化）----
    y_stats = fit_norm(data["y7"][tr_np])
    tr_epe = tr_np[flag[tr_np] == 0]
    tr_eppe = tr_np[flag[tr_np] == 1]
    t1_stats = fit_norm(data["x7"][tr_epe, X7_T1_COL : X7_T1_COL + 1])  # EPE t_piezo
    t2_stats = fit_norm(data["x7"][tr_eppe, X7_T2_COL : X7_T2_COL + 1])  # EPPE t_alscn

    x_n = np.concatenate(
        [apply_norm(data["y7"], y_stats), data["x7"][:, [0]].astype(np.float32)], axis=1
    )
    x_t = torch.from_numpy(x_n)
    flag_t = torch.from_numpy(flag)
    t1_t = torch.from_numpy(apply_norm(data["x7"][:, [X7_T1_COL]], t1_stats)).squeeze(-1)
    t2_t = torch.from_numpy(apply_norm(data["x7"][:, [X7_T2_COL]], t2_stats)).squeeze(-1)
    cls_lut = np.asarray(T_ALN_CLASSES)
    cls_idx = np.array([int(np.argmin(np.abs(cls_lut - v))) for v in data["x7"][:, X7_T1_COL]])
    cls_t = torch.from_numpy(cls_idx.astype(np.int64))

    model = InverseMLP(
        in_dim=8, hidden=cfg["hidden"], n_res_blocks=cfg["n_res_blocks"],
        n_classes=len(T_ALN_CLASSES),
    ).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=cfg["epochs"])
    gen = torch.Generator().manual_seed(cfg["seed"])
    tr = torch.from_numpy(tr_np)
    va = torch.from_numpy(va_np)

    def run_epoch(idx: torch.Tensor, train_mode: bool) -> float:
        model.train(train_mode)
        total, n = 0.0, 0
        order = torch.randperm(len(idx), generator=gen) if train_mode else torch.arange(len(idx))
        with torch.set_grad_enabled(train_mode):
            for i0 in range(0, len(idx), cfg["batch_size"]):
                bi = idx[order[i0 : i0 + cfg["batch_size"]]]
                out = model(x_t[bi].to(dev))
                loss = routed_loss(
                    out, flag_t[bi].to(dev), t1_t[bi].to(dev), cls_t[bi].to(dev), t2_t[bi].to(dev)
                )
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
        if epoch == 1 or epoch % 10 == 0:
            print(
                f"epoch {epoch}: train={tr_l:.5f} val={va_l:.5f} "
                f"best={best_val:.5f}@{best_epoch} [{time.time() - t_ep:.1f}s]",
                flush=True,
            )
        if bad >= cfg["patience"]:
            print(f"early stop at epoch {epoch}", flush=True)
            break
    if best_state is not None:
        model.load_state_dict(best_state)

    train_seconds = time.time() - t_start
    torch.save(
        {"model_state": model.state_dict(), "hidden": cfg["hidden"],
         "n_res_blocks": cfg["n_res_blocks"], "n_classes": len(T_ALN_CLASSES),
         "in_dim": 8, "best_epoch": best_epoch},
        out_dir / "inverse.pt",
    )
    config = {
        **{k: cfg[k] for k in ("epochs", "batch_size", "lr", "weight_decay", "patience",
                               "hidden", "n_res_blocks", "seed", "sensitivity_mhz",
                               "jitter_mhz")},
        "best_epoch": best_epoch,
        "best_val": best_val,
        "epochs_run": len(history),
        "train_seconds": train_seconds,
        "y7_mean": y_stats.mean.tolist(), "y7_std": y_stats.std.tolist(),
        "t1_mean": t1_stats.mean.tolist(), "t1_std": t1_stats.std.tolist(),
        "t2_mean": t2_stats.mean.tolist(), "t2_std": t2_stats.std.tolist(),
        "t_aln_classes": list(T_ALN_CLASSES),
        "dataset_sha256": file_sha256(Path(cfg["data"])),
        "norm_note": "y7 统计仅由 train split 计算；flag 列不归一化；"
        "t1 统计仅 EPE train（t_piezo）；t2 统计仅 EPPE train（t_alscn）",
    }
    (out_dir / "inverse_config.json").write_text(
        json.dumps(json_safe(config), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    (out_dir / "inverse_history.json").write_text(
        json.dumps(json_safe(history), indent=2, allow_nan=False), encoding="utf-8"
    )

    # ---- 评估：clean + fs 抖动鲁棒性 + 灵敏度一致性 ----
    clean = evaluate_from_checkpoint(out_dir, data, config)
    jitter_seed = cfg["seed"] + 1
    jit = evaluate_from_checkpoint(
        out_dir, data, config, jitter_mhz=cfg["jitter_mhz"], jitter_seed=jitter_seed
    )

    # 灵敏度（clean 模型，EPE test）
    ckpt = torch.load(out_dir / "inverse.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(ckpt["model_state"])
    te_epe = (split == 2) & (flag == 0)
    x_n_clean = np.concatenate(
        [apply_norm(data["y7"], y_stats), data["x7"][:, [0]].astype(np.float32)], axis=1
    )

    def _predict_t1(x: NDArray[np.float32]) -> NDArray[np.float64]:
        return predict_all(model, x, t1_stats, t2_stats, dev)["t1_nm"]

    if te_epe.any():
        clean["epe"]["sensitivity"] = sensitivity_check(
            _predict_t1,
            x_n_clean[te_epe],
            data["y7"][te_epe, Y_FS_COL].astype(np.float64),
            fs_std=float(y_stats.std[Y_FS_COL]),
            delta_mhz=cfg["sensitivity_mhz"],
        )

    degradation = {}
    for struct in ("epe", "eppe"):
        if struct not in clean or struct not in jit:
            continue
        d = {}
        for key in clean[struct]:
            if isinstance(clean[struct][key], (int, float)) and key in jit[struct]:
                d[f"{key}_delta"] = jit[struct][key] - clean[struct][key]
        degradation[struct] = d

    eval_doc = {
        "generated_at": datetime.now(UTC).isoformat(),
        "task": "WP4b inverse model: y7+flag → thickness (EPE t_piezo / EPPE t_aln+t_alscn)",
        "seed": cfg["seed"],
        "data_sha256": config["dataset_sha256"],
        "dataset": {
            "n_total": int(len(split)),
            "per_structure": {
                name: {
                    "n_train": int(((split == 0) & (flag == f)).sum()),
                    "n_val": int(((split == 1) & (flag == f)).sum()),
                    "n_test": int(((split == 2) & (flag == f)).sum()),
                }
                for name, f in (("epe", 0), ("eppe", 1))
            },
        },
        "training": {
            "best_epoch": best_epoch,
            "best_val": best_val,
            "epochs_run": len(history),
            "train_seconds": train_seconds,
        },
        "test": {
            "clean": clean,
            "fs_jitter": {"delta_mhz": cfg["jitter_mhz"], "seed": jitter_seed, **jit},
            "degradation": degradation,
        },
    }
    eval_path = Path(cfg["eval_out"])
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    eval_path.write_text(
        json.dumps(json_safe(eval_doc), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(f"saved: {out_dir / 'inverse.pt'} (best val={best_val:.5f} @epoch {best_epoch})")
    print(f"saved: {eval_path}")
    print_eval(eval_doc)
    return {"best_val": best_val, "best_epoch": best_epoch, "epochs_run": len(history),
            "eval": eval_doc, "train_seconds": train_seconds}


def print_eval(doc: dict) -> None:
    t = doc["test"]
    jitter_mhz = t["fs_jitter"]["delta_mhz"]
    print(f"\n== 反向模型 test 评估（clean / fs±{jitter_mhz:.0f}MHz 抖动）==")
    if "epe" in t["clean"]:
        e, ej = t["clean"]["epe"], t["fs_jitter"]["epe"]
        delta = t["degradation"]["epe"]["t_piezo_mae_nm_delta"]
        print(f"[EPE] n={e['n_test']}  t_piezo MAE: {e['t_piezo_mae_nm']:.3f} nm "
              f"(jitter {ej['t_piezo_mae_nm']:.3f}, Δ{delta:+.3f})")
        s = e["sensitivity"]
        print(f"      灵敏度一致性(±{s['delta_mhz']:.0f}MHz): pearson={s['pearson']:.4f} "
              f"sign={s['sign_rate'] * 100:.1f}% mag_ratio_med={s['median_mag_ratio']:.3f}")
    if "eppe" in t["clean"]:
        p, pj = t["clean"]["eppe"], t["fs_jitter"]["eppe"]
        print(f"[EPPE] n={p['n_test']}  t_aln acc: {p['t_aln_acc'] * 100:.2f}% "
              f"(jitter {pj['t_aln_acc'] * 100:.2f}%)  t_alscn MAE: {p['t_alscn_mae_nm']:.3f} nm "
              f"(jitter {pj['t_alscn_mae_nm']:.3f})")
        print("      t_aln 混淆矩阵 (行=真值, 列=预测; 类别 "
              + "/".join(f"{c:.0f}" for c in p["t_aln_classes"]) + "):")
        for cls, row in zip(p["t_aln_classes"], p["t_aln_confusion"], strict=True):
            print(f"        {cls:>5.0f}: " + " ".join(f"{v:>4d}" for v in row))


def main() -> None:
    ap = argparse.ArgumentParser(description="WP4b 反向模型训练（y7+flag → 厚度三头）")
    ap.add_argument("--data", default=DEFAULT_CFG["data"])
    ap.add_argument("--out", dest="out_dir", default=DEFAULT_CFG["out_dir"])
    ap.add_argument("--eval-out", default=DEFAULT_CFG["eval_out"])
    for key in ("epochs", "batch_size", "patience", "hidden", "n_res_blocks", "seed"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=int, default=DEFAULT_CFG[key])
    for key in ("lr", "weight_decay", "sensitivity_mhz", "jitter_mhz"):
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=DEFAULT_CFG[key])
    ap.add_argument("--device", default=DEFAULT_CFG["device"])
    args = ap.parse_args()
    train({**DEFAULT_CFG, **vars(args)})


if __name__ == "__main__":
    main()
