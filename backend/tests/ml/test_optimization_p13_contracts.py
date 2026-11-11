from __future__ import annotations

import copy
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ml.optimization_p13.contracts import (
    BASELINE_DESIGN_GROUP_ID,
    INTERFACE_VERSION,
    O02_SHA256,
    SCHEMA_VERSION,
    ContractError,
    build_schema_document,
    frequency_supervision,
    instantiate_p6c_control,
    invoke_geometry_model,
    k2_from_frequency,
    load_protocol,
    m2_inverse_prediction,
    p6c_control_spec,
    validate_geometry,
    validate_inference_input,
    validate_k2_label,
    validate_prediction_record,
    validate_schema_document,
)

BACKEND = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = (
    BACKEND
    / "surrogate_data/optimization_p13_20261027_r01/contracts/protocol.json"
)


def protocol():
    return load_protocol(PROTOCOL_PATH)


def reason(error: pytest.ExceptionInfo) -> str:
    return error.value.reason


def evaluation_record():
    return {
        "schema_version": SCHEMA_VERSION,
        "interface_version": INTERFACE_VERSION,
        "record_kind": "evaluation",
        "model_family": "M1",
        "structure_id": "s_1",
        "candidate_id": None,
        "design_group_id": "d_1",
        "scope": "structure_holdout",
        "fold": "unit_1",
        "unit_id": "unit_1",
        "family": "ordinary",
        "role": "evaluation",
        "branch_id": "pf0_eg1_fl1",
        "model_id": "m1_test",
        "config_id": "m1_test_config",
        "target": "k2eff_pct",
        "unit": "percent_number",
        "prediction": 12.0,
        "actual": 11.5,
        "valid_prediction": True,
        "invalid_reason": None,
        "support_label": "O05_INNER_EVALUATION",
        "source_mode": "deembedded",
        "label_source_file_id": "P3_STRUCTURES",
        "source_file_id": "P3_STRUCTURES",
        "source_sha256": "a" * 64,
        "source_split_file_id": "P3_SPLIT_STRUCTURE_HOLDOUT",
        "source_split_sha256": "b" * 64,
        "source_structure_file_id": "P3_STRUCTURES",
        "source_structure_sha256": "c" * 64,
        "protocol_sha256": O02_SHA256,
        "measured": True,
        "trusted_for_unmeasured_optimization": False,
        "eg_um": 1.0,
        "fl_um": 1.0,
        "area_um2": 5500.0,
        "has_pf": 0,
    }


def inference_record():
    record = evaluation_record() | {
        "record_kind": "inference",
        "role": "inference",
        "candidate_id": "candidate_1",
        "design_group_id": None,
        "design_group_null_reason": "UNMEASURED_CANDIDATE",
        "actual": None,
        "scope": "deployment",
        "fold": "deployment",
        "source_mode": None,
        "label_source_file_id": None,
        "source_file_id": "UNMEASURED_GEOMETRY_REQUEST",
        "measured": False,
        "support_label": "WITHIN_HULL_UNMEASURED_COMBINATION",
    }
    return record


def m2_record():
    native = k2_from_frequency(5.0, 5.5)
    return evaluation_record() | {
        "model_family": "M2",
        "target": "k2eff_pct_native",
        "prediction": native,
        "pred_a": math.log(5.0),
        "pred_z": math.log(0.1),
        "pred_fs_ghz": 5.0,
        "pred_fp_ghz": 5.5,
        "pred_frequency_ratio": 5.0 / 5.5,
        "pred_k2eff_pct_native": native,
        "p3_comparable_prediction": None,
        "p3_comparable_null_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
        "frequency_valid": True,
        "native_k2_valid": True,
        "aggregation_estimand_match": False,
        "qualified": False,
    }


def test_k2_formula_units_and_label_consistency():
    expected = 25 * math.pi**2 * (5.0 / 5.5) * (1 - 5.0 / 5.5)
    assert k2_from_frequency(5.0, 5.5) == pytest.approx(expected, abs=1e-12)
    assert validate_k2_label(5.0, 5.5, expected + 0.005) == pytest.approx(expected + 0.005)
    with pytest.raises(ContractError) as error:
        validate_k2_label(5.0, 5.5, expected + 0.02)
    assert reason(error) == "K2_FORMULA_MISMATCH"


@pytest.mark.parametrize(
    "fs,fp",
    [(math.nan, 5.5), (math.inf, 5.5), (0.0, 5.5), (-1.0, 5.5), (5.5, 5.5), (6.0, 5.5)],
)
def test_invalid_frequency_is_rejected(fs, fp):
    with pytest.raises(ContractError) as error:
        k2_from_frequency(fs, fp)
    assert reason(error) == "INVALID_FREQUENCY"
    with pytest.raises(ContractError):
        frequency_supervision(fs, fp)


