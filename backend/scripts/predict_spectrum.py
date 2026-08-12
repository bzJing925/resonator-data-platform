"""代理模型推理 CLI：输入结构参数，输出性能参数 + Z11(dB) 全谱图/CSV。

用法：
    uv run python scripts/predict_spectrum.py --eg 1.0 --fl 0.5 --area 1500 \
        --data ./surrogate_data --ckpt ./checkpoints_surrogate --out ./pred_demo
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

# 把 backend 目录加入路径，以便 import app（同 train_sparse_reconstructor.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib.pyplot as plt  # noqa: E402

from app.ml.surrogate.infer import SurrogatePredictor  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eg", type=float, required=True)
    ap.add_argument("--fl", type=float, required=True)
    ap.add_argument("--area", type=float, required=True)
    ap.add_argument("--pf", type=int, default=0, choices=[0, 1], help="massloading 有无（0/1）")
    ap.add_argument("--ag", type=float, default=1.0)
    ap.add_argument("--data", default="./surrogate_data")
    ap.add_argument("--ckpt", default="./checkpoints_surrogate")
    ap.add_argument("--out", default="./pred_out")
    ap.add_argument("--stage2", default="", help="Stage 2 权重（默认优先 ckpt/stage2_ft.pt）")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    data, ckpt = Path(args.data), Path(args.ckpt)

    if args.stage2:
        stage2_path = Path(args.stage2)
    else:  # Step B 微调产物优先（Stage 1 预测点分布训练，端到端口径）
        stage2_path = ckpt / "stage2_ft.pt"
        if not stage2_path.exists():
            stage2_path = ckpt / "stage2.pt"
    pred = SurrogatePredictor(
        stage1_path=ckpt / "stage1.pt",
        stage2_path=stage2_path,
        stage2_config=ckpt / "stage2_config.json",
        baseline_path=data / "baseline.npz",
        meta_path=data / "meta.json",
    )
    result = pred.predict(args.eg, args.fl, args.area, args.pf, args.ag)

    print(json.dumps(result["params"], ensure_ascii=False, indent=2))

    with open(out_dir / "spectrum.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["freq_ghz", "z_db"])
        w.writerows(zip(result["freq_ghz"], result["z_db"], strict=True))

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(result["freq_ghz"], result["z_db"], lw=1.2, label="surrogate")
    ax.plot(result["sample_freq"], result["sample_z"], ".", ms=3, label="sparse pts")
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("|Z11| (dB)")
    ax.set_title(f"EG={args.eg} FL={args.fl} area={args.area} µm²")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "spectrum.png", dpi=150)
    print(f"saved: {out_dir / 'spectrum.png'} , {out_dir / 'spectrum.csv'}")


if __name__ == "__main__":
    main()
