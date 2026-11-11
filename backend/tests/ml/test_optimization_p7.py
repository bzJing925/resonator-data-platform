"""P7 bias-arm regressions: clipping, offsets, fold isolation, frozen selection rule."""

import numpy as np
import pandas as pd
import pytest

from app.ml.optimization_ablation import AblationModel, inner_folds
from app.ml.optimization_bias_p7 import (
    ARMS,
    BranchAreaOffsetModel,
    TrendAreaClipModel,
    area_stress_predictions,
    inner_cv_predictions,
    per_target_recipe_selection,
    select_arm,
)
from app.ml.optimization_data import FEATURES, TARGETS


def p7_catalog():
    """Two PF branches x eight design groups; each group sits at exactly one area level."""
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
            rows.append(row)
    return pd.DataFrame(rows)


def test_trend_area_clip_only_flattens_the_trend_component():
    frame = p7_catalog()
    target = "qp"
    clipped = TrendAreaClipModel(target).fit(frame)
    plain = AblationModel("trend_gp_log_area", target).fit(frame)
    probe = frame.iloc[[0, 0]].copy()
    probe["area_um2"] = [500.0, 700.0]  # Below every fitted area; identical other features.
    transformed = clipped.base.transform(probe)
    low, scale, trend = clipped.base.trends["pf1_eg0_fl0"]
    x = (transformed[list(FEATURES)].to_numpy(float) - low) / scale
    xc = x.copy()
    xc[:, list(FEATURES).index("area_um2")] = np.clip(x[:, 2], 0.0, 1.0)
    flat = trend.predict(xc)
    free = trend.predict(x)
    assert flat[0] == flat[1] and free[0] != free[1]
    m_clip, _ = clipped.predict_log(probe)
    m_plain, _ = plain.predict_log(probe)
    np.testing.assert_allclose(m_clip - m_plain, flat - free, rtol=1e-12)
    inside = frame.iloc[:4].copy()
    c_in, _ = clipped.predict_log(inside)
    p_in, _ = plain.predict_log(inside)
    np.testing.assert_allclose(c_in, p_in, rtol=1e-12)


def test_branch_area_offset_shrinkage_and_zero_outside_levels():
    frame = p7_catalog()
    target = "qp"
    model = BranchAreaOffsetModel(target).fit(frame)
    mean, _ = model.base.predict_log(frame)
    resid = np.log(frame[target].to_numpy(float)) - mean
    key = ("pf1_eg0_fl0", 900.0)
    cell = frame[(frame.branch_id.eq(key[0])) & (frame.area_um2.eq(key[1]))]
    expected = resid[cell.index].mean() * len(cell) / (len(cell) + 4.0)
    assert np.isclose(model.offsets[key], expected)
    probe = frame.iloc[:2].copy()
    probe["area_um2"] = 700.0  # Unobserved level: offset must be exactly zero.
    off, _ = model.predict_log(probe)
    base, _ = model.base.predict_log(probe)
    np.testing.assert_array_equal(off, base)


def test_inner_cv_and_stress_keep_design_groups_indivisible():
    frame = p7_catalog()
    folds = inner_folds(frame)
    inner = inner_cv_predictions(frame, folds)
    assert folds.groupby("design_group_id").fold.nunique().eq(1).all()
    assert set(inner.arm.unique()) == {"A1_trend_area_clip", "A3_branch_area_offset"}
    stress = area_stress_predictions(frame)
    assert set(stress.scenario.unique()) <= {"area_um2_low", "area_um2_high"}
    manifest_groups = stress.groupby(["scenario", "arm"]).design_group_id.nunique()
    assert (manifest_groups > 0).all()


def test_select_arm_rule_thresholds_ties_and_fallback():
    primary = {"A0_control": 0.10, "A1_trend_area_clip": 0.079, "A2_per_target_recipe": 0.078,
               "A3_branch_area_offset": 0.10}
    guard = {a: 0.05 for a in ARMS}
    result = select_arm(primary, guard)
    assert result["selected"] == "A2_per_target_recipe"  # Max improvement wins.
    guard_bad = dict(guard, A2_per_target_recipe=0.06)  # >5% guard degradation blocks A2.
    assert select_arm(primary, guard_bad)["selected"] == "A1_trend_area_clip"
    primary_flat = dict.fromkeys(ARMS, 0.10)
    assert select_arm(primary_flat, guard)["selected"] == "A0_control"


def test_per_target_recipe_selection_isolates_targets():
    rows = []
    recipes = ["gp_raw", "gp_log_area", "trend_gp_raw", "trend_gp_log_area"]
    for target in TARGETS:
        for i, recipe in enumerate(recipes):
            winner = "gp_raw" if target == "qp" else "trend_gp_log_area"
            error = 0.01 if recipe == winner else 0.05 + 0.01 * i
            rows.append(
                dict(
                    structure_id=f"s_{target}_{recipe}",
                    design_group_id=f"d_{target}",
                    fold=0,
                    recipe=recipe,
                    target=target,
                    actual=1.0,
                    prediction=1.0,
                    abs_log_error=error,
                )
            )
    selected, _ = per_target_recipe_selection(pd.DataFrame(rows))
    assert selected["qp"] == "gp_raw"
    assert all(selected[t] == "trend_gp_log_area" for t in TARGETS if t != "qp")


def test_invalid_frames_fail_closed():
    frame = p7_catalog()
    with pytest.raises(ValueError, match="Unknown target"):
        TrendAreaClipModel("not_a_target")
    poisoned = frame.copy()
    poisoned.loc[poisoned.index[0], "area_um2"] = -1.0
    with pytest.raises(ValueError, match="area"):
        TrendAreaClipModel("qp").fit(poisoned)
