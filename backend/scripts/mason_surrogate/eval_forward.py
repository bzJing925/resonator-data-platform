"""WP4a 评估产物合并：checkpoints_mason/{stage1,stage2} + unified_dataset → forward_eval.json。

优先读各阶段 train 落盘的 *_eval.json；缺省则从 checkpoint 重算（确定性前向，不重训）。
Stage 1 训练耗时若 config 无记录，可用 --stage1-log 解析逐 epoch [Xs] 求和。

用法：
    uv run python scripts/mason_surrogate/eval_forward.py --stage1-log /tmp/wp4a_stage1.log
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
for p in (PROJECT_ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402
from mason_surrogate import train_stage1_mason as ts1  # noqa: E402
from mason_surrogate import train_stage2_mason as ts2  # noqa: E402
from mason_surrogate.common import (  # noqa: E402
    NormStats,
    UnifiedMLP,
    batch_sparse_positions,
)

DATA_DIR = PROJECT_ROOT / "surrogate_data_mason"
CKPT_DIR = PROJECT_ROOT / "checkpoints_mason"


def parse_train_seconds_from_log(log_path: Path) -> float | None:
    """从训练日志逐 epoch 的 [Xs] 求和（该 run 的 config 未落 train_seconds 时用）。"""
    if not log_path or not Path(log_path).exists():
        return None
    total = 0.0
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        m = re.search(r"\[(\d+(?:\.\d+)?)s\]", line)
        if m:
            total += float(m.group(1))
    return total if total > 0 else None


def stage1_eval(ckpt_dir: Path, data: dict) -> dict:
    """读 stage1_eval.json；缺则从 checkpoint 重算。"""
    eval_path = ckpt_dir / "stage1_eval.json"
    if eval_path.exists():
        return json.loads(eval_path.read_text(encoding="utf-8"))
    cfg = json.loads((ckpt_dir / "stage1_config.json").read_text(encoding="utf-8"))
    ckpt = torch.load(ckpt_dir / "stage1.pt", map_location="cpu", weights_only=True)
    model = UnifiedMLP(in_dim=7, hidden=ckpt["hidden"], n_res_blocks=ckpt["n_res_blocks"],
                       n_params=7, k=ckpt["k"])
    model.load_state_dict(ckpt["model_state"])
    x_stats = NormStats(np.array(cfg["x7_mean"]), np.array(cfg["x7_std"]))
    y_stats = NormStats(np.array(cfg["y7_mean"]), np.array(cfg["y7_std"]))
    pos_gt = batch_sparse_positions(data["w_grid"].astype(np.float64),
                                    data["fp_ratio"].astype(np.float64), k=ckpt["k"])
    ev = ts1.evaluate(model, data, pos_gt, x_stats, y_stats,
                      float(cfg["db_mean"]), float(cfg["db_std"]), torch.device("cpu"))
    return {"eval": ev, "best_epoch": cfg["best_epoch"], "best_val": cfg["best_val"],
            "train_seconds": cfg.get("train_seconds")}


def stage2_eval(ckpt_dir: Path) -> dict:
    """读 stage2_eval.json；缺则从 checkpoint 重算（含 _prepare 的 Stage 1 前向）。"""
    eval_path = ckpt_dir / "stage2_eval.json"
    if eval_path.exists():
        return json.loads(eval_path.read_text(encoding="utf-8"))
    cfg = dict(ts2.DEFAULT_CFG)
    cfg.update(data=str(DATA_DIR / "unified_dataset.npz"), out_dir=str(ckpt_dir),
               stage1_ckpt=str(ckpt_dir / "stage1.pt"),
               stage1_config=str(ckpt_dir / "stage1_config.json"))
    t0 = time.time()
    a = ts2._prepare(cfg)
    ckpt = torch.load(ckpt_dir / "stage2.pt", map_location="cpu", weights_only=True)
    model = SparseReconstructor(
        d_model=ckpt["d_model"], n_encoder_layers=ckpt["n_layers"], n_heads=ckpt["n_heads"],
        dim_feedforward=ckpt["dim_feedforward"], dropout=ckpt["dropout"],
        max_freq=ckpt["max_freq"],
    )
    model.load_state_dict(ckpt["model_state"])
    ev = ts2.evaluate(cfg, a, model)
    s2_cfg = json.loads((ckpt_dir / "stage2_config.json").read_text(encoding="utf-8"))
    return {"eval": ev, "best_epoch": ckpt["best_epoch"], "best_val": s2_cfg["best_val"],
            "epochs_run": s2_cfg["epochs_run"], "train_seconds": s2_cfg.get("train_seconds"),
            "max_freq": ckpt["max_freq"], "max_freq_compare": s2_cfg.get("max_freq_compare"),
            "eval_recompute_seconds": time.time() - t0}


def main() -> None:
    ap = argparse.ArgumentParser(description="合并 WP4a 两阶段评估 → forward_eval.json")
    ap.add_argument("--ckpt", type=Path, default=CKPT_DIR)
    ap.add_argument("--data", type=Path, default=DATA_DIR / "unified_dataset.npz")
    ap.add_argument("--meta", type=Path, default=DATA_DIR / "unified_dataset.meta.json")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "evals" / "forward_eval.json")
    ap.add_argument("--stage1-log", type=Path, default=None, help="解析 stage1 训练耗时的日志")
    ap.add_argument("--stage1-train-seconds", type=float, default=None,
                    help="stage1 训练耗时直接覆盖（如按进程起止时间实测）")
    args = ap.parse_args()

    data = ts1.load_unified(args.data)
    meta = json.loads(args.meta.read_text(encoding="utf-8"))

    s1 = stage1_eval(args.ckpt, data)
    s2 = stage2_eval(args.ckpt)
    s1_seconds = (
        args.stage1_train_seconds
        or s1.get("train_seconds")
        or parse_train_seconds_from_log(args.stage1_log)
    )

    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "task": "WP4a unified forward surrogate on Mason corpora (EPE+EPPE co-train)",
        "seed": 20260911,
        "dataset": {
            "n_total": int(len(data["split"])),
            "w_grid": meta["w_grid"],
            "sources": {
                k: {"sha256": v["sha256"], "mason_sim_commit_sha": v["mason_sim_commit_sha"]}
                for k, v in meta["sources"].items()
            },
            "per_structure": {
                name: {
                    "n_in": meta["sources"][name]["n_in"],
                    "n_kept": meta["sources"][name]["n_kept"],
                    "n_dropped_zero_coverage": meta["sources"][name]["n_dropped_zero_coverage"],
                    "mask_rate": meta["sources"][name]["mask_rate"],
                    **meta["split"]["counts"][name],
                }
                for name in ("epe", "eppe")
            },
        },
        "stage1": {
            "best_epoch": s1["best_epoch"],
            "best_val": s1["best_val"],
            "train_seconds": s1_seconds,
            "per_structure": s1["eval"],
        },
        "stage2": {
            "best_epoch": s2["best_epoch"],
            "best_val": s2["best_val"],
            "epochs_run": s2.get("epochs_run"),
            "train_seconds": s2.get("train_seconds"),
            "max_freq": s2["max_freq"],
            "max_freq_compare": s2.get("max_freq_compare"),
            "per_structure": s2["eval"],
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved: {args.out}")
    ts1.print_eval_stage1(s1["eval"])
    ts2.print_eval_stage2(s2["eval"])
    print(f"\nstage1 best_epoch={s1['best_epoch']} train_seconds={s1_seconds}")
    print(f"stage2 best_epoch={s2['best_epoch']} max_freq={s2['max_freq']} "
          f"compare={s2.get('max_freq_compare')} train_seconds={s2.get('train_seconds')}")


if __name__ == "__main__":
    main()
