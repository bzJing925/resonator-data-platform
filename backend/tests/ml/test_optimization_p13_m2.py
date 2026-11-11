from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.optimization_ablation import AblationModel
from app.ml.optimization_p13.contracts import (
    ContractError,
    invoke_geometry_model,
    k2_from_frequency,
    load_protocol,
    p6c_control_spec,
)
from app.ml.optimization_p13.m2 import (
    M2_FIT_COLUMNS,
    M2Config,
    M2FrequencyModel,
    UnavailableM2FrequencyModel,
    configurations_from_protocol,
)
from scripts.run_optimization_p13_t08_m2 import (
    aggregation_record,
    baseline_aggregation_record,
    select_side_configurations,
)

BACKEND = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = (
    BACKEND / "surrogate_data/optimization_p13_20261027_r01/contracts/protocol.json"
)


def protocol():
    return load_protocol(PROTOCOL_PATH)


def config(recipe="linear", alpha=0.1):
    return M2Config(0, f"m2_{recipe}_test", recipe, alpha)


def fit_frame():
    rows = []
    for index, (group, eg, fl, area) in enumerate(
        (
            ("d_a", 0.5, 0.5, 1000.0),
            ("d_a", 1.0, 1.0, 2000.0),
            ("d_b", 1.5, 1.5, 3000.0),
            ("d_b", 2.0, 1.75, 4000.0),
        )
    ):
        fs = 4.5 + 0.1 * eg + 0.02 * fl
        fp = fs + 0.4 + 0.03 * eg
        rows.append(
            {
                "structure_id": f"s_{index}",
                "design_group_id": group,
                "branch_id": "pf0_eg1_fl1",
                "eg_um": eg,
                "fl_um": fl,
                "area_um2": area,
                "has_pf": 0,
                "fs_ghz": fs,
                "fp_ghz": fp,
            }
        )
    return pd.DataFrame(rows, columns=list(M2_FIT_COLUMNS))


def geometry(index=0):
    row = fit_frame().iloc[index]
    return {
        "eg_um": row.eg_um,
        "fl_um": row.fl_um,
        "area_um2": row.area_um2,
        "has_pf": 0,
    }


def test_frozen_ten_configurations_match_o02():
    configurations = configurations_from_protocol(protocol())
    assert len(configurations) == 10
    assert [value.grid_index for value in configurations] == list(range(10))
    assert [value.alpha for value in configurations[:5]] == [0.01, 0.1, 1.0, 10.0, 100.0]
    assert {value.feature_recipe for value in configurations} == {
        "linear",
        "additive_quadratic",
    }


def test_fit_weights_and_fit_only_preprocessing():
    model = M2FrequencyModel(config(), "pf0_eg1_fl1").fit(fit_frame())
    statistics = model.fit_statistics_
    np.testing.assert_allclose(statistics["weights"], [0.25, 0.25, 0.25, 0.25])
    assert statistics["group_weight_sums"] == {"d_a": 0.5, "d_b": 0.5}
    raw = model._raw_features(fit_frame())
    expected_mean = 0.25 * raw.sum(axis=0)
    np.testing.assert_allclose(statistics["raw_x_mean"], expected_mean)
    assert statistics["kept_feature_indices"] == [0, 1, 2]
    assert model.single_target_fit_calls == 2


def test_quadratic_recipe_and_deterministic_geometry_prediction():
    first = M2FrequencyModel(config("additive_quadratic"), "pf0_eg1_fl1").fit(fit_frame())
    second = M2FrequencyModel(config("additive_quadratic"), "pf0_eg1_fl1").fit(fit_frame())
    assert len(first.fit_statistics_["kept_feature_indices"]) == 6
    left = first.predict(geometry())
    right = second.predict(geometry())
    assert left == right
    assert left["valid_prediction"] is True
    assert left["pred_fs_ghz"] > 0
    assert left["pred_fp_ghz"] > left["pred_fs_ghz"]
    assert 0 < left["pred_frequency_ratio"] < 1
    assert left["aggregation_estimand_match"] is False
    assert left["qualified"] is False
    assert left["p3_comparable_prediction"] is None


