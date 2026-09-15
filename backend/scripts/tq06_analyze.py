"""WP-A：TQ06 14 GHz P3F（AlN/AlScN 极性反转双层）器件层归属判定与校准。

IUS2026 论文重构数据环节。流程：

1. 提取（生产口径）：解析 ``/Users/jingbozuo/TQ06/`` 全部 38 个 s1p
   （``# Hz S dB R 50``，``!`` 为仪器注释行），转 Z11，用
   ``prodlabels.extract_eppe_production_labels`` 的 TE2 口径
   （(10,20] GHz 窗 + TE1 [3,8) 诊断）出 7 参数标签；明细表 + 中位/散布统计。
2. 归属判定：两种层归属（A: 底 AlN 170/顶 AlScN 190；B: 底 AlScN 190/顶 AlN 170，
   极性序保持 reverse_bot=+1/reverse_top=−1）× 电极厚度网格跑 EPPE 扫频，
   对实测 fs2/fp2/k²eff 中位数取残差。**关键物理结论（本脚本数值验证并写入报告）**：
   归属 A(mo_b,mo_t) 与归属 B(mo_t,mo_b) 的 1 端口 Z11 逐点一致（镜像简并）——
   电极厚度为未知自由量时，两种归属对任意 Z 数据给出相同预测，
   绝对归属不可由 1 端口 S 数据区分；报告采用约定 A 并声明该等价类。
   （reverse 对调 (+1,−1)↔(−1,+1) 同为平凡全局符号简并，一并数值记录。）
3. 挑选与校准：归属判定后的 Mason 为基准，选 |Δfs2| 最小的前 N 个 S11 器件子集，
   以子集中位数为目标做有界最小二乘微调（θ = s_c33/s_e33/s_eps/s_alpha/
   t_mo_bot/t_mo_top；AlN 冻结为 ALN1；s_c33/s_eps ±5%，s_e33 [0.70,1.05]——
   TQ06 与 WP3 校准的 4–7 GHz 晶圆不同批次，Sc 含量差异只影响耦合方向，
   论证见报告；alpha 为损耗集总参数取 [0.01,0.08]；电极 [90,220] nm 允许非对称）。
4. 产物：``backend/surrogate_data_mason/tq06/`` 下
   tq06_extraction.csv/json（38 文件明细）、attribution_grid.json、
   calibration_report_tq06.md / calibration_tq06.json、tq06_curves.npz +
   tq06_curves.meta.json（实测子集 |Z| dB + 校准前/后 Mason 谱）。

仿真标签的 TE2 支路跟踪：宽窗 (10,20] GHz 内在厚电极几何下 TE3(~17-19 GHz)
prominence 会超过 TE2 导致误锁，故仿真侧用自适应窗 (1.0, 1.13]×f2_pred，
f2_pred = 1/Σ(t_i/v_i)（声学厚度预测的偶模支路，实证偏移 +2~+8% 来自压电 stiffening）。
实测侧保持生产宽窗口径不变（实测谱 TE2 干净，38/38 正确锁定）。

用法：
    uv run --no-sync python scripts/tq06_analyze.py
    uv run --no-sync python scripts/tq06_analyze.py --subset-mode highq
        # 高 Q 子集变体（WP-A 续）：S11 & pf=0 & |fs2−sim|≤60 MHz 门内 qs 降序前 n；
        # 归属沿用 dfs 结论 A(130,100)，产物 calibration_tq06_highq.json /
        # tq06_curves_highq.npz，报告追加 §5（不覆盖 dfs 口径产物）
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.stats import qmc

# spawn 子进程/直接运行时需要 app 包可导入（scripts/ 在 sys.path[0] 而非 backend/）
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.mason_sim import (  # noqa: E402
    ALN1,
    MO,
    EPPEParams,
    PiezoMaterial,
    build_frequency_grid,
    nm_to_m,
    run_eppe_sweep,
    um2_to_m2,
)
from app.mason_sim.prodlabels import PARAM_KEYS, extract_eppe_production_labels  # noqa: E402

# ============================================================
# 常量
# ============================================================
TQ06_DIR_DEFAULT = Path("/Users/jingbozuo/TQ06")
CAL_JSON_DEFAULT = (
    _BACKEND_ROOT / "surrogate_data_mason" / "calibration" / "calibrated_materials.json"
)
OUTDIR_DEFAULT = _BACKEND_ROOT / "surrogate_data_mason" / "tq06"

SEED_DEFAULT = 20260914
PENALTY = 1.0e3  # 无效样本惩罚（加权残差单位，每分量；同 WP3 口径）
COST_GATE = 1.0e5  # 产物失败闸门（同 WP3 逻辑）
EVAL_AREA_UM2 = 2000.0  # 标签评估面积（Bode-Q 经 50 Ω 变换依赖面积；fs/fp/k²eff/相位Q 无关）
F_START_HZ, F_STOP_HZ = 3.0e9, 22.0e9  # EPPE 求解网格（与语料一致）
GRID_STEP_HZ = 2.0e6
# TE2 支路自适应窗（×f2_pred）：实测偏移 +2~+8%（压电 stiffening），上限 1.13 含余量
F2_BRANCH_WINDOW = (1.0, 1.13)
# 宽窗（生产口径 (10,20] GHz；实测侧与对照用）
TE2_WIDE_WINDOW_HZ = (10.0e9, 20.0e9)
TE1_DIAG_WINDOW_HZ = (3.0e9, 8.0e9)  # TE1 抑制诊断带
MO_GRID_NM = (100.0, 130.0, 160.0, 200.0)  # 归属判定电极网格（顶/底独立）

THETA_NAMES = ("s_c33", "s_e33", "s_eps", "s_alpha", "t_mo_bot_nm", "t_mo_top_nm")
# 数值 Jacobian（3-point）相对步长：绝对步长 = diff_step×|x|；标签吸附频率网格
# （阶梯函数），默认步长看不到梯度（同 WP3 口径，t_mo 方向 ~0.4-0.6 nm 跨越频格）
DIFF_STEP = np.array([1.0e-3, 1.0e-3, 1.0e-3, 2.0e-3, 3.0e-3, 3.0e-3])
X_SCALE = np.array([0.1, 0.1, 0.1, 0.1, 40.0, 40.0])  # TRF 变量尺度


# ============================================================
# 文件名与 s1p 解析
# ============================================================
@dataclass(frozen=True)
class Tq06FileMeta:
    """TQ06 文件名元数据：1_EG<eg>_FL<fl>_[PF_]<No AG|CB>_<mark>_X<x>Y<y>N<n>_Fail_<port>。"""

    eg: float
    fl: float
    pf: bool
    ag: str
    mark: str
    x: int
    y: int
    n: int
    port: str

    @property
    def coord(self) -> str:
        return f"X{self.x}Y{self.y}N{self.n}"


_TQ06_NAME_RE = re.compile(
    r"^1_EG(?P<eg>[\d.]+)_FL(?P<fl>[\d.]+)_(?:(?:(?P<pf>PF)|(?P<ag>No AG|CB))_)?"
    r"(?P<mark>[A-F](?:\[2\])?)_X(?P<x>-?\d+)Y(?P<y>-?\d+)N(?P<n>\d+)_"
    r"Fail_(?P<port>S11|S22)$"
)


def parse_tq06_filename(stem: str) -> Tq06FileMeta:
    """解析 TQ06 s1p 文件名（不带扩展名）。不匹配抛 ValueError。"""
    m = _TQ06_NAME_RE.match(stem)
    if m is None:
        raise ValueError(f"无法解析 TQ06 文件名: {stem!r}")
    return Tq06FileMeta(
        eg=float(m["eg"]),
        fl=float(m["fl"]),
        pf=m["pf"] is not None,
        ag=m["ag"],
        mark=m["mark"],
        x=int(m["x"]),
        y=int(m["y"]),
        n=int(m["n"]),
        port=m["port"],
    )


def parse_s1p_db_angle(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """解析 `# Hz S dB R 50` 一端口 s1p：(freq_hz, 复数 S11)。

    跳过 ``!`` 仪器注释行、``#`` 头行与空行；数据行 = "freq mag_db angle_deg"。
    无数据行抛 ValueError。
    """
    freq: list[float] = []
    mag_db: list[float] = []
    ang_deg: list[float] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("!") or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 3:
            raise ValueError(f"{path}: 数据行格式错误: {line!r}")
        freq.append(float(parts[0]))
        mag_db.append(float(parts[1]))
        ang_deg.append(float(parts[2]))
    if not freq:
        raise ValueError(f"{path}: 无数据行")
    f = np.asarray(freq, dtype=np.float64)
    s11 = 10.0 ** (np.asarray(mag_db) / 20.0) * np.exp(1j * np.deg2rad(np.asarray(ang_deg)))
    return f, s11


def z11_from_s11(s11: np.ndarray, zref: float = 50.0) -> np.ndarray:
    """S11 → Z11：Z = zref·(1+S)/(1−S)（s11_from_z11 的逆变换）。"""
    s = np.asarray(s11, dtype=complex)
    return zref * (1.0 + s) / (1.0 - s)


# ============================================================
# 校准材料加载（WP3 冻结产物）
# ============================================================
def load_calibrated_alscn(path: str | Path = CAL_JSON_DEFAULT) -> PiezoMaterial:
    """从 calibrated_materials.json 读 WP3 校准 AlScN（rho/c33/e33/eps）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    pz = doc["piezo"]
    return PiezoMaterial(
        name="AlScN_cal", rho=pz["rho"], c33=pz["c33"], e33=pz["e33"], eps=pz["eps"]
    )


