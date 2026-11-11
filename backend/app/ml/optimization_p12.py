"""P12 refined/expanded extrapolation search: fine grid, peak search, candidate assembly."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

FINE_STEP = 0.025
EG_MAX_SEARCH = 3.0
FL_MAX_SEARCH = 2.0
SEARCH_AREA = 5500.0
MERGE_SCALED_DISTANCE = 0.1
EG_EXTENSION = [(eg, fl) for eg in (3.25, 3.5, 4.0) for fl in (0.0, 0.1, 0.5)]
NEGATIVE_CONTROLS = [
    dict(eg_um=3.5, fl_um=0.0, area_um2=5500.0, has_pf=0,
         reason="EG 超出 +1.5 μm：两模型均平坦/回落，无上行证据"),
    dict(eg_um=1.0, fl_um=2.5, area_um2=5500.0, has_pf=0,
         reason="FL>2：FL 上边界 BodeQ 区间覆盖 55.6% 失效史，无仿真支持"),
    dict(eg_um=0.0, fl_um=1.5, area_um2=700.0, has_pf=1,
         reason="700 μm² PF 分支 Qp 0/8 覆盖失败史"),
]


def fine_grid() -> pd.DataFrame:
    egs = np.arange(0.0, EG_MAX_SEARCH + 1e-12, FINE_STEP)
    fls = np.arange(0.0, FL_MAX_SEARCH + 1e-12, FINE_STEP)
    grid = pd.DataFrame(
        [(float(e), float(f)) for e in egs for f in fls], columns=["eg_um", "fl_um"]
    )
    grid["area_um2"] = SEARCH_AREA
    grid["has_pf"] = 0
    grid["branch_id"] = [
        f"pf0_eg{int(e > 0)}_fl{int(f > 0)}"
        for e, f in zip(grid.eg_um, grid.fl_um, strict=True)
    ]
    return grid


def mark_catalog_matches(grid: pd.DataFrame, catalog: pd.DataFrame) -> pd.Series:
    """Exact geometry coincidence with a measured structure (EG/FL/area/PF all equal)."""
    keys = set(
        zip(catalog.eg_um, catalog.fl_um, catalog.area_um2, catalog.has_pf, strict=True)
    )
    return pd.Series(
        [
            (e, f, a, p) in keys
            for e, f, a, p in zip(
                grid.eg_um, grid.fl_um, grid.area_um2, grid.has_pf, strict=True
            )
        ],
        index=grid.index,
    )


def scaled_distance(a, b) -> float:
    return float(np.hypot((a[0] - b[0]) / EG_MAX_SEARCH, (a[1] - b[1]) / FL_MAX_SEARCH))


def grid_argmax(grid: pd.DataFrame, value: str) -> pd.Series:
    return grid.loc[grid[value].idxmax()]


def refine_peak_gp(predict_log, start, bounds=((0.0, EG_MAX_SEARCH), (0.0, FL_MAX_SEARCH))):
    """Nelder-Mead on the smooth GP surface; hard penalty outside; returns (eg, fl, value)."""

    def objective(x):
        in_bounds = bounds[0][0] <= x[0] <= bounds[0][1] and bounds[1][0] <= x[1] <= bounds[1][1]
        if not in_bounds:
            distance = float(np.hypot(
                max(bounds[0][0] - x[0], 0, x[0] - bounds[0][1]),
                max(bounds[1][0] - x[1], 0, x[1] - bounds[1][1]),
            ))
            return 1e6 + 1e6 * distance
        frame = pd.DataFrame(
            dict(
                eg_um=[x[0]], fl_um=[x[1]], area_um2=[SEARCH_AREA], has_pf=[0],
                branch_id=[f"pf0_eg{int(x[0] > 0)}_fl{int(x[1] > 0)}"],
            )
        )
        mean, _ = predict_log(frame)
        value = float(mean[0])
        return -value if np.isfinite(value) else 1e6

    result = minimize(
        objective, np.asarray(start, dtype=float), method="Nelder-Mead",
        options=dict(xatol=1e-6, fatol=1e-12, maxiter=2000),
    )
    if not np.isfinite(result.fun):
        raise ValueError("Nelder-Mead diverged")
    # Snap to a 1e-3 grid and zero out floating noise so branch identity stays stable.
    eg = float(np.clip(round(result.x[0], 3), *bounds[0]))
    fl = float(np.clip(round(result.x[1], 3), *bounds[1]))
    eg = 0.0 if abs(eg) < 5e-4 else eg
    fl = 0.0 if abs(fl) < 5e-4 else fl
    return eg, fl, -float(result.fun)


def pareto_representatives(grid, qp_col, k2_col, frontier, baseline_qp, baseline_k2, n=2):
    """Non-dominated grid points closest to the measured frontier (baseline-normalized)."""
    pts = grid[[qp_col, k2_col]].to_numpy(float)
    keep = []
    for i in range(len(pts)):
        dominated = (
            (pts[:, 0] >= pts[i, 0]) & (pts[:, 1] >= pts[i, 1])
            & ((pts[:, 0] > pts[i, 0]) | (pts[:, 1] > pts[i, 1]))
        )
        keep.append(not dominated.any())
    work = grid.loc[keep].copy()
    fpts = frontier[["qp", "k2eff_pct"]].to_numpy(float)
    dists = []
    for row in work[[qp_col, k2_col]].to_numpy(float):
        d = np.min(
            np.hypot((fpts[:, 0] - row[0]) / baseline_qp, (fpts[:, 1] - row[1]) / baseline_k2),
            axis=0,
        )
        dists.append(float(d))
    work["frontier_distance"] = dists
    work = work.sort_values("frontier_distance").reset_index(drop=True)
    chosen = []
    seen_values = set()
    for row in work.itertuples():
        vkey = (round(getattr(row, qp_col), 9), round(getattr(row, k2_col), 9))
        if vkey in seen_values:
            continue  # Identical predictions (tree leaf plateaus) keep only the first point.
        if all(scaled_distance((row.eg_um, row.fl_um), c) >= 0.05 for c in chosen):
            chosen.append((row.eg_um, row.fl_um))
            seen_values.add(vkey)
        if len(chosen) == n:
            break
    keys = {(round(e, 6), round(f, 6)) for e, f in chosen}
    return work[
        work.apply(lambda r: (round(r.eg_um, 6), round(r.fl_um, 6)) in keys, axis=1)
    ]
