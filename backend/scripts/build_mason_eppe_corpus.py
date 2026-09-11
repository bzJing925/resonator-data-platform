"""构建 EPPE（极性反转双层，AlN/AlScN）Mason 语料：TE2 > 10 GHz 主模态。

IUS2026 论文修订训练数据基础设施（WP2b）。物理目标：反极性双层把电学强耦合模态
从基模 TE1 移到偶模 TE2（~2× 基模），把强耦合器件工作频率推到 10 GHz 以上。
标签口径与生产 extract.py 完全一致（prodlabels.extract_eppe_production_labels：
TE2 对 (10,20] GHz 窗 + k2eff512 + calc_q_phase + calc_bodeq），与 WP2a EPE 语料同源。

材料冻结（WP3 结论）：
- 上层压电 = **校准 AlScN**，从 surrogate_data_mason/calibration/calibrated_materials.json
  机器读取（c33/e33/eps/rho/alpha；启动时打印来源与值；其 sha256 计入 config_tag，
  校准文件变动会使既有 shard 指纹不匹配而被拒绝复用）；
- 下层压电 = ALN1（文献值，未经实测校准——meta.json 注明）；
- 电极 Mo、PF 层 SiN（mason_sim.materials 既有常量，本脚本不修改）。

采样（t_aln 5 档轮换 + 4 维 LHS + 面积 5 档，固定种子 20260911）：
    t_aln ∈ {50, 80, 100, 120, 150} nm 均匀轮换（AlN 下层厚度，brief 定死的离散档）
    t_alscn ∈ (100, T_ALSCN_MAX_NM] nm（LHS；上限标定见 --calibrate）
    t_top / t_bot ∈ [50, 300] nm（LHS）；t_pf ∈ [0, 100] nm（LHS）
    area ∈ {700, 1200, 2000, 3500, 5500} µm² 轮转
    t_aln 与 area 采用正交轮转（i%5 与 (i//5)%5），前 25 样本恰好覆盖 25 种组合。

求解（每样本最多三次扫频，早退省算力）：
    1. 反极性（+1/−1）3–22 GHz @2 MHz（9501 点）→ TE2 生产口径标签
       （labels 无效则不跑后两次扫频）；
    2. 同极性（+1/+1）同网格对照 → f1_twin（该声学堆叠基模），支路核验：
       要求 BRANCH_RATIO_LO <= fs2/f1_twin <= BRANCH_RATIO_HI（f2 偶模支路
       比值窗口锚定，见下文"支路核验"），通过后才跑第 3 次；
    3. 密集输出扫频 8–22 GHz @5 MHz（2801 点，精确求解——非 2 MHz 网格子集：
       5 MHz 不是 2 MHz 的整数倍，"子集"在算术上不成立，故单独精确求解，
       无插值误差；见 meta.json dense_grid 注记）。

支路核验（关键设计）：探测发现窗 (10,20] GHz 内可能有多支模态对（如同几何
t_alscn 300→400nm 最强对从 ~13 跳到 ~18 GHz），find_te2_resonances 按 prominence
选最强对会跳支路。同极性对照扫频的基模 f1_twin 提供偶模锚点：要求窗内最强对的
fs2/f1_twin 落在 [1.9, 2.8]（f2 支路比值窗口）。物理依据（2026-09-11 全盒探测）：
该叠层族实测 f2/f1=2.05–2.6——Mo 电极质量对基模 f1 的频率拉低显著强于 f2
（厚电极偏离裸压电谐波比 2；WP1 黄金案例 AlN/AlN 对称反极性为 2.41）；f3 支路
≥3.2×f1 被拦在窗外。不满足者丢弃并计数 branch_ambiguous；f2 整体跌出 (10,20]
窗的样本由窗覆盖机制丢弃（no_resonance/te2_out_of_window）。f1_twin 存入 diag，
供论文直接使用"TE2≈2×TE1"这层物理。标签 nan、求解异常分别计数 label_nan /
solver_error。

存盘（--out，默认 backend/surrogate_data_mason/eppe_corpus.npz）：
    x_geom   (M,6) float64 = [t_aln_nm, t_alscn_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2]
    y_params (M,7) float64 = [fs_ghz, fp_ghz, k2eff_pct, qs, qp, qs_bodeq, qp_bodeq]
                             （TE2 锚点，与生产 AlgorithmConfig.surr_param_cols 顺序一致）
    z_dense  (M,2801) complex64 = 8–22 GHz @5 MHz 精确求解
    freq_ghz (2801,) float64
    diag     (M,4) float64 = [fs1_ghz, fp1_ghz, k2eff_te1_pct, f1_twin_ghz]
                             （TE1 诊断 + 支路锚；TE1 被抑制时前三列 nan）
外加 <out>.meta.json：材料来源（校准 SHA/sha256 + AlN 文献声明）/支路核验参数/
丢弃计数/标定注记/commit SHA/耗时。

并行与断点续跑设施由 app.mason_sim.corpus 承载（同 WP2a EPE 脚本）。

用法：
    uv run python scripts/build_mason_eppe_corpus.py --calibrate   # 复现 T_ALSCN_MAX_NM 标定
    uv run python scripts/build_mason_eppe_corpus.py --smoke 200   # 冒烟：吞吐/ETA/有效率/标签分布
    uv run python scripts/build_mason_eppe_corpus.py               # 全量 8000 attempted
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
    MO,
    SIN,
    EPPEParams,
    PiezoMaterial,
    build_frequency_grid,
    db20,
    nm_to_m,
    run_eppe_sweep,
    um2_to_m2,
)
from app.mason_sim import corpus as corpus_infra  # noqa: E402
from app.mason_sim.labels import find_te1_resonances  # noqa: E402
from app.mason_sim.prodlabels import (  # noqa: E402
    EPPE_DIAG_KEYS,
    PARAM_KEYS,
    extract_eppe_production_labels,
)

# ============================================================
# 采样常量
# ============================================================
SEED_DEFAULT = 20260911
N_DEFAULT = 8000

T_ALN_LEVELS_NM = (50.0, 80.0, 100.0, 120.0, 150.0)  # AlN 下层厚度 5 档轮换（brief 定死）
T_ALSCN_MIN_NM = 100.0
# T_ALSCN_MAX_NM 标定（2026-09-11 烘焙，`--calibrate` 复现）：nominal 堆叠
# （t_aln=100、t_top=t_bot=150、t_pf=0、area=2000）下偶模支路锚定 fs2 恰好压到
# FS2_CAL_TARGET_GHZ=10.30 GHz（10 GHz 窗下界 + 300 MHz 边距：find_te2_resonances
# 在 (10,20] 窗内的 edge_skip=2% 点=200 MHz，外加 100 MHz 检测余量）。
# 校准 AlScN（calibrated_materials.json）+ alpha=0.022 下二分容差 0.25 nm。
T_ALSCN_MAX_NM = 383.69
T_TOP_RANGE_NM = (50.0, 300.0)
T_BOT_RANGE_NM = (50.0, 300.0)
T_PF_RANGE_NM = (0.0, 100.0)
AREA_LEVELS_UM2 = (700.0, 1200.0, 2000.0, 3500.0, 5500.0)
FS2_CAL_TARGET_GHZ = 10.30

# ============================================================
# 材料冻结（WP3 校准产物）
# ============================================================
CALIBRATION_JSON = (
    PROJECT_ROOT / "surrogate_data_mason" / "calibration" / "calibrated_materials.json"
)

# ============================================================
# 求解 / 标签常量
# ============================================================
ZREF = 50.0
F_SOLVE_START_HZ = 3.0e9
F_SOLVE_STOP_HZ = 22.0e9
F_SOLVE_STEP_HZ = 2.0e6
SOLVE_FREQ_HZ = build_frequency_grid(F_SOLVE_START_HZ, F_SOLVE_STOP_HZ, f_step_hz=F_SOLVE_STEP_HZ)
assert len(SOLVE_FREQ_HZ) == 9501

# z_dense：8–22 GHz @5 MHz（2801 点）独立精确求解网格
F_DENSE_START_HZ = 8.0e9
F_DENSE_STOP_HZ = 22.0e9
F_DENSE_STEP_HZ = 5.0e6
DENSE_FREQ_HZ = build_frequency_grid(F_DENSE_START_HZ, F_DENSE_STOP_HZ, f_step_hz=F_DENSE_STEP_HZ)
assert len(DENSE_FREQ_HZ) == 2801

TE2_WINDOW_HZ = (10.0e9, 20.0e9)  # (10, 20] GHz
TE1_DIAG_WINDOW_HZ = (3.0e9, 8.0e9)  # [3, 8) GHz

# 支路核验：f2 偶模支路比值窗口 BRANCH_RATIO_LO <= fs2/f1_twin <= BRANCH_RATIO_HI。
# 物理依据（2026-09-11 全盒探测）：该叠层族实测 f2/f1=2.05–2.6（Mo 电极质量对 f1
# 的频率拉低强于 f2；WP1 黄金案例 AlN/AlN 对称反极性为 2.41）；f3 支路 ≥3.2×f1，
# 窗口上界 2.8 拦跳支、下界 1.9 拦 TE1 残余/伪对。
BRANCH_RATIO_LO = 1.9
BRANCH_RATIO_HI = 2.8

LABEL_PROVENANCE = "production extract.py k2eff512+q_phase+calc_bodeq anchored at TE2 (10,20] GHz"
GEOM_COLS = ["t_aln_nm", "t_alscn_nm", "t_top_nm", "t_bot_nm", "t_pf_nm", "area_um2"]
PARAM_COLS = list(PARAM_KEYS)
DIAG_COLS = [*EPPE_DIAG_KEYS, "f1_twin_ghz"]

SHARD_FIELDS = (
    corpus_infra.ShardField("x", "x_geom", np.float64, 6),
    corpus_infra.ShardField("y", "y_params", np.float64, 7),
    corpus_infra.ShardField("z", "z_dense", np.complex64, 2801),
    corpus_infra.ShardField("diag", "diag", np.float64, 4),
)

DROP_REASONS = (
    "no_resonance",
    "te2_out_of_window",
    "label_nan",
    "branch_ambiguous",
    "solver_error",
)

SHARD_SIZE_DEFAULT = 500
OUT_DEFAULT = PROJECT_ROOT / "surrogate_data_mason" / "eppe_corpus.npz"


# ============================================================
# 材料冻结加载
# ============================================================
def load_calibrated_piezo(
    path: Path = CALIBRATION_JSON,
) -> tuple[PiezoMaterial, float, dict]:
    """从校准 JSON 加载冻结 AlScN 材料与 alpha。

    Returns:
        (piezo, alpha, info)：info 含 path/sha256/commit_sha/calibrated_at，
        供 meta 溯源与 config_tag 指纹（校准文件变动 → sha256 变 → 旧 shard 拒用）。
    """
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    p = payload["piezo"]
    piezo = PiezoMaterial(
        name=str(p["name"]),
        rho=float(p["rho"]),
        c33=float(p["c33"]),
        e33=float(p["e33"]),
        eps=float(p["eps"]),
    )
    info = {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "commit_sha": payload.get("commit_sha"),
        "calibrated_at": payload.get("calibrated_at"),
        "values": {"rho": piezo.rho, "c33": piezo.c33, "e33": piezo.e33, "eps": piezo.eps},
    }
    return piezo, float(payload["alpha"]), info


# ============================================================
# 采样
# ============================================================
def sample_geometry(n: int, seed: int = SEED_DEFAULT) -> np.ndarray:
    """t_aln 5 档轮换 + 4 维 LHS（t_alscn/t_top/t_bot/t_pf）+ 面积 5 档 → (n,6) float64。

    输出列序 = GEOM_COLS：[t_aln_nm, t_alscn_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2]。
    t_aln 与 area 正交轮转（i%5 与 (i//5)%5）：前 25 个样本恰好覆盖 25 种
    (t_aln, area) 组合各一次，避免两档轮转同相位锁死。
    """
    from scipy.stats import qmc

    u = qmc.LatinHypercube(d=4, seed=seed).random(n=n)
    t_alscn = T_ALSCN_MIN_NM + u[:, 0] * (T_ALSCN_MAX_NM - T_ALSCN_MIN_NM)
    t_top = T_TOP_RANGE_NM[0] + u[:, 1] * (T_TOP_RANGE_NM[1] - T_TOP_RANGE_NM[0])
    t_bot = T_BOT_RANGE_NM[0] + u[:, 2] * (T_BOT_RANGE_NM[1] - T_BOT_RANGE_NM[0])
    t_pf = T_PF_RANGE_NM[0] + u[:, 3] * (T_PF_RANGE_NM[1] - T_PF_RANGE_NM[0])
    t_aln = np.array([T_ALN_LEVELS_NM[i % len(T_ALN_LEVELS_NM)] for i in range(n)])
    area = np.array(
        [AREA_LEVELS_UM2[(i // len(T_ALN_LEVELS_NM)) % len(AREA_LEVELS_UM2)] for i in range(n)]
    )
    return np.column_stack([t_aln, t_alscn, t_top, t_bot, t_pf, area]).astype(np.float64)


# ============================================================
# 支路核验
# ============================================================
def branch_anchor_ok(
    fs2_hz: float,
    f1_twin_hz: float,
    ratio_lo: float = BRANCH_RATIO_LO,
    ratio_hi: float = BRANCH_RATIO_HI,
) -> bool:
    """f2 偶模支路比值窗口锚定：ratio_lo <= fs2/f1_twin <= ratio_hi（含端点）。

    f1_twin 为同几何同极性对照扫频的基模（TE1）。该叠层族实测 f2/f1=2.05–2.6
    （Mo 电极质量对 f1 的拉低强于 f2），f3 支路 ≥3.2×f1；窗口 [1.9, 2.8]
    接受全部偶模 f2 支路并拦截跳支。nan 或非正 f1_twin 一律拒绝
    （无法锚定即不可信，丢弃为 branch_ambiguous）。
    """
    if not (np.isfinite(fs2_hz) and np.isfinite(f1_twin_hz)) or f1_twin_hz <= 0.0:
        return False
    ratio = fs2_hz / f1_twin_hz
    return ratio_lo <= ratio <= ratio_hi


# ============================================================
# 单样本求解 + TE2 生产口径标签 + 支路核验
# ============================================================
def _eppe_params(row: tuple[float, ...], alpha: float, reverse_top: float) -> EPPEParams:
    t_aln_nm, t_alscn_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2 = row
    return EPPEParams(
        t_piezo_bot=nm_to_m(t_aln_nm),
        t_piezo_top=nm_to_m(t_alscn_nm),
        t_mo_bot=nm_to_m(t_bot_nm),
        t_mo_top=nm_to_m(t_top_nm),
        area=um2_to_m2(area_um2),
        alpha=alpha,
        reverse_bot=+1.0,
        reverse_top=reverse_top,
        zref=ZREF,
        t_pf=nm_to_m(t_pf_nm),
    )


def solve_extract_one(
    row: tuple[float, float, float, float, float, float],
    piezo_bot: PiezoMaterial,
    piezo_top: PiezoMaterial,
    alpha: float,
) -> dict:
    """求解一个 EPPE 几何样本：TE2 标签 → 支路核验 → 密集谱，逐级早退。

    Args:
        row: (t_aln_nm, t_alscn_nm, t_top_nm, t_bot_nm, t_pf_nm, area_um2)，
            列序 = GEOM_COLS。
        piezo_bot: 下层压电（AlN，ALN1 文献值）。
        piezo_top: 上层压电（校准 AlScN，load_calibrated_piezo 加载）。
        alpha: 声学衰减参数（校准值 0.022）。

    Returns:
        dict(valid, reason, x, y, z, diag)：
        - valid=True：y 为 TE2 锚点 7 参数（PARAM_COLS 序），z 为 (2801,) complex64，
          diag 为 [fs1_ghz, fp1_ghz, k2eff_te1_pct, f1_twin_ghz]；
        - valid=False：y/z/diag 为 None，reason ∈ DROP_REASONS；
          solver_error 时附 error 字段。
    """
    x = [float(v) for v in row]
    try:
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore")
            # 1) 反极性主扫频 → TE2 生产口径标签
            z_anti = run_eppe_sweep(
                SOLVE_FREQ_HZ,
                piezo_bot,
                piezo_top,
                MO,
                _eppe_params(row, alpha, reverse_top=-1.0),
                pf_material=SIN,
            ).z11
            # 显式传 zref 与窗：不依赖 prodlabels 默认值与本脚本常量恰好一致
            labels = extract_eppe_production_labels(
                SOLVE_FREQ_HZ,
                z_anti,
                zref=ZREF,
                te2_f_min_hz=TE2_WINDOW_HZ[0],
                te2_f_max_hz=TE2_WINDOW_HZ[1],
                te1_diag_f_min_hz=TE1_DIAG_WINDOW_HZ[0],
                te1_diag_f_max_hz=TE1_DIAG_WINDOW_HZ[1],
            )
            if not labels["valid"]:
                # no_resonance / te2_out_of_window / label_nan 原样传播
                return {
                    "valid": False,
                    "reason": str(labels["reason"]),
                    "x": x,
                    "y": None,
                    "z": None,
                    "diag": None,
                }

            # 2) 同极性对照扫频 → f1_twin → 支路核验
            z_twin = run_eppe_sweep(
                SOLVE_FREQ_HZ,
                piezo_bot,
                piezo_top,
                MO,
                _eppe_params(row, alpha, reverse_top=+1.0),
                pf_material=SIN,
            ).z11
            f1_twin_hz, _fp1_twin = find_te1_resonances(SOLVE_FREQ_HZ, np.abs(z_twin))
            fs2_hz = float(labels["fs_ghz"]) * 1e9
            if not branch_anchor_ok(fs2_hz, f1_twin_hz):
                return {
                    "valid": False,
                    "reason": "branch_ambiguous",
                    "x": x,
                    "y": None,
                    "z": None,
                    "diag": None,
                }

            # 3) 密集输出扫频（8–22 GHz @5 MHz 独立网格，精确求解无插值）
            z_dense = run_eppe_sweep(
                DENSE_FREQ_HZ,
                piezo_bot,
                piezo_top,
                MO,
                _eppe_params(row, alpha, reverse_top=-1.0),
                pf_material=SIN,
            ).z11
    except Exception as exc:  # noqa: BLE001 —— worker 兜底，异常样本计为丢弃
        return {
            "valid": False,
            "reason": "solver_error",
            "x": x,
            "y": None,
            "z": None,
            "diag": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    y = [float(labels[k]) for k in PARAM_COLS]
    diag = [
        float(labels["fs1_ghz"]),
        float(labels["fp1_ghz"]),
        float(labels["k2eff_te1_pct"]),
        float(f1_twin_hz) / 1e9,
    ]
    return {
        "valid": True,
        "reason": "ok",
        "x": x,
        "y": y,
        "z": np.asarray(z_dense, dtype=np.complex64),
        "diag": diag,
    }


# ============================================================
# T_ALSCN_MAX_NM 标定（--calibrate 复现）
# ============================================================
def _anchored_te2_fs_ghz(
    z_anti: np.ndarray,
    f1_twin_hz: float,
) -> float:
    """偶模支路锚定 fs2：反极性谱中高于 1.5×f1_twin 的最低显著 |Z| 极小值 (GHz)。

    find_te2_resonances 按 prominence 选窗内最强对，在厚电极几何会跳高阶支路，
    标定必须锚定设计的偶模支路（f2，声学堆叠第二阶模态）。prominence 阈值与
    labels._prominence_threshold_db 同式（max(3 dB, 10% 动态范围)）。
    """
    from scipy.signal import find_peaks

    z_db = db20(np.abs(z_anti))
    prominence_db = max(3.0, (float(z_db.max()) - float(z_db.min())) * 0.1)
    mins, _ = find_peaks(-z_db, prominence=prominence_db)
    for idx in mins:
        if float(SOLVE_FREQ_HZ[idx]) > 1.5 * f1_twin_hz:
            return float(SOLVE_FREQ_HZ[idx]) / 1e9
    return float("nan")


def _anchored_fs2_at_nominal(
    t_alscn_nm: float,
    piezo_bot: PiezoMaterial,
    piezo_top: PiezoMaterial,
    alpha: float,
) -> float:
    """nominal 堆叠（t_aln=100、t_top=t_bot=150、t_pf=0、area=2000）的锚定 fs2 (GHz)。"""
    row = (100.0, t_alscn_nm, 150.0, 150.0, 0.0, 2000.0)
    z_anti = run_eppe_sweep(
        SOLVE_FREQ_HZ,
        piezo_bot,
        piezo_top,
        MO,
        _eppe_params(row, alpha, reverse_top=-1.0),
        pf_material=SIN,
    ).z11
    z_twin = run_eppe_sweep(
        SOLVE_FREQ_HZ,
        piezo_bot,
        piezo_top,
        MO,
        _eppe_params(row, alpha, reverse_top=+1.0),
        pf_material=SIN,
    ).z11
    f1_twin_hz, _ = find_te1_resonances(SOLVE_FREQ_HZ, np.abs(z_twin))
    return _anchored_te2_fs_ghz(z_anti, f1_twin_hz)


def run_calibration(piezo_bot: PiezoMaterial, piezo_top: PiezoMaterial, alpha: float) -> None:
    """复现 T_ALSCN_MAX_NM 标定并打印角点核查表。"""
    print(
        f"标定目标：nominal 堆叠（t_aln=100, Mo 150/150, pf=0）"
        f"偶模支路锚定 fs2 = {FS2_CAL_TARGET_GHZ} GHz"
        f"（10 GHz 窗下界 + 300 MHz 边距：edge_skip 200 MHz + 100 MHz 余量）"
    )
    lo_nm, hi_nm = 200.0, 600.0
    for _ in range(40):
        mid = 0.5 * (lo_nm + hi_nm)
        if _anchored_fs2_at_nominal(mid, piezo_bot, piezo_top, alpha) > FS2_CAL_TARGET_GHZ:
            lo_nm = mid
        else:
            hi_nm = mid
        if hi_nm - lo_nm < 0.25:
            break
    t_max = 0.5 * (lo_nm + hi_nm)
    print(f"  → T_ALSCN_MAX_NM = {t_max:.2f} nm（当前烘焙值 {T_ALSCN_MAX_NM}）")
    print("角点核查（锚定 fs2 / f1_twin / 支路核验比值；越窗或核验失败的样本会被丢弃）：")
    for t_aln in (T_ALN_LEVELS_NM[0], T_ALN_LEVELS_NM[-1]):
        for t_scn in (T_ALSCN_MIN_NM + 1.0, t_max):
            for mo in (50.0, 300.0):
                row = (t_aln, t_scn, mo, mo, 0.0, 2000.0)
                z_anti = run_eppe_sweep(
                    SOLVE_FREQ_HZ,
                    piezo_bot,
                    piezo_top,
                    MO,
                    _eppe_params(row, alpha, reverse_top=-1.0),
                    pf_material=SIN,
                ).z11
                z_twin = run_eppe_sweep(
                    SOLVE_FREQ_HZ,
                    piezo_bot,
                    piezo_top,
                    MO,
                    _eppe_params(row, alpha, reverse_top=+1.0),
                    pf_material=SIN,
                ).z11
                f1_hz, _ = find_te1_resonances(SOLVE_FREQ_HZ, np.abs(z_twin))
                fs2_g = _anchored_te2_fs_ghz(z_anti, f1_hz)
                ratio = (
                    fs2_g * 1e9 / f1_hz
                    if np.isfinite(fs2_g) and np.isfinite(f1_hz)
                    else float("nan")
                )
                print(
                    f"  t_aln={t_aln:5.0f} t_alscn={t_scn:7.2f} mo={mo:5.0f} → "
                    f"fs2_anch={fs2_g:7.3f} GHz f1_twin={f1_hz / 1e9:6.3f} GHz "
                    f"ratio={ratio:.3f}"
                )


# ============================================================
# 语料配置指纹
# ============================================================
def _config_tag(n: int, seed: int, shard_size: int, material_info: dict, alpha: float) -> str:
    """语料配置指纹 payload：凡影响 shard 内容的量都计入（含校准材料 sha256）。"""
    payload = {
        "seed": seed,
        "n": n,
        "shard_size": shard_size,
        "stack": "EPPE(AlN/AlScN_cal)",
        "t_aln_levels_nm": list(T_ALN_LEVELS_NM),
        "ranges_nm": {
            "t_alscn": [T_ALSCN_MIN_NM, T_ALSCN_MAX_NM],
            "t_top": list(T_TOP_RANGE_NM),
            "t_bot": list(T_BOT_RANGE_NM),
            "t_pf": list(T_PF_RANGE_NM),
        },
        "area_levels_um2": list(AREA_LEVELS_UM2),
        "solve_grid": {"f_start_ghz": 3.0, "f_stop_ghz": 22.0, "f_step_mhz": 2.0},
        "dense_grid": {"f_start_ghz": 8.0, "f_stop_ghz": 22.0, "f_step_mhz": 5.0},
        "te2_window_ghz": [TE2_WINDOW_HZ[0] / 1e9, TE2_WINDOW_HZ[1] / 1e9],
        "te1_diag_window_ghz": [TE1_DIAG_WINDOW_HZ[0] / 1e9, TE1_DIAG_WINDOW_HZ[1] / 1e9],
        "branch_guard": {"ratio_lo": BRANCH_RATIO_LO, "ratio_hi": BRANCH_RATIO_HI},
        "alpha": alpha,
        "zref_ohm": ZREF,
        "piezo_bot": ALN1.name,
        "piezo_top_sha256": material_info["sha256"],
    }
    return corpus_infra.make_config_tag(payload)


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
    piezo_bot: PiezoMaterial,
    piezo_top: PiezoMaterial,
    alpha: float,
    material_info: dict,
) -> dict:
    """执行语料生成：逐 shard 求解（已存在跳过）→ 合并 → 写 npz + meta.json。"""
    t0 = time.perf_counter()
    geom = sample_geometry(n, seed=seed)
    rows = [tuple(float(v) for v in geom[i]) for i in range(n)]
    config_tag = _config_tag(n, seed, shard_size, material_info, alpha)

    n_shards = corpus_infra.solve_shards(
        rows=rows,
        solve_one=partial(solve_extract_one, piezo_bot=piezo_bot, piezo_top=piezo_top, alpha=alpha),
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
    diag = merged["arrays"]["diag"]
    n_attempted = merged["n_attempted"]
    drop = {reason: 0 for reason in DROP_REASONS}
    drop.update(merged["drop_counts"])

    freq_ghz = (DENSE_FREQ_HZ / 1e9).astype(np.float64)
    corpus_infra.atomic_savez(
        out_path,
        {"x_geom": x, "y_params": y, "z_dense": z, "freq_ghz": freq_ghz, "diag": diag},
        compressed=True,
    )

    wall = time.perf_counter() - t0
    te1_dead = int(np.isnan(diag[:, 0]).sum()) if len(diag) else 0
    meta = {
        "seed": seed,
        "n_requested": n,
        "n_attempted": n_attempted,
        "n_produced": int(x.shape[0]),
        "n_dropped": int(n_attempted - x.shape[0]),
        "drop_reasons": drop,
        "stack": "EPPE(AlN reverse+1 / AlScN_cal reverse-1), no middle electrode",
        "t_aln_levels_nm": list(T_ALN_LEVELS_NM),
        "ranges_nm": {
            "t_alscn": [T_ALSCN_MIN_NM, T_ALSCN_MAX_NM],
            "t_top": list(T_TOP_RANGE_NM),
            "t_bot": list(T_BOT_RANGE_NM),
            "t_pf": list(T_PF_RANGE_NM),
        },
        "t_alscn_calibration": (
            f"nominal 堆叠(t_aln=100, Mo 150/150, pf=0) 偶模支路锚定 fs2="
            f"{FS2_CAL_TARGET_GHZ} GHz（10 GHz 下界+300 MHz 边距）；"
            "2026-09-11 二分标定（--calibrate 复现）"
        ),
        "area_levels_um2": list(AREA_LEVELS_UM2),
        "area_note": (
            "Mason 1D 中面积只缩放阻抗电平（C0∝area）；"
            "经 50Ω 变换的 S11/Bode-Q 依赖之，故作输入特征"
        ),
        "solve_grid": {"f_start_ghz": 3.0, "f_stop_ghz": 22.0, "f_step_mhz": 2.0},
        "dense_grid": {
            "f_start_ghz": 8.0,
            "f_stop_ghz": 22.0,
            "f_step_mhz": 5.0,
            "note": (
                "独立精确求解网格（非 2 MHz 求解网格子集：5 MHz 不是 2 MHz 整数倍，"
                "子集在算术上不成立；单独精确求解，无插值误差）"
            ),
        },
        "te2_window_ghz": [TE2_WINDOW_HZ[0] / 1e9, TE2_WINDOW_HZ[1] / 1e9],
        "te1_diag_window_ghz": [TE1_DIAG_WINDOW_HZ[0] / 1e9, TE1_DIAG_WINDOW_HZ[1] / 1e9],
        "branch_guard": {
            "ratio_lo": BRANCH_RATIO_LO,
            "ratio_hi": BRANCH_RATIO_HI,
            "definition": "ratio_lo <= fs2/f1_twin <= ratio_hi（含端点），"
            "f1_twin=同几何同极性(+1,+1)对照扫频的 TE1 基模",
            "note": "f2 偶模支路比值窗口：该叠层族实测 f2/f1=2.05–2.6（Mo 电极质量"
            "对 f1 的拉低强于 f2；AlN/AlN 对称黄金案例 2.41）；f3 支路 ≥3.2×f1 被拦",
        },
        "alpha": alpha,
        "zref_ohm": ZREF,
        "materials": {
            "piezo_bot": {
                "name": ALN1.name,
                "source": "AlN literature values, not measurement-calibrated "
                "(mason_sim.materials.ALN1)",
                "values": {"rho": ALN1.rho, "c33": ALN1.c33, "e33": ALN1.e33, "eps": ALN1.eps},
            },
            "piezo_top": {
                "name": piezo_top.name,
                "source": f"WP3 calibrated AlScN: {material_info['path']}",
                "source_sha256": material_info["sha256"],
                "source_commit_sha": material_info["commit_sha"],
                "calibrated_at": material_info["calibrated_at"],
                "values": material_info["values"],
            },
            "electrode": MO.name,
            "pf": SIN.name,
        },
        "geom_cols": GEOM_COLS,
        "param_cols": PARAM_COLS,
        "diag_cols": DIAG_COLS,
        "label_provenance": LABEL_PROVENANCE,
        "mason_sim_commit_sha": corpus_infra.git_sha(PROJECT_ROOT),
        "wall_time_sec": round(wall, 3),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "fs2_ghz_min": float(y[:, 0].min()) if len(y) else None,
        "fs2_ghz_max": float(y[:, 0].max()) if len(y) else None,
        "k2eff_pct_min": float(y[:, 2].min()) if len(y) else None,
        "k2eff_pct_max": float(y[:, 2].max()) if len(y) else None,
        "te1_dead_fraction": (te1_dead / len(diag)) if len(diag) else None,
    }
    corpus_infra.atomic_write_json(corpus_infra.meta_path_for(out_path), meta)
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
        help="冒烟模式：只跑 N 样本并打印吞吐/ETA/有效率/标签分布",
    )
    ap.add_argument("--calibrate", action="store_true", help="复现 T_ALSCN_MAX_NM 标定并退出")
    ap.add_argument(
        "--calibration-json",
        type=str,
        default=str(CALIBRATION_JSON),
        help="校准材料 JSON 路径（默认 calibration/calibrated_materials.json）",
    )
    args = ap.parse_args(argv)

    positive_values: dict[str, int] = {
        "--n": args.n,
        "--nprocs": args.nprocs,
        "--shard-size": args.shard_size,
    }
    if args.smoke is not None:
        positive_values["--smoke"] = args.smoke
    corpus_infra.validate_positive_ints(ap, positive_values)

    # 材料冻结加载：启动时打印来源与值（校准口径审计）
    piezo_top, alpha, material_info = load_calibrated_piezo(Path(args.calibration_json))
    print(
        f"材料冻结：piezo_top={piezo_top.name} 来自 {material_info['path']}\n"
        f"  c33={piezo_top.c33:.6g} e33={piezo_top.e33:.6g} eps={piezo_top.eps:.6g} "
        f"rho={piezo_top.rho:.6g} alpha={alpha:.6g}\n"
        f"  sha256={material_info['sha256'][:16]}… commit={material_info['commit_sha']}\n"
        f"  piezo_bot=AlN（文献值，未经实测校准）"
    )

    if args.calibrate:
        run_calibration(ALN1, piezo_top, alpha)
        return 0

    if args.smoke is not None:
        n = int(args.smoke)
        tmp = Path(tempfile.mkdtemp(prefix="eppe_corpus_smoke_"))
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
        piezo_bot=ALN1,
        piezo_top=piezo_top,
        alpha=alpha,
        material_info=material_info,
    )
    wall = time.perf_counter() - t0

    print(
        f"完成：attempted={meta['n_attempted']} produced={meta['n_produced']} "
        f"dropped={meta['n_dropped']} {meta['drop_reasons']}"
    )
    print(f"输出：{out_path}（+ {corpus_infra.meta_path_for(out_path).name}）")
    if meta["n_produced"] > 0:
        print(
            f"标签分布：fs2 ∈ [{meta['fs2_ghz_min']:.3f}, {meta['fs2_ghz_max']:.3f}] GHz，"
            f"k²eff ∈ [{meta['k2eff_pct_min']:.2f}, {meta['k2eff_pct_max']:.2f}]%，"
            f"TE1 死亡率 {meta['te1_dead_fraction']:.1%}"
        )

    if args.smoke is not None:
        attempted = max(meta["n_attempted"], 1)
        sps_total = attempted / wall if wall > 0 else float("inf")
        sps_per_core = sps_total / max(args.nprocs, 1)
        eta_sec = (N_DEFAULT / sps_total) if sps_total > 0 else float("inf")
        valid_rate = meta["n_produced"] / attempted
        print(f"有效率：{meta['n_produced']}/{attempted} = {valid_rate:.1%}")
        print(
            f"吞吐：{sps_total:.2f} 样本/秒（{sps_per_core:.3f} 样本/秒/核，nprocs={args.nprocs}）"
        )
        print(f"全量 {N_DEFAULT} 样本 ETA（同 nprocs）：{eta_sec / 60:.1f} 分钟")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
