from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.run_optimization_p13_t12 import (
    FORMAL_OUTPUTS,
    T12Error,
    audit_no_model_execution,
    build_failure_controls,
    build_search_protocol,
    validate_generated_outputs,
    validate_o09,
    write_json,
)


def candidate_frame():
    rows = []
    for index in range(20):
        negative = index >= 17
        rows.append(
            {
                "candidate_id": f"c_{index:02d}",
                "group": "C" if negative else "P11" if index >= 7 else "P12",
                "origin": "negative_control" if negative else "peak_k2eff_pct_p3",
                "eg_um": str(index / 10),
                "fl_um": "0.025" if index == 0 else "1.5",
                "k2eff_pct_prediction": f"{14 + index / 1000:.17f}",
                "risk_flags": "" if index == 0 else "existing_source_text",
            }
        )
    return pd.DataFrame(rows)


def bundle():
    return {
        "protocol": {
            "search": {
                "no_eligible_model_rule": "stop_without_eligible_models",
                "base": {"pf": 0, "area_um2": 5500.0, "eg_um": {"max": 3.0}},
                "extension": {"count": 1, "eg_um": {"max": 4.0}},
            }
        },
        "hashes": {
            "O02_PROTOCOL": "o02",
            "O09_SELECTION": "selection",
            "O09_ELIGIBLE_MODELS": "eligible",
            "O09_ARTIFACT_MANIFEST": "o09_manifest",
            "O01_INPUT_MANIFEST": "o01",
            "O01_ARTIFACT_MANIFEST": "o01_manifest",
            "P12_CANDIDATES": "p12_hash",
            "P12_OUTPUTS_MANIFEST": "p12_manifest",
        },
        "candidates": candidate_frame(),
    }


def test_failure_controls_preserve_17_plus_3_rows_and_original_text():
    source = candidate_frame()
    source_sha256 = "a" * 64
    controls = build_failure_controls(source, source_sha256)
    assert len(controls) == 20
    assert controls.control_type.value_counts().to_dict() == {
        "nonnegative_control": 17,
        "negative_control": 3,
    }
    assert controls[list(source.columns)].equals(source)
    assert not controls.new_prediction.any()
    assert not controls.prediction_recomputed.any()
    assert controls.values_preserved_from_source.all()
    assert controls.source_sha256.eq(source_sha256).all()
    assert controls.source_row_number.tolist() == list(range(1, 21))


def test_failure_controls_reject_missing_or_extra_rows():
    with pytest.raises(T12Error, match="ROW_COUNT"):
        build_failure_controls(candidate_frame().iloc[:19], "hash")


def write_outputs(path: Path, input_bundle=None):
    input_bundle = input_bundle or bundle()
    path.mkdir()
    protocol = build_search_protocol(input_bundle, {"status": "PASS"})
    grid = build_failure_controls(
        input_bundle["candidates"], input_bundle["hashes"]["P12_CANDIDATES"]
    )
    write_json(path / "search_protocol.json", protocol)
    grid.to_csv(path / "grid_predictions.csv", index=False)
    return input_bundle


def test_generated_output_validation_checks_zero_activity_and_unexecuted_domains(tmp_path):
    input_bundle = write_outputs(tmp_path / "outputs")
    result = validate_generated_outputs(tmp_path / "outputs", input_bundle)
    assert result["status"] == "PASS"
    assert result["original_values_preserved_exactly_as_text"]
    assert result["deployment_fit_count"] == 0
    assert result["new_grid_prediction_count"] == 0
    assert result["eg_extension_count_executed"] == 0
    assert result["o09_selection_hash_unchanged"]
    protocol = pd.read_json(tmp_path / "outputs/search_protocol.json", typ="series")
    assert protocol["unexecuted_o02_domain_reference"]["reference_only"]


def test_output_validation_rejects_changed_original_value(tmp_path):
    input_bundle = write_outputs(tmp_path / "outputs")
    path = tmp_path / "outputs/grid_predictions.csv"
    grid = pd.read_csv(path, dtype=str, keep_default_na=False)
    grid.loc[0, "k2eff_pct_prediction"] = "999"
    grid.to_csv(path, index=False)
    with pytest.raises(T12Error, match="ORIGINAL_VALUES_NOT_PRESERVED"):
        validate_generated_outputs(tmp_path / "outputs", input_bundle)


def test_output_validation_rejects_new_prediction_or_wrong_provenance(tmp_path):
    input_bundle = write_outputs(tmp_path / "outputs")
    path = tmp_path / "outputs/grid_predictions.csv"
    grid = pd.read_csv(path, dtype=str, keep_default_na=False)
    grid.loc[0, "new_prediction"] = "True"
    grid.to_csv(path, index=False)
    with pytest.raises(T12Error, match="NEW_PREDICTION_FLAG_NOT_FALSE"):
        validate_generated_outputs(tmp_path / "outputs", input_bundle)
    grid.loc[0, "new_prediction"] = "False"
    grid.loc[0, "source_sha256"] = "changed"
    grid.to_csv(path, index=False)
    with pytest.raises(T12Error, match="SOURCE_HASH_MISMATCH"):
        validate_generated_outputs(tmp_path / "outputs", input_bundle)


def test_o09_empty_selection_is_mandatory():
    selection = {
        "status": "REJECTED",
        "selected_configurations": [],
        "new_recommendation_search_allowed": False,
    }
    eligible = {
        "status": "REJECTED",
        "eligible_models": [],
        "new_recommendation_search_allowed": False,
        "selection_sha256": "selection_hash",
    }
    manifest = {
        "outputs": [
            {"path": "selection/selection.json", "sha256": "selection_hash"},
            {"path": "selection/eligible_models.json", "sha256": "eligible_hash"},
        ]
    }
    hashes = {"O09_SELECTION": "selection_hash", "O09_ELIGIBLE_MODELS": "eligible_hash"}
    validate_o09(selection, eligible, manifest, hashes)
    eligible["eligible_models"] = [{"model_family": "M1"}]
    with pytest.raises(T12Error, match="NOT_EMPTY"):
        validate_o09(selection, eligible, manifest, hashes)


def test_static_audit_rejects_fit_or_predict_calls(tmp_path):
    source = tmp_path / "forbidden.py"
    source.write_text("model.fit(rows)\n")
    with pytest.raises(T12Error, match="MODEL_FIT_OR_PREDICT_CALL_DETECTED"):
        audit_no_model_execution(source)
    source.write_text("from sklearn.linear_model import Ridge\n")
    with pytest.raises(T12Error, match="MODEL_LIBRARY_IMPORT_DETECTED"):
        audit_no_model_execution(source)
    actual = audit_no_model_execution(
        Path(__file__).resolve().parents[2] / "scripts/run_optimization_p13_t12.py"
    )
    assert actual["fit_call_count"] == actual["prediction_call_count"] == 0


def test_formal_output_set_contains_only_protocol_and_failure_controls():
    assert FORMAL_OUTPUTS == ("search_protocol.json", "grid_predictions.csv")
