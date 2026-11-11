import numpy as np
import pandas as pd
import pytest

from app.ml.optimization_ablation import AblationModel, inner_folds, select_recipe
from app.ml.optimization_boundary_pilot import BoundaryModel
from scripts.run_optimization_p6c import partitions
from tests.ml.test_optimization_p6a import catalog


def test_control_reproduces_existing_gp_and_inputs_unchanged():
    frame, _ = catalog()
    original = frame.copy(deep=True)
    a = AblationModel("gp_raw", "qp").fit(frame).predict_log(frame)
    b = BoundaryModel("gp", "qp").fit(frame).predict_log(frame)
    for x, y in zip(a, b, strict=True):
        np.testing.assert_array_equal(x, y)
    AblationModel("trend_gp_log_area", "qp").fit(frame)
    pd.testing.assert_frame_equal(frame, original)


def test_inner_groups_are_indivisible_and_outer_labels_cannot_change_selection():
    frame, manifest = catalog()
    fit, _, _ = partitions(frame, manifest, "area_edges")
    selected, scores, folds, pred = select_recipe(fit)
    assert folds.groupby("design_group_id").fold.nunique().eq(1).all()
    assert set(folds.structure_id) == set(fit.structure_id)
    frame.loc[
        ~frame.structure_id.isin(fit.structure_id), ["qp", "qs", "k2eff_pct", "bodeq_fitted"]
    ] *= 100
    newfit, _, _ = partitions(frame, manifest, "area_edges")
    other, otherscores, _, otherpred = select_recipe(newfit)
    assert other == selected and otherscores == scores
    pd.testing.assert_frame_equal(pred, otherpred)


def test_invalid_area_and_group_split_fail_closed():
    frame, manifest = catalog()
    with pytest.raises(ValueError, match="area"):
        AblationModel("gp_log_area", "qp").fit(frame.assign(area_um2=0))
    manifest.loc[0, "role"] = "test"
    with pytest.raises(ValueError, match="leakage"):
        partitions(frame, manifest, "area_edges")
    with pytest.raises(ValueError, match="Four groups"):
        inner_folds(frame.iloc[:4])
