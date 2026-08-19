"""训练 Zp 校正器（规格 §8.3）：ExtraTrees (eg, fl, area, pf) → Zp 峰高 dB。

动机：级联谱形状好（对齐 RMSE 0.73dB）但 Zp 峰高 MAE ~2.9dB——模态交叉使
结构→Zp 高度不连续，MLP 拟合不动（顶点 3.5dB），树模型可达 0.62dB。
推理时对级联输出峰区施加窄高斯加性校正，使顶点匹配树预测。

用法：
    uv run python scripts/train_zp_corrector.py --data ./surrogate_data_sim \
        --out ./ckpt_sim/zp_corrector.joblib
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

WIN_GHZ = 0.05


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./surrogate_data_sim")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    data = Path(args.data)

    from sklearn.ensemble import ExtraTreesRegressor

    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
    with np.load(data / "dataset.npz") as z:
        x, yp, fz, gi = z["x_struct"], z["y_params"], z["full_z"], z["group_idx"]
    freq = np.asarray(meta["freq_ghz"])
    splits = np.array([g["split"] for g in meta["groups"]])

    win = (freq[None, :] >= (yp[:, 1:2] - WIN_GHZ)) & (freq[None, :] <= (yp[:, 1:2] + WIN_GHZ))
    zp = np.array([fz[i][win[i]].max() for i in range(len(fz))])
    feats = np.stack([x[:, 0], x[:, 1], x[:, 3], x[:, 4]], axis=1)
    tr, te = splits[gi] == "train", splits[gi] == "test"

    m = ExtraTreesRegressor(n_estimators=400, n_jobs=-1, random_state=0)
    m.fit(feats[tr], zp[tr])
    e = m.predict(feats[te]) - zp[te]
    print(f"test: bias {e.mean():+.2f} MAE {np.abs(e).mean():.2f} "
          f"p95 {np.percentile(np.abs(e), 95):.2f} max {np.abs(e).max():.2f}")
    print("按面积档 MAE:")
    for area in sorted(set(feats[te, 2].astype(int))):
        ea = np.abs(e[feats[te, 2] == area])
        print(f"  {area}: {ea.mean():.2f} (n={len(ea)})")

    joblib.dump(m, args.out)
    print(f"saved: {args.out}")


if __name__ == "__main__":
    main()
