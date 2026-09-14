"""WP4a 评估产物合并：checkpoints_mason/{stage1,stage2} + unified_dataset → forward_eval.json。

优先读各阶段 train 落盘的 *_eval.json；缺省则从 checkpoint 重算（确定性前向，不重训；
--data 透传到两阶段重算，保证同来源；稀疏重建 k 以 stage1 ckpt 为准）。
checkpoint↔数据集 sha256 绑定校验：不匹配/缺字段 → stderr 警告 + json warnings 字段。
Stage 1 训练耗时若 config 无记录，可用 --stage1-train-seconds 覆盖或 --stage1-log 解析。

用法：
    uv run python scripts/mason_surrogate/eval_forward.py
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
from mason_surrogate.build_unified_dataset import file_sha256  # noqa: E402
from mason_surrogate.common import (  # noqa: E402
    NormStats,
    UnifiedMLP,
    batch_sparse_positions,
)

DATA_DIR = PROJECT_ROOT / "surrogate_data_mason"
CKPT_DIR = PROJECT_ROOT / "checkpoints_mason"

ANCHOR_SENSITIVITY_NOTE = (
    "anchor_sensitivity_rmse_db 为乘性锚点扰动探针：q=w·(1±s)，"
    "s=clip(fs_pred/f_a−1, ±anchor_perturb_max) 是 Stage 1 fs 相对误差注入；"
    "衡量锚点误差敏感度（误差注入必然 ≥ full RMSE），WP5 论文不引用此列。"
)


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
            "epochs_run": cfg.get("epochs_run"), "train_seconds": cfg.get("train_seconds")}


def stage2_eval(ckpt_dir: Path, data_path: Path) -> dict:
    """读 stage2_eval.json；缺则从 checkpoint 重算（--data 透传，k 从 stage1 ckpt 读）。"""
    eval_path = ckpt_dir / "stage2_eval.json"
    if eval_path.exists():
        return json.loads(eval_path.read_text(encoding="utf-8"))
    cfg = dict(ts2.DEFAULT_CFG)
    cfg.update(data=str(data_path), out_dir=str(ckpt_dir),
               stage1_ckpt=str(ckpt_dir / "stage1.pt"),
               stage1_config=str(ckpt_dir / "stage1_config.json"))
    t0 = time.time()
    a = ts2._prepare(cfg)  # k 在 _prepare 内从 stage1 ckpt 读取
    ckpt = torch.load(ckpt_dir / "stage2.pt", map_location="cpu", weights_only=True)
    if int(ckpt.get("k", a["k"])) != a["k"]:
        print(f"WARNING: stage2.pt k={ckpt.get('k')} 与 stage1 ckpt k={a['k']} 不一致",
              file=sys.stderr)
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


def check_dataset_binding(ckpt_dir: Path, data_path: Path) -> tuple[str, list[str]]:
    """checkpoint↔数据集 sha256 校验。返回 (实际 sha256, warnings)。"""
    actual = file_sha256(data_path)
    warnings = []
    for stage in ("stage1", "stage2"):
        cfg = json.loads((ckpt_dir / f"{stage}_config.json").read_text(encoding="utf-8"))
        cfg_sha = cfg.get("dataset_sha256")
        if cfg_sha is None:
            warnings.append(f"{stage}_config 缺 dataset_sha256 字段（旧版产物），未校验")
        elif cfg_sha != actual:
            warnings.append(
                f"{stage} 训练数据集 sha256 与当前 --data 不一致: "
                f"config={cfg_sha[:12]}… actual={actual[:12]}…"
            )
    return actual, warnings


def build_forward_eval(
    ckpt_dir: Path,
    data_path: Path,
    meta: dict,
    stage1_train_seconds: float | None = None,
) -> tuple[dict, list[str]]:
    """组装 forward_eval dict；返回 (out, warnings)。"""
    data = ts1.load_unified(data_path)
    s1_cfg = json.loads((ckpt_dir / "stage1_config.json").read_text(encoding="utf-8"))

    data_sha, warnings = check_dataset_binding(ckpt_dir, data_path)
    s1 = stage1_eval(ckpt_dir, data)
    s2 = stage2_eval(ckpt_dir, data_path)
    s1_seconds = stage1_train_seconds or s1.get("train_seconds")

    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "task": "WP4a unified forward surrogate on Mason corpora (EPE+EPPE co-train)",
        "seed": s1_cfg["seed"],  # 从 stage1 config 读（复审次要项 5）
        "warnings": warnings,
        "dataset": {
            "path": str(data_path),
            "dataset_sha256": data_sha,
            "n_total": int(len(data["split"])),
            "w_grid": meta["w_grid"],
            # 锚点注记（与 unified_dataset.meta.json 一致）：EPPE 行指标锚 TE2 的 fs2
            "anchor": meta.get("anchor"),
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
            "epochs_run": s1.get("epochs_run"),
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
            "anchor_sensitivity_note": ANCHOR_SENSITIVITY_NOTE,
            "per_structure": s2["eval"],
        },
    }
    return out, warnings


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

    meta = json.loads(args.meta.read_text(encoding="utf-8"))
    s1_seconds = args.stage1_train_seconds
    if s1_seconds is None and args.stage1_log:
        s1_seconds = parse_train_seconds_from_log(args.stage1_log)

    out, warnings = build_forward_eval(args.ckpt, args.data, meta, s1_seconds)
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved: {args.out}")
    ts1.print_eval_stage1(out["stage1"]["per_structure"])
    ts2.print_eval_stage2(out["stage2"]["per_structure"])
    s1, s2 = out["stage1"], out["stage2"]
    print(f"\nstage1 best_epoch={s1['best_epoch']} train_seconds={s1['train_seconds']}")
    print(f"stage2 best_epoch={s2['best_epoch']} max_freq={s2['max_freq']} "
          f"compare={s2.get('max_freq_compare')} train_seconds={s2.get('train_seconds')}")


if __name__ == "__main__":
    main()