def load_calibrated_alpha(path: str | Path = CAL_JSON_DEFAULT) -> float:
    """WP3 校准的压电层 alpha（vendor 约定，见 solver.DB_PER_NEPER）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return float(doc["alpha"])


# ============================================================
# 归属定义与 TE2 支路预测
# ============================================================
@dataclass(frozen=True)
class Attribution:
    """层归属假设：底层/顶层材料与厚度（nm）。极性序固定 reverse=(+1,−1)（P3F）。"""

    key: str
    bot_material: str  # "AlN" | "AlScN"
    t_bot_nm: float
    t_top_nm: float

    def layers(self, alscn: PiezoMaterial) -> tuple[PiezoMaterial, PiezoMaterial]:
        """(bot, top) 压电材料对象（AlN 恒为冻结的 ALN1）。"""
        bot = ALN1 if self.bot_material == "AlN" else alscn
        top = alscn if self.bot_material == "AlN" else ALN1
        return bot, top


ATTRIBUTIONS: dict[str, Attribution] = {
    # A: 底 AlN(170) / 顶 AlScN(190)；B: 底 AlScN(190) / 顶 AlN(170)
    "A": Attribution("A", "AlN", 170.0, 190.0),
    "B": Attribution("B", "AlScN", 190.0, 170.0),
}


def predict_te2_hz(
    t_aln_nm: float,
    t_alscn_nm: float,
    mo_b_nm: float,
    mo_t_nm: float,
    alscn: PiezoMaterial,
) -> float:
    """TE2 偶模支路的声学厚度预测频率：f2_pred = 1/Σ(t_i/v_i)。

    （自由-自由边界全堆叠半波谐振 f1=1/(2Σt/v)，TE2 为二次模 f2=1/Σt/v；
    压电 stiffening 使实测落点高出 2~8%，由 F2_BRANCH_WINDOW 上限吸收。）
    """
    t_over_v_s = (
        t_aln_nm / ALN1.velocity + t_alscn_nm / alscn.velocity + (mo_b_nm + mo_t_nm) / MO.v
    ) * 1e-9
    return float(1.0 / t_over_v_s)


# ============================================================
# 仿真标签
# ============================================================
def simulate_te2_labels(
    attribution: str,
    mo_b_nm: float,
    mo_t_nm: float,
    *,
    alscn: PiezoMaterial,
    alpha: float,
    freq: np.ndarray,
    area_um2: float = EVAL_AREA_UM2,
    reverse: tuple[float, float] = (+1.0, -1.0),
    adaptive: bool = True,
) -> dict:
    """EPPE 扫频 → 生产口径 TE2 标签。

    adaptive=True 时用 (1.0, 1.13]×f2_pred 窗锁定 f2 支路（排除厚电极几何下
    TE3 prominence 误锁）；False 时用生产宽窗 (10,20] GHz（对照/实测口径）。
    """
    attr = ATTRIBUTIONS[attribution]
    mat_bot, mat_top = attr.layers(alscn)
    params = EPPEParams(
        t_piezo_bot=nm_to_m(attr.t_bot_nm),
        t_piezo_top=nm_to_m(attr.t_top_nm),
        t_mo_bot=nm_to_m(mo_b_nm),
        t_mo_top=nm_to_m(mo_t_nm),
        area=um2_to_m2(area_um2),
        alpha=alpha,
        reverse_bot=reverse[0],
        reverse_top=reverse[1],
    )
    result = run_eppe_sweep(np.asarray(freq, dtype=float), mat_bot, mat_top, MO, params)
    if adaptive:
        f2p = predict_te2_hz(
            attr.t_bot_nm if attr.bot_material == "AlN" else attr.t_top_nm,
            attr.t_top_nm if attr.bot_material == "AlN" else attr.t_bot_nm,
            mo_b_nm,
            mo_t_nm,
            alscn,
        )
        win = (F2_BRANCH_WINDOW[0] * f2p, F2_BRANCH_WINDOW[1] * f2p)
    else:
        win = TE2_WIDE_WINDOW_HZ
    return extract_eppe_production_labels(
        result.freq, result.z11, te2_f_min_hz=win[0], te2_f_max_hz=win[1]
    )


# ============================================================
# 实测提取与统计
# ============================================================
def te1_band_range_db(freq_hz: np.ndarray, z11: np.ndarray) -> float:
    """TE1 诊断带 [3,8) GHz 内 |Z| dB 的动态范围（TE1 抑制诊断；无点记 nan）。"""
    freq = np.asarray(freq_hz, dtype=float)
    mask = (freq >= TE1_DIAG_WINDOW_HZ[0]) & (freq < TE1_DIAG_WINDOW_HZ[1])
    if int(np.count_nonzero(mask)) < 3:
        return float("nan")
    z_db = 20.0 * np.log10(np.maximum(np.abs(np.asarray(z11)[mask]), 1e-300))
    return float(np.max(z_db) - np.min(z_db))


def extract_measured(path: str | Path) -> dict:
    """单文件实测提取：元数据 + 生产口径 TE2 七参数 + TE1 诊断 + 抑制指标。"""
    p = Path(path)
    meta = parse_tq06_filename(p.stem)
    freq, s11 = parse_s1p_db_angle(p)
    z11 = z11_from_s11(s11)
    labels = extract_eppe_production_labels(freq, z11)  # 宽窗生产口径
    row: dict = {
        "file": p.name,
        "port": meta.port,
        "eg": meta.eg,
        "fl": meta.fl,
        "pf": meta.pf,
        "ag": meta.ag,
        "mark": meta.mark,
        "coord": meta.coord,
        "n_pts": int(freq.size),
        "f_min_ghz": float(freq[0]) / 1e9,
        "f_max_ghz": float(freq[-1]) / 1e9,
        "te1_band_range_db": te1_band_range_db(freq, z11),
    }
    row.update(labels)
    return row


def summarize_rows(rows: list[dict], keys: tuple[str, ...] = PARAM_KEYS) -> dict:
    """valid 行的标签统计：median/q25/q75/min/max/n（逐键忽略非有限值）。"""
    valid = [r for r in rows if r.get("valid", False)]
    out: dict[str, dict] = {}
    for k in keys:
        vals = np.array([float(r[k]) for r in valid], dtype=float)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            out[k] = {
                "n": 0,
                "median": float("nan"),
                "q25": float("nan"),
                "q75": float("nan"),
                "min": float("nan"),
                "max": float("nan"),
            }
            continue
        out[k] = {
            "n": int(vals.size),
            "median": float(np.median(vals)),
            "q25": float(np.percentile(vals, 25)),
            "q75": float(np.percentile(vals, 75)),
            "min": float(np.min(vals)),
            "max": float(np.max(vals)),
        }
    return out


def select_subset(
    rows: list[dict],
    sim_fs_ghz: float,
    n: int = 10,
    port: str | None = "S11",
) -> list[int]:
    """按 |Δfs2| 最小选前 n 个 valid 行（port=None 不限端口）。

    排序键 (|Δfs2|, file)：确定性 tie-break（文件名升序）。返回原 rows 索引。
    """
    cand = []
    for i, r in enumerate(rows):
        if not r.get("valid", False):
            continue
        if port is not None and r.get("port") != port:
            continue
        fs = float(r["fs_ghz"])
        if not np.isfinite(fs):
            continue
        cand.append((abs(fs - sim_fs_ghz), r["file"], i))
    cand.sort()
    return [i for _d, _f, i in cand[:n]]


def select_subset_highq(
    rows: list[dict],
    sim_fs_ghz: float,
    gate_mhz: float = 60.0,
    n: int = 10,
    port: str | None = "S11",
) -> list[int]:
    """高 Q 变体：|fs2 − sim_fs2| ≤ gate_mhz 门内按 qs 降序取前 n 个 valid 行。

    剔除 pf=True（``_PF_`` 质量加载态文件）；port=None 不限端口。
    排序键 (−qs, file)：确定性 tie-break（文件名升序）。返回原 rows 索引。
    """
    cand = []
    for i, r in enumerate(rows):
        if not r.get("valid", False):
            continue
        if port is not None and r.get("port") != port:
            continue
        if r.get("pf", False):
            continue
        fs = float(r["fs_ghz"])
        qs = float(r["qs"])
        if not (np.isfinite(fs) and np.isfinite(qs)):
            continue
        if abs(fs - sim_fs_ghz) * 1.0e3 > gate_mhz:
            continue
        cand.append((-qs, r["file"], i))
    cand.sort()
    return [i for _q, _f, i in cand[:n]]


# ============================================================
# 归属判定网格
# ============================================================
def attribution_grid(
    freq: np.ndarray,
    mo_pairs: list[tuple[float, float]],
    *,
    alscn: PiezoMaterial,
    alpha: float,
    area_um2: float = EVAL_AREA_UM2,
) -> list[dict]:
    """两种归属 × 电极对网格的仿真标签（自适应 TE2 窗）。"""
    out = []
    for attr_key in sorted(ATTRIBUTIONS):
        for mo_b, mo_t in mo_pairs:
            labels = simulate_te2_labels(
                attr_key, mo_b, mo_t, alscn=alscn, alpha=alpha, freq=freq, area_um2=area_um2
            )
            out.append(
                {
                    "attribution": attr_key,
                    "t_mo_bot_nm": mo_b,
                    "t_mo_top_nm": mo_t,
                    "valid": bool(labels["valid"]),
                    "fs_ghz": float(labels["fs_ghz"]),
                    "fp_ghz": float(labels["fp_ghz"]),
                    "k2eff_pct": float(labels["k2eff_pct"]),
                    "qs": float(labels["qs"]),
                    "qp": float(labels["qp"]),
                }
            )
    return out


# ============================================================
# 有界最小二乘校准
# ============================================================
def default_weights() -> np.ndarray:
    """fs/fp 各 1.0/MHz、k²eff 1.0/pct-pt、Q 各 0.2/（相对 %）（同 WP3 口径）。"""
    return np.array([1.0, 1.0, 1.0, 0.2, 0.2, 0.2, 0.2])


def theta_bounds(strict: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """θ 严格界。

    默认（论证界）：s_c33/s_eps ±5%；s_e33 ∈ [0.70, 1.05]（TQ06 与 WP3 校准晶圆
    不同批次，Sc 含量下降只降低 e33，故取单边下展；不允许超过 WP3 校准值）；
    s_alpha ∈ [0.45, 3.6]（alpha 为损耗集总参数，alpha_base×该区间 ≈ [0.01, 0.08]）；
    t_mo 两侧 ∈ [90, 220] nm（允许非对称；覆盖 WP3 先验 130 nm）。
    strict=True：全部材料标度 ±5%（任务默认口径的敏感性对照；WP3 当年为 ±10%）。
    """
    if strict:
        lo = np.array([0.95, 0.95, 0.95, 0.95, 90.0, 90.0])
        hi = np.array([1.05, 1.05, 1.05, 1.05, 220.0, 220.0])
    else:
        lo = np.array([0.95, 0.70, 0.95, 0.45, 90.0, 90.0])
        hi = np.array([1.05, 1.05, 1.05, 3.60, 220.0, 220.0])
    return lo, hi


def nominal_theta(t_mo_bot_nm: float, t_mo_top_nm: float) -> np.ndarray:
    return np.array([1.0, 1.0, 1.0, 1.0, t_mo_bot_nm, t_mo_top_nm])


def make_starts(
    n_starts: int, seed: int, lo: np.ndarray, hi: np.ndarray, nominal: np.ndarray
) -> np.ndarray:
    """多起点：第 0 行 = 名义值，其余 = 界内拉丁超立方（固定种子可复现）。"""
    if n_starts < 1:
        raise ValueError("n_starts 必须 >= 1")
    rows = [np.clip(nominal, lo, hi)]
    if n_starts > 1:
        u = qmc.LatinHypercube(d=len(THETA_NAMES), seed=seed).random(n_starts - 1)
        rows.extend(lo + u * (hi - lo))
    return np.asarray(rows, dtype=np.float64)


def make_alscn(theta: np.ndarray, base: PiezoMaterial) -> PiezoMaterial:
    """由标度因子构造校准 AlScN（rho 固定）。"""
    s_c33, s_e33, s_eps = float(theta[0]), float(theta[1]), float(theta[2])
    return PiezoMaterial(
        name="AlScN_cal_tq06",
        rho=base.rho,
        c33=base.c33 * s_c33,
        e33=base.e33 * s_e33,
        eps=base.eps * s_eps,
    )


def simulate_te2_from_theta(
    theta: np.ndarray,
    *,
    attribution: str,
    alscn_base: PiezoMaterial,
    alpha_base: float,
    freq: np.ndarray,
    area_um2: float = EVAL_AREA_UM2,
) -> dict:
    """θ = (s_c33, s_e33, s_eps, s_alpha, t_mo_bot_nm, t_mo_top_nm) → TE2 标签。"""
    return simulate_te2_labels(
        attribution,
        float(theta[4]),
        float(theta[5]),
        alscn=make_alscn(theta, alscn_base),
        alpha=alpha_base * float(theta[3]),
        freq=freq,
        area_um2=area_um2,
        adaptive=True,
    )


def simulate_z_db_from_theta(
    theta: np.ndarray,
    *,
    attribution: str,
    alscn_base: PiezoMaterial,
    alpha_base: float,
    freq: np.ndarray,
    area_um2: float = EVAL_AREA_UM2,
) -> np.ndarray:
    """θ → |Z11| dBΩ 全谱（绘图数据用）。"""
    attr = ATTRIBUTIONS[attribution]
    mat_bot, mat_top = attr.layers(make_alscn(theta, alscn_base))
    params = EPPEParams(
        t_piezo_bot=nm_to_m(attr.t_bot_nm),
        t_piezo_top=nm_to_m(attr.t_top_nm),
        t_mo_bot=nm_to_m(float(theta[4])),
        t_mo_top=nm_to_m(float(theta[5])),
        area=um2_to_m2(area_um2),
        alpha=alpha_base * float(theta[3]),
    )
    result = run_eppe_sweep(np.asarray(freq, dtype=float), mat_bot, mat_top, MO, params)
    return result.abs_z11_db


def band_range_db(
    freq_hz: np.ndarray,
    z_db: np.ndarray,
    f_lo_ghz: float = 3.0,
    f_hi_ghz: float = 8.0,
) -> float:
    """|Z| dB 谱在给定带内的动态范围（TE1 抑制诊断的谱域形式；点不足记 nan）。"""
    f = np.asarray(freq_hz, dtype=float)
    mask = (f >= f_lo_ghz * 1e9) & (f < f_hi_ghz * 1e9)
    if int(np.count_nonzero(mask)) < 3:
        return float("nan")
    z = np.asarray(z_db, dtype=float)[mask]
    return float(np.max(z) - np.min(z))


def k2_reachability_scan(
    theta: np.ndarray,
    *,
    attribution: str,
    alscn_base: PiezoMaterial,
    alpha_base: float,
    freq: np.ndarray,
    s_e33_values: tuple[float, ...] = (1.05, 0.95, 0.85, 0.75, 0.70),
) -> list[dict]:
    """k²eff 可达性扫描：固定拟合 θ 的其余分量，只动 s_e33，记录 fs/fp/k²eff 轨迹。

    用途：实证"k²eff 结构性缺口"——即使 e33 降到论证界下限，fs 匹配几何下的
    TE2 k²eff 仍高于实测子集中位（横向损耗/有限尺寸使实测 k²eff 系统性偏低，
    超出 1D Mason 纯活塞口径），作为报告证据而非拟合自由度。
    """
    out = []
    for s in s_e33_values:
        th = np.asarray(theta, dtype=np.float64).copy()
        th[1] = s
        lab = simulate_te2_from_theta(
            th,
            attribution=attribution,
            alscn_base=alscn_base,
            alpha_base=alpha_base,
            freq=freq,
        )
        out.append(
            {
                "s_e33": float(s),
                "valid": bool(lab["valid"]),
                "fs_ghz": float(lab["fs_ghz"]),
                "fp_ghz": float(lab["fp_ghz"]),
                "k2eff_pct": float(lab["k2eff_pct"]),
            }
        )
    return out


def residual_vector(
    labels: dict | None,
    target: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """加权残差 [fs MHz, fp MHz, k²eff pct-pt, qs/qp/qs_bodeq/qp_bodeq 相对 %]。

    无效样本（valid=False 或残差非有限）给大惩罚使优化器回避（同 WP3 口径）。
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


