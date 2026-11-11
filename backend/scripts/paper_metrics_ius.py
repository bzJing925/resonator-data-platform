"""为 IUS 论文计算实测代理模型的逐参数 MSE/R² 与工艺下限。

数据源：surrogate_data/（仿真参考清洗版）+ checkpoints_surrogate/（含 zp_corrector）。
输出：paper/ius2026/figures/measured_metrics.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import AlgorithmConfig  # noqa: E402
from app.ml.surrogate.infer import SurrogatePredictor  # noqa: E402

PARAM_COLS = list(AlgorithmConfig.surr_param_cols)


def r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def main() -> None:
    data_dir = Path("surrogate_data")
    ckpt_dir = Path("checkpoints_surrogate")
    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    freq = np.asarray(meta["freq_ghz"])
    with np.load(data_dir / "dataset.npz") as z:
        npz = {k: z[k] for k in z.files}

    test_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == "test"}
    idx = [i for i, ci in enumerate(npz["group_idx"]) if ci in test_groups]

    pred = SurrogatePredictor(
        ckpt_dir / "stage1.pt",
        ckpt_dir / "stage2_ft.pt",
        ckpt_dir / "stage2_config.json",
        data_dir / "baseline.npz",
        data_dir / "meta.json",
    )

    y_true_p, y_pred_p = [], []  # params
    z_true, z_pred = [], []  # full spectra
    s_true, s_pred = [], []  # sparse 256 pts (at true-fs positions)
    zp_true, zp_pred = [], []  # fp ± 50 MHz 峰高
    for i in idx:
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        r = pred.predict(float(eg), float(fl), float(area), float(pf))
        y_pred_p.append([r["params"][c] for c in PARAM_COLS])
        y_true_p.append(npz["y_params"][i])
        z_true.append(npz["full_z"][i])
        z_pred.append(r["z_db"])
        s_true.append(npz["y_sparse"][i])
        # 用真值 fs/fp 位置取预测谱的稀疏值（隔离 Stage 2 重建质量）
        s_pred.append(r["z_db"][npz["sample_pos"][i]])
        fp_t = float(npz["y_params"][i][1])
        win = (freq >= fp_t - 0.05) & (freq <= fp_t + 0.05)
        zp_true.append(float(np.max(npz["full_z"][i][win])))
        zp_pred.append(float(np.max(r["z_db"][win])))

    y_true_p = np.array(y_true_p)
    y_pred_p = np.array(y_pred_p)
    z_true = np.array(z_true)
    z_pred = np.array(z_pred)
    s_true = np.array(s_true)
    s_pred = np.array(s_pred)

    out: dict = {"n_test_devices": len(idx), "params": {}}
    for j, col in enumerate(PARAM_COLS):
        t, p = y_true_p[:, j], y_pred_p[:, j]
        scale = 1e6 if col in ("fs_ghz", "fp_ghz") else 1.0  # GHz² → MHz²
        out["params"][col] = {
            "mse": float(np.mean((t - p) ** 2)) * scale,
            "rmse": float(np.sqrt(np.mean((t - p) ** 2)) * np.sqrt(scale)),
            "mae": float(np.mean(np.abs(t - p))) * np.sqrt(scale),
            "r2": r2(t, p),
            "unit": "MHz" if col in ("fs_ghz", "fp_ghz") else ("pct" if col == "k2eff_pct" else "1"),
        }

    # 频谱级指标（dB 域）
    per_dev_mse = np.mean((z_true - z_pred) ** 2, axis=1)
    per_dev_r2 = np.array([r2(z_true[i], z_pred[i]) for i in range(len(idx))])
    out["spectrum"] = {
        "mse_db2": float(per_dev_mse.mean()),
        "rmse_db": float(np.sqrt(per_dev_mse.mean())),
        "r2_macro": float(per_dev_r2.mean()),
        "r2_pooled": r2(z_true.ravel(), z_pred.ravel()),
    }
    sparse_mse = np.mean((s_true - s_pred) ** 2, axis=1)
    out["sparse_256"] = {
        "mse_db2": float(sparse_mse.mean()),
        "rmse_db": float(np.sqrt(sparse_mse.mean())),
        "r2_pooled": r2(s_true.ravel(), s_pred.ravel()),
    }

    # Zp 峰高（带 zp_corrector 的生产行为）
    zp_true = np.array(zp_true)
    zp_pred = np.array(zp_pred)
    out["zp_peak_db"] = {
        "mae": float(np.mean(np.abs(zp_true - zp_pred))),
        "bias": float(np.mean(zp_pred - zp_true)),
        "p95_abs": float(np.percentile(np.abs(zp_true - zp_pred), 95)),
    }

    # 工艺下限：test 格内留一（同格其他器件均值预测该器件）
    cell_members: dict[tuple, list[int]] = {}
    for i in idx:
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        cell_members.setdefault((float(eg), float(fl), float(pf), float(area)), []).append(i)
    loo_rmse, loo_fs, n_multi = [], [], 0
    for key, members in cell_members.items():
        if len(members) < 2:
            continue
        n_multi += 1
        for i in members:
            others = [j for j in members if j != i]
            mean_z = npz["full_z"][others].mean(axis=0)
            mean_fs = float(np.median(npz["y_params"][others, 0]))
            loo_rmse.append(float(np.sqrt(np.mean((npz["full_z"][i] - mean_z) ** 2))))
            loo_fs.append(abs(float(npz["y_params"][i, 0]) - mean_fs) * 1000)
    out["process_floor"] = {
        "n_cells_multi": n_multi,
        "loo_rmse_db": float(np.mean(loo_rmse)),
        "loo_fs_mae_mhz": float(np.mean(loo_fs)),
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    out_path = Path("eval_out_current/measured_metrics.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out_path}")


if __name__ == "__main__":
    main()
