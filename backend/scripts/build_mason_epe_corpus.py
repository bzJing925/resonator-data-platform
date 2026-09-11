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

压电材料默认 **ALSCN**（--material 可选 ALN1 用于对照）：两个下游用途都要求
AlScN 口径——(1) WP3 要把 Mason EPE 校准到生产代理基准，实测器件是 AlScN(30AT)
晶圆（基准 k²eff≈9.83%，ALN1 材料 kt2=7.19% 物理上够不到）；(2) WP4 的
EPE 预训练→EPPE(AlN+AlScN) 微调迁移要求 EPE 侧与 EPPE 的 AlScN 层材料一致。
注意 t_piezo 标定范围随材料变化（T_PIEZO_RANGE_NM_BY_MATERIAL）。

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
每个 shard 内含 config_tag 配置指纹（seed/n/material/shard_size/厚度范围/求解窗等）：
续跑跳过时校验，不匹配即报错退出（防止不同配置的 shard 静默混料；
此时请更换 --shard-dir 或清空目录重跑）。shard 与最终产物均为 tmp+replace 原子写。
以上机械设施由 app.mason_sim.corpus 承载（WP2b 抽取，EPPE 语料脚本同用）。

用法：
    uv run python scripts/build_mason_epe_corpus.py --calibrate     # 复现 t_piezo 标定
    uv run python scripts/build_mason_epe_corpus.py --smoke 200     # 冒烟：吞吐 + ETA
    uv run python scripts/build_mason_epe_corpus.py                 # 全量 8000
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import warnings
from functools import partial
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.mason_sim import (  # noqa: E402
    ALN1,
    ALSCN,
    MO,
    SIN,
    EPEStackParams,
    PiezoMaterial,
    build_frequency_grid,
    extract_epe_production_labels,
    nm_to_m,
    run_epe_stack_sweep,
    um2_to_m2,
)
from app.mason_sim import corpus as corpus_infra  # noqa: E402

# ============================================================
# 采样常量
# ============================================================
SEED_DEFAULT = 20260910
N_DEFAULT = 8000

# 压电材料注册表：默认 ALSCN（生产 AlScN(30AT) 晶圆口径，见模块 docstring）；
# ALN1 保留用于对照实验。
PIEZO_MATERIALS: dict[str, PiezoMaterial] = {"ALSCN": ALSCN, "ALN1": ALN1}
PIEZO_DEFAULT = "ALSCN"

T_TOP_RANGE_NM = (50.0, 300.0)
T_BOT_RANGE_NM = (50.0, 300.0)
T_PF_RANGE_NM = (0.0, 100.0)
# t_piezo 范围（随材料变化）：标定使 nominal 堆叠（t_top=t_bot=150 nm, t_pf=0,
# area=2000 µm²）的 fs1 恰好覆盖 [3.95, 7.05] GHz（对 4–7 GHz 目标带各留 50 MHz
# 边距）。均标定于 2026-09-10，二分容差 0.25 nm（`--calibrate --material X` 复现
# 并打印角点核查表）。
FS_CAL_LO_GHZ = 3.95
FS_CAL_HI_GHZ = 7.05
T_PIEZO_RANGE_NM_BY_MATERIAL: dict[str, tuple[float, float]] = {
    "ALSCN": (134.70, 552.03),
    "ALN1": (171.85, 683.69),
}
T_PIEZO_RANGE_NM = T_PIEZO_RANGE_NM_BY_MATERIAL[PIEZO_DEFAULT]

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

# shard 字段规格（result_key → npz 键 / dtype / 宽度），corpus_infra 通用设施消费
SHARD_FIELDS = (
    corpus_infra.ShardField("x", "x_geom", np.float64, 5),
    corpus_infra.ShardField("y", "y_params", np.float64, 7),
    corpus_infra.ShardField("z", "z_dense", np.complex64, 1001),
)

DROP_REASONS = ("no_resonance", "label_nan", "edge_margin", "solver_error")


