"""仿真↔实测域差量化：同一结构格上 Δ=仿真−实测 的 fs/fp/Q 偏移分布。

判定仿真数据能否用于补充实测代理模型的第一步：
- 系统性、可学的偏移 → 仿真可用于预训练/校准；
- 混乱无规律的偏移 → 仿真只适合做谱形预训练。

口径：
- 仿真行筛选 SNP_Status=MATCHED 且 L_AG=1.0（与实测 AG 占位一致）；
- 每格多行时（模板/厚度变体或 DOE 重复）取各行提取值中位数，
  格内行间 fs 极差 >5 MHz 的格标记为 ambiguous 单独报告；
- 实测值取 npz 中该格抽样记录的中位数；
- 仿真谱用与实测完全相同的 extract_resonator_params 提取（同一标签管线）。

用法：
    uv run python scripts/sim_offset_analysis.py --nprocs 8
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.extract import extract_resonator_params  # noqa: E402

REPO = Path("/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master")  # 主仓库（仿真文件所在）
XLSX = REPO / "D1_D2_DOE_SNP_training_map_Lair_Lbot_minus6um.xlsx"
SNP_ROOT = REPO / "L_air = L_bot-6[um]"
NPZ = PROJECT_ROOT / "surrogate_data" / "dataset.npz"

_AMBIG_MHZ = 5.0  # 格内仿真行间 fs 极差超过该值 → 模态跟踪/模板歧义，剔除统计


def _extract_one(rel_path: str) -> dict | None:
    """单仿真文件 → 标量标签（与实测同一提取器）。"""
    p = SNP_ROOT / rel_path.replace("\\", "/")
    if not p.exists():
        return None
    try:
        r = extract_resonator_params(p, f_start_ghz=4.25, f_end_ghz=7.0, skip_validation=True)
        d = r if isinstance(r, dict) else r.__dict__
        if not d.get("fs_ghz") or not d.get("fp_ghz"):
            return None
        return {
            "fs_ghz": float(d["fs_ghz"]),
            "fp_ghz": float(d["fp_ghz"]),
            "qs": float(d.get("qs") or np.nan),
            "qp": float(d.get("qp") or np.nan),
            "k2eff_pct": float(d.get("k2eff_pct") or np.nan),
        }
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nprocs", type=int, default=8)
    ap.add_argument("--out", default="sim_offset_report")
    args = ap.parse_args()

    ti = pd.read_excel(XLSX, sheet_name="Training_Index")
    ok = ti[ti["SNP_Status"] == "MATCHED"].copy()
    num_cols = ["Train_L_EG_um", "Train_L_FL_um", "Train_L_AG_um", "Train_A_res_um2", "Train_PF_nm"]
    for c in num_cols:
        ok[c] = pd.to_numeric(ok[c], errors="coerce")
    ok["has_PF"] = (ok["Train_PF_nm"].fillna(0) > 0).astype(int)
    ok = ok[ok["Train_L_AG_um"] == 1.0]  # 对齐实测 AG 占位
    ok["cell"] = list(
        zip(
            ok["Train_L_EG_um"].round(4),
            ok["Train_L_FL_um"].round(4),
            ok["Train_A_res_um2"].astype(int),
            ok["has_PF"],
            strict=False,
        )
    )

    with np.load(NPZ) as z:
        x, y = z["x_struct"], z["y_params"]
    meas_cells: dict[tuple, list[int]] = {}
    for i in range(len(x)):
        key = (round(float(x[i, 0]), 4), round(float(x[i, 1]), 4), int(x[i, 3]), int(x[i, 4]))
        meas_cells.setdefault(key, []).append(i)
    meas_med = {
        k: np.median(y[idxs, :5], axis=0)
        for k, idxs in meas_cells.items()  # fs,fp,kt2,qs,qp
    }

    inter = ok[ok["cell"].isin(meas_cells)]
    print(f"交集仿真行 {len(inter)}（格 {inter['cell'].nunique()}），开始提取标签…")

    paths = inter["SNP_Relative_Path"].tolist()
    with ProcessPoolExecutor(max_workers=args.nprocs) as exe:
        results = list(exe.map(_extract_one, paths))
    inter = inter.assign(
        sim_fs=[r["fs_ghz"] if r else np.nan for r in results],
        sim_fp=[r["fp_ghz"] if r else np.nan for r in results],
        sim_qs=[r["qs"] if r else np.nan for r in results],
        sim_qp=[r["qp"] if r else np.nan for r in results],
    ).dropna(subset=["sim_fs", "sim_fp"])
    print(f"提取成功 {len(inter)} 行（失败 {len(paths) - len(inter)}）")

    # 每格聚合：行数、行间极差、格中位数
    rows = []
    for cell, g in inter.groupby("cell"):
        spread_fs = (g["sim_fs"].max() - g["sim_fs"].min()) * 1000  # MHz
        rows.append(
            {
                "eg": cell[0],
                "fl": cell[1],
                "area": cell[2],
                "pf": cell[3],
                "n_rows": len(g),
                "row_spread_fs_mhz": spread_fs,
                "sim_fs": g["sim_fs"].median(),
                "sim_fp": g["sim_fp"].median(),
                "sim_qs": g["sim_qs"].median(),
                "sim_qp": g["sim_qp"].median(),
            }
        )
    cells = pd.DataFrame(rows)
    cells["ambiguous"] = cells["row_spread_fs_mhz"] > _AMBIG_MHZ

    # 拼实测中位数并计算 Δ
    meas_df = pd.DataFrame(
        [
            {
                "eg": k[0],
                "fl": k[1],
                "area": k[2],
                "pf": k[3],
                "meas_fs": v[0],
                "meas_fp": v[1],
                "meas_qs": v[3],
                "meas_qp": v[4],
            }
            for k, v in meas_med.items()
        ]
    )
    df = cells.merge(meas_df, on=["eg", "fl", "area", "pf"], how="inner")
    for p in ("fs", "fp"):
        df[f"d_{p}_mhz"] = (df[f"sim_{p}"] - df[f"meas_{p}"]) * 1000
    df["d_qs_pct"] = (df["sim_qs"] / df["meas_qs"] - 1) * 100
    df["d_qp_pct"] = (df["sim_qp"] / df["meas_qp"] - 1) * 100

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "sim_vs_measured_cells.csv", index=False)

    clean = df[~df["ambiguous"]]
    print(f"\n=== 格级统计（全部 {len(df)} 格，其中歧义格 {df['ambiguous'].sum()} 剔除）===")
    for col, unit in [
        ("d_fs_mhz", "MHz"),
        ("d_fp_mhz", "MHz"),
        ("d_qs_pct", "%"),
        ("d_qp_pct", "%"),
    ]:
        v = clean[col].dropna()
        print(
            f"{col:10s} n={len(v):4d} 中位数 {v.median():+8.2f}{unit}  "
            f"IQR [{v.quantile(0.25):+.2f}, {v.quantile(0.75):+.2f}]  "
            f"p5/p95 [{v.quantile(0.05):+.2f}, {v.quantile(0.95):+.2f}]"
        )

    # 偏移与结构参数的相关性（系统性 vs 随机）
    for p in ("fs", "fp"):
        for v in ("eg", "fl", "area"):
            c = clean[[f"d_{p}_mhz", v]].corr().iloc[0, 1]
            print(f"corr(Δ{p}, {v}) = {c:+.2f}")

    # 汇总图
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    axes[0].hist(clean["d_fs_mhz"], bins=40)
    axes[0].set_title("Δfs (sim-meas) MHz")
    axes[1].hist(clean["d_fp_mhz"], bins=40)
    axes[1].set_title("Δfp (sim-meas) MHz")
    sc = axes[2].scatter(clean["eg"], clean["fl"], c=clean["d_fs_mhz"], cmap="coolwarm")
    axes[2].set_xlabel("EG")
    axes[2].set_ylabel("FL")
    axes[2].set_title("Δfs 随结构")
    fig.colorbar(sc, ax=axes[2], label="MHz")
    fig.tight_layout()
    fig.savefig(out / "offset_summary.png", dpi=140)
    print(f"\n输出: {out / 'sim_vs_measured_cells.csv'} 与 {out / 'offset_summary.png'}")


if __name__ == "__main__":
    main()
