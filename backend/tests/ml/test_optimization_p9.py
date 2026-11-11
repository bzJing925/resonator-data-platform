"""P9 label-uncertainty regressions: dispersion proxy, LOO conformal math, selection rule."""

import numpy as np
import pandas as pd
import pytest

from app.ml.optimization_ablation import inner_folds
from app.ml.optimization_data import TARGETS
from app.ml.optimization_label_p9 import (
    HeteroTrendGPModel,
    dispersion,
    inner_interval_metrics,
    inner_interval_predictions,
    loo_quantile,
    method_scores,
    select_arm_p9,
)


def p9_catalog():
    rows = []
    levels = [900.0, 1200.0, 2000.0, 2500.0, 3500.0]
    for branch in ["pf1_eg0_fl0", "pf1_eg0_fl1"]:
        for g in range(8):
            area = levels[g % len(levels)]
            row = dict(
                structure_id=f"{branch}_s{g}",
                design_group_id=f"{branch}_d{g}",
                branch_id=branch,
                eg_um=0.0,
                fl_um=0.5 + 0.25 * (g % 3),
                area_um2=area,
                has_pf=1,
            )
            for j, target in enumerate(TARGETS):
                row[target] = (j + 2) * (50 + 20 * np.log(area) + 0.7 * g)
                row[f"{target}_position_mad"] = row[target] * (0.05 + 0.01 * g)
            rows.append(row)
    return pd.DataFrame(rows)


def test_dispersion_proxy_formula_and_fail_closed():
    frame = p9_catalog()
    got = dispersion(frame, "qp")
    expected = frame.qp_position_mad / frame.qp
    np.testing.assert_allclose(got, expected)
    with pytest.raises(ValueError, match="nonnegative"):
        dispersion(frame.assign(qp_position_mad=-1.0), "qp")


def test_hetero_gp_uses_per_point_alpha_and_predicts_finitely():
    frame = p9_catalog()
    base = HeteroTrendGPModel("qp").fit(frame)
    louder = frame.copy()
    louder["qp_position_mad"] = louder.qp_position_mad * 10
    noisy = HeteroTrendGPModel("qp").fit(louder)
    mean_a, std_a = base.predict_log(frame.iloc[:3])
    mean_b, std_b = noisy.predict_log(frame.iloc[:3])
    assert np.isfinite(mean_a).all() and np.isfinite(mean_b).all()
    assert not np.allclose(std_a, std_b)  # Alpha array really enters the fit.
    with pytest.raises(ValueError, match="Unknown target"):
        HeteroTrendGPModel("not_a_target")


def test_loo_quantile_excludes_own_group():
    scores = np.arange(1.0, 11.0)
    groups = np.array([f"g{i}" for i in range(10)])
    q = loo_quantile(scores, groups)  # n=10 groups, rank=ceil(10*0.9)=9 over 9 others.
    # Row in the largest group: LOO set is 1..9 -> 9th of 9 = 9.
    assert q[9] == 9.0
    # Row in the smallest group: LOO set is 2..10 -> 9th = 10.
    assert q[0] == 10.0
    assert np.isnan(loo_quantile(np.array([1.0]), np.array(["only"]))).all()


def test_method_scores_c2_formulas():
    g = pd.DataFrame(
        dict(abs_log_error=[0.1], log_scale=[0.3], disp=[0.4])
    )
    scores_g, scale_g = method_scores("C2_hetero_calibration", g)["global"]
    scores_s, scale_s = method_scores("C2_hetero_calibration", g)["scaled"]
    assert scores_g[0] == 0.25 and scale_g[0] == 0.4
    assert np.isclose(scale_s[0], 0.5) and np.isclose(scores_s[0], 0.2)
    with pytest.raises(ValueError, match="Unknown arm"):
        method_scores("nobody", g)


def test_inner_interval_metrics_subset_and_cells():
    rows = []
    for scope in ["s1", "s2"]:
        for branch in ["pf1_eg0_fl1", "pf0_eg1_fl1"]:
            for i in range(12):
                rows.append(
                    dict(
                        structure_id=f"{scope}_{branch}_{i}",
                        design_group_id=f"{scope}_{branch}_d{i}",
                        branch_id=branch,
                        fold=0,
                        arm="C0_control",
                        target="qp",
                        actual=100.0,
                        log_mean=np.log(100.0) + (0.01 if branch.startswith("pf1") else 5.0),
                        log_scale=0.5,
                        abs_log_error=0.01 if branch.startswith("pf1") else 5.0,
                        disp=0.1,
                        scope=scope,
                        area_um2=700.0 if branch.startswith("pf1") else 900.0,
                    )
                )
    metrics = inner_interval_metrics(pd.DataFrame(rows))
    c0 = metrics[(metrics.arm.eq("C0_control")) & (metrics.method.eq("scaled"))].iloc[0]
    assert c0.subset_n == 24  # PF branch at each scope's minimum area, both scopes.
    assert 0.0 <= c0.subset_qp_coverage <= 1.0
    assert 0.0 <= c0.balanced_coverage <= 1.0


def test_inner_interval_predictions_isolation():
    frame = p9_catalog()
    folds = inner_folds(frame)
    inner = inner_interval_predictions(frame, folds, "C0_control")
    assert set(inner.arm.unique()) == {"C0_control"}
    assert folds.groupby("design_group_id").fold.nunique().eq(1).all()
    c1 = inner_interval_predictions(frame, folds, "C1_hetero_gp")
    assert set(c1.arm.unique()) == {"C1_hetero_gp"}
    assert np.isfinite(c1.log_scale).all()


def test_select_arm_p9_rule():
    metrics = pd.DataFrame(
        [
            dict(arm="C0_control", method="scaled", subset_qp_coverage=0.25, subset_n=8,
                 balanced_coverage=0.90, subset_median_half_width=0.5),
            dict(arm="C0_control", method="global", subset_qp_coverage=0.25, subset_n=8,
                 balanced_coverage=0.90, subset_median_half_width=0.5),
            dict(arm="C1_hetero_gp", method="scaled", subset_qp_coverage=0.75, subset_n=8,
                 balanced_coverage=0.89, subset_median_half_width=0.6),
            dict(arm="C1_hetero_gp", method="global", subset_qp_coverage=0.75, subset_n=8,
                 balanced_coverage=0.89, subset_median_half_width=0.6),
            dict(arm="C2_hetero_calibration", method="scaled", subset_qp_coverage=0.75, subset_n=8,
                 balanced_coverage=0.70, subset_median_half_width=0.4),
            dict(arm="C2_hetero_calibration", method="global", subset_qp_coverage=0.75, subset_n=8,
                 balanced_coverage=0.70, subset_median_half_width=0.4),
        ]
    )
    point = {"C0_control": 0.1, "C1_hetero_gp": 0.1, "C2_hetero_calibration": 0.1}
    sel = select_arm_p9(metrics, point, point)
    assert sel["selected"] == "C1_hetero_gp"  # C2 fails the balanced-coverage guard.
    metrics.loc[metrics.arm.eq("C1_hetero_gp"), "subset_median_half_width"] = 1.5  # >2x C0.
    assert select_arm_p9(metrics, point, point)["selected"] == "C0_control"
