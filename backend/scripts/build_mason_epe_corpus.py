"""构建 EPE（单层）Mason 语料：非对称电极 + 可选 PF 质量加载层。

IUS2026 论文修订训练数据基础设施（WP2a）。用 app.mason_sim 的 1D Mason MNA 求解器
批量生成 5–10k 样本，供后续训练统一正向/反向代理；WP3 将用本语料把 Mason 校准到
生产代理的 frame-free 基准，因此**标签口径与生产 extract.py 完全一致**
（prodlabels：k2eff512 + calc_q_phase + calc_bodeq，见 meta.json label_provenance）。

采样（4 维 LHS + 面积轮转）：
    t_top ∈ [50, 300] nm、t_bot ∈ [50, 300] nm、t_pf ∈ [0, 100] nm、
    t_piezo ∈ T_PIEZO_RANGE_NM（标定使 fs1 覆盖 [3.95, 7.05] GHz，见 --calibrate）、
    area 取 5 档 {700, 1200, 2000, 3500, 5500} µm² 轮转分配。
    注：Mason 1D 模型中面积只缩放阻抗电平（C0 ∝ area），不改变 fs/fp 位置；
    但经 50 Ω 变换后的 S11 与 Bode-Q 依赖阻抗电平，故面积必须作为输入特征。

求解：每样本 3–8 GHz @ 1 MHz（5001 点）run_epe_stack_sweep（alpha=0.02），
prodlabels 全窗提 7 参数标签；无效样本丢弃并计数（原因分类见 meta.json）。
除 prodlabels 的 valid 规则外，本脚本额外施加 edge-margin 守卫：fs < 3.15 GHz
或 fp > 7.85 GHz 的样本丢弃——find_te1_resonances 的 edge_skip（2% 点 = 100 MHz）
会使靠窗沿的峰定位产生最高 ~100 MHz 偏差，对 (fp−fs) ~ 150 MHz 的器件意味着
k²eff 偏差 30%+，必须剔除（2026-09-10 角点实验实测：200 样本中 1 例边沿偏移）。

存盘（--out，默认 backend/surrogate_data_mason/epe_corpus.npz）：
    x_geom   (M,5) float64 = [t_piezo_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2]
    y_params (M,7) float64 = [fs_ghz, fp_ghz, k2eff_pct, qs, qp, qs_bodeq, qp_bodeq]
                             （与生产 AlgorithmConfig.surr_param_cols 顺序一致）
    z_dense  (M,1001) complex64 = 4.000–7.000 GHz @ 3 MHz 精确子集
                                  （1 MHz 网格含 3 MHz 整点，索引 1000+3j）
    freq_ghz (1001,) float64
外加 <out>.meta.json：种子/范围/面积档/求解窗/产出与丢弃数/标签口径/commit SHA/耗时。

并行与断点续跑：ProcessPoolExecutor（--nprocs 默认 cpu_count()-1）；每 --shard-size
个样本落一个 shard npz 到 --shard-dir，已存在 shard 直接跳过，最后合并。

用法：
    uv run python scripts/build_mason_epe_corpus.py --calibrate     # 复现 t_piezo 标定
    uv run python scripts/build_mason_epe_corpus.py --smoke 200     # 冒烟：吞吐 + ETA
    uv run python scripts/build_mason_epe_corpus.py                 # 全量 8000
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.mason_sim import (  # noqa: E402
    ALN1,
    MO,
    SIN,
    EPEStackParams,
    build_frequency_grid,
    extract_epe_production_labels,
    nm_to_m,
    run_epe_stack_sweep,
    um2_to_m2,
)

# ============================================================
# 采样常量
# ============================================================
SEED_DEFAULT = 20260910
N_DEFAULT = 8000

T_TOP_RANGE_NM = (50.0, 300.0)
T_BOT_RANGE_NM = (50.0, 300.0)
T_PF_RANGE_NM = (0.0, 100.0)
# t_piezo 范围：标定使 nominal 堆叠（t_top=t_bot=150 nm, t_pf=0, ALN1, area=2000 µm²）
# 的 fs1 恰好覆盖 [3.95, 7.05] GHz（对 4–7 GHz 目标带各留 50 MHz 边距）。
# 标定于 2026-09-10，二分容差 0.25 nm（`--calibrate` 可复现并打印角点核查表）。
FS_CAL_LO_GHZ = 3.95
FS_CAL_HI_GHZ = 7.05
T_PIEZO_RANGE_NM = (171.85, 683.69)

AREA_LEVELS_UM2 = (700.0, 1200.0, 2000.0, 3500.0, 5500.0)

# ============================================================
# 求解 / 标签常量
# ============================================================
ALPHA = 0.02
ZREF = 50.0
F_SOLVE_START_HZ = 3.0e9
F_SOLVE_STOP_HZ = 8.0e9
F_SOLVE_STEP_HZ = 1.0e6
SOLVE_FREQ_HZ = build_frequency_grid(F_SOLVE_START_HZ, F_SOLVE_STOP_HZ, f_step_hz=F_SOLVE_STEP_HZ)

# z_dense：4.000–7.000 GHz @ 3 MHz = 1 MHz 网格的索引 1000+3j（j=0..1000）子集
Z_DENSE_IDX = 1000 + 3 * np.arange(1001)
assert SOLVE_FREQ_HZ[Z_DENSE_IDX[0]] == 4.0e9
assert SOLVE_FREQ_HZ[Z_DENSE_IDX[-1]] == 7.0e9

# edge-margin 守卫：find_te1_resonances 的 edge_skip（2% 点 = 100 MHz）会使靠窗沿
# 峰定位偏差最高 ~100 MHz → k²eff 偏差 30%+；要求 fs/fp 离窗沿 ≥ 150 MHz
EDGE_MARGIN_HZ = 150.0e6

LABEL_PROVENANCE = "production extract.py k2eff512+q_phase+calc_bodeq"
GEOM_COLS = ["t_piezo_nm", "t_top_nm", "t_bot_nm", "t_pf_nm", "area_um2"]
PARAM_COLS = ["fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "qs_bodeq", "qp_bodeq"]

SHARD_SIZE_DEFAULT = 500
OUT_DEFAULT = PROJECT_ROOT / "surrogate_data_mason" / "epe_corpus.npz"


# ============================================================
# 采样
# ============================================================
def sample_geometry(n: int, seed: int = SEED_DEFAULT) -> np.ndarray:
    """4 维 LHS（t_top/t_bot/t_pf/t_piezo）+ 面积 5 档轮转 → (n,5) float64。

    输出列序 = GEOM_COLS：[t_piezo_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2]。
    """
    from scipy.stats import qmc

    u = qmc.LatinHypercube(d=4, seed=seed).random(n=n)
    t_top = T_TOP_RANGE_NM[0] + u[:, 0] * (T_TOP_RANGE_NM[1] - T_TOP_RANGE_NM[0])
    t_bot = T_BOT_RANGE_NM[0] + u[:, 1] * (T_BOT_RANGE_NM[1] - T_BOT_RANGE_NM[0])
    t_pf = T_PF_RANGE_NM[0] + u[:, 2] * (T_PF_RANGE_NM[1] - T_PF_RANGE_NM[0])
    t_piezo = T_PIEZO_RANGE_NM[0] + u[:, 3] * (T_PIEZO_RANGE_NM[1] - T_PIEZO_RANGE_NM[0])
    area = np.array([AREA_LEVELS_UM2[i % len(AREA_LEVELS_UM2)] for i in range(n)])
    return np.column_stack([t_piezo, t_top, t_bot, t_pf, area]).astype(np.float64)


# ============================================================
# 单样本求解 + 生产口径标签
# ============================================================
def solve_extract_one(row: tuple[float, float, float, float, float]) -> dict:
    """求解一个几何样本并提取生产口径标签。

    Args:
        row: (t_piezo_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2)，列序 = GEOM_COLS。

    Returns:
        dict(valid, reason, x, y, z)：
        - valid=True：y 为 7 参数 list（PARAM_COLS 序），z 为 (1001,) complex64；
        - valid=False：y/z 为 None，reason ∈ {no_resonance, label_nan, edge_margin}。
    """
    t_piezo_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2 = row
    params = EPEStackParams(
        t_piezo=nm_to_m(t_piezo_nm),
        t_top=nm_to_m(t_top_nm),
        t_bot=nm_to_m(t_bot_nm),
        t_pf=nm_to_m(t_pf_nm),
        area=um2_to_m2(area_um2),
        alpha=ALPHA,
        zref=ZREF,
    )
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        z11 = run_epe_stack_sweep(SOLVE_FREQ_HZ, ALN1, MO, SIN, params).z11
        labels = extract_epe_production_labels(SOLVE_FREQ_HZ, z11)

    x = [float(v) for v in row]
    if not labels["valid"]:
        fs_g, fp_g = labels["fs_ghz"], labels["fp_ghz"]
        if not (np.isfinite(fs_g) and np.isfinite(fp_g)) or fp_g <= fs_g:
            reason = "no_resonance"
        else:
            reason = "label_nan"
        return {"valid": False, "reason": reason, "x": x, "y": None, "z": None}

    fs_ghz = float(labels["fs_ghz"])
    fp_ghz = float(labels["fp_ghz"])
    if fs_ghz * 1e9 < F_SOLVE_START_HZ + EDGE_MARGIN_HZ or (
        fp_ghz * 1e9 > F_SOLVE_STOP_HZ - EDGE_MARGIN_HZ
    ):
        return {"valid": False, "reason": "edge_margin", "x": x, "y": None, "z": None}

    y = [float(labels[k]) for k in PARAM_COLS]
    z_dense = np.asarray(z11[Z_DENSE_IDX], dtype=np.complex64)
    return {"valid": True, "reason": "ok", "x": x, "y": y, "z": z_dense}


def _solve_one_star(row: tuple[float, ...]) -> dict:
    """ProcessPoolExecutor map 入口（顶层函数，可 pickle）。"""
    return solve_extract_one(row)


# ============================================================
# t_piezo 标定（--calibrate 复现 T_PIEZO_RANGE_NM）
# ============================================================
def _fs_at_nominal(t_piezo_nm: float) -> float:
    """nominal 堆叠（150/150/0, area=2000）下 fs1 (GHz)。"""
    params = EPEStackParams(
        t_piezo=nm_to_m(t_piezo_nm),
        t_top=nm_to_m(150.0),
        t_bot=nm_to_m(150.0),
        t_pf=0.0,
        area=um2_to_m2(2000.0),
        alpha=ALPHA,
    )
    from app.mason_sim.labels import find_te1_resonances

    z11 = run_epe_stack_sweep(SOLVE_FREQ_HZ, ALN1, MO, SIN, params).z11
    fs, _fp = find_te1_resonances(SOLVE_FREQ_HZ, np.abs(z11))
    return fs / 1e9


def _bisect_t_for_fs(target_ghz: float, lo_nm: float, hi_nm: float, tol_nm: float = 0.25) -> float:
    """fs 随 t_piezo 单调下降，二分求厚度。"""
    for _ in range(40):
        mid = 0.5 * (lo_nm + hi_nm)
        if _fs_at_nominal(mid) > target_ghz:
            lo_nm = mid
        else:
            hi_nm = mid
        if hi_nm - lo_nm < tol_nm:
            break
    return 0.5 * (lo_nm + hi_nm)


def run_calibration() -> None:
    """复现 T_PIEZO_RANGE_NM 标定并打印角点核查表。"""
    print(f"标定目标：nominal 堆叠（150/150/0）fs1 ∈ [{FS_CAL_LO_GHZ}, {FS_CAL_HI_GHZ}] GHz")
    t_hi = _bisect_t_for_fs(FS_CAL_LO_GHZ, 400.0, 1200.0)
    t_lo = _bisect_t_for_fs(FS_CAL_HI_GHZ, 100.0, 700.0)
    print(f"  t_hi (fs={FS_CAL_LO_GHZ} GHz) = {t_hi:.2f} nm")
    print(f"  t_lo (fs={FS_CAL_HI_GHZ} GHz) = {t_lo:.2f} nm")
    print(f"  → T_PIEZO_RANGE_NM = ({t_lo:.2f}, {t_hi:.2f})  # 当前脚本值 {T_PIEZO_RANGE_NM}")
    print("角点核查（电极/PF 极端组合下 fs 位置；越出 [3.15, 7.85] GHz 的样本会被丢弃）：")
    for t_p in (t_lo, t_hi):
        for tt, tb, pf in ((50, 50, 0), (300, 300, 100), (50, 300, 50), (300, 50, 50)):
            params = EPEStackParams(
                t_piezo=nm_to_m(t_p),
                t_top=nm_to_m(float(tt)),
                t_bot=nm_to_m(float(tb)),
                t_pf=nm_to_m(float(pf)),
                area=um2_to_m2(2000.0),
                alpha=ALPHA,
            )
            from app.mason_sim.labels import find_te1_resonances

            z11 = run_epe_stack_sweep(SOLVE_FREQ_HZ, ALN1, MO, SIN, params).z11
            fs, fp = find_te1_resonances(SOLVE_FREQ_HZ, np.abs(z11))
            pair = f"fs={fs / 1e9:.3f} fp={fp / 1e9:.3f}" if fp > fs else "窗内无有效对（丢弃）"
            print(f"  t_piezo={t_p:7.2f} top={tt:3d} bot={tb:3d} pf={pf:3d} → {pair}")


# ============================================================
# shard 落盘 / 合并
# ============================================================
def _write_shard(shard_path: Path, results: list[dict], n_attempted: int) -> None:
    """把一个 shard 的有效样本写入 npz（原子替换）。"""
    valid = [r for r in results if r["valid"]]
    drop_counts = {
        "no_resonance": sum(1 for r in results if r["reason"] == "no_resonance"),
        "label_nan": sum(1 for r in results if r["reason"] == "label_nan"),
        "edge_margin": sum(1 for r in results if r["reason"] == "edge_margin"),
    }
    if valid:
        x = np.array([r["x"] for r in valid], dtype=np.float64)
        y = np.array([r["y"] for r in valid], dtype=np.float64)
        z = np.stack([r["z"] for r in valid]).astype(np.complex64)
    else:
        x = np.empty((0, 5), dtype=np.float64)
        y = np.empty((0, 7), dtype=np.float64)
        z = np.empty((0, 1001), dtype=np.complex64)
    shard_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = shard_path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:  # 文件句柄写入，避免 savez 自动追加 .npz 后缀
        np.savez(
            fh,
            x_geom=x,
            y_params=y,
            z_dense=z,
            n_attempted=np.int64(n_attempted),
            n_no_resonance=np.int64(drop_counts["no_resonance"]),
            n_label_nan=np.int64(drop_counts["label_nan"]),
            n_edge_margin=np.int64(drop_counts["edge_margin"]),
        )
    os.replace(tmp, shard_path)


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _meta_path(out_path: Path) -> Path:
    return out_path.with_name(out_path.name[: -len(".npz")] + ".meta.json")


def run_pipeline(
    n: int,
    out_path: Path,
    shard_dir: Path,
    shard_size: int,
    nprocs: int,
    seed: int,
) -> dict:
    """执行语料生成：逐 shard 求解（已存在跳过）→ 合并 → 写 npz + meta.json。

    Returns:
        meta dict（同时写入 <out>.meta.json）。
    """
    t0 = time.perf_counter()
    geom = sample_geometry(n, seed=seed)
    n_shards = (n + shard_size - 1) // shard_size

    with ProcessPoolExecutor(max_workers=nprocs) as exe:
        for shard_idx in range(n_shards):
            shard_path = shard_dir / f"shard_{shard_idx:05d}.npz"
            if shard_path.exists():
                print(f"[shard {shard_idx + 1}/{n_shards}] 已存在，跳过（断点续跑）")
                continue
            lo = shard_idx * shard_size
            hi = min(n, lo + shard_size)
            rows = [tuple(float(v) for v in geom[i]) for i in range(lo, hi)]
            chunksize = max(1, len(rows) // (nprocs * 4))
            results = list(exe.map(_solve_one_star, rows, chunksize=chunksize))
            _write_shard(shard_path, results, n_attempted=len(rows))
            n_ok = sum(1 for r in results if r["valid"])
            print(f"[shard {shard_idx + 1}/{n_shards}] 求解 {len(rows)} 样本：有效 {n_ok}")

    # ---- 合并 shards ----
    xs, ys, zs = [], [], []
    n_attempted = 0
    drop = {"no_resonance": 0, "label_nan": 0, "edge_margin": 0}
    for shard_idx in range(n_shards):
        shard_path = shard_dir / f"shard_{shard_idx:05d}.npz"
        if not shard_path.exists():
            raise FileNotFoundError(f"缺少 shard：{shard_path}（求解不完整）")
        d = np.load(shard_path)
        xs.append(d["x_geom"])
        ys.append(d["y_params"])
        zs.append(d["z_dense"])
        n_attempted += int(d["n_attempted"])
        drop["no_resonance"] += int(d["n_no_resonance"])
        drop["label_nan"] += int(d["n_label_nan"])
        drop["edge_margin"] += int(d["n_edge_margin"])

    x = np.concatenate(xs) if any(len(a) for a in xs) else np.empty((0, 5))
    y = np.concatenate(ys) if any(len(a) for a in ys) else np.empty((0, 7))
    z = np.concatenate(zs) if any(len(a) for a in zs) else np.empty((0, 1001))
    freq_ghz = (SOLVE_FREQ_HZ[Z_DENSE_IDX] / 1e9).astype(np.float64)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, x_geom=x, y_params=y, z_dense=z, freq_ghz=freq_ghz)

    wall = time.perf_counter() - t0
    meta = {
        "seed": seed,
        "n_requested": n,
        "n_attempted": n_attempted,
        "n_produced": int(x.shape[0]),
        "n_dropped": int(n_attempted - x.shape[0]),
        "drop_reasons": drop,
        "ranges_nm": {
            "t_piezo": list(T_PIEZO_RANGE_NM),
            "t_top": list(T_TOP_RANGE_NM),
            "t_bot": list(T_BOT_RANGE_NM),
            "t_pf": list(T_PF_RANGE_NM),
        },
        "t_piezo_calibration": (
            f"nominal 堆叠(150/150/0) fs1 覆盖 [{FS_CAL_LO_GHZ}, {FS_CAL_HI_GHZ}] GHz；"
            "2026-09-10 二分标定（--calibrate 复现）"
        ),
        "area_levels_um2": list(AREA_LEVELS_UM2),
        "area_note": (
            "Mason 1D 中面积只缩放阻抗电平（C0∝area）；"
            "经 50Ω 变换的 S11/Bode-Q 依赖之，故作输入特征"
        ),
        "solve_grid": {"f_start_ghz": 3.0, "f_stop_ghz": 8.0, "f_step_mhz": 1.0},
        "z_dense_grid": {"f_start_ghz": 4.0, "f_stop_ghz": 7.0, "f_step_mhz": 3.0},
        "edge_margin_mhz": EDGE_MARGIN_HZ / 1e6,
        "alpha": ALPHA,
        "zref_ohm": ZREF,
        "materials": {"piezo": ALN1.name, "electrode": MO.name, "pf": SIN.name},
        "geom_cols": GEOM_COLS,
        "param_cols": PARAM_COLS,
        "label_provenance": LABEL_PROVENANCE,
        "mason_sim_commit_sha": _git_sha(),
        "wall_time_sec": round(wall, 3),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "fs_ghz_min": float(y[:, 0].min()) if len(y) else None,
        "fs_ghz_max": float(y[:, 0].max()) if len(y) else None,
    }
    _meta_path(out_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


# ============================================================
# CLI
# ============================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=N_DEFAULT, help=f"采样数（默认 {N_DEFAULT}）")
    ap.add_argument("--out", type=str, default=str(OUT_DEFAULT), help="输出 npz 路径")
    ap.add_argument(
        "--seed", type=int, default=SEED_DEFAULT, help=f"LHS 种子（默认 {SEED_DEFAULT}）"
    )
    ap.add_argument(
        "--nprocs",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="并行进程数（默认 cpu_count()-1）",
    )
    ap.add_argument("--shard-size", type=int, default=SHARD_SIZE_DEFAULT, help="每 shard 样本数")
    ap.add_argument(
        "--shard-dir", type=str, default=None, help="shard 临时目录（默认 <out同名>_shards/）"
    )
    ap.add_argument(
        "--smoke",
        type=int,
        default=None,
        metavar="N",
        help="冒烟模式：只跑 N 样本并打印吞吐/ETA",
    )
    ap.add_argument("--calibrate", action="store_true", help="复现 t_piezo 范围标定并退出")
    args = ap.parse_args(argv)

    if args.calibrate:
        run_calibration()
        return 0

    if args.smoke is not None:
        n = int(args.smoke)
        tmp = Path(tempfile.mkdtemp(prefix="epe_corpus_smoke_"))
        out_path = tmp / "smoke.npz"
        shard_dir = tmp / "shards"
        print(f"冒烟模式：n={n}，nprocs={args.nprocs}，临时目录 {tmp}")
    else:
        n = int(args.n)
        out_path = Path(args.out)
        shard_dir = (
            Path(args.shard_dir) if args.shard_dir else out_path.parent / f"{out_path.stem}_shards"
        )

    t0 = time.perf_counter()
    meta = run_pipeline(
        n=n,
        out_path=out_path,
        shard_dir=shard_dir,
        shard_size=int(args.shard_size),
        nprocs=int(args.nprocs),
        seed=int(args.seed),
    )
    wall = time.perf_counter() - t0

    print(
        f"完成：attempted={meta['n_attempted']} produced={meta['n_produced']} "
        f"dropped={meta['n_dropped']} {meta['drop_reasons']}"
    )
    print(f"输出：{out_path}（+ {_meta_path(out_path).name}）")

    if args.smoke is not None:
        attempted = max(meta["n_attempted"], 1)
        sps_total = attempted / wall if wall > 0 else float("inf")
        sps_per_core = sps_total / max(args.nprocs, 1)
        eta_sec = (N_DEFAULT / sps_total) if sps_total > 0 else float("inf")
        print(
            f"吞吐：{sps_total:.2f} 样本/秒（{sps_per_core:.3f} 样本/秒/核，nprocs={args.nprocs}）"
        )
        print(f"全量 {N_DEFAULT} 样本 ETA（同 nprocs）：{eta_sec / 60:.1f} 分钟")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
