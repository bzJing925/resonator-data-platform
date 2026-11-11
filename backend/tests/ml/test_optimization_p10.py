"""P10 regressions: subset assembly, interval formula, non-subset identity, selection rules."""

import numpy as np
import pandas as pd
import pytest

from app.ml.optimization_p10 import (
    assemble_e1_arms,
    eval_e1_inner,
    select_e1,
    select_e2,
    subset_mask,
)


def toy_inner(scale=0.1, disp=0.4, resid=0.25):
    rows = []
    for scope in ["s1"]:
        for i in range(12):
            is_sub = i < 4
            rows.append(
                dict(
                    structure_id=f"s{i}",
                    design_group_id=f"d{i}",
                    branch_id="pf1_eg0_fl1" if is_sub else "pf0_eg1_fl1",
                    scope=scope,
                    area_um2=700.0 if is_sub else 900.0,
                    target="qp",
                    actual=100.0,
                    log_mean=np.log(100.0) - (resid if is_sub else 0.01),
                    log_scale=scale,
                    disp=disp,
                    abs_log_error=resid if is_sub else 0.01,
                )
            )
    return pd.DataFrame(rows)


def test_subset_mask_is_geometric_only():
    frame = pd.DataFrame(
        dict(
            branch_id=["pf1_eg0_fl0", "pf1_eg0_fl1", "pf0_eg0_fl1", "pf1_eg0_fl1"],
            area_um2=[700.0, 700.0, 700.0, 900.0],
        )
    )
    assert subset_mask(frame).tolist() == [True, True, False, False]


def test_assemble_e1_arms_formula_and_identity():
    c0 = toy_inner()
    c1 = toy_inner(scale=0.2, resid=0.2)
    arms = assemble_e1_arms(c0, c1)
    d0, d1, d2 = arms["D0_control"], arms["D1_subset_hetero_interval"], arms["D2_subset_c1_switch"]
    sub = d0.is_subset.to_numpy()
    np.testing.assert_allclose(
        d1.scale[sub], np.sqrt(c0.log_scale[sub] ** 2 + c0.disp[sub] ** 2)
    )
    assert d1.log_mean.equals(d0.log_mean)  # D1 never moves points.
    ns = ~sub
    assert d1.scale[ns].equals(d0.scale[ns]) and d2.scale[ns].equals(d0.scale[ns])
    np.testing.assert_allclose(d2.log_mean[sub], c1.log_mean[sub])
    shuffled = c1.sample(frac=1.0, random_state=0)  # Misaligned control must fail closed.
    with pytest.raises(AssertionError, match="align"):
        assemble_e1_arms(c0, shuffled)


def test_eval_e1_inner_counts_and_identity_flags():
    c0 = toy_inner()
    c1 = toy_inner(scale=0.2, resid=0.2)
    metrics = eval_e1_inner(assemble_e1_arms(c0, c1))
    assert metrics.subset_n.tolist() == [4, 4, 4]
    assert metrics.non_subset_bit_identical.all()
    d1 = metrics[metrics.arm.eq("D1_subset_hetero_interval")].iloc[0]
    d0 = metrics[metrics.arm.eq("D0_control")].iloc[0]
    assert d1.subset_median_half_width > d0.subset_median_half_width
    assert d1.subset_qp_coverage >= d0.subset_qp_coverage


def test_select_e1_priority_width_gate_and_fallback():
    base = dict(
        subset_qp_coverage=0.9, subset_n=19, balanced_coverage=0.95,
        subset_median_half_width=0.3, non_subset_bit_identical=True,
    )
    metrics = pd.DataFrame(
        [
            dict(arm="D0_control", subset_qp_coverage=0.5, subset_n=19, balanced_coverage=0.95,
                 subset_median_half_width=0.1, non_subset_bit_identical=True),
            dict(arm="D1_subset_hetero_interval", **base),
            dict(arm="D2_subset_c1_switch", **{**base, "subset_median_half_width": 0.2}),
        ]
    )
    point = {"D0_control": 0.1, "D2_subset_c1_switch": 0.1}
    assert select_e1(metrics, point, point)["selected"] == "D1_subset_hetero_interval"
    wide = metrics.copy()
    wide.loc[wide.arm.eq("D1_subset_hetero_interval"), "subset_median_half_width"] = 0.9
    assert select_e1(wide, point, point)["selected"] == "D2_subset_c1_switch"
    both_wide = wide.copy()
    both_wide.loc[both_wide.arm.eq("D2_subset_c1_switch"), "subset_median_half_width"] = 0.9
    assert select_e1(both_wide, point, point)["selected"] == "D0_control"
    broken = metrics.copy()
    broken.loc[broken.arm.eq("D1_subset_hetero_interval"), "non_subset_bit_identical"] = False
    assert select_e1(broken, point, point)["selected"] == "D2_subset_c1_switch"


def test_select_e2_thresholds():
    primary = {"F0_control": 0.10, "F1_qp_frequency": 0.079}
    guard = {"F0_control": 0.05, "F1_qp_frequency": 0.05}
    assert select_e2(primary, guard)["selected"] == "F1_qp_frequency"
    guard_bad = {"F0_control": 0.05, "F1_qp_frequency": 0.0555}
    assert select_e2(primary, guard_bad)["selected"] == "F0_control"
    primary_flat = {"F0_control": 0.10, "F1_qp_frequency": 0.095}
    assert select_e2(primary_flat, guard)["selected"] == "F0_control"