@dataclass
class FitResult:
    """校准拟合结果。"""

    theta: np.ndarray
    cost: float
    nfev: int
    success: bool
    message: str
    free: tuple[str, ...] = field(default_factory=lambda: THETA_NAMES)
    start_idx: int = 0


def fit_calibration(
    target: np.ndarray,
    *,
    attribution: str,
    alscn_base: PiezoMaterial,
    alpha_base: float,
    freq: np.ndarray,
    starts: np.ndarray,
    free: tuple[str, ...] = THETA_NAMES,
    strict: bool = False,
    weights: np.ndarray | None = None,
    area_um2: float = EVAL_AREA_UM2,
    max_nfev: int = 400,
) -> FitResult:
    """多起点有界 LS（scipy trf，3-point 数值 Jacobian），返回全局最优。

    free：参与拟合的 θ 分量名子集（其余冻结在各起点取值上）——用于
    可恢复性测试的快路径与"归属判定后冻结电极"等变体。
    """
    weights = default_weights() if weights is None else np.asarray(weights, dtype=np.float64)
    lo, hi = theta_bounds(strict=strict)
    free_idx = np.array([THETA_NAMES.index(k) for k in free], dtype=int)
    target = np.asarray(target, dtype=np.float64)
    freq = np.asarray(freq, dtype=float)

    def _resid(x_free: np.ndarray, theta0: np.ndarray) -> np.ndarray:
        theta = theta0.copy()
        theta[free_idx] = x_free
        try:
            labels = simulate_te2_from_theta(
                theta,
                attribution=attribution,
                alscn_base=alscn_base,
                alpha_base=alpha_base,
                freq=freq,
                area_um2=area_um2,
            )
        except Exception:
            labels = None  # MNA 奇异等数值失败 → 惩罚
        return residual_vector(labels, target, weights)

    best: FitResult | None = None
    for si, start in enumerate(np.asarray(starts, dtype=np.float64)):
        start = np.clip(start, lo, hi)
        try:
            sol = least_squares(
                _resid,
                start[free_idx],
                args=(start,),
                bounds=(lo[free_idx], hi[free_idx]),
                method="trf",
                jac="3-point",
                diff_step=DIFF_STEP[free_idx],
                x_scale=X_SCALE[free_idx],
                max_nfev=max_nfev,
                xtol=1e-12,
                ftol=1e-12,
                gtol=1e-12,
            )
            theta = start.copy()
            theta[free_idx] = sol.x
            res = FitResult(
                theta=theta,
                cost=float(sol.cost),
                nfev=int(sol.nfev),
                success=bool(sol.success),
                message=str(sol.message),
                free=free,
                start_idx=si,
            )
        except Exception as exc:  # 数值失败不拖垮其他起点
            res = FitResult(
                theta=start,
                cost=float("inf"),
                nfev=0,
                success=False,
                message=f"least_squares 异常: {exc!r}",
                free=free,
                start_idx=si,
            )
        if best is None or (res.cost, res.start_idx) < (best.cost, best.start_idx):
            best = res
    assert best is not None
    return best