def test_frequency_supervision_and_m2_inverse_are_consistent():
    supervision = frequency_supervision(5.0, 5.5)
    result = m2_inverse_prediction(supervision["a"], supervision["z"])
    assert result["pred_fs_ghz"] == pytest.approx(5.0, abs=1e-12)
    assert result["pred_fp_ghz"] == pytest.approx(5.5, abs=1e-12)
    assert result["pred_frequency_ratio"] == pytest.approx(5.0 / 5.5, abs=1e-12)
    assert result["prediction"] == pytest.approx(k2_from_frequency(5.0, 5.5), abs=1e-12)
    assert result["valid_prediction"] is True
    assert result["aggregation_estimand_match"] is False
    assert result["qualified"] is False
    assert result["p3_comparable_prediction"] is None
    assert result["p3_comparable_null_reason"] == "P3_AGGREGATION_ESTIMAND_MISMATCH"


@pytest.mark.parametrize("a,z", [(math.nan, 0.0), (0.0, math.inf), (0.0, -1000.0), (1000.0, 0.0)])
def test_m2_invalid_inverse_returns_null_with_reason(a, z):
    result = m2_inverse_prediction(a, z)
    assert result["valid_prediction"] is False
    assert result["invalid_reason"] == "INVALID_FREQUENCY"
    for field in ("pred_fs_ghz", "pred_fp_ghz", "pred_frequency_ratio", "prediction"):
        assert result[field] is None
    assert result["qualified"] is False


def test_geometry_branch_and_invalid_values():
    geometry = validate_geometry(
        {"eg_um": 1.0, "fl_um": 0.0, "area_um2": 5500.0, "has_pf": 0}
    )
    assert geometry["branch_id"] == "pf0_eg1_fl0"
    assert math.log(geometry["area_um2"]) > 0
    with pytest.raises(ContractError) as error:
        validate_geometry({"fl_um": 1.0, "area_um2": 1.0, "has_pf": 0})
    assert reason(error) == "missing_or_nonfinite_geometry"
    with pytest.raises(ContractError) as error:
        validate_geometry(
            {"eg_um": -1.0, "fl_um": 1.0, "area_um2": 1.0, "has_pf": 0}, "M2"
        )
    assert reason(error) == "INVALID_GEOMETRY"
    with pytest.raises(ContractError) as error:
        validate_geometry({"eg_um": 1.0, "fl_um": 1.0, "area_um2": 0.0, "has_pf": 0})
    assert reason(error) == "nonpositive_area"
    with pytest.raises(ContractError) as error:
        validate_geometry({"eg_um": 1.0, "fl_um": 1.0, "area_um2": 1.0, "has_pf": 2})
    assert reason(error) == "invalid_has_pf"
    with pytest.raises(ContractError) as error:
        validate_geometry(
            {
                "eg_um": 1.0,
                "fl_um": 1.0,
                "area_um2": 1.0,
                "has_pf": 0,
                "branch_id": "pf0_eg1_fl0",
            }
        )
    assert reason(error) == "branch_mismatch"


def test_inference_rejects_measured_and_unknown_fields():
    features = {"eg_um": 1.0, "fl_um": 1.0, "area_um2": 5500.0, "has_pf": 0}
    assert validate_inference_input(features) == features
    with pytest.raises(ContractError) as error:
        validate_inference_input(features | {"fs_ghz": 5.0})
    assert reason(error) == "MEASURED_INPUT_FORBIDDEN"
    with pytest.raises(ContractError) as error:
        validate_inference_input(features | {"sim_k2eff_pct": 10.0})
    assert reason(error) == "RAW_OR_SIMULATION_FIELD_FORBIDDEN"
    with pytest.raises(ContractError) as error:
        validate_inference_input(features | {"unknown": 1})
    assert reason(error) == "UNKNOWN_INFERENCE_FIELD"


