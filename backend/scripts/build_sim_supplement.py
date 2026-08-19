"""构建仿真补充数据集（Stage 1 混合训练用）。

流程：
1. 提取全部仿真 SNP 的 fs/fp（与实测同一提取器，L_AG=1.0 对齐 AG 占位）；
2. 在"实测训练组"交集格上训练 Δ 校准器（ExtraTrees：(eg,fl,area,pf)→Δfs/Δfp），
   实测 val/test 组的格不参与校准器训练（防泄漏）；
3. 对仿真独有格（实测未覆盖、落在实测参数范围内）应用校准 → 伪标签
   （仅 fs/fp/kt2 有效，Q 系由训练侧 mask 屏蔽——仿真 Q 系统性偏高不可用）；
4. 歧义格（仿真行间 fs 极差 >5 MHz）剔除。

输出 sim_supplement.npz：x_struct(M,5)、params3(M,3)=[fs,fp,kt2]、cells(M,4)、
report.json（校准器 CV 精度等）。

用法：
    uv run python scripts/build_sim_supplement.py --nprocs 8
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sim_offset_analysis import _AMBIG_MHZ, _extract_one  # noqa: E402 复用提取与歧义阈值

REPO = Path("/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master")
XLSX = REPO / "D1_D2_DOE_SNP_training_map_Lair_Lbot_minus6um.xlsx"
NPZ = PROJECT_ROOT / "surrogate_data" / "dataset.npz"
META = PROJECT_ROOT / "surrogate_data" / "meta.json"
OUT = PROJECT_ROOT / "surrogate_data" / "sim_supplement.npz"


def _kt2_pct(fs: float, fp: float) -> float:
    """与 extract.py 同一公式。"""
    return float((np.pi**2 / 4) * (fs / fp) * ((fp - fs) / fp) * 100)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nprocs", type=int, default=8)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    # ── 仿真表 ──
    ti = pd.read_excel(XLSX, sheet_name="Training_Index")
    ok = ti[ti["SNP_Status"] == "MATCHED"].copy()
    for c in ["Train_L_EG_um", "Train_L_FL_um", "Train_L_AG_um", "Train_A_res_um2", "Train_PF_nm"]:
        ok[c] = pd.to_numeric(ok[c], errors="coerce")
    ok["has_PF"] = (ok["Train_PF_nm"].fillna(0) > 0).astype(int)
    ok = ok[ok["Train_L_AG_um"] == 1.0]
    ok["eg"] = ok["Train_L_EG_um"].round(4)
    ok["fl"] = ok["Train_L_FL_um"].round(4)
    ok["area"] = ok["Train_A_res_um2"].astype(int)
    ok["cell"] = list(zip(ok["eg"], ok["fl"], ok["area"], ok["has_PF"], strict=False))

    # ── 实测格与分组归属（防泄漏：校准器只用 train 组的格）──
    meta = json.loads(META.read_text(encoding="utf-8"))
    with np.load(NPZ) as z:
        x, y, gi = z["x_struct"], z["y_params"], z["group_idx"]
    cell_split: dict[tuple, set] = {}
    cell_yidx: dict[tuple, list[int]] = {}
    for i in range(len(x)):
        key = (round(float(x[i, 0]), 4), round(float(x[i, 1]), 4), int(x[i, 3]), int(x[i, 4]))
        cell_split.setdefault(key, set()).add(meta["groups"][int(gi[i])]["split"])
        cell_yidx.setdefault(key, []).append(i)
    meas_cells = set(cell_yidx)
    train_cells = {k for k, v in cell_split.items() if v == {"train"}}
    print(f"实测格 {len(meas_cells)}，其中纯 train 组格 {len(train_cells)}（校准器训练用）")

    # ── 提取全部仿真行标签 ──
    paths = ok["SNP_Relative_Path"].tolist()
    print(f"提取 {len(paths)} 行仿真标签…")
    with ProcessPoolExecutor(max_workers=args.nprocs) as exe:
        results = list(exe.map(_extract_one, paths))
    ok = ok.assign(
        sim_fs=[r["fs_ghz"] if r else np.nan for r in results],
        sim_fp=[r["fp_ghz"] if r else np.nan for r in results],
    ).dropna(subset=["sim_fs", "sim_fp"])
    print(f"提取成功 {len(ok)} 行")

    # ── 格聚合 + 歧义剔除 ──
    agg = (
        ok.groupby("cell")
        .agg(
            eg=("eg", "first"),
            fl=("fl", "first"),
            area=("area", "first"),
            has_PF=("has_PF", "first"),
            n_rows=("sim_fs", "size"),
            sim_fs=("sim_fs", "median"),
            sim_fp=("sim_fp", "median"),
            spread_fs=("sim_fs", lambda s: (s.max() - s.min()) * 1000),
        )
        .reset_index()
    )
    agg = agg[agg["spread_fs"] <= _AMBIG_MHZ]
    print(f"聚合后 {len(agg)} 格（剔除歧义格 {ok['cell'].nunique() - len(agg)}）")

    # ── Δ 校准器（仅纯 train 交集格）──
    from sklearn.ensemble import ExtraTreesRegressor
    from sklearn.model_selection import GroupKFold, cross_val_predict

    inter = agg[agg["cell"].isin(meas_cells)].copy()
    meas_med = {k: np.median(y[idxs, :2], axis=0) for k, idxs in cell_yidx.items()}
    inter["meas_fs"] = inter["cell"].map(lambda c: meas_med[c][0])
    inter["meas_fp"] = inter["cell"].map(lambda c: meas_med[c][1])
    inter["d_fs"] = (inter["sim_fs"] - inter["meas_fs"]) * 1000
    inter["d_fp"] = (inter["sim_fp"] - inter["meas_fp"]) * 1000
    tr = inter[inter["cell"].isin(train_cells)]
    x_tr = tr[["eg", "fl", "area", "has_PF"]].to_numpy()
    grp = (tr["eg"].astype(str) + "|" + tr["fl"].astype(str)).to_numpy()
    calibrators = {}
    cv_report = {}
    for tgt in ("d_fs", "d_fp"):
        yv = tr[tgt].to_numpy()
        preds = cross_val_predict(
            ExtraTreesRegressor(n_estimators=300, n_jobs=-1, random_state=0),
            x_tr,
            yv,
            cv=GroupKFold(5).split(x_tr, yv, grp),
            n_jobs=1,
        )
        resid = preds - yv
        cv_report[tgt] = {
            "mae_mhz": float(np.abs(resid).mean()),
            "p95_mhz": float(np.percentile(np.abs(resid), 95)),
        }
        m = ExtraTreesRegressor(n_estimators=300, n_jobs=-1, random_state=0)
        m.fit(x_tr, yv)
        calibrators[tgt] = m
    print(f"校准器 CV（train 组 {len(tr)} 格）: {cv_report}")

    # ── 仿真独有格（实测未覆盖 + 参数范围内）→ 校准伪标签 ──
    eg_lo, eg_hi = min(c[0] for c in meas_cells), max(c[0] for c in meas_cells)
    fl_lo, fl_hi = min(c[1] for c in meas_cells), max(c[1] for c in meas_cells)
    ar_lo, ar_hi = min(c[2] for c in meas_cells), max(c[2] for c in meas_cells)
    only = agg[~agg["cell"].isin(meas_cells)].copy()
    in_range = only[
        only["eg"].between(eg_lo, eg_hi)
        & only["fl"].between(fl_lo, fl_hi)
        & only["area"].between(ar_lo, ar_hi)
    ].copy()
    x_new = in_range[["eg", "fl", "area", "has_PF"]].to_numpy()
    in_range["fs_cal"] = in_range["sim_fs"] - calibrators["d_fs"].predict(x_new) / 1000
    in_range["fp_cal"] = in_range["sim_fp"] - calibrators["d_fp"].predict(x_new) / 1000
    # 物理合法性：校准后 fp 必须仍 > fs
    bad = in_range["fp_cal"] <= in_range["fs_cal"]
    if bad.any():
        print(f"校准后 fp<=fs 剔除 {bad.sum()} 格")
        in_range = in_range[~bad]
    print(f"仿真独有格 {len(only)}，范围内可用 {len(in_range)} 格")

    # ── 范围外扩展格（如 area=600）：eg/fl 在实测范围内（校准器可插值），
    #    仅 area 越界（ExtraTrees 平推，Δ≈端点值）→ 单独写 sim_extension.npz ──
    ext = only[
        only["eg"].between(eg_lo, eg_hi)
        & only["fl"].between(fl_lo, fl_hi)
        & ~only["area"].between(ar_lo, ar_hi)
    ].copy()
    if len(ext):
        x_ext = ext[["eg", "fl", "area", "has_PF"]].to_numpy()
        ext["fs_cal"] = ext["sim_fs"] - calibrators["d_fs"].predict(x_ext) / 1000
        ext["fp_cal"] = ext["sim_fp"] - calibrators["d_fp"].predict(x_ext) / 1000
        ext = ext[ext["fp_cal"] > ext["fs_cal"]]
        ext_path = Path(args.out).parent / "sim_extension.npz"
        np.savez_compressed(
            ext_path,
            x_struct=(
                ext[["eg", "fl"]]
                .assign(ag=1.0, area=ext["area"], pf=ext["has_PF"])
                .to_numpy(dtype=np.float32)
            ),
            params3=np.stack(
                [
                    ext["fs_cal"].to_numpy(),
                    ext["fp_cal"].to_numpy(),
                    [_kt2_pct(f, p) for f, p in zip(ext["fs_cal"], ext["fp_cal"], strict=False)],
                ],
                axis=1,
            ).astype(np.float32),
            cells=ext[["eg", "fl", "area", "has_PF"]].to_numpy(),
        )
        print(f"范围外扩展格 {len(ext)}（area {sorted(ext['area'].unique())}）→ {ext_path}")

    x_out = (
        in_range[["eg", "fl"]]
        .assign(ag=1.0, area=in_range["area"], pf=in_range["has_PF"])
        .to_numpy(dtype=np.float32)
    )
    p3 = np.stack(
        [
            in_range["fs_cal"].to_numpy(),
            in_range["fp_cal"].to_numpy(),
            [_kt2_pct(f, p) for f, p in zip(in_range["fs_cal"], in_range["fp_cal"], strict=False)],
        ],
        axis=1,
    ).astype(np.float32)
    out_path = Path(args.out)
    np.savez_compressed(
        out_path,
        x_struct=x_out,
        params3=p3,
        cells=in_range[["eg", "fl", "area", "has_PF"]].to_numpy(),
    )
    report = {
        "n_cells": len(in_range),
        "calibrator_cv": cv_report,
        "calibrator_train_cells": len(tr),
        "note": "params3=[fs_ghz, fp_ghz, k2eff_pct]（校准后）；Q 系不可用，训练侧屏蔽",
    }
    (out_path.parent / "sim_supplement_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"输出: {out_path}（{len(in_range)} 格）")


if __name__ == "__main__":
    main()
