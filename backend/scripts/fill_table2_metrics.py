"""填充 IUS 论文 Table II 空白单元格——全部用真实可计算的基线数字。

新增计算（均基于生产 surrogate_data + checkpoints_surrogate 同源数据）：
- B0 标量指标：基准表 params_base 按 (area, pf) 查询 → fs/fp/k2eff MAE
- B2 标量指标：同 (area, pf) train 格均值参数 IDW 插值（权重与谱插值一致）→ fs/fp/k2eff MAE
- B0/B2 对齐 RMSE：预测谱在 ±15 格点（≈±45 MHz）内最优平移后与实测谱的 RMSE
- B0/B2 Zp 峰高 MAE：真值 fp ± 50 MHz 窗内峰高误差
- Floor（格内留一）扩展到 fp / k2eff / Zp

同时交叉校验论文中已有数字（B0/B2/Ours 的谱 RMSE、LOO fs/RMSE、B3 fs）
不随管线漂移。

用法（ML worktree backend/ 下）：
    uv run python scripts/fill_table2_metrics.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import AlgorithmConfig  # noqa: E402
from app.ml.surrogate.baseline import BaselineTable  # noqa: E402
from app.ml.surrogate.infer import SurrogatePredictor  # noqa: E402

PARAM_COLS = list(AlgorithmConfig.surr_param_cols)
J_FS, J_FP, J_K2 = PARAM_COLS.index("fs_ghz"), PARAM_COLS.index("fp_ghz"), PARAM_COLS.index("k2eff_pct")


def aligned_rmse(pred: np.ndarray, true: np.ndarray, max_shift: int = 15) -> float:
    """与 evaluate_surrogate._aligned_rmse 同定义：±15 格点（≈3 MHz/点）最优平移。"""
    best = float("inf")
    for s in range(-max_shift, max_shift + 1):
        if s >= 0:
            p, t = pred[s:], true[: len(true) - s] if s else true
        else:
            p, t = pred[:s], true[-s:]
        v = float(np.sqrt(np.mean((p - t) ** 2)))
        best = min(best, v)
    return best


def main() -> None:
    data_dir = Path("surrogate_data")
    ckpt_dir = Path("checkpoints_surrogate")
    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    freq = np.asarray(meta["freq_ghz"])
    with np.load(data_dir / "dataset.npz") as z:
        npz = {k: z[k] for k in z.files}

    test_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == "test"}
    idx = [i for i, ci in enumerate(npz["group_idx"]) if ci in test_groups]

    baseline = BaselineTable.load(data_dir / "baseline.npz")
    pred = SurrogatePredictor(
        ckpt_dir / "stage1.pt",
        ckpt_dir / "stage2_ft.pt",
        ckpt_dir / "stage2_config.json",
        data_dir / "baseline.npz",
        data_dir / "meta.json",
    )

    # train 格均值谱与均值参数（B2 插值源；格键 (eg, fl, pf, area) 取自 x_struct）
    train_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == "train"}
    cell_z: dict[tuple, list[np.ndarray]] = {}
    cell_p: dict[tuple, list[np.ndarray]] = {}
    for i, ci in enumerate(npz["group_idx"]):
        if ci not in train_groups:
            continue
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        key = (float(eg), float(fl), float(pf), float(area))
        cell_z.setdefault(key, []).append(npz["full_z"][i])
        cell_p.setdefault(key, []).append(npz["y_params"][i])
    cell_z_mean = {k: np.mean(v, axis=0) for k, v in cell_z.items()}
    cell_p_mean = {k: np.mean(v, axis=0) for k, v in cell_p.items()}

    # test 格成员（LOO 下限）
    cell_members: dict[tuple, list[int]] = {}
    for i in idx:
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        cell_members.setdefault((float(eg), float(fl), float(pf), float(area)), []).append(i)

    out = {
        "b0": {"fs": [], "fp": [], "k2": [], "rmse": [], "aligned": [], "zp": []},
        "b2": {"fs": [], "fp": [], "k2": [], "rmse": [], "aligned": [], "zp": []},
        "ours": {"fs": [], "fp": [], "k2": [], "rmse": [], "aligned": [], "zp": []},
    }
    for i in idx:
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        eg, fl, area, pf = float(eg), float(fl), float(area), float(pf)
        true_z = npz["full_z"][i]
        tp = npz["y_params"][i]
        fp_t = float(tp[J_FP])
        win = (freq >= fp_t - 0.05) & (freq <= fp_t + 0.05)
        zp_t = float(np.max(true_z[win]))

        # B0
        p_b0, z_b0 = baseline.lookup(area, pf)
        # B2：同 (area, pf) train 格 IDW（排除本格），谱与参数用同一组权重
        nbrs = [
            k for k in cell_z_mean
            if k[3] == area and k[2] == pf and not (k[0] == eg and k[1] == fl)
        ]
        if nbrs:
            wsum, z_acc, p_acc = 0.0, np.zeros_like(true_z), np.zeros_like(tp)
            for k in nbrs:
                wi = 1.0 / max((k[0] - eg) ** 2 + (k[1] - fl) ** 2, 1e-6)
                z_acc += wi * cell_z_mean[k]
                p_acc += wi * cell_p_mean[k]
                wsum += wi
            z_b2, p_b2 = z_acc / wsum, p_acc / wsum
        else:
            z_b2, p_b2 = z_b0, p_b0

        # Ours（交叉校验 + Zp 需校正器后的生产行为）
        r = pred.predict(eg, fl, area, pf)
        z_ours = r["z_db"]
        p_ours = np.array([r["params"][c] for c in PARAM_COLS])

        for name, p_x, z_x in (("b0", p_b0, z_b0), ("b2", p_b2, z_b2), ("ours", p_ours, z_ours)):
            out[name]["fs"].append(abs(float(p_x[J_FS]) - float(tp[J_FS])) * 1000)
            out[name]["fp"].append(abs(float(p_x[J_FP]) - float(tp[J_FP])) * 1000)
            out[name]["k2"].append(abs(float(p_x[J_K2]) - float(tp[J_K2])))
            out[name]["rmse"].append(float(np.sqrt(np.mean((z_x - true_z) ** 2))))
            out[name]["aligned"].append(aligned_rmse(z_x, true_z))
            out[name]["zp"].append(abs(float(np.max(z_x[win])) - zp_t))

    # Floor：test 格内留一（≥2 器件的格），谱用均值、标量用中位数（与原定义一致）
    loo = {"fs": [], "fp": [], "k2": [], "rmse": [], "zp": [], "zp_scalar": []}
    for key, members in cell_members.items():
        if len(members) < 2:
            continue
        for i in members:
            others = [j for j in members if j != i]
            mean_z = npz["full_z"][others].mean(axis=0)
            med_p = np.median(npz["y_params"][others], axis=0)
            tp = npz["y_params"][i]
            fp_t = float(tp[J_FP])
            win = (freq >= fp_t - 0.05) & (freq <= fp_t + 0.05)
            zp_i = float(np.max(npz["full_z"][i][win]))
            loo["fs"].append(abs(float(tp[J_FS]) - float(med_p[J_FS])) * 1000)
            loo["fp"].append(abs(float(tp[J_FP]) - float(med_p[J_FP])) * 1000)
            loo["k2"].append(abs(float(tp[J_K2]) - float(med_p[J_K2])))
            loo["rmse"].append(float(np.sqrt(np.mean((npz["full_z"][i] - mean_z) ** 2))))
            # 均值谱峰高（含平均压峰伪差，仅供参考，不入表）
            loo["zp"].append(abs(float(np.max(mean_z[win])) - zp_i))
            # 标量口径：同格器件各自自身谐振窗峰高的中位数 → 固有工艺散布（入表）
            zp_others = []
            for j in others:
                fp_j = float(npz["y_params"][j][J_FP])
                win_j = (freq >= fp_j - 0.05) & (freq <= fp_j + 0.05)
                zp_others.append(float(np.max(npz["full_z"][j][win_j])))
            loo["zp_scalar"].append(abs(zp_i - float(np.median(zp_others))))

    summary: dict = {"n_test_devices": len(idx)}
    for name in ("b0", "b2", "ours"):
        summary[name] = {k: float(np.mean(v)) for k, v in out[name].items()}
    summary["floor"] = {k: float(np.mean(v)) for k, v in loo.items()}

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out_path = Path("eval_out_current/table2_fills.json")
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out_path}")


if __name__ == "__main__":
    main()
