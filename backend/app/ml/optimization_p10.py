"""P10: subset heteroscedastic intervals (E1) and Qp-only frequency submodel (E2) assembly."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.ml.optimization_label_p9 import loo_quantile

E1_ARMS = ("D0_control", "D1_subset_hetero_interval", "D2_subset_c1_switch")
E2_ARMS = ("F0_control", "F1_qp_frequency")
SUBSET_BRANCHES = ("pf1_eg0_fl0", "pf1_eg0_fl1")
SUBSET_AREA = 700.0
E1_MIN_COVERAGE_GAIN = 0.25
E1_MAX_WIDTH_RATIO = 4.0
E1_BALANCED_MAX_DROP = 0.05
E1_POINT_GUARD_MAX = 0.05
E2_MIN_IMPROVEMENT = 0.20
E2_GUARD_MAX_DEGRADATION = 0.05


def subset_mask(frame):
    """Frozen geometric rule: PF EG0 branches at the 700 um2 level; no labels involved."""
    return frame.branch_id.isin(SUBSET_BRANCHES) & frame.area_um2.eq(SUBSET_AREA)


def _loo_values(inner):
    """Per-row LOO conformal quantile of |dlog|/log_scale scores, grouped by scope/target."""
    out = pd.Series(np.nan, index=inner.index, dtype=float)
    for _, g in inner.groupby(["scope", "target"]):
        scores = (g.abs_log_error / g.log_scale).to_numpy()
        out.loc[g.index] = loo_quantile(scores, g.design_group_id.to_numpy())
    return out


def assemble_e1_arms(inner_c0, inner_c1):
    """D0/D1/D2 per-row (point, scale, quantile) assembly from frozen P9 out-of-fold rows."""
    c0 = inner_c0.reset_index(drop=True)
    c1 = inner_c1.reset_index(drop=True)
    keys = ["scope", "structure_id", "target"]
    assert len(c0) == len(c1) and c0[keys].equals(c1[keys]), "C0/C1 inner rows must align"
    is_subset = subset_mask(c0).to_numpy()
    q0 = _loo_values(inner_c0).to_numpy()
    q1 = _loo_values(inner_c1).to_numpy()
    base = c0[keys + ["design_group_id", "branch_id", "area_um2", "actual"]].copy()
    base["is_subset"] = is_subset

    arms = {}
    d0 = base.copy()
    d0["log_mean"], d0["scale"], d0["q"] = c0.log_mean, c0.log_scale, q0
    arms["D0_control"] = d0
    d1 = base.copy()
    d1["log_mean"], d1["q"] = c0.log_mean, q0
    d1["scale"] = c0.log_scale.to_numpy()
    d1.loc[is_subset, "scale"] = np.sqrt(
        c0.log_scale.to_numpy()[is_subset] ** 2 + c0.disp.to_numpy()[is_subset] ** 2
    )
    arms["D1_subset_hetero_interval"] = d1
    d2 = base.copy()
    d2["log_mean"], d2["scale"], d2["q"] = c0.log_mean, c0.log_scale, q0
    for col, source in [("log_mean", c1.log_mean), ("scale", c1.log_scale)]:
        d2.loc[is_subset, col] = source.to_numpy()[is_subset]
    d2.loc[is_subset, "q"] = q1[is_subset]
    arms["D2_subset_c1_switch"] = d2
    for name, frame in arms.items():
        frame["arm"] = name
    return arms


def eval_e1_inner(arms):
    """Frozen inner interval metrics for the three E1 arms."""
    metrics = []
    d0 = arms["D0_control"]
    for name, work in arms.items():
        half = (work.q * work.scale).to_numpy()
        resid = np.abs(np.log(work.actual.to_numpy()) - work.log_mean.to_numpy())
        work = work.assign(covered=resid <= half, half_width=half)
        subset = work[work.is_subset & work.target.eq("qp")]
        cells = work.groupby(["scope", "branch_id", "target"]).covered.mean()
        non_subset = work[~work.is_subset]
        identical = bool(
            non_subset.log_mean.equals(d0.log_mean[~d0.is_subset])
            and non_subset.scale.equals(d0.scale[~d0.is_subset])
            and non_subset.q.equals(d0.q[~d0.is_subset])
        )
        metrics.append(
            dict(
                arm=name,
                subset_qp_coverage=float(subset.covered.mean()),
                subset_n=int(len(subset)),
                balanced_coverage=float(cells.mean()),
                subset_median_half_width=float(subset.half_width.median()),
                non_subset_bit_identical=identical,
            )
        )
    return pd.DataFrame(metrics)


def select_e1(metrics, point_primary, point_guard):
    """Frozen E1 rule: D1 conservative arm has priority; D2 needs the point guard."""
    d0 = metrics[metrics.arm.eq("D0_control")].iloc[0]
    verdicts = {}
    for arm in E1_ARMS[1:]:
        m = metrics[metrics.arm.eq(arm)].iloc[0]
        gain_ok = m.subset_qp_coverage >= d0.subset_qp_coverage + E1_MIN_COVERAGE_GAIN
        width_ok = m.subset_median_half_width <= E1_MAX_WIDTH_RATIO * d0.subset_median_half_width
        balanced_ok = m.balanced_coverage >= d0.balanced_coverage - E1_BALANCED_MAX_DROP
        identical_ok = bool(m.non_subset_bit_identical)
        point_ok = True
        if arm == "D2_subset_c1_switch":
            point_ok = (
                point_primary[arm] <= point_primary["D0_control"] * (1 + E1_POINT_GUARD_MAX)
                and point_guard[arm] <= point_guard["D0_control"] * (1 + E1_POINT_GUARD_MAX)
            )
        verdicts[arm] = dict(
            gain=bool(gain_ok),
            width=bool(width_ok),
            balanced=bool(balanced_ok),
            non_subset_identical=identical_ok,
            point=bool(point_ok),
            passed=bool(gain_ok and width_ok and balanced_ok and identical_ok and point_ok),
        )
    if verdicts["D1_subset_hetero_interval"]["passed"]:
        selected = "D1_subset_hetero_interval"
    elif verdicts["D2_subset_c1_switch"]["passed"]:
        selected = "D2_subset_c1_switch"
    else:
        selected = "D0_control"
    return {
        "selected": selected,
        "verdicts": verdicts,
        "metrics": metrics.to_dict("records"),
        "point_primary": point_primary,
        "point_guard": point_guard,
        "thresholds": dict(
            min_coverage_gain=E1_MIN_COVERAGE_GAIN,
            max_width_ratio=E1_MAX_WIDTH_RATIO,
            balanced_max_drop=E1_BALANCED_MAX_DROP,
            point_guard_max=E1_POINT_GUARD_MAX,
        ),
        "selection_uses_outer_test": False,
    }


def select_e2(primary, guard):
    """Frozen E2 rule: identical thresholds to P7/P8."""
    base_p, base_g = primary["F0_control"], guard["F0_control"]
    ok = (
        primary["F1_qp_frequency"] <= base_p * (1 - E2_MIN_IMPROVEMENT)
        and guard["F1_qp_frequency"] <= base_g * (1 + E2_GUARD_MAX_DEGRADATION)
    )
    return {
        "selected": "F1_qp_frequency" if ok else "F0_control",
        "primary_scores": {k: float(v) for k, v in primary.items()},
        "guard_scores": {k: float(v) for k, v in guard.items()},
        "primary_min_improvement": E2_MIN_IMPROVEMENT,
        "guard_max_degradation": E2_GUARD_MAX_DEGRADATION,
        "selection_uses_outer_test": False,
    }