def test_inference_rejects_measured_fields_and_branch_mismatch():
    model = M2FrequencyModel(config(), "pf0_eg1_fl1").fit(fit_frame())
    geometry_with_extra = fit_frame().assign(extra=1)[
        ["eg_um", "fl_um", "area_um2", "has_pf", "extra"]
    ]
    with pytest.raises(ValueError, match="exactly four geometry"):
        model.predict_frame(geometry_with_extra)
    with pytest.raises(ContractError) as error:
        invoke_geometry_model(model, geometry() | {"fs_ghz": 5.0})
    assert error.value.reason == "MEASURED_INPUT_FORBIDDEN"
    with pytest.raises(ContractError) as error:
        model.predict(geometry() | {"fl_um": 0.0})
    assert error.value.reason == "UNSUPPORTED_BRANCH"


def test_insufficient_branch_returns_null_not_clipped():
    with pytest.raises(ValueError, match="FIT_UNAVAILABLE"):
        M2FrequencyModel(config(), "pf0_eg1_fl1").fit(fit_frame().iloc[:1])
    model = UnavailableM2FrequencyModel(config(), "pf0_eg1_fl1")
    result = model.predict(geometry())
    assert result["valid_prediction"] is False
    assert result["invalid_reason"] == "FIT_UNAVAILABLE"
    for field in (
        "pred_fs_ghz",
        "pred_fp_ghz",
        "pred_frequency_ratio",
        "pred_k2eff_pct_native",
        "prediction",
    ):
        assert result[field] is None
    assert result["qualified"] is False


def test_aggregation_and_baseline_records_keep_estimand_separate():
    source = fit_frame().iloc[0].copy()
    source["k2eff_pct"] = k2_from_frequency(source.fs_ghz, source.fp_ghz) + 0.01
    source["n_rows"] = 50
    source["n_coords"] = 50
    source["scope"] = "structure_holdout"
    source["fold"] = "unit_synthetic"
    source["unit_id"] = "unit_synthetic"
    source["family"] = "ordinary"
    source["source_split_sha256"] = "b" * 64
    prediction = {
        "pred_k2eff_pct_native": 99.0,
        "valid_prediction": True,
        "invalid_reason": None,
    }
    row = aggregation_record(source, prediction, "m2_linear_test")
    assert row["aggregation_estimand_match"] is False
    assert row["qualified_model_set_eligible"] is False
    assert row["pred_k2eff_pct_p3_comparable"] is None
    assert row["observed_aggregation_delta_pp"] == pytest.approx(-0.01)
    assert row["pred_native_k2_minus_measured_p3_pp"] > 0
    baseline = baseline_aggregation_record(source)
    assert baseline["row_kind"] == "baseline_observation"
    assert baseline["config_id"] is None
    assert baseline["fold"] is None
    assert baseline["prediction_status"] == "OBSERVED"


def test_side_selection_uses_frequency_score_and_never_qualifies():
    scores = []
    configurations = configurations_from_protocol(protocol())
    for config_value in configurations:
        for scope in ("structure_holdout", "area_edges", "eg_upper", "fl_upper"):
            for family in ("ordinary", "eg_upper", "fl_lower", "joint_eg_upper_fl_lower"):
                scores.append(
                    {
                        "config_id": config_value.config_id,
                        "scope": scope,
                        "unit_id": f"{scope}_{family}",
                        "family": family,
                        "branch_id": "all_branches",
                        "status": "PASS",
                        "score": float(config_value.grid_index),
                        "structure_count": 2,
                        "design_group_count": 1,
                    }
                )
    selected = select_side_configurations(protocol(), scores)
    assert len(selected) == 4
    assert all(row["selected_config_id"] == configurations[0].config_id for row in selected)
    assert all(row["qualified_model_set_eligible"] is False for row in selected)


def test_p6c_recipe_is_fixed_and_synthetic_fit_has_no_optimizer():
    spec = p6c_control_spec(protocol())
    model = AblationModel(spec.recipe, spec.target)
    frame = fit_frame()
    frame["k2eff_pct"] = [
        k2_from_frequency(fs, fp)
        for fs, fp in zip(frame.fs_ghz, frame.fp_ghz, strict=True)
    ]
    model.fit(
        frame[
            [
                "structure_id",
                "design_group_id",
                "branch_id",
                "eg_um",
                "fl_um",
                "area_um2",
                "has_pf",
                "k2eff_pct",
            ]
        ]
    )
    mean, _ = model.predict_log(frame)
    assert np.isfinite(mean).all()
    assert model.recipe == "trend_gp_log_area"
    assert model.gp.branches["pf0_eg1_fl1"]["model"].optimizer is None


def test_native_k2_formula_percent_not_fraction():
    value = k2_from_frequency(1.0, 5.0)
    assert value == pytest.approx(4 * math.pi**2, abs=1e-12)
    assert value > 1
