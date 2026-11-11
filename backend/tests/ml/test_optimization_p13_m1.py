import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.ml.optimization_ablation import AblationModel
from app.ml.optimization_p13.contracts import (
    ContractError,
    invoke_geometry_model,
    load_protocol,
    validate_prediction_record,
)
from app.ml.optimization_p13.m1 import M1SplineRidgeModel
from scripts.run_optimization_p13_m1 import make_prediction_record, validate_outputs

RUN = Path(
    "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01"
)


def config(config_id="m1_d1_k4_a1", degree=1, n_knots=4, alpha=1.0, grid_index=0):
    return {
        "config_id": config_id,
        "degree": degree,
        "n_knots": n_knots,
        "alpha": alpha,
        "grid_index": grid_index,
    }


def test_frozen_grid_is_exactly_o02():
    protocol = load_protocol(RUN / "contracts/protocol.json")
    configurations = protocol["models"]["M1"]["configurations"]
    assert len(configurations) == 12
    assert configurations[0]["config_id"] == "m1_d1_k4_a1"
    assert [row["grid_index"] for row in configurations] == list(range(12))


def test_intercept_only_and_refit_determinism():
    frame = pd.DataFrame(
        {
            "structure_id": ["a", "b"],
            "design_group_id": ["ga", "gb"],
            "branch_id": ["pf0_eg1_fl1"] * 2,
            "eg_um": [1.0, 1.0],
            "fl_um": [1.0, 1.0],
            "area_um2": [5500.0, 5500.0],
            "has_pf": [0, 0],
            "k2eff_pct": [10.0, 20.0],
        }
    )
    model = M1SplineRidgeModel(config()).fit(frame)
    state = model.branches["pf0_eg1_fl1"]
    assert state["intercept_only"] is True
    assert state["dropped_features"] == ["eg_um", "fl_um", "log_area_um2"]
    prediction = model.predict(
        {"eg_um": 1.0, "fl_um": 1.0, "area_um2": 5500.0, "has_pf": 0}
    )
    assert prediction["prediction"] == pytest.approx(math.sqrt(200.0))
    second = M1SplineRidgeModel(config()).fit(frame)
    np.testing.assert_allclose(model.predict_log(frame), second.predict_log(frame), atol=0, rtol=0)


def test_spline_linear_extrapolation_and_constant_drop():
    frame = pd.DataFrame(
        {
            "structure_id": list("abcd"),
            "design_group_id": list("abcd"),
            "branch_id": ["pf0_eg1_fl1"] * 4,
            "eg_um": [0.75, 1.0, 1.5, 2.0],
            "fl_um": [0.5] * 4,
            "area_um2": [5500.0] * 4,
            "has_pf": [0] * 4,
            "k2eff_pct": np.exp(2 + 0.1 * np.array([0.75, 1.0, 1.5, 2.0])),
        }
    )
    model = M1SplineRidgeModel(config()).fit(frame)
    state = model.branches["pf0_eg1_fl1"]
    assert state["intercept_only"] is False
    assert state["features"] == ["eg_um"]
    assert state["dropped_features"] == ["fl_um", "log_area_um2"]
    prediction = model.predict(
        {"eg_um": 3.0, "fl_um": 0.5, "area_um2": 5500.0, "has_pf": 0}
    )
    assert prediction["valid_prediction"] is True
    assert prediction["prediction"] > 0
    assert prediction["prediction"] == pytest.approx(math.exp(2.3), rel=0.2)


def test_geometry_only_unknown_branch_and_measured_input():
    frame = pd.DataFrame(
        {
            "structure_id": ["a"],
            "design_group_id": ["ga"],
            "branch_id": ["pf0_eg1_fl1"],
            "eg_um": [1.0],
            "fl_um": [1.0],
            "area_um2": [5500.0],
            "has_pf": [0],
            "k2eff_pct": [10.0],
        }
    )
    model = M1SplineRidgeModel(config()).fit(frame)
    unknown = model.predict(
        {"eg_um": 0.0, "fl_um": 0.0, "area_um2": 1000.0, "has_pf": 0}
    )
    assert unknown["valid_prediction"] is False
    assert unknown["invalid_reason"] == "unknown_or_empty_branch"
    assert unknown["prediction"] is None
    with pytest.raises(ContractError) as error:
        invoke_geometry_model(
            model,
            {
                "eg_um": 1.0,
                "fl_um": 1.0,
                "area_um2": 5500.0,
                "has_pf": 0,
                "fs_ghz": 5.0,
            },
        )
    assert error.value.reason == "MEASURED_INPUT_FORBIDDEN"
    with pytest.raises(ContractError) as error:
        model.predict(
            {"eg_um": -1.0, "fl_um": 1.0, "area_um2": 5500.0, "has_pf": 0}
        )
    assert error.value.reason == "negative_eg_or_fl"


