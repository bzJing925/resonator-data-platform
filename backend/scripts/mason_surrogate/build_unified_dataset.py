"""WP4a：EPE + EPPE 统一正向代理数据集构建。

把两个 Mason 语料统一到相对频率网格 w = f / f_a 上：
- EPE（单层）：f_a = fs（TE1）；EPPE（极性反转双层）：f_a = fs2（TE2）。
- 统一输入 x7 = [structure_flag, t1_nm, t2_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2]
  EPE: flag=0, t1=t_piezo, t2=0；EPPE: flag=1, t1=t_aln, t2=t_alscn。
- |Z| dB 线性插值到 w ∈ [0.80, 1.20]（N=1001）；绝对轴 f = w × f_a 落出
  该样本语料窗的网格点记 mask=0（值填 0）。
- 零覆盖样本（w 窗与语料窗无交）丢弃：无法插值、对两阶段训练均无贡献。
- 分层划分 85/7.5/7.5（按结构分层，种子 20260911）。

用法：
    uv run python scripts/mason_surrogate/build_unified_dataset.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.mason_sim.corpus import atomic_savez, atomic_write_json  # noqa: E402

W_LO, W_HI, W_N = 0.80, 1.20, 1001
SPLIT_SEED = 20260911
SPLIT_RATIOS = (0.85, 0.075, 0.075)  # train / val / test
EPE, EPPE = 0, 1

DATA_DIR = PROJECT_ROOT / "surrogate_data_mason"


def w_grid() -> NDArray[np.float64]:
    """统一相对频率网格 w ∈ [0.80, 1.20]，N=1001（端点精确）。"""
    return np.linspace(W_LO, W_HI, W_N, dtype=np.float64)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_x7(flag: int, x_geom: NDArray[np.float64]) -> NDArray[np.float64]:
    """映射到统一 7 维几何输入。EPE(M,5)→t2 补 0；EPPE(M,6)→t1/t2 双压电层。"""
    x_geom = np.asarray(x_geom, dtype=np.float64)
    expect = 5 if flag == EPE else 6
    if x_geom.ndim != 2 or x_geom.shape[1] != expect:
        raise ValueError(f"flag={flag} 期望 (M,{expect})，得到 {x_geom.shape}")
    m = len(x_geom)
    x7 = np.zeros((m, 7), dtype=np.float64)
    x7[:, 0] = float(flag)
    if flag == EPE:
        # [t_piezo, t_top, t_bot, t_pf, area] → [0, t_piezo, 0, t_top, t_bot, t_pf, area]
        x7[:, 1] = x_geom[:, 0]
        x7[:, 3:7] = x_geom[:, 1:5]
    else:
        # [t_aln, t_alscn, t_top, t_bot, t_pf, area] → [1, t_aln, t_alscn, ...]
        x7[:, 1:7] = x_geom[:, 0:6]
    return x7


def interp_to_w(
    freq_ghz: NDArray[np.float64],
    z_complex: NDArray,
    f_a: float,
    grid: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    """把单条复 Z 谱插值到相对网格 w。返回 (z_w_db, mask)。

    绝对查询频率 f_q = w × f_a；落出语料窗 [freq[0], freq[-1]] 的点 mask=0、值填 0。
    """
    z_mag = np.abs(z_complex)
    z_db = 20.0 * np.log10(np.maximum(z_mag, 1e-12))
    f_q = grid * f_a
    mask = (f_q >= freq_ghz[0]) & (f_q <= freq_ghz[-1])
    z_w = np.zeros(len(grid), dtype=np.float64)
    if mask.any():
        z_w[mask] = np.interp(f_q[mask], freq_ghz, z_db)
    return z_w, mask


def stratified_split(
    flags: NDArray[np.int64], seed: int = SPLIT_SEED
) -> NDArray[np.int8]:
    """按结构分层划分 0=train / 1=val / 2=test，比例 85/7.5/7.5，种子确定。"""
    rng = np.random.default_rng(seed)
    split = np.zeros(len(flags), dtype=np.int8)
    for flag in np.unique(flags):
        idx = np.where(flags == flag)[0]
        perm = rng.permutation(len(idx))
        n = len(idx)
        n_test = int(round(n * SPLIT_RATIOS[2]))
        n_val = int(round(n * SPLIT_RATIOS[1]))
        split[idx[perm[:n_test]]] = 2
        split[idx[perm[n_test : n_test + n_val]]] = 1
    return split


def _load_corpus(path: Path, flag: int, grid: NDArray[np.float64]) -> dict:
    """加载单个语料，构建 x7/谱插值/掩码，丢弃零覆盖样本。"""
    with np.load(path) as d:
        x_geom = d["x_geom"]
        y_params = d["y_params"]
        z_dense = d["z_dense"]
        freq = d["freq_ghz"]
    x7 = build_x7(flag, x_geom)
    f_a = y_params[:, 0].astype(np.float64)  # EPE: fs；EPPE: fs2（均为 param_cols 第 0 列）
    fp_ratio = (y_params[:, 1] / y_params[:, 0]).astype(np.float64)

    m = len(y_params)
    z_w_db = np.zeros((m, len(grid)), dtype=np.float32)
    w_mask = np.zeros((m, len(grid)), dtype=bool)
    for i in range(m):
        z_w, mask = interp_to_w(freq, z_dense[i], f_a[i], grid)
        z_w_db[i] = z_w.astype(np.float32)
        w_mask[i] = mask

    coverage = w_mask.mean(axis=1)
    keep = coverage > 0.0  # 零覆盖样本丢弃（无有效谱信息）
    return {
        "x7": x7[keep],
        "y7": y_params[keep].astype(np.float64),
        "z_w_db": z_w_db[keep],
        "w_mask": w_mask[keep],
        "f_a": f_a[keep],
        "fp_ratio": fp_ratio[keep],
        "src_index": np.where(keep)[0],
        "n_in": m,
        "n_dropped": int((~keep).sum()),
        "mask_rate": float(1.0 - w_mask[keep].mean()),
        "coverage_pct": np.percentile(coverage[keep], [0, 50, 100]).tolist(),
    }


def build_unified(epe_path: Path, eppe_path: Path) -> dict:
    """全量构建：EPE 在前、EPPE 在后，返回全部数组与统计（不落盘）。"""
    grid = w_grid()
    parts = {}
    for flag, path in [(EPE, Path(epe_path)), (EPPE, Path(eppe_path))]:
        parts[flag] = _load_corpus(path, flag, grid)

    x7 = np.concatenate([parts[EPE]["x7"], parts[EPPE]["x7"]], axis=0)
    out = {
        "x7": x7.astype(np.float32),
        "y7": np.concatenate([parts[EPE]["y7"], parts[EPPE]["y7"]], axis=0).astype(np.float32),
        "z_w_db": np.concatenate([parts[EPE]["z_w_db"], parts[EPPE]["z_w_db"]], axis=0),
        "w_mask": np.concatenate([parts[EPE]["w_mask"], parts[EPPE]["w_mask"]], axis=0),
        "w_grid": grid,
        "f_a": np.concatenate([parts[EPE]["f_a"], parts[EPPE]["f_a"]], axis=0),
        "fp_ratio": np.concatenate([parts[EPE]["fp_ratio"], parts[EPPE]["fp_ratio"]], axis=0),
        "src_structure": np.concatenate(
            [np.full(parts[EPE]["n_in"] - parts[EPE]["n_dropped"], EPE, dtype=np.int64),
             np.full(parts[EPPE]["n_in"] - parts[EPPE]["n_dropped"], EPPE, dtype=np.int64)]
        ),
        "src_index": np.concatenate([parts[EPE]["src_index"], parts[EPPE]["src_index"]], axis=0),
        "n_dropped": {EPE: parts[EPE]["n_dropped"], EPPE: parts[EPPE]["n_dropped"]},
        "mask_rate": {EPE: parts[EPE]["mask_rate"], EPPE: parts[EPPE]["mask_rate"]},
        "coverage_pct": {EPE: parts[EPE]["coverage_pct"], EPPE: parts[EPPE]["coverage_pct"]},
    }
    out["split"] = stratified_split(out["src_structure"], seed=SPLIT_SEED)
    return out


def _corpus_meta(path: Path) -> dict:
    meta_path = path.with_suffix(".meta.json")
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def build_and_save(epe_path: Path, eppe_path: Path, out_npz: Path, out_meta: Path) -> dict:
    """构建并落盘 npz（spec 键）+ meta.json（来源 SHA/网格/掩码率/样本数）。

    落盘走 app/mason_sim/corpus.py 的原子写（tmp + os.replace，防中断半截文件）。
    """
    out = build_unified(epe_path, eppe_path)
    atomic_savez(
        out_npz,
        {
            "x7": out["x7"],
            "y7": out["y7"],
            "z_w_db": out["z_w_db"],
            "w_mask": out["w_mask"],
            "w_grid": out["w_grid"],
            "f_a": out["f_a"],
            "fp_ratio": out["fp_ratio"],
            "split": out["split"],
            # 行回溯索引（复审次要项 6）：按行定位来源语料
            "src_structure": out["src_structure"].astype(np.int8),
            "src_index": out["src_index"].astype(np.int64),
        },
        compressed=False,  # 与既往 np.savez 行为一致（58MB 不压缩）
    )
    n = {
        EPE: int((out["src_structure"] == EPE).sum()),
        EPPE: int((out["src_structure"] == EPPE).sum()),
    }
    split_counts = {}
    for flag in (EPE, EPPE):
        sub = out["split"][out["src_structure"] == flag]
        split_counts["epe" if flag == EPE else "eppe"] = {
            "train": int((sub == 0).sum()),
            "val": int((sub == 1).sum()),
            "test": int((sub == 2).sum()),
        }
    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": {
            "epe": {
                "path": str(epe_path),
                "sha256": file_sha256(epe_path),
                "mason_sim_commit_sha": _corpus_meta(epe_path).get("mason_sim_commit_sha"),
                "n_in": n[EPE] + out["n_dropped"][EPE],
                "n_kept": n[EPE],
                "n_dropped_zero_coverage": out["n_dropped"][EPE],
                "mask_rate": out["mask_rate"][EPE],
                "coverage_percentiles": out["coverage_pct"][EPE],
            },
            "eppe": {
                "path": str(eppe_path),
                "sha256": file_sha256(eppe_path),
                "mason_sim_commit_sha": _corpus_meta(eppe_path).get("mason_sim_commit_sha"),
                "n_in": n[EPPE] + out["n_dropped"][EPPE],
                "n_kept": n[EPPE],
                "n_dropped_zero_coverage": out["n_dropped"][EPPE],
                "mask_rate": out["mask_rate"][EPPE],
                "coverage_percentiles": out["coverage_pct"][EPPE],
            },
        },
        "w_grid": {"w_lo": W_LO, "w_hi": W_HI, "n": W_N, "step": (W_HI - W_LO) / (W_N - 1)},
        "x7_cols": ["structure_flag", "t1_nm", "t2_nm", "t_top_nm", "t_bot_nm", "t_pf_nm",
                    "area_um2"],
        "y7_cols": ["fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "qs_bodeq", "qp_bodeq"],
        "anchor": {"epe": "fs (TE1)", "eppe": "fs2 (TE2)"},
        "split": {"seed": SPLIT_SEED, "ratios": SPLIT_RATIOS, "counts": split_counts},
        "n_total": int(n[EPE] + n[EPPE]),
    }
    atomic_write_json(out_meta, meta)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="构建 EPE+EPPE 统一相对频率网格数据集")
    ap.add_argument("--epe", type=Path, default=DATA_DIR / "epe_corpus.npz")
    ap.add_argument("--eppe", type=Path, default=DATA_DIR / "eppe_corpus.npz")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "unified_dataset.npz")
    ap.add_argument("--meta", type=Path, default=DATA_DIR / "unified_dataset.meta.json")
    args = ap.parse_args()
    meta = build_and_save(args.epe, args.eppe, args.out, args.meta)
    print(f"saved: {args.out}")
    for name in ("epe", "eppe"):
        s = meta["sources"][name]
        print(
            f"  {name}: n_in={s['n_in']} kept={s['n_kept']} "
            f"dropped={s['n_dropped_zero_coverage']} mask_rate={s['mask_rate']:.3f}"
        )
    print(f"  total={meta['n_total']} split={meta['split']['counts']}")


if __name__ == "__main__":
    main()
