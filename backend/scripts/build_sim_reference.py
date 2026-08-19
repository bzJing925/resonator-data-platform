"""构建仿真格参考（仿真参考清洗用）：每结构格的仿真 fs/fp/Zp 中位数。

与 build_sim_supplement.py 的区别：本脚本覆盖**全部与实测相交的格**（不只仿真独有格），
并额外提取 Zp 峰高（dB）。输出为**未校准**的仿真原始中位数；校准（Δ 模型）在数据集
提取时按纯 train 格拟合（防泄漏），见 extract.py 的 fit_sim_calibrators。

口径：SNP_Status=MATCHED 且 L_AG=1.0；每格多行取中位数；行间 fs 极差 >5 MHz 的
歧义格剔除。Zp 取提取器 zp_ohm（fp 处阻抗）转 dB。

用法：
    uv run python scripts/build_sim_reference.py --nprocs 8
输出：surrogate_data/sim_cell_reference.npz（cells(M,4)=[eg,fl,area,pf]、
      ref(M,3)=[fs_ghz,fp_ghz,zp_db]）
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sim_offset_analysis import _AMBIG_MHZ, _extract_one  # noqa: E402 复用提取与歧义阈值

REPO = Path("/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master")
XLSX = REPO / "D1_D2_DOE_SNP_training_map_Lair_Lbot_minus6um.xlsx"
OUT = PROJECT_ROOT / "surrogate_data" / "sim_cell_reference.npz"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nprocs", type=int, default=8)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

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

    paths = ok["SNP_Relative_Path"].tolist()
    print(f"提取 {len(paths)} 行仿真标签（fs/fp/Zp）…")
    with ProcessPoolExecutor(max_workers=args.nprocs) as exe:
        results = list(exe.map(_extract_one, paths))
    ok = ok.assign(
        sim_fs=[r["fs_ghz"] if r else np.nan for r in results],
        sim_fp=[r["fp_ghz"] if r else np.nan for r in results],
        sim_zp=[r["zp_db"] if r else np.nan for r in results],
    ).dropna(subset=["sim_fs", "sim_fp", "sim_zp"])
    print(f"提取成功 {len(ok)} 行")

    agg = (
        ok.groupby("cell")
        .agg(
            n_rows=("sim_fs", "size"),
            fs=("sim_fs", "median"),
            fp=("sim_fp", "median"),
            zp=("sim_zp", "median"),
            spread_fs=("sim_fs", lambda s: (s.max() - s.min()) * 1000),
        )
        .reset_index()
    )
    n_before = len(agg)
    agg = agg[agg["spread_fs"] <= _AMBIG_MHZ]
    print(f"聚合后 {len(agg)} 格（剔除歧义格 {n_before - len(agg)}）")

    cells = np.array([list(c) for c in agg["cell"]], dtype=np.float64)
    ref = agg[["fs", "fp", "zp"]].to_numpy(dtype=np.float64)
    out_path = Path(args.out)
    np.savez_compressed(out_path, cells=cells, ref=ref)
    print(f"输出: {out_path}（{len(cells)} 格）")


if __name__ == "__main__":
    main()
