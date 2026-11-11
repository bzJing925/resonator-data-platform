from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND / "scripts" / "run_optimization_p13_failure_contract.py"
CONTRACT_PATH = (
    BACKEND
    / "surrogate_data"
    / "optimization_p13_20261027_r01"
    / "resume_20261029_r01"
    / "contracts"
    / "failure_delivery_schema.json"
)

SPEC = importlib.util.spec_from_file_location("p13_failure_contract", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
failure_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(failure_contract)


def _decision() -> dict:
    return {
        "scientific_status": "REJECTED",
        "decision": "evidence_insufficient",
        "reason": "no_eligible_models",
        "eligible_models": [],
        "recommendation_eligible": False,
        "recommendation_count": 0,
        "grade": None,
        "grade_null_reason": "no_eligible_models",
        "grade_counts": {"A": 0, "B": 0, "C": 0},
        "baseline": {
            "value": "14.674470284168734",
            "unit": "percent_number",
        },
        "absolute_target": {
            "value": "15.674470284168734",
            "unit": "percent_number",
        },
        "required_uplift": {"value": "1", "unit": "percentage_points"},
        "conservative_evidence": {
            "conservative_value": None,
            "conservative_value_null_reasons": [
                "no_eligible_models",
                "P3_AGGREGATION_ESTIMAND_MISMATCH",
                "E1_COORDINATE_OBSERVATIONS_MISSING",
                "HARD_CAPACITY_INSUFFICIENT_13_KEYS",
            ],
            "error_margin": None,
            "error_margin_null_reasons": [
                "no_eligible_models",
                "HARD_CAPACITY_INSUFFICIENT_13_KEYS",
            ],
            "extrapolation_distance": None,
            "extrapolation_distance_null_reasons": ["no_eligible_models"],
            "testable_max_distance": None,
            "testable_max_distance_null_reasons": ["no_eligible_models"],
        },
        "neighborhood": {
            "status": "not_executed",
            "null_reason": "no_eligible_models",
            "nine_point_prediction_executed": False,
        },
        "random_process": {
            "status": "not_executed",
            "reason": "no_eligible_models",
            "seed": 20261027,
            "seed_used": False,
            "random_draw_count": 0,
            "bootstrap_prediction_count": 0,
            "refit_count": 0,
        },
        "search_status": "not_executed",
        "trusted_for_unmeasured_optimization": False,
    }


def _candidate(expected: dict, source_sha256: str) -> dict:
    is_c19 = expected["candidate_id"].startswith("C19_")
    return {
        "candidate_id": expected["candidate_id"],
        "control_type": expected["control_type"],
        "eg_um": 0.0 if is_c19 else 1.0,
        "fl_um": 1.5,
        "area_um2": 700.0 if is_c19 else 5500.0,
        "has_pf": 1 if is_c19 else 0,
        "branch_id": "pf1_eg0_fl1" if is_c19 else "pf0_eg1_fl1",
        "support_label": expected["support_label"],
        "source_file_id": "P12_CANDIDATES",
        "source_sha256": source_sha256,
        "source_row_key": expected["source_row_key"],
        "legacy_recommendation_text": "historical text only",
        "legacy_nearest_scaled_distance": 0.25,
        "recommendation_eligible": False,
        "grade": None,
        "grade_null_reason": "no_eligible_models",
        "evidence_status": "evidence_insufficient",
        "trusted_for_unmeasured_optimization": False,
        "new_candidate": False,
        "model_prediction_provenance": {
            "value_origin": "legacy_model_prediction",
            "new_prediction": False,
            "prediction_recomputed": False,
            "trusted_for_unmeasured_optimization": False,
        },
        "measured_geometry_match": copy.deepcopy(expected["measured_geometry_match"]),
        "exclusion_reason": "retained C19 negative-control exclusion" if is_c19 else "",
        "conservative_value": None,
        "conservative_value_null_reason": (
            "no_eligible_models;P3_AGGREGATION_ESTIMAND_MISMATCH;"
            "E1_COORDINATE_OBSERVATIONS_MISSING;HARD_CAPACITY_INSUFFICIENT_13_KEYS"
        ),
        "error_margin": None,
        "error_margin_null_reason": "no_eligible_models;HARD_CAPACITY_INSUFFICIENT_13_KEYS",
        "extrapolation_distance": None,
        "extrapolation_distance_null_reason": "no_eligible_models",
        "testable_max_distance": None,
        "testable_max_distance_null_reason": "no_eligible_models",
        "neighborhood_result": None,
        "neighborhood_status": "not_executed",
        "neighborhood_null_reason": "no_eligible_models",
    }


def _metric(
    candidate: dict,
    model_id: str,
    metric: str,
    spec: dict,
    source_sha256: str,
) -> dict:
    has_value = metric in spec["historical_value_metrics"]
    value = None
    if has_value:
        value = 14.25 if metric == "k2eff_pct" else 1200.0
    return {
        "candidate_id": candidate["candidate_id"],
        "model_id": model_id,
        "metric": metric,
        "value": value,
        "unit": spec["metric_units"][metric],
        "source_file_id": "P12_CANDIDATES" if has_value else None,
        "source_sha256": source_sha256 if has_value else None,
        "source_row_key": candidate["source_row_key"] if has_value else None,
        "scope": spec["historical_scope"] if has_value else None,
        "support_label": candidate["support_label"] if has_value else None,
        "evidence_status": (
            "registered_historical_reference" if has_value else "evidence_insufficient"
        ),
        "null_reason": None if has_value else spec["missing_value_null_reason"],
    }


@pytest.fixture
def valid_payload() -> dict:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    spec = contract["x-p13-failure-delivery"]
    source_sha256 = spec["source_hashes"]["P12_CANDIDATES"]
    candidates = [
        _candidate(expected, source_sha256) for expected in spec["candidate_order"]
    ]
    metrics = [
        _metric(candidate, model_id, metric, spec, source_sha256)
        for candidate in candidates
        for model_id in spec["required_metric_models"]
        for metric in spec["required_metrics"]
    ]
    return {
        "decision": _decision(),
        "candidates": candidates,
        "metrics": metrics,
        "source_registry": {
            source_id: {"sha256": sha256}
            for source_id, sha256 in spec["source_hashes"].items()
        },
    }


def _assert_rejected(payload: dict) -> None:
    with pytest.raises(failure_contract.ContractViolationError):
        failure_contract.validate_delivery(payload, contract_path=CONTRACT_PATH)


def test_empty_eligible_set_is_accepted(valid_payload: dict) -> None:
    assert valid_payload["decision"]["eligible_models"] == []
    assert failure_contract.validate_delivery(
        valid_payload, contract_path=CONTRACT_PATH
    ) is None


def test_non_empty_eligible_set_is_rejected(valid_payload: dict) -> None:
    valid_payload["decision"]["eligible_models"] = ["M1"]
    _assert_rejected(valid_payload)


@pytest.mark.parametrize("injected_grade", ["A", "B"])
def test_a_or_b_injection_is_rejected(valid_payload: dict, injected_grade: str) -> None:
    valid_payload["candidates"][0]["grade"] = injected_grade
    valid_payload["decision"]["grade_counts"][injected_grade] = 1
    _assert_rejected(valid_payload)


def test_c_grade_is_not_inferred_from_empty_eligible_set(valid_payload: dict) -> None:
    valid_payload["candidates"][0]["grade"] = "C"
    valid_payload["decision"]["grade_counts"]["C"] = 1
    _assert_rejected(valid_payload)


def test_null_replaced_by_zero_is_rejected(valid_payload: dict) -> None:
    missing = next(row for row in valid_payload["metrics"] if row["value"] is None)
    missing["value"] = 0
    _assert_rejected(valid_payload)


def test_source_hash_tamper_is_rejected(valid_payload: dict) -> None:
    sourced = next(row for row in valid_payload["metrics"] if row["value"] is not None)
    sourced["source_sha256"] = "0" * 64
    _assert_rejected(valid_payload)


def test_duplicate_candidate_id_is_rejected(valid_payload: dict) -> None:
    valid_payload["candidates"][1] = copy.deepcopy(valid_payload["candidates"][0])
    _assert_rejected(valid_payload)


def test_negative_control_deletion_is_rejected(valid_payload: dict) -> None:
    removed = valid_payload["candidates"].pop()
    valid_payload["metrics"] = [
        row for row in valid_payload["metrics"] if row["candidate_id"] != removed["candidate_id"]
    ]
    _assert_rejected(valid_payload)


def test_c19_measured_match_status_loss_is_rejected(valid_payload: dict) -> None:
    c19 = valid_payload["candidates"][-1]
    assert c19["candidate_id"].startswith("C19_")
    c19["measured_geometry_match"] = {
        "status": "no_exact_match",
        "match_count": 0,
        "structure_ids": [],
    }
    _assert_rejected(valid_payload)


def test_percentage_point_value_written_as_ratio_is_rejected(valid_payload: dict) -> None:
    valid_payload["decision"]["required_uplift"]["value"] = "0.01"
    _assert_rejected(valid_payload)


def test_metrics_fields_are_exact_and_ordered(valid_payload: dict) -> None:
    expected = [
        "candidate_id",
        "model_id",
        "metric",
        "value",
        "unit",
        "source_file_id",
        "source_sha256",
        "source_row_key",
        "scope",
        "support_label",
        "evidence_status",
        "null_reason",
    ]
    assert failure_contract.METRICS_CSV_FIELDS == expected
    assert list(valid_payload["metrics"][0]) == expected


def test_missing_required_metric_row_is_rejected(valid_payload: dict) -> None:
    valid_payload["metrics"].pop()
    _assert_rejected(valid_payload)


def test_registered_c19_same_structure_qs_value_is_accepted(valid_payload: dict) -> None:
    source_sha256 = "426d844b4854c95593e3e9b621637d12f42eb4422f6ad75b8e6e67a86d837000"
    valid_payload["source_registry"]["P3_STRUCTURES"] = {"sha256": source_sha256}
    row = next(
        row
        for row in valid_payload["metrics"]
        if row["candidate_id"].startswith("C19_")
        and row["model_id"] == "P3"
        and row["metric"] == "qs"
    )
    row.update(
        {
            "value": 800.0,
            "source_file_id": "P3_STRUCTURES",
            "source_sha256": source_sha256,
            "source_row_key": "structure_id=s_c454ac7ec8a2a7f8",
            "scope": "O01_P3_deembedded_structure_aggregates",
            "support_label": "exact_training_structure",
            "evidence_status": "registered_historical_reference",
            "null_reason": None,
        }
    )
    assert failure_contract.validate_delivery(
        valid_payload, contract_path=CONTRACT_PATH
    ) is None


def test_cross_structure_metric_source_is_rejected(valid_payload: dict) -> None:
    source_sha256 = "426d844b4854c95593e3e9b621637d12f42eb4422f6ad75b8e6e67a86d837000"
    valid_payload["source_registry"]["P3_STRUCTURES"] = {"sha256": source_sha256}
    row = next(
        row
        for row in valid_payload["metrics"]
        if row["candidate_id"].startswith("C19_")
        and row["model_id"] == "P3"
        and row["metric"] == "qs"
    )
    row.update(
        {
            "value": 800.0,
            "source_file_id": "P3_STRUCTURES",
            "source_sha256": source_sha256,
            "source_row_key": "structure_id=s_not_the_c19_match",
            "scope": "O01_P3_deembedded_structure_aggregates",
            "support_label": "exact_training_structure",
            "evidence_status": "registered_historical_reference",
            "null_reason": None,
        }
    )
    _assert_rejected(valid_payload)