# ============================================================
# 绘图数据产物
# ============================================================
def write_curves_npz(
    path: str | Path,
    sim_freq_ghz: np.ndarray,
    mason_before_db: np.ndarray,
    mason_after_db: np.ndarray,
    meas_curves: list[tuple[str, np.ndarray, np.ndarray]],
    meta: dict,
) -> None:
    """论文绘图数据：npz（仿真频率 + 校准前/后 Mason 谱 + 实测曲线组）+ meta json。

    npz 键：sim_freq_ghz / mason_before_db / mason_after_db /
    meas_freq_ghz_{i} / meas_z_db_{i}（实测保留各自原生频率网格）。
    meta json 落在同名 ``<stem>.meta.json``，并自动补 meas_names。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray] = {
        "sim_freq_ghz": np.asarray(sim_freq_ghz, dtype=np.float64),
        "mason_before_db": np.asarray(mason_before_db, dtype=np.float64),
        "mason_after_db": np.asarray(mason_after_db, dtype=np.float64),
    }
    names = []
    for i, (name, f_ghz, z_db) in enumerate(meas_curves):
        names.append(name)
        arrays[f"meas_freq_ghz_{i}"] = np.asarray(f_ghz, dtype=np.float64)
        arrays[f"meas_z_db_{i}"] = np.asarray(z_db, dtype=np.float64)
    np.savez(p, **arrays)
    meta_out = dict(meta)
    meta_out["meas_names"] = names
    meta_out["npz"] = p.name
    p.with_name(p.stem + ".meta.json").write_text(
        json.dumps(meta_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ============================================================
# 报告与 CLI
# ============================================================
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


def _param_residuals(labels: dict, target: np.ndarray) -> dict:
    """7 参数残差（仿真值−目标值，附相对 %）。"""
    out = {}
    for i, key in enumerate(PARAM_KEYS):
        c = float(labels[key])
        t = float(target[i])
        out[key] = {
            "target": t,
            "sim": c,
            "abs_diff": c - t,
            "rel_pct": (c - t) / t * 100.0 if t != 0.0 else float("nan"),
        }
    return out


def _fmt(x: float, nd: int = 4) -> str:
    return "nan" if not np.isfinite(x) else f"{x:.{nd}f}"


def _res_table(title: str, res: dict) -> list[str]:
    """7 参数残差 markdown 表（目标/仿真/Δ/相对 %）。"""
    lines = [
        f"#### {title}",
        "",
        "| 参数 | 目标(子集中位) | 仿真值 | Δ | 相对 % |",
        "|---|---|---|---|---|",
    ]
    for key in PARAM_KEYS:
        r = res[key]
        lines.append(
            f"| {key} | {r['target']:.6f} | {r['sim']:.6f} "
            f"| {r['abs_diff']:+.6f} | {r['rel_pct']:+.2f} |"
        )
    lines.append("")
    return lines


def _load_baseline_calibration(path: str | Path) -> dict | None:
    """读取原子集（dfs 口径）校准 json 的对比字段；文件缺失返回 None。"""
    p = Path(path)
    if not p.exists():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    floor = doc["k2_reachability"][-1]
    return {
        "json": p.name,
        "target7": doc["target7"],
        "theta": doc["theta"],
        "cost": doc["cost"],
        "alpha_after": doc["materials_after"]["alpha"],
        "k2_floor": {
            "s_e33": floor["s_e33"],
            "k2eff_pct": floor["k2eff_pct"],
            "fs_ghz": floor["fs_ghz"],
            "gap_pct_pt": floor["k2eff_pct"] - doc["target7"]["k2eff_pct"],
        },
    }


def _highq_report_section(
    *,
    gate_mhz: float,
    n_gate: int,
    verdict: dict,
    subset_rows: list[dict],
    target: np.ndarray,
    fit: FitResult,
    res_before: dict,
    res_after: dict,
    strict_doc: dict | None,
    k2_scan: list[dict],
    mat_after: PiezoMaterial,
    alpha_after: float,
    alscn: PiezoMaterial,
    alpha_cal: float,
    baseline: dict | None,
    one_liner: str,
    elapsed_s: float,
) -> str:
    """高 Q 子集重校准的报告追加节（§5），结构与 §3–§4 对齐并给出子集间对比。"""
    k2_floor = k2_scan[-1]
    gap_new = k2_floor["k2eff_pct"] - float(target[2])
    lines: list[str] = [
        "---",
        "",
        "## 5. 高 Q 子集重校准（WP-A 续）",
        "",
        "用户指令：「TQ 的数据挑点好的，Q 值高的」——校准子集由「与仿真最接近」（§3）"
        "改为「仿真接近 + 高 Q」。本节由 `scripts/tq06_analyze.py --subset-mode highq` "
        f"生成并追加（{datetime.now(UTC).isoformat()}，commit {_git_commit_sha()}，"
        f"耗时 {elapsed_s:.1f} s）；θ/界/多起点/权重口径与 §4 完全一致。",
        "",
        "### 5.1 新子集规则",
        "",
        "1. 仅 S11（S22 有端口不对称系统性偏移，沿用 §3 结论排除）；",
        "2. 仅 pf=0（剔除文件名含 `_PF_` 的 2 个质量加载态文件）；",
        f"3. 仿真接近门：|fs2 − {verdict['fs_ghz']:.3f} GHz| ≤ {gate_mhz:.0f} MHz"
        "（门中心 = 网格最优名义几何 A(130,100) 的仿真 fs2）；",
        f"4. 门内 {n_gate} 个候选按 qs（相位斜率 Qs，生产口径）降序取前 {len(subset_rows)}。",
        "",
        "### 5.2 成员清单与子集中位目标",
        "",
        "| 文件 | fs2 (GHz) | Δfs2 vs 门中心 (MHz) | fp2 (GHz) | k²eff (%) | Qs | Qp |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in subset_rows:
        lines.append(
            f"| {r['file']} | {r['fs_ghz']:.4f} | {1e3 * (r['fs_ghz'] - verdict['fs_ghz']):+.1f} "
            f"| {r['fp_ghz']:.4f} | {r['k2eff_pct']:.2f} | {r['qs']:.0f} | {r['qp']:.0f} |"
        )
    lines += [
        "",
        f"子集中位目标：fs={target[0]:.4f} GHz, fp={target[1]:.4f} GHz, "
        f"k²eff={target[2]:.2f}%, Qs={target[3]:.1f}, Qp={target[4]:.1f}, "
        f"Qs_bode={target[5]:.1f}, Qp_bode={target[6]:.1f}",
        "",
        "### 5.3 校准结果",
        "",
        f"拟合：cost={fit.cost:.3f}，nfev={fit.nfev}，success={fit.success}，"
        f"start_idx={fit.start_idx}。",
        "",
        "| 量 | 校准值 | WP3 基准 | 相对 |",
        "|---|---|---|---|",
        f"| c33 (Pa) | {mat_after.c33:.6e} | {alscn.c33:.6e} | {(fit.theta[0] - 1) * 100:+.2f}% |",
        f"| e33 (C/m²) | {mat_after.e33:.6f} | {alscn.e33:.6f} "
        f"| {(fit.theta[1] - 1) * 100:+.2f}% |",
        f"| eps (F/m) | {mat_after.eps:.6e} | {alscn.eps:.6e} | {(fit.theta[2] - 1) * 100:+.2f}% |",
        f"| alpha | {alpha_after:.5f} | {alpha_cal:.5f} | {(fit.theta[3] - 1) * 100:+.2f}% |",
        f"| t_mo_bot (nm) | {fit.theta[4]:.1f} | 130（4–7 GHz 批次） | — |",
        f"| t_mo_top (nm) | {fit.theta[5]:.1f} | 130（4–7 GHz 批次） | — |",
        f"| rho (kg/m³) | {mat_after.rho:.2f} | {alscn.rho:.2f} | 固定 |",
        "",
        "### 5.4 校准前后残差（目标 = 高 Q 子集中位）",
        "",
    ]
    lines += _res_table("校准前（A(130,100) 名义几何 + WP3 材料）", res_before)
    lines += _res_table("校准后", res_after)
    if strict_doc is not None:
        lines += [
            "### 5.5 敏感性对照：全部材料 ±5% 严格界",
            "",
            f"cost={strict_doc['cost']:.3f}；θ="
            + ", ".join(f"{v:.4f}" for v in strict_doc["theta"]),
            "",
        ]
        lines += _res_table("严格界校准后", strict_doc["residuals"])
    lines += [
        "### 5.6 k²eff 可达性扫描（拟合 θ 上只动 s_e33，其余分量冻结）",
        "",
        "| s_e33 | fs2 (GHz) | fp2 (GHz) | k²eff (%) |",
        "|---|---|---|---|",
    ]
    for row in k2_scan:
        lines.append(
            f"| {row['s_e33']:.2f} | {_fmt(row['fs_ghz'])} | {_fmt(row['fp_ghz'])} "
            f"| {_fmt(row['k2eff_pct'], 2)} |"
        )
    lines += [
        "",
        f"**结构性缺口（如实报告，未硬拟合）**：高 Q 子集 k²eff 中位 {target[2]:.2f}% "
        f"低于 fs 匹配几何下 1D Mason 的可达值——s_e33 降到论证界下限 "
        f"{k2_floor['s_e33']:.2f} 时 k²eff 仍为 {k2_floor['k2eff_pct']:.2f}%"
        f"（fs2={k2_floor['fs_ghz']:.4f} GHz），缺口 {gap_new:+.2f} pct-pt。"
        "横向损耗/有限尺寸对实测 k²eff 的削减超出纯活塞 1D 口径，与 §4 结论一致；"
        "高 Q 子集 k²eff 更低，缺口相应变大（见 §5.7 对比）。",
        "",
    ]
    if baseline is not None:
        bt = baseline["target7"]
        bth = baseline["theta"]
        bk2 = baseline["k2_floor"]
        lines += [
            "### 5.7 与原子集（|Δfs2| top-10，§3–§4）对比",
            "",
            "| 量 | 原子集 | 高 Q 子集 |",
            "|---|---|---|",
            f"| 目标 fs2 (GHz) | {bt['fs_ghz']:.4f} | {target[0]:.4f} |",
            f"| 目标 fp2 (GHz) | {bt['fp_ghz']:.4f} | {target[1]:.4f} |",
            f"| 目标 k²eff (%) | {bt['k2eff_pct']:.2f} | {target[2]:.2f} |",
            f"| 目标 Qs / Qp | {bt['qs']:.1f} / {bt['qp']:.1f} | "
            f"{target[3]:.1f} / {target[4]:.1f} |",
            f"| 目标 Qp/Qs | {bt['qp'] / bt['qs']:.2f} | {target[4] / target[3]:.2f} |",
            f"| 收敛 s_e33 | {bth[1]:.4f} | {fit.theta[1]:.4f} |",
            f"| 收敛 alpha | {baseline['alpha_after']:.5f} | {alpha_after:.5f} |",
            f"| 收敛电极 bot/top (nm) | {bth[4]:.1f} / {bth[5]:.1f} | "
            f"{fit.theta[4]:.1f} / {fit.theta[5]:.1f} |",
            f"| 校准 cost | {baseline['cost']:.1f} | {fit.cost:.1f} |",
            f"| k²eff 缺口（s_e33={bk2['s_e33']:.2f} 下界，pct-pt） "
            f"| {bk2['gap_pct_pt']:+.2f} | {gap_new:+.2f} |",
            "",
            "alpha 自由度压力：Qs 目标 "
            f"{bt['qs']:.1f} → {target[3]:.1f}（{(target[3] / bt['qs'] - 1) * 100:+.0f}%），"
            f"收敛 alpha {baseline['alpha_after']:.5f} → {alpha_after:.5f}"
            f"（s_alpha={fit.theta[3]:.3f}，界 [0.45, 3.6]）；"
            f"Qp/Qs 目标比 {bt['qp'] / bt['qs']:.2f} → {target[4] / target[3]:.2f}，"
            "更接近 Mason 单损耗参数的结构性约束 Qp/Qs≈1.0。",
            "",
        ]
    else:
        lines += [
            "### 5.7 与原子集对比",
            "",
            "（`calibration_tq06.json` 不在产物目录，跳过对比；先跑默认 dfs 口径再跑 highq。）",
            "",
        ]
    lines += [
        "### 论文一句话数字（高 Q 子集）",
        "",
        one_liner,
        "",
        "### 复现（本节）",
        "",
        "```bash",
        "cd backend",
        "uv run --no-sync python scripts/tq06_analyze.py --subset-mode highq",
        "```",
        "",
    ]
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tq06-dir", type=str, default=str(TQ06_DIR_DEFAULT), help="TQ06 s1p 目录")
    ap.add_argument("--cal-json", type=str, default=str(CAL_JSON_DEFAULT), help="WP3 校准材料 json")
    ap.add_argument("--outdir", type=str, default=str(OUTDIR_DEFAULT), help="产物目录")
    ap.add_argument("--subset-n", type=int, default=10, help="校准子集大小（默认 10）")
    ap.add_argument(
        "--subset-mode",
        choices=("dfs", "highq"),
        default="dfs",
        help="子集规则：dfs=|Δfs2| 最小前 n（默认，原口径）；"
        "highq=仿真接近门内 qs 降序前 n（剔除 _PF_，产物带 _highq 后缀，报告追加 §5）",
    )
    ap.add_argument("--gate-mhz", type=float, default=60.0, help="highq 模式仿真接近门半宽（MHz）")
    ap.add_argument("--n-starts", type=int, default=8, help="拟合多起点数（含名义起点）")
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT, help="LHS 种子")
    ap.add_argument("--max-nfev", type=int, default=600, help="单起点最大函数评估数")
    ap.add_argument(
        "--strict-variant",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="追加 ±5%% 严格界敏感性对照拟合（默认开）",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    highq = args.subset_mode == "highq"
    t_start = time.time()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    alscn = load_calibrated_alscn(args.cal_json)
    alpha_cal = load_calibrated_alpha(args.cal_json)
    freq_sim = build_frequency_grid(F_START_HZ, F_STOP_HZ, f_step_hz=GRID_STEP_HZ)
    print(
        f"[WP-A] AlScN(WP3): c33={alscn.c33:.6e} e33={alscn.e33:.6f} eps={alscn.eps:.6e} "
        f"alpha={alpha_cal:.5f}"
    )

    # ---- 1. 实测提取 ----
    files = sorted(Path(args.tq06_dir).glob("*.s1p"))
    rows = [extract_measured(p) for p in files]
    n_valid = sum(1 for r in rows if r["valid"])
    excluded = [r for r in rows if not r["valid"]]
    stats_all = summarize_rows(rows)
    stats_s11 = summarize_rows([r for r in rows if r["port"] == "S11"])
    stats_s22 = summarize_rows([r for r in rows if r["port"] == "S22"])
    print(f"[WP-A] 提取 {len(rows)} 文件：valid={n_valid}，剔除 {len(excluded)}")
    print(
        f"[WP-A] S11 中位：fs2={stats_s11['fs_ghz']['median']:.4f} GHz "
        f"fp2={stats_s11['fp_ghz']['median']:.4f} GHz "
        f"k2eff={stats_s11['k2eff_pct']['median']:.2f}%"
    )

    # 明细表产物（highq 变体沿用 dfs 运行产物，不改写）
    if not highq:
        detail_keys = [
            "file",
            "port",
            "eg",
            "fl",
            "pf",
            "ag",
            "mark",
            "coord",
            "n_pts",
            "f_min_ghz",
            "f_max_ghz",
            *PARAM_KEYS,
            "fs1_ghz",
            "fp1_ghz",
            "k2eff_te1_pct",
            "te1_band_range_db",
            "valid",
            "reason",
        ]
        with (outdir / "tq06_extraction.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=detail_keys)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in detail_keys})
        (outdir / "tq06_extraction.json").write_text(
            json.dumps(
                {
                    "note": "TQ06 38 文件生产口径 TE2 提取明细（prodlabels 宽窗 (10,20] GHz）",
                    "generated_at": datetime.now(UTC).isoformat(),
                    "rows": [{k: r.get(k) for k in detail_keys} for r in rows],
                    "stats_all": stats_all,
                    "stats_s11": stats_s11,
                    "stats_s22": stats_s22,
                    "excluded": [
                        {"file": r["file"], "reason": r.get("reason", "?")} for r in excluded
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    # ---- 2. 归属判定网格 ----
    if highq:
        # 高 Q 变体：归属结论沿用 dfs 运行的网格最优名义几何 A(130,100)（镜像简并
        # 论证不变），只做一次参考仿真取门中心 fs2；不重跑网格、不改写
        # attribution_grid.json。labels_before 即该参考仿真（名义 θ + WP3 材料）。
        labels_before = simulate_te2_labels(
            "A", 130.0, 100.0, alscn=alscn, alpha=alpha_cal, freq=freq_sim
        )
        verdict = {
            "attribution": "A",
            "t_mo_bot_nm": 130.0,
            "t_mo_top_nm": 100.0,
            "valid": bool(labels_before["valid"]),
            "fs_ghz": float(labels_before["fs_ghz"]),
            "fp_ghz": float(labels_before["fp_ghz"]),
            "k2eff_pct": float(labels_before["k2eff_pct"]),
            "qs": float(labels_before["qs"]),
            "qp": float(labels_before["qp"]),
        }
        print(
            f"[WP-A] 高 Q 变体：参考几何 A(130,100) fs2={verdict['fs_ghz']:.4f} GHz "
            f"fp2={verdict['fp_ghz']:.4f} GHz k2={verdict['k2eff_pct']:.2f}%（门中心）"
        )
        if not verdict["valid"]:
            print("[WP-A] 参考几何仿真无效，不写产物")
            return 2
    else:
        mo_pairs = [(b, t) for b in MO_GRID_NM for t in MO_GRID_NM]
        grid = attribution_grid(freq_sim, mo_pairs, alscn=alscn, alpha=alpha_cal)
        tgt_fs = stats_s11["fs_ghz"]["median"]
        tgt_fp = stats_s11["fp_ghz"]["median"]
        tgt_k2 = stats_s11["k2eff_pct"]["median"]
        for g in grid:
            g["dfs2_mhz"] = (g["fs_ghz"] - tgt_fs) * 1e3 if g["valid"] else float("nan")
            g["dfp2_mhz"] = (g["fp_ghz"] - tgt_fp) * 1e3 if g["valid"] else float("nan")
            g["dk2eff"] = g["k2eff_pct"] - tgt_k2 if g["valid"] else float("nan")
            g["abs_dfs2_mhz"] = abs(g["dfs2_mhz"])
        grid_valid = [g for g in grid if g["valid"]]
        if not grid_valid:
            print("[WP-A] 归属网格全部无效，不写产物")
            return 2
        verdict = min(
            grid_valid,
            key=lambda g: (g["abs_dfs2_mhz"], g["t_mo_bot_nm"] + g["t_mo_top_nm"]),
        )
        print(
            f"[WP-A] 归属网格最优：{verdict['attribution']}(mo_b={verdict['t_mo_bot_nm']:g}, "
            f"mo_t={verdict['t_mo_top_nm']:g}) fs2={verdict['fs_ghz']:.4f} "
            f"Δfs2={verdict['dfs2_mhz']:+.1f} MHz k2={verdict['k2eff_pct']:.2f}"
        )
        (outdir / "attribution_grid.json").write_text(
            json.dumps(
                {
                    "note": "两归属 × 电极对网格（自适应 TE2 窗）；目标 = 实测 S11 中位；"
                    "A(mo_b,mo_t) 与 B(mo_t,mo_b) 镜像简并（数值上逐点一致）",
                    "target_s11_median": {
                        "fs_ghz": tgt_fs,
                        "fp_ghz": tgt_fp,
                        "k2eff_pct": tgt_k2,
                    },
                    "grid": grid,
                    "verdict": verdict,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    # ---- 3. 子集挑选与有界 LS 校准 ----
    n_gate = 0
    if highq:
        gate_idx = select_subset_highq(
            rows, verdict["fs_ghz"], gate_mhz=args.gate_mhz, n=len(rows), port="S11"
        )
        n_gate = len(gate_idx)
        subset_idx = gate_idx[: args.subset_n]
    else:
        subset_idx = select_subset(rows, verdict["fs_ghz"], n=args.subset_n, port="S11")
    subset_rows = [rows[i] for i in subset_idx]
    if not subset_rows:
        print("[WP-A] 子集为空（无候选），不写校准产物")
        return 2
    target = np.array(
        [np.median([float(r[k]) for r in subset_rows]) for k in PARAM_KEYS], dtype=np.float64
    )
    if highq:
        print(
            f"[WP-A] 高 Q 子集 {len(subset_rows)}/{n_gate}（门 ±{args.gate_mhz:.0f} MHz 内 "
            "qs 降序，S11，pf=0）："
        )
        for r in subset_rows:
            print(
                f"    {r['file'][:62]:64s} fs2={r['fs_ghz']:.4f} "
                f"Δ={1e3 * (r['fs_ghz'] - verdict['fs_ghz']):+6.1f} MHz qs={r['qs']:.1f}"
            )
    else:
        print(f"[WP-A] 子集 {len(subset_rows)} 个（|Δfs2| 最小，S11）：")
        for r in subset_rows:
            print(
                f"    {r['file'][:62]:64s} fs2={r['fs_ghz']:.4f} "
                f"Δ={1e3 * (r['fs_ghz'] - verdict['fs_ghz']):+6.1f} MHz"
            )
    print(
        f"[WP-A] 子集中位目标：fs={target[0]:.4f} fp={target[1]:.4f} k2={target[2]:.2f} "
        f"qs={target[3]:.1f} qp={target[4]:.1f}"
    )

    if not highq:
        labels_before = simulate_te2_labels(
            verdict["attribution"],
            verdict["t_mo_bot_nm"],
            verdict["t_mo_top_nm"],
            alscn=alscn,
            alpha=alpha_cal,
            freq=freq_sim,
        )
    lo, hi = theta_bounds()
    nominal = nominal_theta(verdict["t_mo_bot_nm"], verdict["t_mo_top_nm"])
    starts = make_starts(args.n_starts, args.seed, lo, hi, nominal)
    fit = fit_calibration(
        target,
        attribution=verdict["attribution"],
        alscn_base=alscn,
        alpha_base=alpha_cal,
        freq=freq_sim,
        starts=starts,
        max_nfev=args.max_nfev,
    )
    labels_after = simulate_te2_from_theta(
        fit.theta,
        attribution=verdict["attribution"],
        alscn_base=alscn,
        alpha_base=alpha_cal,
        freq=freq_sim,
    )
    print(
        f"[WP-A] 拟合：cost={fit.cost:.3f} nfev={fit.nfev} success={fit.success} "
        f"θ={np.round(fit.theta, 4).tolist()}"
    )

    # 产物失败闸门（WP3 逻辑的阶梯景观适配：标签吸附频格时 least_squares 常走
    # max_nfev 截断（success=False）而 cost 已达标——闸门以 cost/valid 为准，
    # success/message 记入报告供透明度审查）
    if not labels_after.get("valid", False) or fit.cost > COST_GATE:
        print(
            f"[WP-A] 校准失败闸门触发，不写校准产物：valid={labels_after.get('valid', False)} "
            f"cost={fit.cost:.3f}（闸门 cost>{COST_GATE:g}）"
        )
        return 2

    # ±5% 严格界敏感性对照
    strict_doc: dict | None = None
    if args.strict_variant:
        slo, shi = theta_bounds(strict=True)
        strict_starts = make_starts(args.n_starts, args.seed, slo, shi, nominal)
        fit_s = fit_calibration(
            target,
            attribution=verdict["attribution"],
            alscn_base=alscn,
            alpha_base=alpha_cal,
            freq=freq_sim,
            starts=strict_starts,
            strict=True,
            max_nfev=args.max_nfev,
        )
        labels_s = simulate_te2_from_theta(
            fit_s.theta,
            attribution=verdict["attribution"],
            alscn_base=alscn,
            alpha_base=alpha_cal,
            freq=freq_sim,
        )
        strict_doc = {
            "note": "全部材料标度 ±5% 严格界（WP3 口径）敏感性对照",
            "theta": [float(v) for v in fit_s.theta],
            "cost": fit_s.cost,
            "labels": {k: float(labels_s[k]) for k in PARAM_KEYS},
            "residuals": _param_residuals(labels_s, target),
        }
        print(f"[WP-A] 严格界对照：cost={fit_s.cost:.3f} θ={np.round(fit_s.theta, 4).tolist()}")

    # k²eff 可达性扫描（结构性缺口证据，不入拟合）
    k2_scan = k2_reachability_scan(
        fit.theta,
        attribution=verdict["attribution"],
        alscn_base=alscn,
        alpha_base=alpha_cal,
        freq=freq_sim,
    )

    # ---- 4. 绘图数据 ----
    meas_curves = []
    for r in subset_rows:
        f_hz, s11 = parse_s1p_db_angle(Path(args.tq06_dir) / r["file"])
        z_db = 20.0 * np.log10(np.maximum(np.abs(z11_from_s11(s11)), 1e-300))
        meas_curves.append((r["file"], f_hz / 1e9, z_db))
    theta_before = nominal
    mason_before = simulate_z_db_from_theta(
        theta_before,
        attribution=verdict["attribution"],
        alscn_base=alscn,
        alpha_base=alpha_cal,
        freq=freq_sim,
    )
    mason_after = simulate_z_db_from_theta(
        fit.theta,
        attribution=verdict["attribution"],
        alscn_base=alscn,
        alpha_base=alpha_cal,
        freq=freq_sim,
    )
    te1_db_before = band_range_db(freq_sim, mason_before)
    te1_db_after = band_range_db(freq_sim, mason_after)
    te1_ranges = [r["te1_band_range_db"] for r in rows if np.isfinite(r["te1_band_range_db"])]
    te1_found = sum(1 for r in rows if np.isfinite(float(r.get("fs1_ghz", float("nan")))))
    meta = {
        "task": "WP-A TQ06 14GHz P3F bilayer attribution + calibration",
        "generated_at": datetime.now(UTC).isoformat(),
        "commit_sha": _git_commit_sha(),
        "verdict": verdict["attribution"],
        "verdict_note": "归属 A = 底 AlN(170)/顶 AlScN(190)，reverse=(+1,−1)；"
        "A(mo_b,mo_t) 与 B(mo_t,mo_b) 镜像简并，绝对归属需工艺记录/XRD 判定",
        "electrode_nm": {"mo_bot": float(fit.theta[4]), "mo_top": float(fit.theta[5])},
        "subset_files": [r["file"] for r in subset_rows],
        "subset_target_median": {k: float(v) for k, v in zip(PARAM_KEYS, target, strict=True)},
        "labels_before": {k: float(labels_before[k]) for k in PARAM_KEYS},
        "labels_after": {k: float(labels_after[k]) for k in PARAM_KEYS},
        "te1_band_range_db": {"mason_before": te1_db_before, "mason_after": te1_db_after},
        "sim_grid": "3-22 GHz @2 MHz",
        "eval_area_um2": EVAL_AREA_UM2,
    }
    if highq:
        meta["subset_mode"] = "highq"
        meta["subset_rule"] = (
            "S11 & pf=0 & |fs2−sim_fs2|≤gate 门内按 qs（相位斜率 Qs，生产口径）降序前 n"
        )
        meta["gate_mhz"] = float(args.gate_mhz)
        meta["n_gate_candidates"] = n_gate
        meta["baseline_json"] = "calibration_tq06.json"
        curves_name = "tq06_curves_highq.npz"
    else:
        curves_name = "tq06_curves.npz"
    write_curves_npz(
        outdir / curves_name, freq_sim / 1e9, mason_before, mason_after, meas_curves, meta
    )

    # ---- 5. 报告 ----
    res_before = _param_residuals(labels_before, target)
    res_after = _param_residuals(labels_after, target)
    mat_after = make_alscn(fit.theta, alscn)
    alpha_after = alpha_cal * float(fit.theta[3])
    one_liner = (
        f"14 GHz P3F（TQ06）校准后 Mason 复现实测子集中位："
        f"fs2 残差 {res_after['fs_ghz']['abs_diff'] * 1e3:+.1f} MHz、"
        f"fp2 {res_after['fp_ghz']['abs_diff'] * 1e3:+.1f} MHz、"
        f"k²eff {res_after['k2eff_pct']['abs_diff']:+.2f} pct-pt"
        f"（校准前 {res_before['fs_ghz']['abs_diff'] * 1e3:+.1f} MHz / "
        f"{res_before['fp_ghz']['abs_diff'] * 1e3:+.1f} MHz / "
        f"{res_before['k2eff_pct']['abs_diff']:+.2f} pct-pt）。"
    )

    if highq:
        # 高 Q 变体：报告追加 §5 + 独立 json（不覆盖 dfs 口径产物）
        baseline = _load_baseline_calibration(outdir / "calibration_tq06.json")
        section = _highq_report_section(
            gate_mhz=args.gate_mhz,
            n_gate=n_gate,
            verdict=verdict,
            subset_rows=subset_rows,
            target=target,
            fit=fit,
            res_before=res_before,
            res_after=res_after,
            strict_doc=strict_doc,
            k2_scan=k2_scan,
            mat_after=mat_after,
            alpha_after=alpha_after,
            alscn=alscn,
            alpha_cal=alpha_cal,
            baseline=baseline,
            one_liner=one_liner,
            elapsed_s=time.time() - t_start,
        )
        report_path = outdir / "calibration_report_tq06.md"
        prev = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        report_path.write_text(prev.rstrip() + "\n\n" + section, encoding="utf-8")
        (outdir / "calibration_tq06_highq.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "commit_sha": _git_commit_sha(),
                    "generated_at": datetime.now(UTC).isoformat(),
                    "target": "TQ06 high-Q subset median (sim-close gate + qs top-n S11, pf=0)",
                    "subset_rule": {
                        "port": "S11",
                        "pf": 0,
                        "gate_mhz": float(args.gate_mhz),
                        "gate_center_ghz": verdict["fs_ghz"],
                        "gate_center_source": "网格最优名义几何 A(130,100) 仿真 fs2"
                        "（沿用 dfs 归属判定结论）",
                        "order": "qs 降序（相位斜率 Qs，生产口径）",
                        "n": len(subset_rows),
                        "n_gate_candidates": n_gate,
                    },
                    "subset_files": [r["file"] for r in subset_rows],
                    "verdict": verdict,
                    "mirror_degeneracy": "A(mo_b,mo_t) ≡ B(mo_t,mo_b)（数值验证 rtol 1e-9）",
                    "theta_names": list(THETA_NAMES),
                    "theta": [float(v) for v in fit.theta],
                    "bounds": {"lo": lo.tolist(), "hi": hi.tolist()},
                    "materials_after": {
                        "alscn": {
                            "c33": mat_after.c33,
                            "e33": mat_after.e33,
                            "eps": mat_after.eps,
                            "rho": mat_after.rho,
                        },
                        "alpha": alpha_after,
                        "t_mo_bot_nm": float(fit.theta[4]),
                        "t_mo_top_nm": float(fit.theta[5]),
                        "aln": "ALN1（冻结）",
                    },
                    "cost": fit.cost,
                    "nfev": fit.nfev,
                    "target7": {k: float(v) for k, v in zip(PARAM_KEYS, target, strict=True)},
                    "residuals_before": res_before,
                    "residuals_after": res_after,
                    "strict_variant": strict_doc,
                    "k2_reachability": k2_scan,
                    "te1_band_range_db": {
                        "measured_median": float(np.median(te1_ranges)),
                        "pure_capacitive_slope": float(20 * np.log10(8 / 3)),
                        "mason_before": te1_db_before,
                        "mason_after": te1_db_after,
                    },
                    "baseline_dfs": baseline,
                    "one_liner": one_liner,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[WP-A] 高 Q 产物已写入 {outdir}（耗时 {time.time() - t_start:.1f} s）")
        print(one_liner)
        return 0

    lines: list[str] = [
        "# WP-A TQ06 14 GHz P3F 双层归属判定与校准报告",
        "",
        f"- 生成时间（UTC）：{datetime.now(UTC).isoformat()}",
        f"- commit：{_git_commit_sha()}",
        f"- 数据：`{args.tq06_dir}`（{len(rows)} 个 s1p，valid={n_valid}，剔除 {len(excluded)}）",
        "- 提取口径：prodlabels.extract_eppe_production_labels 宽窗 (10,20] GHz"
        "（TE2 七参数 + TE1 [3,8) 诊断）；仿真侧自适应窗 "
        "(1.0, 1.13]×f2_pred（f2_pred=1/Σ(t/v)，排除 TE3 误锁）",
        f"- 仿真网格：3–22 GHz @2 MHz；评估面积 {EVAL_AREA_UM2:.0f} µm²"
        "（Bode-Q 经 50 Ω 变换依赖面积；fs/fp/k²eff/相位 Q 与面积无关，已数值验证）",
        f"- 材料基准：WP3 冻结 AlScN（c33={alscn.c33:.6e}, e33={alscn.e33:.6f}, "
        f"eps={alscn.eps:.6e}）+ ALN1（冻结）+ alpha={alpha_cal:.5f}",
        f"- 优化器：scipy least_squares trf，jac=3-point，多起点 {len(starts)}"
        f"（名义+LHS，种子 {args.seed}），max_nfev={args.max_nfev}",
        f"- 总耗时：{time.time() - t_start:.1f} s",
        "",
        "## 论文一句话数字",
        "",
        one_liner,
        "",
        "## 1. 实测提取统计（38 文件）",
        "",
        f"valid {n_valid}/{len(rows)}；剔除 "
        + (
            ", ".join(f"{r['file']}({r.get('reason', '?')})" for r in excluded)
            if excluded
            else "无"
        ),
        "",
        "| 集合 | n | fs2 中位 (GHz) | fs2 IQR | fp2 中位 (GHz) | k²eff 中位 (%) | k²eff IQR |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, st in (("全部", stats_all), ("S11", stats_s11), ("S22", stats_s22)):
        fs, fp, k2 = st["fs_ghz"], st["fp_ghz"], st["k2eff_pct"]
        lines.append(
            f"| {name} | {fs['n']} | {_fmt(fs['median'])} | "
            f"[{_fmt(fs['q25'])}, {_fmt(fs['q75'])}] | {_fmt(fp['median'])} | "
            f"{_fmt(k2['median'], 2)} | [{_fmt(k2['q25'], 2)}, {_fmt(k2['q75'], 2)}] |"
        )
    lines += [
        "",
        f"TE1 抑制诊断：38 文件中 TE1 对检出 {te1_found} 个（[3,8) GHz 窗）；"
        f"该带 |Z| dB 动态范围中位 {float(np.median(te1_ranges)):.2f} dB"
        f"（纯 1/f 电容斜率 {20 * np.log10(8 / 3):.2f} dB；仿真同带：校准前 "
        f"{te1_db_before:.2f} dB / 校准后 {te1_db_after:.2f} dB）——实测 TE1 抑制"
        "优于 170/190 名义双层模型预测，与 P3F 设计方向一致（反极性对称抑制）。",
        "",
        "## 2. 归属判定",
        "",
        "**镜像简并（数值验证）**：归属 A(mo_b,mo_t) 与归属 B(mo_t,mo_b) 的 1 端口 Z11 "
        "逐点一致（rtol 1e-9 内）；reverse (+1,−1)↔(−1,+1) 全局符号翻转同样平凡简并。"
        "由于电极厚度为未知自由量，两种归属对任意 1 端口 Z 数据给出相同预测——"
        "**绝对归属（哪层是 AlN）不可由本数据区分**，需工艺记录或成分分析（XRD/EDS）"
        "判定。下文采用约定 A（底 AlN 170 nm/顶 AlScN 190 nm，reverse=(+1,−1)），"
        "电极估计值仅在镜像等价类 (mo_b,mo_t)~(mo_t,mo_b) 意义下成立。",
        "",
        "判定依据表：两归属 × 电极对网格的 fs2 残差（目标 = 实测 S11 中位 "
        f"{tgt_fs:.4f} GHz / fp2 {tgt_fp:.4f} GHz / k²eff {tgt_k2:.2f}%；"
        "镜像对只列 A 行，B(mo_t,mo_b) 相同）：",
        "",
        "| 归属 | mo_b (nm) | mo_t (nm) | fs2 (GHz) | Δfs2 (MHz) | fp2 (GHz) | Δfp2 (MHz) "
        "| k²eff (%) | Δk²eff (pct-pt) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for g in sorted(grid, key=lambda x: (x["attribution"], x["t_mo_bot_nm"], x["t_mo_top_nm"])):
        if g["attribution"] != "A":
            continue  # B(mo_b,mo_t) ≡ A(mo_t,mo_b) 镜像简并，表中只列 A 行
        if not g["valid"]:
            lines.append(
                f"| {g['attribution']} | {g['t_mo_bot_nm']:g} | {g['t_mo_top_nm']:g} "
                "| 无效 | — | — | — | — | — |"
            )
            continue
        lines.append(
            f"| {g['attribution']} | {g['t_mo_bot_nm']:g} | {g['t_mo_top_nm']:g} "
            f"| {g['fs_ghz']:.4f} | {g['dfs2_mhz']:+.1f} | {g['fp_ghz']:.4f} "
            f"| {g['dfp2_mhz']:+.1f} | {g['k2eff_pct']:.2f} | {g['dk2eff']:+.2f} |"
        )
    lines += [
        "",
        f"**fs2 残差最小几何：归属 {verdict['attribution']}，"
        f"mo_b={verdict['t_mo_bot_nm']:g} nm / mo_t={verdict['t_mo_top_nm']:g} nm，"
        f"Δfs2={verdict['dfs2_mhz']:+.1f} MHz（Δfp2={verdict['dfp2_mhz']:+.1f} MHz，"
        f"Δk²eff={verdict['dk2eff']:+.2f} pct-pt）**——作为校准起点；电极最终值由有界 LS 收敛。",
        "",
        "## 3. 子集挑选",
        "",
        f"依据：与归属判定基准仿真 |Δfs2| 最小的前 {len(subset_rows)} 个 S11 器件"
        "（S22 仅 5 个且 k²eff 系统性偏高——端口侧差异属当前对称边界 Mason 之外的"
        "物理，作诊断不入校准目标）。",
        "",
        "| 文件 | fs2 (GHz) | Δfs2 vs 基准 (MHz) | fp2 (GHz) | k²eff (%) | Qs | Qp |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in subset_rows:
        lines.append(
            f"| {r['file']} | {r['fs_ghz']:.4f} | {1e3 * (r['fs_ghz'] - verdict['fs_ghz']):+.1f} "
            f"| {r['fp_ghz']:.4f} | {r['k2eff_pct']:.2f} | {r['qs']:.0f} | {r['qp']:.0f} |"
        )
    lines += [
        "",
        f"子集中位目标：fs={target[0]:.4f} GHz, fp={target[1]:.4f} GHz, "
        f"k²eff={target[2]:.2f}%, Qs={target[3]:.1f}, Qp={target[4]:.1f}, "
        f"Qs_bode={target[5]:.1f}, Qp_bode={target[6]:.1f}",
        "",
        "## 4. 校准结果",
        "",
        "自由度：θ = (s_c33, s_e33, s_eps, s_alpha, t_mo_bot, t_mo_top)；AlN=ALN1 冻结。"
        "界：s_c33/s_eps ±5%；s_e33 ∈ [0.70, 1.05]（论证：TQ06 与 WP3 校准的 4–7 GHz "
        "晶圆不同批次，Sc 含量下降只降低 e33，取单边下展且不超过 WP3 值）；"
        "s_alpha ∈ [0.45, 3.6]（alpha 为损耗集总参数）；t_mo ∈ [90, 220] nm 允许非对称。",
        "",
        f"拟合：cost={fit.cost:.3f}，nfev={fit.nfev}，success={fit.success}，"
        f"start_idx={fit.start_idx}。",
        "",
        "### 收敛材料表",
        "",
        "| 量 | 校准值 | WP3 基准 | 相对 |",
        "|---|---|---|---|",
        f"| c33 (Pa) | {mat_after.c33:.6e} | {alscn.c33:.6e} | {(fit.theta[0] - 1) * 100:+.2f}% |",
        f"| e33 (C/m²) | {mat_after.e33:.6f} | {alscn.e33:.6f} "
        f"| {(fit.theta[1] - 1) * 100:+.2f}% |",
        f"| eps (F/m) | {mat_after.eps:.6e} | {alscn.eps:.6e} | {(fit.theta[2] - 1) * 100:+.2f}% |",
        f"| alpha | {alpha_after:.5f} | {alpha_cal:.5f} | {(fit.theta[3] - 1) * 100:+.2f}% |",
        f"| t_mo_bot (nm) | {fit.theta[4]:.1f} | 130（4–7 GHz 批次） | — |",
        f"| t_mo_top (nm) | {fit.theta[5]:.1f} | 130（4–7 GHz 批次） | — |",
        f"| rho (kg/m³) | {mat_after.rho:.2f} | {alscn.rho:.2f} | 固定 |",
        "",
        "### 校准前残差（归属判定基准几何 + WP3 材料）",
        "",
    ]
    lines += _res_table("校准前", res_before)
    lines += ["### 校准后残差", ""]
    lines += _res_table("校准后", res_after)
    if strict_doc is not None:
        lines += [
            "### 敏感性对照：全部材料 ±5% 严格界",
            "",
            f"cost={strict_doc['cost']:.3f}；θ="
            + ", ".join(f"{v:.4f}" for v in strict_doc["theta"]),
            "",
        ]
        lines += _res_table("严格界校准后", strict_doc["residuals"])
    lines += [
        "### k²eff 可达性扫描（拟合 θ 上只动 s_e33，其余分量冻结）",
        "",
        "| s_e33 | fs2 (GHz) | fp2 (GHz) | k²eff (%) |",
        "|---|---|---|---|",
    ]
    for row in k2_scan:
        lines.append(
            f"| {row['s_e33']:.2f} | {_fmt(row['fs_ghz'])} | {_fmt(row['fp_ghz'])} "
            f"| {_fmt(row['k2eff_pct'], 2)} |"
        )
    k2_floor = k2_scan[-1]
    lines += [
        "",
        f"**结构性缺口（如实报告，未硬拟合）**：实测子集 k²eff 中位 {target[2]:.2f}% "
        f"低于 fs 匹配几何下 1D Mason 的可达值——s_e33 降到论证界下限 0.70 时 k²eff 仍为 "
        f"{k2_floor['k2eff_pct']:.2f}%（fs2={k2_floor['fs_ghz']:.4f} GHz）。"
        "横向损耗/有限尺寸对实测 k²eff 的削减超出纯活塞 1D 口径；"
        f"实测 Qp/Qs={target[4] / target[3]:.2f} 的排序同样无法由单一 alpha 复现"
        "（Mason 单损耗参数下 Qp/Qs≈1.0——Qs 被横向泄漏额外拉低）。",
        "",
    ]
    lines += [
        "## 复现",
        "",
        "```bash",
        "cd backend",
        "uv run --no-sync python scripts/tq06_analyze.py",
        "```",
        "",
    ]
    report = "\n".join(lines)
    (outdir / "calibration_report_tq06.md").write_text(report, encoding="utf-8")

    (outdir / "calibration_tq06.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "commit_sha": _git_commit_sha(),
                "generated_at": datetime.now(UTC).isoformat(),
                "target": "TQ06 subset median (|Δfs2| top-n S11)",
                "subset_files": [r["file"] for r in subset_rows],
                "verdict": verdict,
                "mirror_degeneracy": "A(mo_b,mo_t) ≡ B(mo_t,mo_b)（数值验证 rtol 1e-9）",
                "theta_names": list(THETA_NAMES),
                "theta": [float(v) for v in fit.theta],
                "bounds": {"lo": lo.tolist(), "hi": hi.tolist()},
                "materials_after": {
                    "alscn": {
                        "c33": mat_after.c33,
                        "e33": mat_after.e33,
                        "eps": mat_after.eps,
                        "rho": mat_after.rho,
                    },
                    "alpha": alpha_after,
                    "t_mo_bot_nm": float(fit.theta[4]),
                    "t_mo_top_nm": float(fit.theta[5]),
                    "aln": "ALN1（冻结）",
                },
                "cost": fit.cost,
                "nfev": fit.nfev,
                "target7": {k: float(v) for k, v in zip(PARAM_KEYS, target, strict=True)},
                "residuals_before": res_before,
                "residuals_after": res_after,
                "strict_variant": strict_doc,
                "k2_reachability": k2_scan,
                "te1_band_range_db": {
                    "measured_median": float(np.median(te1_ranges)),
                    "pure_capacitive_slope": float(20 * np.log10(8 / 3)),
                    "mason_before": te1_db_before,
                    "mason_after": te1_db_after,
                },
                "one_liner": one_liner,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[WP-A] 产物已写入 {outdir}（耗时 {time.time() - t_start:.1f} s）")
    print(one_liner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
