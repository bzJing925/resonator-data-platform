"""B3 轻量基线：ExtraTrees 结构参数 → 性能参数（规格 §8 对照基线）。

用法：
    uv run python scripts/train_baseline_b3.py --data ./surrogate_data \
        --out ./checkpoints_surrogate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor

# 把 backend 目录加入路径，以便 import app（同 train_sparse_reconstructor.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import AlgorithmConfig  # noqa: E402

_PARAM_COLS = list(AlgorithmConfig.surr_param_cols)


def _load_split(data_dir: str, split: str) -> tuple[np.ndarray, np.ndarray]:
    meta = json.loads((Path(data_dir) / "meta.json").read_text(encoding="utf-8"))
    keep = {i for i, g in enumerate(meta["groups"]) if g["split"] == split}
    with np.load(Path(data_dir) / "dataset.npz") as data:
        mask = np.array([ci in keep for ci in data["group_idx"]])
        # 输入 eg/fl/area/pf（ag 恒值不用）
        return data["x_struct"][mask][:, [0, 1, 3, 4]], data["y_params"][mask]


def run(data_dir: str, out_dir: str, n_estimators: int = 300) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    x_tr, y_tr = _load_split(data_dir, "train")
    x_te, y_te = _load_split(data_dir, "test")

    model = ExtraTreesRegressor(n_estimators=n_estimators, n_jobs=-1, random_state=42)
    model.fit(x_tr, y_tr)
    pred = model.predict(x_te)

    mae = np.abs(pred - y_te).mean(axis=0)
    report = {
        "model": "ExtraTreesRegressor",
        "n_train": int(len(x_tr)),
        "n_test": int(len(x_te)),
        "mae": {col: float(m) for col, m in zip(_PARAM_COLS, mae, strict=True)},
        "fs_mae_mhz": float(mae[0] * 1000),
        "fp_mae_mhz": float(mae[1] * 1000),
    }
    joblib.dump(model, out / "b3_extratrees.joblib")
    (out / "b3_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="./checkpoints_surrogate")
    ap.add_argument("--n-estimators", type=int, default=300)
    args = ap.parse_args()
    run(args.data, args.out, args.n_estimators)


if __name__ == "__main__":
    main()