def test_geometry_model_invocation_is_versioned_and_geometry_only():
    class MockModel:
        interface_version = INTERFACE_VERSION
        model_id = "mock"
        config_id = "mock_config"
        model_family = "M1"

        def predict(self, features):
            self.features = features
            return {"prediction": 12.0}

    model = MockModel()
    output = invoke_geometry_model(
        model, {"eg_um": 1.0, "fl_um": 1.0, "area_um2": 5500.0, "has_pf": 0}
    )
    assert output == {"prediction": 12.0}
    assert set(model.features) == {"eg_um", "fl_um", "area_um2", "has_pf"}
    with pytest.raises(ContractError) as error:
        invoke_geometry_model(
            model,
            {"eg_um": 1.0, "fl_um": 1.0, "area_um2": 5500.0, "has_pf": 0, "fp_ghz": 5.5},
        )
    assert reason(error) == "MEASURED_INPUT_FORBIDDEN"
    model.interface_version = "wrong"
    with pytest.raises(ContractError) as error:
        invoke_geometry_model(
            model, {"eg_um": 1.0, "fl_um": 1.0, "area_um2": 5500.0, "has_pf": 0}
        )
    assert reason(error) == "INTERFACE_VERSION_MISMATCH"


def test_prediction_record_accepts_evaluation_fit_inference_and_baseline_difference():
    validate_prediction_record(evaluation_record())
    fit = evaluation_record() | {"record_kind": "fit", "role": "fit", "actual": None}
    validate_prediction_record(fit)
    validate_prediction_record(inference_record())
    baseline = evaluation_record() | {
        "record_kind": "baseline_difference",
        "role": "baseline_only",
        "design_group_id": BASELINE_DESIGN_GROUP_ID,
        "fold": "baseline_difference",
        "unit_id": "baseline_difference",
    }
    validate_prediction_record(baseline)
    validate_prediction_record(m2_record())


def test_prediction_record_rejects_unknown_raw_sim_and_measured_misuse():
    with pytest.raises(ContractError) as error:
        validate_prediction_record(evaluation_record() | {"unknown": 1})
    assert reason(error) == "UNKNOWN_FIELD"
    with pytest.raises(ContractError) as error:
        validate_prediction_record(evaluation_record() | {"sim_k2eff_pct": 10.0})
    assert reason(error) == "RAW_OR_SIMULATION_FIELD_FORBIDDEN"
    with pytest.raises(ContractError) as error:
        validate_prediction_record(inference_record() | {"actual": 10.0})
    assert reason(error) == "MEASURED_VALUE_FORBIDDEN"
    raw_fit = evaluation_record() | {
        "record_kind": "fit",
        "role": "fit",
        "actual": None,
        "source_mode": "raw",
    }
    with pytest.raises(ContractError) as error:
        validate_prediction_record(raw_fit)
    assert reason(error) == "RAW_OR_SIMULATION_SOURCE_FORBIDDEN"
    with pytest.raises(ContractError) as error:
        validate_prediction_record(evaluation_record() | {"source_sha256": "bad"})
    assert reason(error) == "SOURCE_HASH_INVALID"


def test_null_prediction_requires_null_and_reason_not_zero():
    record = evaluation_record() | {
        "valid_prediction": False,
        "prediction": 0.0,
        "invalid_reason": "nonfinite_model_output",
    }
    with pytest.raises(ContractError) as error:
        validate_prediction_record(record)
    assert reason(error) == "NULL_REQUIRED"
    record["prediction"] = None
    validate_prediction_record(record)
    record["invalid_reason"] = None
    with pytest.raises(ContractError) as error:
        validate_prediction_record(record)
    assert reason(error) == "NULL_REASON_REQUIRED"
    with pytest.raises(ContractError) as error:
        validate_prediction_record(inference_record() | {"design_group_null_reason": None})
    assert reason(error) == "NULL_REASON_REQUIRED"


def test_m2_record_cannot_override_aggregation_or_qualification():
    with pytest.raises(ContractError) as error:
        validate_prediction_record(m2_record() | {"aggregation_estimand_match": True})
    assert reason(error) == "M2_AGGREGATION_CONTRACT"
    with pytest.raises(ContractError) as error:
        validate_prediction_record(m2_record() | {"qualified": True})
    assert reason(error) == "M2_QUALIFIED_CONTRACT"
    with pytest.raises(ContractError) as error:
        validate_prediction_record(m2_record() | {"p3_comparable_prediction": 12.0})
    assert reason(error) == "M2_AGGREGATION_CONTRACT"


def test_p6c_fixed_recipe_and_injected_factory():
    spec = p6c_control_spec(protocol())
    assert spec.recipe == "trend_gp_log_area"
    assert spec.target == "k2eff_pct"

    class FakeModel:
        def __init__(self, recipe, target):
            self.recipe = recipe
            self.target = target

    def fake_import(name):
        assert name == "app.ml.optimization_ablation"
        return SimpleNamespace(AblationModel=FakeModel)

    model = instantiate_p6c_control(spec, fake_import)
    assert model.recipe == "trend_gp_log_area"
    assert model.target == "k2eff_pct"
    changed = copy.deepcopy(protocol())
    changed["models"]["P6C_control"]["recipe"] = "gp_raw"
    with pytest.raises(ContractError) as error:
        p6c_control_spec(changed)
    assert reason(error) == "P6C_RECIPE_CHANGED"


