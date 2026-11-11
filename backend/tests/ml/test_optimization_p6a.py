"""P6A leakage and simultaneous-coverage regressions."""

import numpy as np
import pandas as pd
import pytest

from app.ml.optimization_boundary_pilot import BoundaryModel, fit_scope, group_quantile
from app.ml.optimization_data import TARGETS


def catalog():
    rows = []
    for i in range(24):
        for port in range(2):
            row = dict(
                structure_id=f"s{i}_{port}",
                design_group_id=f"d{i}",
                branch_id="pf0_eg1_fl1",
                eg_um=1.0,
                fl_um=0.5,
                area_um2=700 + i * 80 + port * 30,
                has_pf=0,
            )
            for j, target in enumerate(TARGETS):
                row[target] = (j + 1) * (10 + i * 0.1 + port * 0.2)
            row["role"] = "train" if i < 10 else "calibration" if i < 20 else "test"
            rows.append(row)
    frame = pd.DataFrame(rows)
    return frame.drop(columns="role"), frame[["structure_id", "design_group_id", "role"]]


def test_group_max_joint_rank_and_insufficient_groups():
    groups = np.repeat(np.arange(10), 2)
    errors = np.column_stack([np.arange(20), np.zeros(20)])
    q = group_quantile(groups, errors)
    assert q["rank"] == 10 and q["quantile"] == 19
    assert q["scores_by_group"]["0"] == 1
    assert group_quantile(groups[:2], errors[:2])["quantile"] is None
    errors[0, 1] = np.nan
    with pytest.raises(ValueError, match="finite"):
        group_quantile(groups, errors)


def test_test_labels_cannot_change_models_or_calibration():
    structures, manifest = catalog()
    a, models, calibration, _ = fit_scope(structures, manifest, "area_edges")
    poisoned = structures.copy()
    poisoned.loc[manifest.role.eq("test"), list(TARGETS)] *= 100
    b, _, other_calibration, _ = fit_scope(poisoned, manifest, "area_edges")
    np.testing.assert_array_equal(a.prediction, b.prediction)
    np.testing.assert_array_equal(a.lower, b.lower)
    assert calibration == other_calibration
    state = models[("gp", TARGETS[0])].branches["pf0_eg1_fl1"]
    assert state["low"][2] == 700 and state["scale"][2] == 750
    gp = a[a.model.str.startswith("gp")]
    pivot = gp.pivot(index=["structure_id", "target"], columns="model", values="prediction")
    np.testing.assert_array_equal(pivot.iloc[:, 0], pivot.iloc[:, 1])
    widths = gp.assign(width=gp.upper - gp.lower).pivot(
        index=["structure_id", "target"], columns="model", values="width"
    )
    assert (widths.gp_matern_scaled_joint >= widths.gp_matern_scaled_marginal).all()


def test_calibration_labels_cannot_change_point_predictions():
    structures, manifest = catalog()
    a, _, _, _ = fit_scope(structures, manifest, "area_edges")
    structures.loc[manifest.role.eq("calibration"), list(TARGETS)] *= 10
    b, _, _, _ = fit_scope(structures, manifest, "area_edges")
    np.testing.assert_array_equal(a.prediction, b.prediction)
    assert (b.upper > a.upper).all()


def test_group_leakage_or_forged_group_manifest_rejected():
    structures, manifest = catalog()
    manifest.loc[0, "role"] = "test"
    with pytest.raises(ValueError, match="leakage"):
        fit_scope(structures, manifest, "area_edges")
    manifest.loc[0, "design_group_id"] = "forged"
    with pytest.raises(ValueError, match="differ"):
        fit_scope(structures, manifest, "area_edges")


def test_unknown_topology_does_not_get_a_prediction():
    structures, manifest = catalog()
    model = BoundaryModel("gp", TARGETS[0]).fit(structures[manifest.role.eq("train")])
    unknown = structures.iloc[:1].assign(branch_id="pf1_eg0_fl0")
    mean, std = model.predict_log(unknown)
    assert np.isnan(mean).all() and np.isnan(std).all()


def simulation_rows():
    from scripts.audit_optimization_sim_p6a import AXES, CONTROLS

    rows = []
    for i, width in enumerate([0.5, 1.0, 1.5]):
        row = dict.fromkeys(AXES + CONTROLS, 1)
        row.update(
            Training_Row_ID=f"r{i}",
            Stage="D1",
            Template="T11",
            Variant="STANDARD",
            EG_State="patterned",
            Train_L_FL_um=width,
            Train_PF_nm=np.nan,
            has_PF=0,
            Train_L_top_um=30,
            Train_L_bot_um=46,
            Train_L_air_um=40,
            Cross_Source_Status="CONSISTENT",
            extraction_status="ok",
            passivity_flag=False,
        )
        for axis in AXES:
            row[axis.replace("Train_", "SNP_")] = row[axis]
        for metric in ["fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "bodeq_fitted"]:
            row[metric] = 10 + i
        rows.append(row)
    return pd.DataFrame(rows)


def test_simulation_unknown_present_pf_quarantined_and_height_controlled():
    from scripts.audit_optimization_sim_p6a import prepare_geometry, trend_tables

    frame = simulation_rows()
    prepared = prepare_geometry(frame)
    assert prepared.trend_eligible.all()
    trends, _, _ = trend_tables(prepared)
    assert len(trends) == 12  # Six metrics, separate broad and consistent-only cohorts.
    frame.loc[0, "has_PF"] = 1
    assert not prepare_geometry(frame).trend_eligible.iloc[0]
    frame = simulation_rows()
    frame.loc[0, ["Train_h_FL_nm", "SNP_h_FL_nm"]] = 70
    trends, _, _ = trend_tables(prepare_geometry(frame))
    assert trends.empty  # Different height cannot supply the third width level.


def test_simulation_duplicate_axis_levels_are_not_averaged():
    from scripts.audit_optimization_sim_p6a import prepare_geometry, trend_tables

    frame = simulation_rows()
    frame = pd.concat([frame, frame.iloc[:1].assign(Training_Row_ID="duplicate")])
    trends, _, rejected = trend_tables(prepare_geometry(frame))
    assert trends.empty and len(rejected) == 2
