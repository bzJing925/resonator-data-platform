"""WP3：Mason EPE ↔ 生产代理校准（4–7 GHz，IUS2026 修订物理锚定）。

校准目标：``surrogate_data/baseline.npz``（生产代理 frame-free 基准，EG=FL=0）的
**面积均值** 7 参数（fs/fp/k²eff/qs/qp/qs_bodeq/qp_bodeq，生产口径）。1D Mason 的
fs/fp/k²eff 与面积无关，实测跨面积散布是横向物理，论文口径取面积均值；脚本从 npz
自行计算面积均值（不硬编码）。pf=0 → t_pf=0；pf=1 → t_pf=63.5 nm（62/65 合并态）SiN。

搜索空间（每个几何候选独立拟合）：
- 几何候选：t_mo 对称（t_top=t_bot）∈ {130, 150, 165, 185, 200} nm；
- θ = (t_piezo_nm, s_c33, s_e33, s_eps, s_alpha)：
  t_piezo 连续有界 [324, 365] nm（工艺档案簇，来源日期 2026-09-10）；
  c33/e33/eps/alpha 相对 ALSCN 名义 ±10% 有界（标度因子 ∈ [0.9, 1.1]）；rho 固定。
- scipy.optimize.least_squares（trf，bounds 严格），多起点（名义值 + 界内拉丁超立方，
  固定种子），可 ProcessPoolExecutor 并行。

目标函数：残差向量 [fs (MHz)、fp (MHz)、k²eff (pct-pt)、qs/qp/qs_bodeq/qp_bodeq
(相对 %)]，默认权重 fs/fp 各 1.0/MHz、k²eff 1.0/pct-pt、Q 各 0.2/%（CLI 可调）。
每次评估：EPEStackParams(t_piezo, t_mo, t_mo, t_pf, area=2000 µm², alpha) →
run_epe_stack_sweep(4–7 GHz @1 MHz) → extract_epe_production_labels。
注意 Bode-Q 经 50 Ω 变换依赖面积，评估面积固定 2000 µm²（EVAL_AREA_UM2）；
无效样本（无谐振对 / valid=False）给大惩罚 PENALTY 使优化器回避。

pf=1 两套：(A) 主流程——pf=0 拟合结果直接预测 pf=1（t_pf=63.5 nm），报告预测残差；
(B) 变体——pf=0/pf=1 联合拟合试一次，报告是否显著优于 (A)。最终以 (A) 为主校准
（pf=1 的 fs −9.6% 位移疑似非质量加载物理，硬拟合会污染材料参数）。

产物（--outdir，默认 backend/surrogate_data_mason/calibration/）：
calibrated_materials.json（WP2b 冻结输入）、residuals.json、calibration_report.md，
终端打印论文一句话数字。

用法：
    uv run --no-sync python scripts/calibrate_mason_epe.py
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.stats import qmc

# spawn 子进程重新 import 本模块时需要 app 包可导入（scripts/ 在 sys.path[0] 而非 backend/）
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.mason_sim import (  # noqa: E402
    ALSCN,
    MO,
    SIN,
    EPEStackParams,
    PiezoMaterial,
    build_frequency_grid,
    nm_to_m,
    run_epe_stack_sweep,
    um2_to_m2,
)
from app.mason_sim.prodlabels import PARAM_KEYS, extract_epe_production_labels  # noqa: E402
from app.ml.surrogate.baseline import BaselineTable  # noqa: E402

# ============================================================
# 常量
# ============================================================
SEED_DEFAULT = 20260910  # 与 build_mason_epe_corpus.py 一致
BOUNDS_PCT = 10.0  # 材料标度因子 ±10% 有界
T_PIEZO_BOUNDS_NM = (324.0, 365.0)  # 工艺档案簇（来源日期 2026-09-10）
T_MO_CANDIDATES_NM = (130.0, 150.0, 165.0, 185.0, 200.0)  # 对称电极候选
ALPHA_NOMINAL = 0.02  # 压电层声学衰减名义值 (dB/m)，同 EPEStackParams 默认
T_PF_NM_PF1 = 63.5  # pf=1 态 SiN 钝化层厚度（62/65 合并态）
EVAL_AREA_UM2 = 2000.0  # 标签评估面积：Bode-Q 经 50 Ω 变换依赖面积，固定 2000 µm²
F_START_HZ, F_STOP_HZ = 4.0e9, 7.0e9  # 校准频带
FULL_GRID_STEP_HZ = 3.0e6  # 最终谱评估网格（与 z_base_full 的 1001 点一致）
LOW_BAND_GHZ = (4.0, 4.3)  # 谱残差"低频电平偏移"统计带（低于 fs 的电容区）
ZP_HALF_WIN_GHZ = 0.15  # Zp 峰值统计窗口半宽
PENALTY = 1.0e3  # 无效样本惩罚（加权残差单位，每分量）

THETA_NAMES = ("t_piezo_nm", "s_c33", "s_e33", "s_eps", "s_alpha")
# 名义起点：t_piezo 取工艺簇区间中值，材料标度取 1
NOMINAL_THETA = np.array([(T_PIEZO_BOUNDS_NM[0] + T_PIEZO_BOUNDS_NM[1]) / 2.0, 1.0, 1.0, 1.0, 1.0])
# 数值 Jacobian（3-point 中心差分）相对步长：绝对步长 = diff_step×|x|，
# 即 ≈[1 nm, 1e-3, 1e-3, 1e-3, 2e-3]。fs/fp/k²eff 标签吸附频率网格（阶梯函数），
# 默认步长（~1e-8）看不到梯度，必须放大到足以跨越 1 MHz 频格。
DIFF_STEP = np.array([2.9e-3, 1.0e-3, 1.0e-3, 1.0e-3, 2.0e-3])
X_SCALE = np.array([40.0, 0.1, 0.1, 0.1, 0.1])  # TRF 变量尺度

BASELINE_DEFAULT = _BACKEND_ROOT / "surrogate_data" / "baseline.npz"
OUTDIR_DEFAULT = _BACKEND_ROOT / "surrogate_data_mason" / "calibration"


# ============================================================
# 目标与标签评估
# ============================================================
def theta_bounds() -> tuple[np.ndarray, np.ndarray]:
    """θ 严格界：t_piezo ∈ [324, 365] nm；材料标度 ∈ [0.9, 1.1]。"""
    pct = BOUNDS_PCT / 100.0
    lo = np.array([T_PIEZO_BOUNDS_NM[0], 1.0 - pct, 1.0 - pct, 1.0 - pct, 1.0 - pct])
    hi = np.array([T_PIEZO_BOUNDS_NM[1], 1.0 + pct, 1.0 + pct, 1.0 + pct, 1.0 + pct])
    return lo, hi


def default_weights() -> np.ndarray:
    """默认权重：fs/fp 各 1.0/MHz、k²eff 1.0/pct-pt、Q 各 0.2/（相对 %）。"""
    return np.array([1.0, 1.0, 1.0, 0.2, 0.2, 0.2, 0.2])


def make_starts(n_starts: int, seed: int) -> np.ndarray:
    """多起点：第 0 行 = 名义值，其余 = 界内拉丁超立方（固定种子可复现）。"""
    if n_starts < 1:
        raise ValueError("n_starts 必须 >= 1")
    lo, hi = theta_bounds()
    rows = [NOMINAL_THETA]
    if n_starts > 1:
        u = qmc.LatinHypercube(d=len(THETA_NAMES), seed=seed).random(n_starts - 1)
        rows.extend(lo + u * (hi - lo))
    return np.asarray(rows, dtype=np.float64)


def area_mean_targets(params_base: np.ndarray) -> np.ndarray:
    """校准目标：params_base (P, A, 7) 沿面积轴取均值 → (P, 7)。"""
    return np.asarray(params_base, dtype=np.float64).mean(axis=1)


def make_piezo(theta: np.ndarray) -> PiezoMaterial:
    """由标度因子构造校准 AlScN 材料（rho 固定）。"""
    _t, s_c33, s_e33, s_eps, _s_alpha = [float(v) for v in theta]
    return PiezoMaterial(
        name="AlScN_cal",
        rho=ALSCN.rho,
        c33=ALSCN.c33 * s_c33,
        e33=ALSCN.e33 * s_e33,
        eps=ALSCN.eps * s_eps,
    )


def simulate_labels(
    theta: np.ndarray,
    t_mo_nm: float,
    t_pf_nm: float,
    area_um2: float,
    freq: np.ndarray,
) -> dict[str, float | bool]:
    """单次评估：θ + 几何 → EPE 扫频 → 生产口径 7 参数标签。"""
    t_piezo_nm = float(theta[0])
    alpha = ALPHA_NOMINAL * float(theta[4])
    stack = EPEStackParams(
        t_piezo=nm_to_m(t_piezo_nm),
        t_top=nm_to_m(t_mo_nm),
        t_bot=nm_to_m(t_mo_nm),
        t_pf=nm_to_m(t_pf_nm),
        area=um2_to_m2(area_um2),
        alpha=alpha,
    )
    result = run_epe_stack_sweep(freq, make_piezo(theta), MO, SIN, stack)
    return extract_epe_production_labels(result.freq, result.z11)


def simulate_z_db(
    theta: np.ndarray,
    t_mo_nm: float,
    t_pf_nm: float,
    area_um2: float,
    freq: np.ndarray,
) -> np.ndarray:
    """单次评估：θ + 几何 → |Z11| dBΩ 全谱（用于谱残差）。"""
    t_piezo_nm = float(theta[0])
    alpha = ALPHA_NOMINAL * float(theta[4])
    stack = EPEStackParams(
        t_piezo=nm_to_m(t_piezo_nm),
        t_top=nm_to_m(t_mo_nm),
        t_bot=nm_to_m(t_mo_nm),
        t_pf=nm_to_m(t_pf_nm),
        area=um2_to_m2(area_um2),
        alpha=alpha,
    )
    result = run_epe_stack_sweep(freq, make_piezo(theta), MO, SIN, stack)
    return result.abs_z11_db


def residual_vector(
    labels: dict[str, float | bool] | None,
    target: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """加权残差 [fs MHz, fp MHz, k²eff pct-pt, qs/qp/qs_bodeq/qp_bodeq 相对 %]。

    无效样本（无谐振对、fp<=fs 或任一 Q 为 nan → valid=False）给大惩罚使优化器回避。
    """
    if labels is None or not labels.get("valid", False):
        return np.full(len(PARAM_KEYS), PENALTY)
    cal = np.array([float(labels[k]) for k in PARAM_KEYS], dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    r = np.empty(len(PARAM_KEYS))
    r[0] = (cal[0] - target[0]) * 1.0e3  # fs：GHz→MHz
    r[1] = (cal[1] - target[1]) * 1.0e3  # fp：GHz→MHz
    r[2] = cal[2] - target[2]  # k²eff：pct-pt
    r[3:] = (cal[3:] - target[3:]) / target[3:] * 100.0  # Q：相对 %
    if not np.all(np.isfinite(r)):
        return np.full(len(PARAM_KEYS), PENALTY)
    return np.asarray(weights, dtype=np.float64) * r


# ============================================================
# 有界多起点最小二乘拟合
# ============================================================
@dataclass
class FitResult:
    """单起点拟合结果。"""

    t_mo_nm: float
    start_idx: int
    theta: np.ndarray
    cost: float
    nfev: int
    success: bool
    message: str
    labels_by_state: list[dict] = field(default_factory=list)


def _solve_residuals(
    theta: np.ndarray,
    states: list[tuple[float, np.ndarray]],
    t_mo_nm: float,
    weights: np.ndarray,
    freq: np.ndarray,
    area_um2: float,
) -> np.ndarray:
    """多态残差：对每个 (t_pf_nm, target) 状态评估并拼接（联合拟合用）。"""
    parts = []
    for t_pf_nm, target in states:
        try:
            labels = simulate_labels(theta, t_mo_nm, t_pf_nm, area_um2, freq)
        except Exception:
            labels = None  # MNA 奇异等数值失败 → 惩罚
        parts.append(residual_vector(labels, target, weights))
    return np.concatenate(parts)


def _fit_one_start(payload: dict) -> FitResult:
    """单起点有界 LS（顶层函数，ProcessPoolExecutor 可 pickle）。"""
    states = payload["states"]
    t_mo_nm = float(payload["t_mo_nm"])
    start = np.asarray(payload["start"], dtype=np.float64)
    weights = np.asarray(payload["weights"], dtype=np.float64)
    freq = np.asarray(payload["freq"], dtype=np.float64)
    area_um2 = float(payload["area_um2"])
    max_nfev = payload["max_nfev"]
    start_idx = int(payload["start_idx"])
    lo, hi = theta_bounds()
    try:
        sol = least_squares(
            _solve_residuals,
            start,
            args=(states, t_mo_nm, weights, freq, area_um2),
            bounds=(lo, hi),
            method="trf",
            jac="3-point",
            diff_step=DIFF_STEP,
            x_scale=X_SCALE,
            max_nfev=max_nfev,
            xtol=1e-12,
            ftol=1e-12,
            gtol=1e-12,
        )
        theta = np.asarray(sol.x, dtype=np.float64)
        cost = float(sol.cost)
        nfev = int(sol.nfev)
        success = bool(sol.success)
        message = str(sol.message)
    except Exception as exc:  # 数值失败不拖垮整个几何候选
        theta = start
        cost = float("inf")
        nfev = 0
        success = False
        message = f"least_squares 异常: {exc!r}"
    labels_by_state = []
    for t_pf_nm, _target in states:
        try:
            labels_by_state.append(simulate_labels(theta, t_mo_nm, t_pf_nm, area_um2, freq))
        except Exception:
            labels_by_state.append({"valid": False})
    return FitResult(
        t_mo_nm=t_mo_nm,
        start_idx=start_idx,
        theta=theta,
        cost=cost,
        nfev=nfev,
        success=success,
        message=message,
        labels_by_state=labels_by_state,
    )


def fit_geometry(
    states: list[tuple[float, np.ndarray]],
    t_mo_nm: float,
    starts: np.ndarray,
    weights: np.ndarray,
    freq: np.ndarray,
    *,
    area_um2: float = EVAL_AREA_UM2,
    max_nfev: int | None = None,
    workers: int = 1,
) -> list[FitResult]:
    """对一个几何候选（t_mo 对称）跑多起点有界 LS，按 (cost, start_idx) 排序返回。

    states: [(t_pf_nm, target7)]——单态为主校准（pf=0），双态为联合拟合变体。
    workers>1 且多起点时用 spawn ProcessPoolExecutor 并行；排序确定性与完成顺序无关。
    """
    payloads = [
        {
            "states": states,
            "t_mo_nm": t_mo_nm,
            "start": np.asarray(s, dtype=np.float64),
            "weights": np.asarray(weights, dtype=np.float64),
            "freq": np.asarray(freq, dtype=np.float64),
            "area_um2": area_um2,
            "max_nfev": max_nfev,
            "start_idx": i,
        }
        for i, s in enumerate(starts)
    ]
    if workers > 1 and len(payloads) > 1:
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            results = list(pool.map(_fit_one_start, payloads))
    else:
        results = [_fit_one_start(p) for p in payloads]
    return sorted(results, key=lambda r: (r.cost, r.start_idx))


# ============================================================
# 谱 / Zp 残差
# ============================================================
def spectral_residuals(
    mason_db: np.ndarray,
    base_db: np.ndarray,
    freq_hz: np.ndarray,
    low_band_ghz: tuple[float, float] = LOW_BAND_GHZ,
) -> dict[str, float | int]:
    """逐点谱残差：RMSE、max|Δ|、低频电平偏移（LOW_BAND_GHZ 内均值差）。"""
    d = np.asarray(mason_db, dtype=np.float64) - np.asarray(base_db, dtype=np.float64)
    mask = (freq_hz >= low_band_ghz[0] * 1e9) & (freq_hz <= low_band_ghz[1] * 1e9)
    return {
        "rmse_db": float(np.sqrt(np.mean(d**2))),
        "max_abs_db": float(np.max(np.abs(d))),
        "low_level_offset_db": float(np.mean(d[mask])),
        "n_points": int(d.size),
    }


def zp_peak_db(
    z_db: np.ndarray,
    freq_hz: np.ndarray,
    fp_ghz: float,
    half_win_ghz: float = ZP_HALF_WIN_GHZ,
) -> float:
    """fp 邻域窗口 [fp±half_win] 内 |Z| dB 峰值（Zp 的 dB 表达）。"""
    freq = np.asarray(freq_hz, dtype=np.float64)
    mask = (freq >= (fp_ghz - half_win_ghz) * 1e9) & (freq <= (fp_ghz + half_win_ghz) * 1e9)
    if not np.any(mask):
        return float("nan")
    return float(np.max(np.asarray(z_db, dtype=np.float64)[mask]))


# ============================================================
# 报告与产物
# ============================================================
def _param_residuals(labels: dict, target: np.ndarray) -> dict:
    """7 参数残差（校准值−目标值，附相对 %）。"""
    out = {}
    for i, key in enumerate(PARAM_KEYS):
        c = float(labels[key])
        t = float(target[i])
        out[key] = {
            "target": t,
            "calibrated": c,
            "abs_diff": c - t,
            "rel_pct": (c - t) / t * 100.0 if t != 0.0 else float("nan"),
        }
    return out


def _weighted_rms(labels: dict, target: np.ndarray, weights: np.ndarray) -> float:
    r = residual_vector(labels, np.asarray(target, dtype=np.float64), weights)
    return float(np.sqrt(np.mean(r**2)))


def _one_liner(
    labels0: dict,
    target0: np.ndarray,
    labels1: dict,
    target1: np.ndarray,
) -> str:
    """论文一句话数字（pf=0 校准残差 + pf=1 (A) 预测残差）。"""
    dfs = (float(labels0["fs_ghz"]) - float(target0[0])) * 1e3
    dfp = (float(labels0["fp_ghz"]) - float(target0[1])) * 1e3
    dk2 = float(labels0["k2eff_pct"]) - float(target0[2])
    dfs1 = (float(labels1["fs_ghz"]) - float(target1[0])) * 1e3
    dfp1 = (float(labels1["fp_ghz"]) - float(target1[1])) * 1e3
    dk21 = float(labels1["k2eff_pct"]) - float(target1[2])
    return (
        "校准后 Mason 在 4–7 GHz 复现实测代理面积均值基准："
        f"fs 残差 {dfs:+.1f} MHz、fp {dfp:+.1f} MHz、k²eff {dk2:+.2f} pct-pt（pf=0）；"
        f"pf=1 预测残差 fs {dfs1:+.1f} MHz、fp {dfp1:+.1f} MHz、k²eff {dk21:+.2f} pct-pt。"
    )


def _git_commit_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_BACKEND_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _fmt_pct(x: float) -> str:
    return f"{x:+.2f}"


def build_materials_payload(
    best: FitResult,
    residuals_summary: dict,
    commit_sha: str,
) -> dict:
    """calibrated_materials.json 负载（WP2b 的冻结输入）。"""
    theta = best.theta
    piezo = make_piezo(theta)
    return {
        "piezo": {
            "name": piezo.name,
            "rho": piezo.rho,  # 固定（未参与校准）
            "c33": piezo.c33,
            "e33": piezo.e33,
            "eps": piezo.eps,
        },
        "alpha": ALPHA_NOMINAL * float(theta[4]),
        "t_piezo_nm": float(theta[0]),
        "t_mo_nm": float(best.t_mo_nm),
        "t_pf_nm_pf1": T_PF_NM_PF1,
        "target": "baseline.npz area-mean pf=0",
        "method": (
            "scipy.optimize.least_squares (trf, jac=3-point, bounds 严格) 多起点"
            "（名义+LHS，固定种子）；pf=0 面积均值 7 参数加权残差；"
            "pf=1 由 pf=0 结果直接预测（变体 A，t_pf=63.5 nm SiN）"
        ),
        "bounds_pct": BOUNDS_PCT,
        "residuals_summary": residuals_summary,
        "commit_sha": commit_sha,
        "calibrated_at": datetime.now(UTC).isoformat(),
    }


def _materials_table(best: FitResult) -> list[str]:
    theta = best.theta
    piezo = make_piezo(theta)
    rows = [
        "| 量 | 校准值 | 名义值 | 相对名义 |",
        "|---|---|---|---|",
        f"| t_piezo (nm) | {theta[0]:.2f} | {NOMINAL_THETA[0]:.2f}（区间中值） | — |",
        f"| t_mo (nm, 对称) | {best.t_mo_nm:.1f} | — (离散候选) | — |",
        f"| c33 (Pa) | {piezo.c33:.6e} | {ALSCN.c33:.6e} | {_fmt_pct((theta[1] - 1) * 100)}% |",
        f"| e33 (C/m²) | {piezo.e33:.6f} | {ALSCN.e33:.6f} | {_fmt_pct((theta[2] - 1) * 100)}% |",
        f"| eps (F/m) | {piezo.eps:.6e} | {ALSCN.eps:.6e} | {_fmt_pct((theta[3] - 1) * 100)}% |",
        f"| alpha (dB/m) | {ALPHA_NOMINAL * theta[4]:.5f} | {ALPHA_NOMINAL:.5f} "
        f"| {_fmt_pct((theta[4] - 1) * 100)}% |",
        f"| rho (kg/m³) | {piezo.rho:.2f} | {ALSCN.rho:.2f} | 固定 |",
    ]
    return rows


def _param_residual_table(title: str, res: dict) -> list[str]:
    rows = [
        f"#### {title}",
        "",
        "| 参数 | 目标值 | 校准/预测值 | Δ | 相对 % |",
        "|---|---|---|---|---|",
    ]
    for key in PARAM_KEYS:
        r = res[key]
        rows.append(
            f"| {key} | {r['target']:.6f} | {r['calibrated']:.6f} "
            f"| {r['abs_diff']:+.6f} | {r['rel_pct']:+.2f} |"
        )
    rows.append("")
    return rows


def _spectra_table(title: str, rows_in: list[dict]) -> list[str]:
    rows = [
        f"#### {title}",
        "",
        "| area (µm²) | RMSE (dB) | max\\|Δ\\| (dB) | 低频电平偏移 (dB) |",
        "|---|---|---|---|",
    ]
    for r in rows_in:
        rows.append(
            f"| {r['area_um2']:.0f} | {r['rmse_db']:.3f} | {r['max_abs_db']:.3f} "
            f"| {r['low_level_offset_db']:+.3f} |"
        )
    rmse = np.array([r["rmse_db"] for r in rows_in])
    off = np.array([r["low_level_offset_db"] for r in rows_in])
    mx = np.array([r["max_abs_db"] for r in rows_in])
    rows.append(
        f"| **均值/最差** | **{rmse.mean():.3f}** | **{mx.max():.3f}** | **{off.mean():+.3f}** |"
    )
    rows.append("")
    return rows


def render_report(
    *,
    args: argparse.Namespace,
    weights: np.ndarray,
    starts: np.ndarray,
    geom_best: dict[float, FitResult],
    best: FitResult,
    target0: np.ndarray,
    target1: np.ndarray,
    labels_a0: dict,
    labels_a1: dict,
    joint: FitResult,
    labels_b0: dict,
    labels_b1: dict,
    res0: dict,
    res1: dict,
    spectra: dict,
    zp: dict,
    one_liner: str,
    verdict_b: str,
    runtime_s: float,
    commit_sha: str,
    n_freq_full: int,
) -> str:
    """calibration_report.md 文本。"""
    lines: list[str] = [
        "# WP3 Mason EPE ↔ 生产代理校准报告（4–7 GHz）",
        "",
        f"- 生成时间（UTC）：{datetime.now(UTC).isoformat()}",
        f"- commit：{commit_sha}",
        f"- 目标：`{args.baseline}` 面积均值（12 面积 × 2 pf → (pf,7)）；"
        "1D Mason 的 fs/fp/k²eff 与面积无关，实测跨面积散布是横向物理，论文口径取面积均值",
        f"- 主校准：pf=0（t_pf=0）；pf=1 预测 t_pf={T_PF_NM_PF1} nm SiN（62/65 合并态）",
        f"- 评估：4–7 GHz @{args.grid_step_mhz:g} MHz；标签评估面积固定 "
        f"{EVAL_AREA_UM2:.0f} µm²（Bode-Q 经 50 Ω 变换依赖面积）；"
        f"最终谱评估 4–7 GHz @3 MHz（{n_freq_full} 点，与 z_base_full 对齐）",
        f"- 优化器：scipy least_squares trf，jac=3-point，diff_step≈[1 nm, 1e-3×4]，"
        f"max_nfev={args.max_nfev}，多起点 {len(starts)}（名义+LHS，种子 {args.seed}）",
        f"- 权重：fs/fp {weights[0]:g}/MHz、{weights[1]:g}/MHz；k²eff {weights[2]:g}/pct-pt；"
        f"Q 各 {weights[3]:g}/（相对 %）",
        f"- 材料界：c33/e33/eps/alpha 相对 ALSCN 名义 ±{BOUNDS_PCT:g}%；rho 固定；"
        f"t_piezo ∈ [{T_PIEZO_BOUNDS_NM[0]:g}, {T_PIEZO_BOUNDS_NM[1]:g}] nm"
        "（工艺档案簇，2026-09-10）；t_mo 对称候选 "
        + "/".join(f"{g:g}" for g in sorted(geom_best))
        + " nm",
        f"- 总耗时：{runtime_s:.1f} s",
        "",
        "## 论文一句话数字",
        "",
        one_liner,
        "",
        "## 各几何候选最优目标值对比",
        "",
        "| t_mo (nm) | cost (0.5Σr²) | t_piezo (nm) | s_c33 | s_e33 | s_eps | s_alpha "
        "| fs Δ (MHz) | fp Δ (MHz) | k²eff Δ (pct-pt) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t_mo in sorted(geom_best):
        r = geom_best[t_mo]
        lab = r.labels_by_state[0]
        dfs = (float(lab["fs_ghz"]) - float(target0[0])) * 1e3
        dfp = (float(lab["fp_ghz"]) - float(target0[1])) * 1e3
        dk2 = float(lab["k2eff_pct"]) - float(target0[2])
        lines.append(
            f"| {t_mo:g} | {r.cost:.3f} | {r.theta[0]:.2f} | {r.theta[1]:.4f} "
            f"| {r.theta[2]:.4f} | {r.theta[3]:.4f} | {r.theta[4]:.4f} "
            f"| {dfs:+.1f} | {dfp:+.1f} | {dk2:+.2f} |"
        )
    lines += [
        "",
        f"**最优几何：t_mo = {best.t_mo_nm:g} nm（cost={best.cost:.3f}，"
        f"start_idx={best.start_idx}，nfev={best.nfev}）**",
        "",
        "## 收敛材料表",
        "",
    ]
    lines += _materials_table(best)
    lines += ["", "## pf=0 校准残差（7 参数，面积均值目标）", ""]
    lines += _param_residual_table("pf=0（校准态）", res0)
    lines += ["## pf=1 预测残差", ""]
    lines += _param_residual_table("pf=1（变体 A：pf=0 材料直接预测）", res1)
    rms_a1 = _weighted_rms(labels_a1, target1, weights)
    rms_b1 = _weighted_rms(labels_b1, target1, weights)
    rms_a0 = _weighted_rms(labels_a0, target0, weights)
    rms_b0 = _weighted_rms(labels_b0, target0, weights)
    lines += [
        "### 变体 (B)：pf=0/pf=1 联合拟合（仅最优几何，试一次）",
        "",
        "| 口径 | pf=0 加权 RMS | pf=1 加权 RMS |",
        "|---|---|---|",
        f"| (A) pf=0 拟合→预测 pf=1 | {rms_a0:.3f} | {rms_a1:.3f} |",
        f"| (B) 联合拟合 | {rms_b0:.3f} | {rms_b1:.3f} |",
        "",
        verdict_b,
        "",
        "**最终选择：(A) 为主校准。** 理由：pf=1 的 fs −9.6% 位移远超 63.5 nm SiN 纯质量"
        "加载可解释的范围（疑似横向/工艺相关非质量加载物理），把 pf=1 硬塞进同一组材料"
        "参数会污染 pf=0 校准出的 c33/e33/eps；(A) 保持材料参数的物理干净性，pf=1 偏差"
        "作为模型-实测差异如实报告。",
        "",
        "## 谱残差（Mason |Z| dB 逐点 vs z_base_full，4–7 GHz @3 MHz）",
        "",
        f"低频电平偏移统计带：[{LOW_BAND_GHZ[0]:g}, {LOW_BAND_GHZ[1]:g}] GHz（低于 fs 的电容区）。",
        "",
    ]
    lines += _spectra_table("pf=0（校准材料直接评估各面积）", spectra["pf0"])
    lines += _spectra_table("pf=1（t_pf=63.5 nm 直接预测）", spectra["pf1"])
    lines += [
        "## Zp 残差（fp 邻域峰值 dB，窗口 ±" + f"{ZP_HALF_WIN_GHZ:g}" + " GHz）",
        "",
        "| pf | 面积范围 | Mason 峰值均值 (dB) | 基准峰值均值 (dB) | Δ 均值 (dB) |",
        "|---|---|---|---|---|",
    ]
    for key, name in (("pf0", "pf=0"), ("pf1", "pf=1")):
        rows = zp[key]
        m = float(np.mean([r["mason_peak_db"] for r in rows]))
        b = float(np.mean([r["base_peak_db"] for r in rows]))
        areas = [r["area_um2"] for r in rows]
        lines.append(
            f"| {name} | {min(areas):.0f}–{max(areas):.0f} | {m:.3f} | {b:.3f} | {m - b:+.3f} |"
        )
    lines += [
        "",
        "## 复现",
        "",
        "```bash",
        "uv run --no-sync python scripts/calibrate_mason_epe.py \\",
        f"    --baseline {args.baseline} --outdir {args.outdir} --seed {args.seed}",
        "```",
        "",
    ]
    return "\n".join(lines)


# ============================================================
# CLI / 主流程
# ============================================================
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=str, default=str(BASELINE_DEFAULT), help="校准目标 npz")
    ap.add_argument("--outdir", type=str, default=str(OUTDIR_DEFAULT), help="产物目录")
    ap.add_argument(
        "--geometries",
        type=str,
        default=",".join(f"{g:g}" for g in T_MO_CANDIDATES_NM),
        help="t_mo 候选（对称，逗号分隔，nm）",
    )
    ap.add_argument("--n-starts", type=int, default=6, help="多起点数（含名义起点，默认 6）")
    ap.add_argument(
        "--seed", type=int, default=SEED_DEFAULT, help=f"LHS 种子（默认 {SEED_DEFAULT}）"
    )
    ap.add_argument("--workers", type=int, default=min(8, mp.cpu_count()), help="并行进程数")
    ap.add_argument("--max-nfev", type=int, default=150, help="单起点最大函数评估数")
    ap.add_argument("--grid-step-mhz", type=float, default=1.0, help="校准评估网格步长 (MHz)")
    ap.add_argument("--w-fs", type=float, default=1.0, help="fs 权重（每 MHz）")
    ap.add_argument("--w-fp", type=float, default=1.0, help="fp 权重（每 MHz）")
    ap.add_argument("--w-k2", type=float, default=1.0, help="k²eff 权重（每 pct-pt）")
    ap.add_argument("--w-q", type=float, default=0.2, help="Q 权重（每相对 %）")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    t_start = time.time()

    table = BaselineTable.load(args.baseline)
    freq_full = build_frequency_grid(F_START_HZ, F_STOP_HZ, f_step_hz=FULL_GRID_STEP_HZ)
    if table.z_base_full.shape[-1] != freq_full.size:
        raise ValueError(
            f"z_base_full 末维 {table.z_base_full.shape[-1]} != 4–7 GHz @3 MHz 网格 "
            f"{freq_full.size}，谱残差无法逐点对齐"
        )
    targets = area_mean_targets(table.params_base)  # (P,7)，面积均值（不硬编码）
    i0 = int(np.argmin(np.abs(table.pfs - 0.0)))
    i1 = int(np.argmin(np.abs(table.pfs - 1.0)))
    target0, target1 = targets[i0], targets[i1]
    print(
        f"[WP3] 目标（面积均值）pf=0: fs={target0[0]:.4f} GHz fp={target0[1]:.4f} GHz "
        f"k2eff={target0[2]:.2f}% qs={target0[3]:.1f} qp={target0[4]:.1f}；"
        f"pf=1: fs={target1[0]:.4f} GHz fp={target1[1]:.4f} GHz k2eff={target1[2]:.2f}%"
    )

    freq_eval = build_frequency_grid(F_START_HZ, F_STOP_HZ, f_step_hz=args.grid_step_mhz * 1e6)
    starts = make_starts(args.n_starts, args.seed)
    weights = np.array([args.w_fs, args.w_fp, args.w_k2, args.w_q, args.w_q, args.w_q, args.w_q])
    geometries = [float(x) for x in args.geometries.split(",")]

    # ---- (A) 主校准：pf=0，逐几何多起点 ----
    geom_best: dict[float, FitResult] = {}
    for t_mo in geometries:
        results = fit_geometry(
            [(0.0, target0)],
            t_mo,
            starts,
            weights,
            freq_eval,
            max_nfev=args.max_nfev,
            workers=args.workers,
        )
        geom_best[t_mo] = results[0]
        r = results[0]
        print(
            f"[WP3] t_mo={t_mo:g} nm 最优 cost={r.cost:.3f} θ="
            f"({r.theta[0]:.2f}, {r.theta[1]:.4f}, {r.theta[2]:.4f}, {r.theta[3]:.4f}, "
            f"{r.theta[4]:.4f}) nfev={r.nfev}"
        )
    best_t_mo = min(geometries, key=lambda g: (geom_best[g].cost, g))
    best = geom_best[best_t_mo]
    print(f"[WP3] 最优几何 t_mo={best_t_mo:g} nm, cost={best.cost:.3f}")

    labels_a0 = simulate_labels(best.theta, best_t_mo, 0.0, EVAL_AREA_UM2, freq_eval)
    labels_a1 = simulate_labels(best.theta, best_t_mo, T_PF_NM_PF1, EVAL_AREA_UM2, freq_eval)

    # ---- (B) 变体：pf=0/pf=1 联合拟合（仅最优几何，试一次） ----
    joint = fit_geometry(
        [(0.0, target0), (T_PF_NM_PF1, target1)],
        best_t_mo,
        starts,
        weights,
        freq_eval,
        max_nfev=args.max_nfev,
        workers=args.workers,
    )[0]
    labels_b0, labels_b1 = joint.labels_by_state[0], joint.labels_by_state[1]
    rms_a1 = _weighted_rms(labels_a1, target1, weights)
    rms_b1 = _weighted_rms(labels_b1, target1, weights)
    verdict_b = f"(B) pf=1 加权 RMS {rms_b1:.3f} vs (A) {rms_a1:.3f}：" + (
        "显著优于 (A)（但需检查 pf=0 污染）。" if rms_b1 < 0.8 * rms_a1 else "未显著优于 (A)。"
    )

    # ---- 最终评估：12 面积 × 2 pf 谱残差 + Zp（用 (A) 校准结果直接评估/预测） ----
    spectra: dict[str, list[dict]] = {"pf0": [], "pf1": []}
    zp: dict[str, list[dict]] = {"pf0": [], "pf1": []}
    for key, pi, t_pf_nm, fp_mason in (
        ("pf0", i0, 0.0, float(labels_a0["fp_ghz"])),
        ("pf1", i1, T_PF_NM_PF1, float(labels_a1["fp_ghz"])),
    ):
        for ai, area in enumerate(table.areas):
            z_db = simulate_z_db(best.theta, best_t_mo, t_pf_nm, float(area), freq_full)
            base_db = table.z_base_full[pi, ai]
            spectra[key].append(
                {"area_um2": float(area), **spectral_residuals(z_db, base_db, freq_full)}
            )
            # Zp：各自 fp 邻域峰值比较（Mason fp 面积无关；基准取该面积档 fp）
            fp_base = float(table.params_base[pi, ai, 1])
            zp_m = zp_peak_db(z_db, freq_full, fp_mason)
            zp_b = zp_peak_db(base_db, freq_full, fp_base)
            zp[key].append(
                {
                    "area_um2": float(area),
                    "mason_peak_db": zp_m,
                    "base_peak_db": zp_b,
                    "diff_db": zp_m - zp_b,
                }
            )

    # ---- 产物 ----
    res0 = _param_residuals(labels_a0, target0)
    res1 = _param_residuals(labels_a1, target1)
    one_liner = _one_liner(labels_a0, target0, labels_a1, target1)
    residuals_summary = {
        "pf0": {
            "fs_mhz": res0["fs_ghz"]["abs_diff"] * 1e3,
            "fp_mhz": res0["fp_ghz"]["abs_diff"] * 1e3,
            "k2eff_pct_pt": res0["k2eff_pct"]["abs_diff"],
            "qs_rel_pct": res0["qs"]["rel_pct"],
            "qp_rel_pct": res0["qp"]["rel_pct"],
            "qs_bodeq_rel_pct": res0["qs_bodeq"]["rel_pct"],
            "qp_bodeq_rel_pct": res0["qp_bodeq"]["rel_pct"],
            "objective_cost": best.cost,
        },
        "pf1_predict": {
            "fs_mhz": res1["fs_ghz"]["abs_diff"] * 1e3,
            "fp_mhz": res1["fp_ghz"]["abs_diff"] * 1e3,
            "k2eff_pct_pt": res1["k2eff_pct"]["abs_diff"],
            "weighted_rms": rms_a1,
        },
        "joint_variant_pf1_weighted_rms": rms_b1,
    }
    commit_sha = _git_commit_sha()
    mats = build_materials_payload(best, residuals_summary, commit_sha)

    residuals_doc = {
        "note": "残差 = 校准/预测值 − 目标值（目标 = baseline.npz 面积均值）；"
        "谱/Zp 残差用变体 (A) 材料直接评估/预测",
        "target_area_mean": {
            "pf0": {k: float(v) for k, v in zip(PARAM_KEYS, target0, strict=True)},
            "pf1": {k: float(v) for k, v in zip(PARAM_KEYS, target1, strict=True)},
        },
        "pf0": {"params": res0, "objective_cost": best.cost},
        "pf1": {
            "params": res1,
            "note": "变体 (A)：pf=0 拟合材料 + t_pf=63.5 nm 直接预测（未重拟合）",
            "joint_variant": {
                "note": "变体 (B)：pf=0/pf=1 联合拟合（仅最优几何，试一次）",
                "theta": [float(v) for v in joint.theta],
                "pf0_params": _param_residuals(labels_b0, target0),
                "pf1_params": _param_residuals(labels_b1, target1),
                "verdict": verdict_b,
            },
        },
        "spectra": {
            "freq_grid": "4–7 GHz @3 MHz (1001 点)",
            "low_band_ghz": list(LOW_BAND_GHZ),
            "pf0": spectra["pf0"],
            "pf1": spectra["pf1"],
        },
        "zp": {
            "half_window_ghz": ZP_HALF_WIN_GHZ,
            "pf0": zp["pf0"],
            "pf1": zp["pf1"],
        },
    }

    runtime_s = time.time() - t_start
    report = render_report(
        args=args,
        weights=weights,
        starts=starts,
        geom_best=geom_best,
        best=best,
        target0=target0,
        target1=target1,
        labels_a0=labels_a0,
        labels_a1=labels_a1,
        joint=joint,
        labels_b0=labels_b0,
        labels_b1=labels_b1,
        res0=res0,
        res1=res1,
        spectra=spectra,
        zp=zp,
        one_liner=one_liner,
        verdict_b=verdict_b,
        runtime_s=runtime_s,
        commit_sha=commit_sha,
        n_freq_full=freq_full.size,
    )

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "calibrated_materials.json").write_text(
        json.dumps(mats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (outdir / "residuals.json").write_text(
        json.dumps(residuals_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (outdir / "calibration_report.md").write_text(report, encoding="utf-8")
    print(f"[WP3] 产物已写入 {outdir}（耗时 {runtime_s:.1f} s）")
    print(one_liner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
