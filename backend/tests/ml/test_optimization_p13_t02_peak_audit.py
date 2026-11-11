import numpy as np
import pytest

from app.ml.optimization_ablation import AblationModel
from app.ml.optimization_p13.audit import (
    InferenceMismatchError,
    decimal_delta,
    decompose_trend_gp,
    make_geometry_frame,
    nine_point_neighborhood,
    verify_numeric_match,
)


class ConstantTrend:
    def __init__(self, value):
        self.value = value

    def predict(self, x):
        return np.full(len(x), self.value)


class ConstantGP:
    def predict_log(self, frame):
        return np.full(len(frame), 0.2), np.full(len(frame), 0.01)


def test_trend_residual_decomposition_matches_existing_predict_log() -> None:
    model = AblationModel("trend_gp_log_area", "k2eff_pct")
    model.gp = ConstantGP()
    model.trends = {
        "pf0_eg1_fl1": (
            np.zeros(3),
            np.ones(3),
            ConstantTrend(0.5),
        )
    }
    frame = make_geometry_frame([(3.0, 0.025, 5500.0, 0)])

    result = decompose_trend_gp(model, frame)
    existing_mean, existing_std = model.predict_log(frame)

    assert result["trend_log"].item() == pytest.approx(0.5)
    assert result["residual_log"].item() == pytest.approx(0.2)
    assert result["total_log"].item() == pytest.approx(existing_mean.item())
    assert result["std"].item() == pytest.approx(existing_std.item())
    assert result["trend_only"].item() == pytest.approx(np.exp(0.5))
    assert result["residual_factor"].item() == pytest.approx(np.exp(0.2))
    assert result["prediction"].item() == pytest.approx(np.exp(0.7))


def test_fl_zero_uses_a_distinct_branch_and_neighborhood_has_nine_points() -> None:
    points = nine_point_neighborhood(3.0, 0.025)
    frame = make_geometry_frame([(eg, fl, 5500.0, 0) for eg, fl in points])

    assert len(frame) == 9
    assert set(frame.loc[frame.fl_um.eq(0), "branch_id"]) == {"pf0_eg1_fl0"}
    assert set(frame.loc[frame.fl_um.gt(0), "branch_id"]) == {"pf0_eg1_fl1"}
    assert frame.fl_um.min() == 0
    assert set(frame.eg_um) == {2.975, 3.0, 3.025}
    assert set(frame.fl_um) == {0.0, 0.025, 0.05}


def test_decimal_delta_uses_full_saved_precision() -> None:
    baseline = "14.674470284168734"
    target = "15.674470284168734"
    prediction = "15.639782124032903"

    assert decimal_delta(prediction, baseline) == "0.965311839864169"
    assert decimal_delta(prediction, target) == "-0.034688160135831"


def test_numeric_mismatch_is_an_inference_code_error() -> None:
    result = verify_numeric_match([1.0, 2.0], [1.0, 2.0], "identity")

    assert result["max_abs_difference"] == 0
    assert result["all_close"] is True
    with pytest.raises(InferenceMismatchError, match="inference/code mismatch"):
        verify_numeric_match([1.0], [1.1], "broken")


def test_nonfinite_prediction_is_rejected_as_code_error() -> None:
    with pytest.raises(InferenceMismatchError, match="non-finite"):
        verify_numeric_match([np.nan], [1.0], "nan")
