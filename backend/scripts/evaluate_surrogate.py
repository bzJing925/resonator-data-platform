"""代理模型评估（规格 §8）：三层指标 + B0/B2/B3 对照 + 报告图。

用法：
    uv run python scripts/evaluate_surrogate.py --data ./surrogate_data \
        --ckpt ./checkpoints_surrogate --out ./eval_out
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")

# 把 backend 目录加入路径，以便 import app（同 train_sparse_reconstructor.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from app.config import AlgorithmConfig  # noqa: E402
from app.ml.surrogate.baseline import BaselineTable  # noqa: E402
from app.ml.surrogate.infer import SurrogatePredictor  # noqa: E402

_PARAM_COLS = list(AlgorithmConfig.surr_param_cols)


def _region_masks(freq: np.ndarray, fs: float, fp: float) -> dict[str, np.ndarray]:
    """按真值 fs/fp 划评估区域（与采样规则同边界）。"""
    d = fp - fs
    dense = (freq >= fs - 0.5 * d) & (freq <= fp + 0.5 * d)
    trans = ((freq >= fs - 1.5 * d) & (freq < fs - 0.5 * d)) | (
        (freq > fp + 0.5 * d) & (freq <= fp + 1.5 * d)
    )
    far = ~(dense | trans)
    return {"dense": dense, "transition": trans, "far": far}


def _rmse(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float:
    if mask is not None:
        if mask.sum() == 0:
            return float("nan")
        a, b = a[mask], b[mask]
    return float(np.sqrt(np.mean((a - b) ** 2)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./surrogate_data")
    ap.add_argument("--ckpt", default="./checkpoints_surrogate")
    ap.add_argument("--out", default="./eval_out")
    ap.add_argument("--max-devices", type=int, default=0, help="调试：最多评估 N 个器件")
    args = ap.parse_args()

    data_dir, ckpt_dir = Path(args.data), Path(args.ckpt)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    freq = np.asarray(meta["freq_ghz"])
    # savez_compressed 每次按键访问都重新解压：一次性全部物化，避免器件循环内 O(M) 解压
    with np.load(data_dir / "dataset.npz") as z:
        npz = {k: z[k] for k in z.files}
    test_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == "test"}
    mask = np.array([ci in test_groups for ci in npz["group_idx"]])
    idx_all = np.where(mask)[0]
    if args.max_devices > 0:
        idx_all = idx_all[: args.max_devices]

    pred = SurrogatePredictor(
        ckpt_dir / "stage1.pt",
        ckpt_dir / "stage2.pt",
        ckpt_dir / "stage2_config.json",
        data_dir / "baseline.npz",
        data_dir / "meta.json",
    )
    baseline = BaselineTable.load(data_dir / "baseline.npz")
    b3_path = ckpt_dir / "b3_extratrees.joblib"
    b3 = joblib.load(b3_path) if b3_path.exists() else None

    # B2 准备：train 行按 (eg, fl, pf, area) 格求均值谱（v4：格键取自 x_struct）
    train_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == "train"}
    tr_mask = np.array([ci in train_groups for ci in npz["group_idx"]])
    cell_mean_z: dict[tuple, list[np.ndarray]] = {}
    for i in np.where(tr_mask)[0]:
        eg_i, fl_i, _ag, area_i, pf_i = npz["x_struct"][i]
        key = (float(eg_i), float(fl_i), float(pf_i), float(area_i))
        cell_mean_z.setdefault(key, []).append(npz["full_z"][i])
    cell_mean_z_mean = {key: np.mean(v, axis=0) for key, v in cell_mean_z.items()}

    rows = []
    for i in idx_all:
        true_z = npz["full_z"][i]
        true_params = npz["y_params"][i]
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        r = pred.predict(float(eg), float(fl), float(area), float(pf))
        pred_z = r["z_db"]
        pred_params = np.array([r["params"][c] for c in _PARAM_COLS])
        fs_t, fp_t = float(true_params[0]), float(true_params[1])
        regions = _region_masks(freq, fs_t, fp_t)

        # B0：纯基准
        _, z_b0 = baseline.lookup(float(area), float(pf))
        # B2：同 (area, pf) train 格 IDW 插值（排除本格）
        neighbors = [
            (key, mz)
            for key, mz in cell_mean_z_mean.items()
            if key[3] == float(area)
            and key[2] == float(pf)
            and not (key[0] == float(eg) and key[1] == float(fl))
        ]
        if neighbors:
            w, z_acc = 0.0, np.zeros_like(true_z)
            for key, mz in neighbors:
                d2 = (key[0] - float(eg)) ** 2 + (key[1] - float(fl)) ** 2
                wi = 1.0 / max(d2, 1e-6)
                z_acc += wi * mz
                w += wi
            z_b2 = z_acc / w
        else:
            z_b2 = z_b0

        # 预测谱重提取 fs/fp（argmin/argmax）
        fs_re = float(freq[int(np.argmin(pred_z))])
        fp_re = float(freq[int(np.argmax(pred_z))])

        row = {
            "device_idx": int(i),
            "cell": {"eg": float(eg), "fl": float(fl), "area": float(area), "pf": float(pf)},
            "fs_err_mhz": abs(float(pred_params[0]) - fs_t) * 1000,
            "fp_err_mhz": abs(float(pred_params[1]) - fp_t) * 1000,
            "fs_reextract_err_mhz": abs(fs_re - fs_t) * 1000,
            "fp_reextract_err_mhz": abs(fp_re - fp_t) * 1000,
            "rmse_full": _rmse(pred_z, true_z),
            **{f"rmse_{k}": _rmse(pred_z, true_z, m) for k, m in regions.items()},
            "rmse_b0": _rmse(z_b0, true_z),
            "rmse_b2": _rmse(z_b2, true_z),
        }
        if b3 is not None:
            p3 = b3.predict(np.array([[eg, fl, area, pf]]))[0]
            row["b3_fs_err_mhz"] = abs(float(p3[0]) - fs_t) * 1000
            row["b3_fp_err_mhz"] = abs(float(p3[1]) - fp_t) * 1000
        rows.append(row)

    def _mean(key: str) -> float:
        vals = [r[key] for r in rows if r[key] == r[key]]  # 去 NaN
        return float(np.mean(vals)) if vals else float("nan")

    def _mean_pf(key: str, pf_val: float) -> float:
        vals = [r[key] for r in rows if r["cell"]["pf"] == pf_val and r[key] == r[key]]
        return float(np.mean(vals)) if vals else float("nan")

    report = {
        "n_test_devices": len(rows),
        "n_test_groups": len(test_groups),
        "rmse_full_db_pf0": _mean_pf("rmse_full", 0.0),
        "rmse_full_db_pf1": _mean_pf("rmse_full", 1.0),
        "fs_mae_mhz_pf0": _mean_pf("fs_err_mhz", 0.0),
        "fs_mae_mhz_pf1": _mean_pf("fs_err_mhz", 1.0),
        "fs_mae_mhz": _mean("fs_err_mhz"),
        "fp_mae_mhz": _mean("fp_err_mhz"),
        "fs_reextract_mae_mhz": _mean("fs_reextract_err_mhz"),
        "fp_reextract_mae_mhz": _mean("fp_reextract_err_mhz"),
        "rmse_full_db": _mean("rmse_full"),
        "rmse_dense_db": _mean("rmse_dense"),
        "rmse_transition_db": _mean("rmse_transition"),
        "rmse_far_db": _mean("rmse_far"),
        "rmse_b0_db": _mean("rmse_b0"),
        "rmse_b2_db": _mean("rmse_b2"),
        "improvement_vs_b0_pct": (1 - _mean("rmse_full") / _mean("rmse_b0")) * 100
        if _mean("rmse_b0") > 0
        else float("nan"),
        "b3_fs_mae_mhz": _mean("b3_fs_err_mhz") if b3 else None,
        "targets": {
            "fs_mae_mhz": 10,
            "fp_mae_mhz": 15,
            "rmse_full_db": 0.5,
            "improvement_vs_b0_pct": 50,
        },
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # 图 1：随机 6 个 test 器件 预测 vs 真值
    rng = np.random.default_rng(0)
    pick = rng.choice(len(rows), size=min(6, len(rows)), replace=False)
    fig, axes = plt.subplots(2, 3, figsize=(15, 7))
    for ax, ri in zip(axes.flat, pick, strict=False):
        i = rows[ri]["device_idx"]
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        r = pred.predict(float(eg), float(fl), float(area), float(pf))
        ax.plot(freq, npz["full_z"][i], lw=1, label="true")
        ax.plot(freq, r["z_db"], lw=1, label="pred")
        ax.set_title(f"eg={eg} fl={fl} a={int(area)}", fontsize=9)
        ax.legend(fontsize=7)
    fig.suptitle("Surrogate vs True (test cells)")
    fig.tight_layout()
    fig.savefig(out_dir / "pred_vs_true.png", dpi=140)

    # 图 2：RMSE 随 (eg, fl) 热图（每面积档一张，取档数 ≤4）
    areas = sorted({r["cell"]["area"] for r in rows})[:4]
    egs = sorted({r["cell"]["eg"] for r in rows})
    fls = sorted({r["cell"]["fl"] for r in rows})
    fig, axes = plt.subplots(1, len(areas), figsize=(4 * len(areas), 4), squeeze=False)
    for ax, area in zip(axes.flat, areas, strict=True):
        grid = np.full((len(fls), len(egs)), np.nan)
        for r in rows:
            if r["cell"]["area"] != area:
                continue
            grid[fls.index(r["cell"]["fl"]), egs.index(r["cell"]["eg"])] = r["rmse_full"]
        im = ax.imshow(
            grid, origin="lower", aspect="auto", extent=[min(egs), max(egs), min(fls), max(fls)]
        )
        ax.set_title(f"area={int(area)}")
        ax.set_xlabel("eg")
        ax.set_ylabel("fl")
        fig.colorbar(im, ax=ax, label="RMSE dB")
    fig.suptitle("Full-spectrum RMSE over design grid (test)")
    fig.tight_layout()
    fig.savefig(out_dir / "rmse_heatmap.png", dpi=140)
    print(f"saved: {out_dir}")


if __name__ == "__main__":
    main()