# ============================================================
# 采样
# ============================================================
def sample_geometry(n: int, seed: int = SEED_DEFAULT, material: str = PIEZO_DEFAULT) -> np.ndarray:
    """4 维 LHS（t_top/t_bot/t_pf/t_piezo）+ 面积 5 档轮转 → (n,5) float64。

    输出列序 = GEOM_COLS：[t_piezo_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2]。
    t_piezo 范围取该材料的标定值（T_PIEZO_RANGE_NM_BY_MATERIAL）。
    """
    from scipy.stats import qmc

    t_piezo_range = T_PIEZO_RANGE_NM_BY_MATERIAL[material]
    u = qmc.LatinHypercube(d=4, seed=seed).random(n=n)
    t_top = T_TOP_RANGE_NM[0] + u[:, 0] * (T_TOP_RANGE_NM[1] - T_TOP_RANGE_NM[0])
    t_bot = T_BOT_RANGE_NM[0] + u[:, 1] * (T_BOT_RANGE_NM[1] - T_BOT_RANGE_NM[0])
    t_pf = T_PF_RANGE_NM[0] + u[:, 2] * (T_PF_RANGE_NM[1] - T_PF_RANGE_NM[0])
    t_piezo = t_piezo_range[0] + u[:, 3] * (t_piezo_range[1] - t_piezo_range[0])
    area = np.array([AREA_LEVELS_UM2[i % len(AREA_LEVELS_UM2)] for i in range(n)])
    return np.column_stack([t_piezo, t_top, t_bot, t_pf, area]).astype(np.float64)


