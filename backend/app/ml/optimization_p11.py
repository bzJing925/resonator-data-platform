"""P11 joint Qp/k2 extrapolation candidates: frozen grid, risk labels, layered ranking."""

from __future__ import annotations

import pandas as pd

JOINT_TARGETS = ("qp", "k2eff_pct")
BASELINE_QP = dict(marks="C6-1", eg_um=1.0, fl_um=1.5, area_um2=5500.0, has_pf=0)
BASELINE_K2 = dict(marks="E1-1", eg_um=2.0, fl_um=0.0, area_um2=5500.0, has_pf=0)
# Frozen candidate grid (protocol): A = EG extrapolation, B = frontier interpolation.
GRID_A = [(eg, fl) for eg in (2.5, 3.0) for fl in (0.0, 0.5)]
GRID_B = (
    [(1.25, fl) for fl in (1.25, 1.5, 1.75)]
    + [(1.0, fl) for fl in (1.375, 1.625)]
    + [(2.0, 0.25)]
)
GRID_C_DEMO = [(0.0, 2.5, 700.0, 1)]  # Computed risk-label demo; never recommended.
EXCLUSIONS = [
    ("700_um2_pf_qp_extrapolation", "700 μm² PF 分支 Qp 0/8 覆盖失败史（P6C–P10）；区间不可靠"),
    ("area_above_5500", "无仿真趋势支持；面积边缘留出 k² 覆盖 64.6% 失效史"),
    ("fl_above_2", "FL 上边界留出 BodeQ 区间覆盖 55.6% 失效史；无仿真支持"),
    ("eg_above_2_with_fl_ge_1", "该 FL 段实测 Qp 在 EG≥1.5 已回落；双轴同时外推不可辨识"),
]


def build_candidates() -> pd.DataFrame:
    rows = []
    for eg, fl in GRID_A:
        rows.append(
            dict(group="A", eg_um=eg, fl_um=fl, area_um2=5500.0, has_pf=0,
                 extrapolation_axis="eg_um", extrapolation_distance=eg - 2.0)
        )
    for eg, fl in GRID_B:
        rows.append(
            dict(group="B", eg_um=eg, fl_um=fl, area_um2=5500.0, has_pf=0,
                 extrapolation_axis="interpolate", extrapolation_distance=0.0)
        )
    for eg, fl, area, pf in GRID_C_DEMO:
        rows.append(
            dict(group="C", eg_um=eg, fl_um=fl, area_um2=area, has_pf=pf,
                 extrapolation_axis="eg_um+area_um2+pf", extrapolation_distance=eg - 2.0)
        )
    frame = pd.DataFrame(rows)
    frame["candidate_id"] = [
        f"{r.group}{i:02d}_EG{r.eg_um}_FL{r.fl_um}_A{int(r.area_um2)}_PF{r.has_pf}"
        for i, r in enumerate(frame.itertuples())
    ]
    frame["branch_id"] = [
        f"pf{r.has_pf}_eg{int(r.eg_um > 0)}_fl{int(r.fl_um > 0)}" for r in frame.itertuples()
    ]
    return frame


def measured_frontier(catalog: pd.DataFrame) -> pd.DataFrame:
    """(Qp, k2) Pareto front of the measured catalog; ties kept."""
    pts = catalog[["qp", "k2eff_pct"]].to_numpy(float)
    keep = []
    for i in range(len(pts)):
        dominated = (
            (pts[:, 0] >= pts[i, 0]) & (pts[:, 1] >= pts[i, 1])
            & ((pts[:, 0] > pts[i, 0]) | (pts[:, 1] > pts[i, 1]))
        )
        keep.append(not dominated.any())
    return catalog.loc[keep].sort_values("k2eff_pct").reset_index(drop=True)


def dominance(qp, k2, frontier):
    """Dominance of one candidate point against the measured frontier points."""
    wins, dominated_by = [], []
    for _, f in frontier.iterrows():
        if qp >= f.qp and k2 >= f.k2eff_pct and (qp > f.qp or k2 > f.k2eff_pct):
            wins.append(f.marks)
        elif f.qp >= qp and f.k2eff_pct >= k2 and (f.qp > qp or f.k2eff_pct > k2):
            dominated_by.append(f.marks)
    return wins, dominated_by


def pareto_layer(frame, qp_col, k2_col):
    pts = frame[[qp_col, k2_col]].to_numpy(float)
    keep = []
    for i in range(len(pts)):
        dominated = (
            (pts[:, 0] >= pts[i, 0]) & (pts[:, 1] >= pts[i, 1])
            & ((pts[:, 0] > pts[i, 0]) | (pts[:, 1] > pts[i, 1]))
        )
        keep.append(not dominated.any())
    return keep


def risk_flags(row, max_train_eg=2.0, max_train_fl=2.0):
    flags = []
    if row.eg_um > max_train_eg:
        flags.append(f"EG 超出实测范围 +{row.eg_um - max_train_eg:.2f} μm")
    if row.group == "C":
        flags.append("700 μm² PF 分支 Qp 区间失败史（0/8）——不推荐")
    if row.fl_um > max_train_fl:
        flags.append("FL 超出实测范围")
    return flags
