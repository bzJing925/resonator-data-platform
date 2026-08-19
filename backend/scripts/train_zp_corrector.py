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
    ap.add_argument(
        "--sim-ext",
        default="",
        help="范围外仿真扩展格 npz（build_sim_supplement.py 产物）；"
        "给定后这些格以 ΔZp 校准伪标签并入训练（前端'仿真外推'档）",
    )
    ap.add_argument("--sim-ref", default="", help="仿真格参考 npz（取 Zp 原始值）")
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

    feats_tr, zp_tr = feats[tr], zp[tr]
    # ── 范围外仿真扩展格（如 area=600）：ΔZp 校准（纯 train 格防泄漏）后并入训练 ──
    if args.sim_ext and args.sim_ref:
        with np.load(args.sim_ext) as z:
            ext_cells = z["cells"]
        with np.load(args.sim_ref) as z:
            ref_cells, ref = z["cells"], z["ref"]
        ref_zp = {
            (round(float(c[0]), 4), round(float(c[1]), 4), int(c[2]), int(c[3])): float(r[2])
            for c, r in zip(ref_cells, ref, strict=False)
        }
        # 实测 train 格的 Zp 中位数 → ΔZp = 实测 − 仿真 校准器
        by_cell: dict[tuple, list[float]] = {}
        for i in np.where(tr)[0]:
            key = (round(float(x[i, 0]), 4), round(float(x[i, 1]), 4), int(x[i, 3]), int(x[i, 4]))
            by_cell.setdefault(key, []).append(float(zp[i]))
        inter = [k for k in by_cell if k in ref_zp]
        x_cal = np.array([list(k) for k in inter])
        y_cal = np.array([float(np.median(by_cell[k])) - ref_zp[k] for k in inter])
        cal = ExtraTreesRegressor(n_estimators=300, n_jobs=-1, random_state=0)
        cal.fit(x_cal, y_cal)
        ext_x, ext_zp = [], []
        for c in ext_cells:
            key = (round(float(c[0]), 4), round(float(c[1]), 4), int(c[2]), int(c[3]))
            if key not in ref_zp:
                continue
            ext_x.append([float(c[0]), float(c[1]), float(c[2]), float(c[3])])
            ext_zp.append(ref_zp[key] + float(cal.predict([ext_x[-1]])[0]))
        if ext_x:
            feats_tr = np.concatenate([feats_tr, np.array(ext_x)])
            zp_tr = np.concatenate([zp_tr, np.array(ext_zp)])
            print(f"并入范围外仿真扩展格 {len(ext_x)}（ΔZp 校准）")

    m = ExtraTreesRegressor(n_estimators=400, n_jobs=-1, random_state=0)
    m.fit(feats_tr, zp_tr)
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