def test_nonphysical_prediction_is_null_not_clipped():
    frame = pd.DataFrame(
        {
            "structure_id": ["a"],
            "design_group_id": ["ga"],
            "branch_id": ["pf0_eg1_fl1"],
            "eg_um": [1.0],
            "fl_um": [1.0],
            "area_um2": [5500.0],
            "has_pf": [0],
            "k2eff_pct": [10.0],
        }
    )
    model = M1SplineRidgeModel(config()).fit(frame)
    model.branches["pf0_eg1_fl1"]["log_mean"] = math.log(200.0)
    result = model.predict(
        {"eg_um": 1.0, "fl_um": 1.0, "area_um2": 5500.0, "has_pf": 0}
    )
    assert result["valid_prediction"] is False
    assert result["invalid_reason"] == "non_physical_k2_prediction"
    assert result["prediction"] is None


def prediction_row():
    return SimpleNamespace(
        structure_id="s_1",
        design_group_id="d_1",
        scope="structure_holdout",
        unit_id="unit_1",
        family="ordinary",
        branch_id="pf0_eg1_fl1",
        source_file_id="P3_SPLIT_STRUCTURE_HOLDOUT",
        source_sha256="a" * 64,
        source_split_file_id="P3_SPLIT_STRUCTURE_HOLDOUT",
        source_split_sha256="b" * 64,
        source_structure_file_id="P3_STRUCTURES",
        source_structure_sha256="c" * 64,
        k2eff_pct=12.0,
        eg_um=1.0,
        fl_um=1.0,
        area_um2=5500.0,
        has_pf=0,
    )


def test_prediction_record_matches_o06_and_null_contract():
    record = make_prediction_record(
        prediction_row(),
        model_family="M1",
        model_id="m1_test",
        config_id="m1_d1_k4_a1",
        prediction=12.5,
        valid=True,
        reason=None,
    )
    validate_prediction_record(record)
    invalid = make_prediction_record(
        prediction_row(),
        model_family="M1",
        model_id="m1_test",
        config_id="m1_d1_k4_a1",
        prediction=None,
        valid=False,
        reason="unknown_or_empty_branch",
    )
    validate_prediction_record(invalid)
    invalid["prediction"] = 0.0
    with pytest.raises(ContractError) as error:
        validate_prediction_record(invalid)
    assert error.value.reason == "NULL_REQUIRED"


def test_p6c_recipe_object_is_frozen_without_fit():
    model = AblationModel("trend_gp_log_area", "k2eff_pct")
    assert model.recipe == "trend_gp_log_area"
    assert model.target == "k2eff_pct"
    assert model.trends == {}


def test_validate_outputs_separates_configurations():
    required = pd.DataFrame(
        [
            {
                "unit_id": "unit_1",
                "structure_id": "s_1",
                "design_group_id": "d_1",
                "scope": "structure_holdout",
                "fold": "unit_1",
                "role": "evaluation",
                "branch_id": "pf0_eg1_fl1",
            }
        ]
    )
    inner = [
        make_prediction_record(
            prediction_row(),
            model_family="M1",
            model_id="m1_test",
            config_id=config_id,
            prediction=12.5,
            valid=True,
            reason=None,
        )
        for config_id in ("m1_d1_k4_a1", "m1_d1_k3_a0p1")
    ]
    control = [
        make_prediction_record(
            prediction_row(),
            model_family="P6C",
            model_id="p6c_test",
            config_id="trend_gp_log_area",
            prediction=12.5,
            valid=True,
            reason=None,
        )
    ]
    columns = list(inner[0])
    inner_frame, control_frame = validate_outputs(
        inner, control, columns, required, expected_count=1
    )
    assert len(inner_frame) == 2
    assert len(control_frame) == 1