# ============================================================
# 单样本求解 + 生产口径标签
# ============================================================
def solve_extract_one(
    row: tuple[float, float, float, float, float],
    piezo: PiezoMaterial = ALSCN,
) -> dict:
    """求解一个几何样本并提取生产口径标签。

    Args:
        row: (t_piezo_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2)，列序 = GEOM_COLS。
        piezo: 压电材料（默认 ALSCN，生产 AlScN 口径；对照实验可传 ALN1）。

    Returns:
        dict(valid, reason, x, y, z)：
        - valid=True：y 为 7 参数 list（PARAM_COLS 序），z 为 (1001,) complex64；
        - valid=False：y/z 为 None，
          reason ∈ {no_resonance, label_nan, edge_margin, solver_error}；
          solver_error 时附 error 字段（异常类型与消息）——求解器异常兜底丢弃，
          不让单个坏样本（如 MNA 奇异矩阵）炸掉整轮生成。
    """
    t_piezo_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2 = row
    x = [float(v) for v in row]
    try:
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
            z11 = run_epe_stack_sweep(SOLVE_FREQ_HZ, piezo, MO, SIN, params).z11
            # 显式传 zref=ZREF：不依赖 prodlabels 默认值与本脚本常量恰好一致
            labels = extract_epe_production_labels(SOLVE_FREQ_HZ, z11, zref=ZREF)
    except Exception as exc:  # noqa: BLE001 —— worker 兜底，异常样本计为丢弃
        return {
            "valid": False,
            "reason": "solver_error",
            "x": x,
            "y": None,
            "z": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

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


# ============================================================
# t_piezo 标定（--calibrate 复现 T_PIEZO_RANGE_NM_BY_MATERIAL）
# ============================================================
def _fs_at_nominal(t_piezo_nm: float, piezo: PiezoMaterial) -> float:
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

    z11 = run_epe_stack_sweep(SOLVE_FREQ_HZ, piezo, MO, SIN, params).z11
    fs, _fp = find_te1_resonances(SOLVE_FREQ_HZ, np.abs(z11))
    return fs / 1e9


def _bisect_t_for_fs(
    target_ghz: float,
    lo_nm: float,
    hi_nm: float,
    piezo: PiezoMaterial,
    tol_nm: float = 0.25,
) -> float:
    """fs 随 t_piezo 单调下降，二分求厚度。"""
    for _ in range(40):
        mid = 0.5 * (lo_nm + hi_nm)
        if _fs_at_nominal(mid, piezo) > target_ghz:
            lo_nm = mid
        else:
            hi_nm = mid
        if hi_nm - lo_nm < tol_nm:
            break
    return 0.5 * (lo_nm + hi_nm)


def run_calibration(material: str = PIEZO_DEFAULT) -> None:
    """复现指定材料的 t_piezo 范围标定并打印角点核查表。"""
    piezo = PIEZO_MATERIALS[material]
    print(
        f"标定目标：{material} nominal 堆叠（150/150/0）"
        f"fs1 ∈ [{FS_CAL_LO_GHZ}, {FS_CAL_HI_GHZ}] GHz"
    )
    t_hi = _bisect_t_for_fs(FS_CAL_LO_GHZ, 300.0, 1200.0, piezo)
    t_lo = _bisect_t_for_fs(FS_CAL_HI_GHZ, 50.0, 700.0, piezo)
    print(f"  t_hi (fs={FS_CAL_LO_GHZ} GHz) = {t_hi:.2f} nm")
    print(f"  t_lo (fs={FS_CAL_HI_GHZ} GHz) = {t_lo:.2f} nm")
    current = T_PIEZO_RANGE_NM_BY_MATERIAL[material]
    print(f"  → T_PIEZO_RANGE_NM_BY_MATERIAL[{material!r}] = ({t_lo:.2f}, {t_hi:.2f})")
    print(f"    （当前脚本烘焙值 {current}）")
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

            z11 = run_epe_stack_sweep(SOLVE_FREQ_HZ, piezo, MO, SIN, params).z11
            fs, fp = find_te1_resonances(SOLVE_FREQ_HZ, np.abs(z11))
            pair = f"fs={fs / 1e9:.3f} fp={fp / 1e9:.3f}" if fp > fs else "窗内无有效对（丢弃）"
            print(f"  t_piezo={t_p:7.2f} top={tt:3d} bot={tb:3d} pf={pf:3d} → {pair}")


# ============================================================
# 语料配置指纹 / meta 路径（机械设施在 app.mason_sim.corpus；此处为兼容薄封装）
# ============================================================
def _config_tag(n: int, seed: int, material: str, shard_size: int) -> str:
    """语料配置指纹 payload（canonical JSON）：凡影响 shard 内容的量都计入。"""
    payload = {
        "seed": seed,
        "n": n,
        "material": material,
        "shard_size": shard_size,
        "ranges_nm": {
            "t_piezo": list(T_PIEZO_RANGE_NM_BY_MATERIAL[material]),
            "t_top": list(T_TOP_RANGE_NM),
            "t_bot": list(T_BOT_RANGE_NM),
            "t_pf": list(T_PF_RANGE_NM),
        },
        "area_levels_um2": list(AREA_LEVELS_UM2),
        "solve_grid": {"f_start_ghz": 3.0, "f_stop_ghz": 8.0, "f_step_mhz": 1.0},
        "edge_margin_mhz": EDGE_MARGIN_HZ / 1e6,
        "alpha": ALPHA,
        "zref_ohm": ZREF,
        # 代码常量材料指纹（当前压电材料/MO/SIN）：防 mason_sim.materials 常量
        # 改动后旧 shard 被静默复用（WP2b 复审加固）
        "code_materials": corpus_infra.materials_fingerprint([PIEZO_MATERIALS[material], MO, SIN]),
    }
    return corpus_infra.make_config_tag(payload)


def _meta_path(out_path: Path) -> Path:
    """兼容封装：corpus npz 路径 → 配套 meta.json 路径。"""
    return corpus_infra.meta_path_for(out_path)


# ============================================================
# 语料流水线
# ============================================================
def run_pipeline(
    n: int,
    out_path: Path,
    shard_dir: Path,
    shard_size: int,
    nprocs: int,
    seed: int,
    material: str = PIEZO_DEFAULT,
) -> dict:
    """执行语料生成：逐 shard 求解（已存在跳过）→ 合并 → 写 npz + meta.json。

    Returns:
        meta dict（同时写入 <out>.meta.json）。
    """
    t0 = time.perf_counter()
    piezo = PIEZO_MATERIALS[material]
    geom = sample_geometry(n, seed=seed, material=material)
    rows = [tuple(float(v) for v in geom[i]) for i in range(n)]
    config_tag = _config_tag(n, seed, material, shard_size)

    n_shards = corpus_infra.solve_shards(
        rows=rows,
        solve_one=partial(solve_extract_one, piezo=piezo),
        shard_dir=shard_dir,
        shard_size=shard_size,
        nprocs=nprocs,
        config_tag=config_tag,
        fields=SHARD_FIELDS,
    )
    merged = corpus_infra.merge_shards(
        shard_dir=shard_dir, n_shards=n_shards, config_tag=config_tag, fields=SHARD_FIELDS
    )
    x = merged["arrays"]["x_geom"]
    y = merged["arrays"]["y_params"]
    z = merged["arrays"]["z_dense"]
    n_attempted = merged["n_attempted"]
    drop = {reason: 0 for reason in DROP_REASONS}
    drop.update(merged["drop_counts"])

    freq_ghz = (SOLVE_FREQ_HZ[Z_DENSE_IDX] / 1e9).astype(np.float64)
    corpus_infra.atomic_savez(
        out_path,
        {"x_geom": x, "y_params": y, "z_dense": z, "freq_ghz": freq_ghz},
        compressed=True,
    )

    wall = time.perf_counter() - t0
    meta = {
        "seed": seed,
        "n_requested": n,
        "n_attempted": n_attempted,
        "n_produced": int(x.shape[0]),
        "n_dropped": int(n_attempted - x.shape[0]),
        "drop_reasons": drop,
        "material": material,
        "ranges_nm": {
            "t_piezo": list(T_PIEZO_RANGE_NM_BY_MATERIAL[material]),
            "t_top": list(T_TOP_RANGE_NM),
            "t_bot": list(T_BOT_RANGE_NM),
            "t_pf": list(T_PF_RANGE_NM),
        },
        "t_piezo_calibration": (
            f"{material} nominal 堆叠(150/150/0) fs1 覆盖 [{FS_CAL_LO_GHZ}, {FS_CAL_HI_GHZ}] GHz；"
            "2026-09-10 二分标定（--calibrate --material 复现）"
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
        "materials": {"piezo": piezo.name, "electrode": MO.name, "pf": SIN.name},
        "geom_cols": GEOM_COLS,
        "param_cols": PARAM_COLS,
        "label_provenance": LABEL_PROVENANCE,
        "mason_sim_commit_sha": corpus_infra.git_sha(PROJECT_ROOT),
        "wall_time_sec": round(wall, 3),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "fs_ghz_min": float(y[:, 0].min()) if len(y) else None,
        "fs_ghz_max": float(y[:, 0].max()) if len(y) else None,
    }
    corpus_infra.atomic_write_json(_meta_path(out_path), meta)
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
    ap.add_argument(
        "--material",
        choices=sorted(PIEZO_MATERIALS),
        default=PIEZO_DEFAULT,
        help=f"压电材料（默认 {PIEZO_DEFAULT}，生产 AlScN 口径；ALN1 用于对照）",
    )
    args = ap.parse_args(argv)

    # 数值参数校验（非正数给人类可读报错，argparse error → exit code 2）
    positive_values: dict[str, int] = {
        "--n": args.n,
        "--nprocs": args.nprocs,
        "--shard-size": args.shard_size,
    }
    if args.smoke is not None:
        positive_values["--smoke"] = args.smoke
    corpus_infra.validate_positive_ints(ap, positive_values)

    if args.calibrate:
        run_calibration(material=args.material)
        return 0

    if args.smoke is not None:
        n = int(args.smoke)
        tmp = Path(tempfile.mkdtemp(prefix="epe_corpus_smoke_"))
        out_path = tmp / "smoke.npz"
        shard_dir = tmp / "shards"
        print(f"冒烟模式：n={n}，nprocs={args.nprocs}，material={args.material}，临时目录 {tmp}")
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
        material=args.material,
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
