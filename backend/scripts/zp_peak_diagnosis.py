"""Zp 峰（fp 处反谐振峰）误差诊断：定位"小面积峰值差几个 dB"的问题。

口径（test 组逐器件）：
- height_err : 预测峰高 − 真值峰高（各自在自己 fp 附近 ±50MHz 取峰）——纯高度误差
- atfp_err   : 真值 fp 处预测峰 − 真值峰——含频率误差的综合效果（用户看到的）
- 地板       : 同格器件间真值峰高的 std / 极差（工艺离散，不可约）
- 格均值口径 : 预测峰高 − 格均值谱峰高（既定公平参照）

按 12 个面积档分解，确认小面积恶化程度。

用法：
    uv run python scripts/zp_peak_diagnosis.py --data ./surrogate_data \
        --ckpt ./checkpoints_surrogate
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.surrogate.infer import SurrogatePredictor  # noqa: E402

WIN_GHZ = 0.05  # 峰搜索半窗


def _peak(freq: np.ndarray, z: np.ndarray, f0: float) -> float:
    m = (freq >= f0 - WIN_GHZ) & (freq <= f0 + WIN_GHZ)
    return float(z[m].max()) if m.any() else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./surrogate_data")
    ap.add_argument("--ckpt", default="./checkpoints_surrogate")
    args = ap.parse_args()
    data, ckpt = Path(args.data), Path(args.ckpt)

    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
    with np.load(data / "dataset.npz") as z:
        x, yp, fz, gi = z["x_struct"], z["y_params"], z["full_z"], z["group_idx"]
    freq = np.asarray(meta["freq_ghz"])
    splits = np.array([g["split"] for g in meta["groups"]])
    test_mask = splits[gi] == "test"
    idxs = np.where(test_mask)[0]
    print(f"test 器件 {test_mask.sum()} 条 / {len(set(gi[test_mask].tolist()))} 组")

    s2 = ckpt / "stage2_ft.pt"
    if not s2.exists():
        s2 = ckpt / "stage2.pt"
    pred = SurrogatePredictor(
        ckpt / "stage1.pt",
        s2,
        ckpt / "stage2_config.json",
        data / "baseline.npz",
        data / "meta.json",
    )

    rows = []
    for i in idxs:
        eg, fl, ag, area, pf = [float(v) for v in x[i]]
        r = pred.predict(eg, fl, area, pf, ag)
        fs_t, fp_t = float(yp[i, 0]), float(yp[i, 1])
        zp_t = _peak(freq, fz[i], fp_t)
        zp_p_own = _peak(freq, r["z_db"], r["params"]["fp_ghz"])
        zp_p_at = _peak(freq, r["z_db"], fp_t)
        rows.append(
            dict(
                i=int(i),
                eg=eg,
                fl=fl,
                area=area,
                pf=pf,
                fs_t=fs_t,
                fp_t=fp_t,
                fs_p=r["params"]["fs_ghz"],
                fp_p=r["params"]["fp_ghz"],
                zp_t=zp_t,
                height_err=zp_p_own - zp_t,
                atfp_err=zp_p_at - zp_t,
            )
        )

    # 格均值参照与格内地板
    cells: dict[tuple, list[int]] = defaultdict(list)
    for i in range(len(x)):
        cells[
            (round(float(x[i, 0]), 4), round(float(x[i, 1]), 4), int(x[i, 3]), int(x[i, 4]))
        ].append(i)
    cell_zp = {
        c: np.array([_peak(freq, fz[j], float(yp[j, 1])) for j in js]) for c, js in cells.items()
    }

    print("\n=== 总量（test 逐器件, dB）===")
    he = np.array([r["height_err"] for r in rows])
    ae = np.array([r["atfp_err"] for r in rows])
    for name, v in [("height_err(纯高度)", he), ("atfp_err(真fp处)", ae)]:
        print(
            f"{name}: MAE {np.abs(v).mean():.2f}  p95 {np.percentile(np.abs(v), 95):.2f}  "
            f"max {np.abs(v).max():.2f}  bias {v.mean():+.2f}"
        )

    print("\n=== 按面积档（test）===")
    print(
        f"{'area':>6} {'n':>3} {'Zp真值中位':>9} {'height MAE':>10} {'atfp MAE':>9} "
        f"{'atfp max':>8} {'格内std':>7} {'格内极差':>8}"
    )
    for area in sorted({r["area"] for r in rows}):
        rs = [r for r in rows if r["area"] == area]
        h = np.abs([r["height_err"] for r in rs])
        a = np.abs([r["atfp_err"] for r in rs])
        zp_med = np.median([r["zp_t"] for r in rs])
        sp = [
            cell_zp[(round(r["eg"], 4), round(r["fl"], 4), int(r["area"]), int(r["pf"]))]
            for r in rs
        ]
        stds = [s.std() for s in sp if len(s) > 1]
        rngs = [s.max() - s.min() for s in sp if len(s) > 1]
        print(
            f"{area:6.0f} {len(rs):3d} {zp_med:9.2f} {h.mean():10.2f} {a.mean():9.2f} "
            f"{a.max():8.2f} {np.median(stds) if stds else float('nan'):7.2f} "
            f"{max(rngs) if rngs else 0.0:8.2f}"
        )

    # 最差 10 器件
    rows.sort(key=lambda r: -abs(r["atfp_err"]))
    print("\n=== atfp 误差最差 10 ===")
    for r in rows[:10]:
        print(
            f"area={r['area']:.0f} eg={r['eg']} fl={r['fl']} pf={r['pf']:.0f} "
            f"fp {r['fp_t']:.3f}→{r['fp_p']:.3f} Zp {r['zp_t']:.1f} 误差 {r['atfp_err']:+.2f} dB"
        )


if __name__ == "__main__":
    main()
