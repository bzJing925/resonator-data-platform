"""P8 frequency-contract regressions: feature usage, fail-closed, contract labels, selection."""

import numpy as np
import pandas as pd
import pytest

from app.ml.optimization_ablation import inner_folds
from app.ml.optimization_boundary_selection import pseudo_boundaries
from app.ml.optimization_data import TARGETS
from app.ml.optimization_freq_p8 import (
    CONTRACT,
    FreqTrendGPModel,
    area_stress_predictions_p8,
    inner_cv_predictions_p8,
    select_arm_p8,
)


def p8_catalog(freq_signal=False):
    rows = []
    levels = [900.0, 1200.0, 2000.0, 2500.0, 3500.0]
    for branch in ["pf1_eg0_fl0", "pf1_eg0_fl1"]:
        for g in range(8):
            area = levels[g % len(levels)]
            fs = 4.4 + 0.01 * g
            row = dict(
                structure_id=f"{branch}_s{g}",
                design_group_id=f"{branch}_d{g}",
                branch_id=branch,
                eg_um=0.0,
                fl_um=0.5 + 0.25 * (g % 3),
                area_um2=area,
                has_pf=1,
                fs_ghz=fs,
                fp_ghz=fs + 0.12,
            )
            for j, target in enumerate(TARGETS):
                base = (j + 2) * (50 + 20 * np.log(area) + 0.7 * g)
                row[target] = base * (10.0 * fs if freq_signal else 1.0)
            rows.append(row)
    return pd.DataFrame(rows)


def test_frequency_features_change_predictions_when_they_carry_signal():
    frame = p8_catalog(freq_signal=True)
    model = FreqTrendGPModel("qp").fit(frame)
    probe = frame.iloc[[0, 0]].copy()
    probe["fs_ghz"] = [4.0, 5.0]
    probe["fp_ghz"] = probe.fs_ghz + 0.12
    mean, std = model.predict_log(probe)
    assert np.isfinite(mean).all() and (std > 0).all()
    assert mean[0] != mean[1]
    fixed = probe.copy()
    fixed["fs_ghz"] = 4.4
    fixed["fp_ghz"] = 4.52
    mean_fixed, _ = model.predict_log(fixed)
    assert mean_fixed[0] == mean_fixed[1]


def test_missing_frequency_fails_closed_and_contract_labels_fixed():
    frame = p8_catalog()
    frame.loc[frame.index[0], "fs_ghz"] = np.nan
    with pytest.raises(ValueError, match="finite"):
        FreqTrendGPModel("qp").fit(frame)
    assert CONTRACT == {"B0_control": "geometry_only", "B1_freq_trend_gp": "measured_frequency"}
    with pytest.raises(ValueError, match="Unknown target"):
        FreqTrendGPModel("not_a_target")


def test_p8_inner_cv_and_stress_group_isolation():
    frame = p8_catalog()
    folds = inner_folds(frame)
    inner = inner_cv_predictions_p8(frame, folds)
    assert set(inner.arm.unique()) == {"B1_freq_trend_gp"}
    stress = area_stress_predictions_p8(frame, pseudo_boundaries)
    assert set(stress.scenario.unique()) <= {"area_um2_low", "area_um2_high"}
    assert (stress.groupby(["scenario"]).design_group_id.nunique() > 0).all()


def test_select_arm_p8_thresholds_and_fallback():
    primary = {"B0_control": 0.10, "B1_freq_trend_gp": 0.079}
    guard = {"B0_control": 0.05, "B1_freq_trend_gp": 0.05}
    assert select_arm_p8(primary, guard)["selected"] == "B1_freq_trend_gp"
    guard_bad = {"B0_control": 0.05, "B1_freq_trend_gp": 0.0555}
    assert select_arm_p8(primary, guard_bad)["selected"] == "B0_control"
    primary_flat = {"B0_control": 0.10, "B1_freq_trend_gp": 0.10}
    assert select_arm_p8(primary_flat, guard)["selected"] == "B0_control"