def test_schema_document_preserves_o02_and_downstream_fields():
    document = build_schema_document(protocol())
    validate_schema_document(document, protocol())
    assert document["o02_sha256"] == O02_SHA256
    assert document["model_interfaces"]["M2"]["aggregation_estimand_match"] is False
    assert document["model_interfaces"]["M2"]["qualified"] is False
    assert document["evaluation"]["acceptance"] == protocol()["evaluation"]["acceptance"]
    assert document["numerical_reproduction"] == protocol()["numerical_reproduction"]
    for field in (
        "structure_id",
        "design_group_id",
        "scope",
        "fold",
        "role",
        "branch_id",
        "model_id",
        "config_id",
        "target",
        "prediction",
        "support_label",
        "source_sha256",
    ):
        assert field in document["artifact_contracts"]["O07"]["required_fields"]
    for field in ("pred_fs_ghz", "pred_fp_ghz", "pred_k2eff_pct_native"):
        assert field in document["artifact_contracts"]["O08"]["required_fields"]
    with pytest.raises(ContractError) as error:
        load_protocol(PROTOCOL_PATH, "0" * 64)
    assert reason(error) == "O02_HASH_MISMATCH"


def test_control_measurement_and_m2_branch_contracts():
    with pytest.raises(ContractError) as error:
        validate_prediction_record(
            evaluation_record() | {"record_kind": "control", "model_family": "M1"}
        )
    assert reason(error) == "CONTROL_MODEL_REQUIRED"
    validate_prediction_record(
        evaluation_record() | {"record_kind": "control", "model_family": "P6C"}
    )
    with pytest.raises(ContractError) as error:
        validate_prediction_record(evaluation_record() | {"measured": False})
    assert reason(error) == "MEASUREMENT_FLAG_REQUIRED"
    with pytest.raises(ContractError) as error:
        validate_geometry(
            {
                "eg_um": 1.0,
                "fl_um": 1.0,
                "area_um2": 1.0,
                "has_pf": 0,
                "branch_id": "pf0_eg1_fl0",
            },
            "M2",
        )
    assert reason(error) == "UNSUPPORTED_BRANCH"


def test_crosscheck_record_unit_id_required():
    record = evaluation_record()
    record.pop("unit_id")
    with pytest.raises(ContractError) as error:
        validate_prediction_record(record)
    assert reason(error) == "MISSING_FIELD"


def test_crosscheck_fold_must_match_unit_id():
    with pytest.raises(ContractError) as error:
        validate_prediction_record(evaluation_record() | {"fold": "different_unit"})
    assert reason(error) == "FOLD_UNIT_MISMATCH"


def test_crosscheck_record_family_required():
    record = evaluation_record()
    record.pop("family")
    with pytest.raises(ContractError) as error:
        validate_prediction_record(record)
    assert reason(error) == "MISSING_FIELD"


def test_crosscheck_source_file_ids_required():
    record = evaluation_record()
    record.pop("source_split_file_id")
    record.pop("source_structure_file_id")
    with pytest.raises(ContractError) as error:
        validate_prediction_record(record)
    assert reason(error) == "MISSING_FIELD"


def test_crosscheck_m1_invalid_reason_vocabulary():
    record = evaluation_record() | {
        "valid_prediction": False,
        "prediction": None,
        "invalid_reason": "not_an_o02_reason",
    }
    with pytest.raises(ContractError) as error:
        validate_prediction_record(record)
    assert reason(error) == "INVALID_REASON_VOCABULARY"


def test_crosscheck_inference_raw_mode_rejected():
    with pytest.raises(ContractError) as error:
        validate_prediction_record(inference_record() | {"source_mode": "raw"})
    assert reason(error) == "RAW_OR_SIMULATION_SOURCE_FORBIDDEN"


@pytest.mark.parametrize(
    "changes",
    [
        {"pred_fs_ghz": 5.5, "pred_fp_ghz": 5.0},
        {"pred_frequency_ratio": 1.1},
        {"frequency_valid": False},
        {"native_k2_valid": False},
    ],
)
def test_crosscheck_m2_frequency_and_flag_consistency(changes):
    with pytest.raises(ContractError):
        validate_prediction_record(m2_record() | changes)
