#!/usr/bin/env python3
"""Validate the P13 failure-delivery contract without generating O14/O15."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT_PATH = (
    BACKEND
    / "surrogate_data"
    / "optimization_p13_20261027_r01"
    / "resume_20261029_r01"
    / "contracts"
    / "failure_delivery_schema.json"
)
METRICS_CSV_FIELDS = [
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


class ContractViolationError(ValueError):
    """Raised when a payload violates the frozen failure-delivery contract."""


def _load_contract(contract_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    with contract_path.open(encoding="utf-8") as handle:
        contract = json.load(handle)
    spec = contract["x-p13-failure-delivery"]
    if spec["metrics_csv_fields"] != METRICS_CSV_FIELDS:
        raise ContractViolationError("Contract and validator metrics.csv fields differ")
    return contract, spec


def _check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _check_fields(
    record: Any, required: list[str], label: str, errors: list[str]
) -> bool:
    if not isinstance(record, dict):
        errors.append(f"{label} must be an object")
        return False
    _check(
        set(record) == set(required),
        f"{label} fields must exactly match the contract",
        errors,
    )
    return True


def _check_constant(
    record: dict[str, Any], field: str, expected: Any, label: str, errors: list[str]
) -> None:
    actual = record.get(field)
    if isinstance(expected, bool):
        _check(actual is expected, f"{label}.{field} must be {expected}", errors)
    else:
        _check(actual == expected, f"{label}.{field} must be {expected!r}", errors)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_decision(
    decision: Any, schema: dict[str, Any], errors: list[str]
) -> None:
    required = schema["$defs"]["decision"]["required"]
    if not _check_fields(decision, required, "decision", errors):
        return

    constants = {
        "scientific_status": "REJECTED",
        "decision": "evidence_insufficient",
        "reason": "no_eligible_models",
        "recommendation_eligible": False,
        "recommendation_count": 0,
        "grade": None,
        "grade_null_reason": "no_eligible_models",
        "grade_counts": {"A": 0, "B": 0, "C": 0},
        "search_status": "not_executed",
        "trusted_for_unmeasured_optimization": False,
    }
    for field, expected in constants.items():
        _check_constant(decision, field, expected, "decision", errors)

    eligible = decision.get("eligible_models")
    _check(isinstance(eligible, list), "decision.eligible_models must be a list", errors)
    if isinstance(eligible, list):
        _check(
            not eligible,
            "decision.eligible_models must be empty; non-empty sets have no success path",
            errors,
        )

    quantities = {
        "baseline": {"value": "14.674470284168734", "unit": "percent_number"},
        "absolute_target": {"value": "15.674470284168734", "unit": "percent_number"},
        "required_uplift": {"value": "1", "unit": "percentage_points"},
    }
    for field, expected in quantities.items():
        _check(decision.get(field) == expected, f"decision.{field} changed value or unit", errors)

    conservative = decision.get("conservative_evidence")
    if not isinstance(conservative, dict):
        errors.append("decision.conservative_evidence must be an object")
    else:
        expected_reasons = {
            "conservative_value": [
                "no_eligible_models",
                "P3_AGGREGATION_ESTIMAND_MISMATCH",
                "E1_COORDINATE_OBSERVATIONS_MISSING",
                "HARD_CAPACITY_INSUFFICIENT_13_KEYS",
            ],
            "error_margin": ["no_eligible_models", "HARD_CAPACITY_INSUFFICIENT_13_KEYS"],
            "extrapolation_distance": ["no_eligible_models"],
            "testable_max_distance": ["no_eligible_models"],
        }
        for field, reasons in expected_reasons.items():
            _check(conservative.get(field) is None, f"{field} must be null", errors)
            _check(
                conservative.get(f"{field}_null_reasons") == reasons,
                f"{field} null reasons changed",
                errors,
            )

    expected_neighborhood = {
        "status": "not_executed",
        "null_reason": "no_eligible_models",
        "nine_point_prediction_executed": False,
    }
    _check(
        decision.get("neighborhood") == expected_neighborhood,
        "decision.neighborhood must be not_executed with its null reason",
        errors,
    )
    expected_random = {
        "status": "not_executed",
        "reason": "no_eligible_models",
        "seed": 20261027,
        "seed_used": False,
        "random_draw_count": 0,
        "bootstrap_prediction_count": 0,
        "refit_count": 0,
    }
    _check(
        decision.get("random_process") == expected_random,
        "decision.random_process must be not_executed with an unused seed",
        errors,
    )


def _validate_candidates(
    candidates: Any,
    schema: dict[str, Any],
    spec: dict[str, Any],
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(candidates, list):
        errors.append("candidates must be a list")
        return {}

    expected_rows = spec["candidate_order"]
    expected_ids = [row["candidate_id"] for row in expected_rows]
    actual_ids = [
        row.get("candidate_id") if isinstance(row, dict) else None for row in candidates
    ]
    _check(len(candidates) == 20, "candidates must contain exactly 20 rows", errors)
    _check(len(set(actual_ids)) == len(actual_ids), "duplicate candidate_id", errors)
    _check(actual_ids == expected_ids, "candidate IDs or original O13 order changed", errors)
    _check(
        sum(
            isinstance(row, dict) and row.get("control_type") == "nonnegative_control"
            for row in candidates
        )
        == 17,
        "exactly 17 non-negative controls are required",
        errors,
    )
    _check(
        sum(
            isinstance(row, dict) and row.get("control_type") == "negative_control"
            for row in candidates
        )
        == 3,
        "exactly 3 negative controls are required",
        errors,
    )

    required = schema["$defs"]["candidate"]["required"]
    by_id: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(candidates):
        label = f"candidates[{index}]"
        if not _check_fields(candidate, required, label, errors):
            continue
        candidate_id = candidate["candidate_id"]
        if isinstance(candidate_id, str):
            by_id[candidate_id] = candidate
        if index >= len(expected_rows):
            continue
        expected = expected_rows[index]
        for field in ("candidate_id", "control_type", "source_row_key", "support_label"):
            _check(candidate.get(field) == expected[field], f"{label}.{field} changed", errors)
        _check(
            candidate.get("measured_geometry_match") == expected["measured_geometry_match"],
            f"{label} measured-geometry match provenance changed",
            errors,
        )
        if expected.get("exclusion_reason_required"):
            _check(
                isinstance(candidate.get("exclusion_reason"), str)
                and bool(candidate["exclusion_reason"]),
                "C19 exclusion reason must be preserved",
                errors,
            )

        _check(
            candidate.get("source_file_id") == "P12_CANDIDATES",
            f"{label} source changed",
            errors,
        )
        _check(
            candidate.get("source_sha256") == spec["source_hashes"]["P12_CANDIDATES"],
            f"{label} source hash changed",
            errors,
        )
        current_constants = {
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
        for field, expected_value in current_constants.items():
            _check_constant(candidate, field, expected_value, label, errors)

        geometry = {field: candidate.get(field) for field in ("eg_um", "fl_um", "area_um2")}
        _check(
            all(_finite_number(value) for value in geometry.values()),
            f"{label} geometry must be finite numbers",
            errors,
        )
        if all(_finite_number(value) for value in geometry.values()):
            _check(geometry["eg_um"] >= 0, f"{label}.eg_um must be non-negative", errors)
            _check(geometry["fl_um"] >= 0, f"{label}.fl_um must be non-negative", errors)
            _check(geometry["area_um2"] > 0, f"{label}.area_um2 must be positive", errors)
        has_pf = candidate.get("has_pf")
        _check(has_pf in (0, 1) and not isinstance(has_pf, bool), f"{label}.has_pf invalid", errors)
        if has_pf in (0, 1) and all(_finite_number(value) for value in geometry.values()):
            expected_branch = (
                f"pf{has_pf}_eg{int(geometry['eg_um'] > 0)}_fl{int(geometry['fl_um'] > 0)}"
            )
            _check(
                candidate.get("branch_id") == expected_branch,
                f"{label} branch mismatch",
                errors,
            )
        distance = candidate.get("legacy_nearest_scaled_distance")
        _check(
            distance is None or (_finite_number(distance) and distance >= 0),
            f"{label} legacy reference distance must be finite, non-negative, or null",
            errors,
        )
    return by_id


def _validate_source_registry(
    registry: Any, spec: dict[str, Any], errors: list[str]
) -> None:
    if not isinstance(registry, dict):
        errors.append("source_registry must be an object")
        return
    for source_id, expected_hash in spec["source_hashes"].items():
        record = registry.get(source_id)
        _check(isinstance(record, dict), f"source_registry.{source_id} is missing", errors)
        if isinstance(record, dict):
            _check(
                record.get("sha256") == expected_hash,
                f"source_registry.{source_id} hash changed",
                errors,
            )


def _validate_metrics(
    metrics: Any,
    candidates: dict[str, dict[str, Any]],
    source_registry: Any,
    schema: dict[str, Any],
    spec: dict[str, Any],
    errors: list[str],
) -> None:
    if not isinstance(metrics, list):
        errors.append("metrics must be a list")
        return
    required = schema["$defs"]["metric"]["required"]
    registry = source_registry if isinstance(source_registry, dict) else {}
    _check(required == METRICS_CSV_FIELDS, "metrics.csv field order changed", errors)
    keys: list[tuple[Any, Any, Any]] = []
    for index, row in enumerate(metrics):
        label = f"metrics[{index}]"
        if not _check_fields(row, required, label, errors):
            continue
        candidate = candidates.get(row["candidate_id"])
        _check(candidate is not None, f"{label} references an unknown candidate", errors)
        keys.append((row["candidate_id"], row["model_id"], row["metric"]))
        metric = row["metric"]
        _check(
            metric in spec["metric_units"],
            f"{label} has an unregistered metric or model",
            errors,
        )
        if metric not in spec["metric_units"]:
            continue
        _check(row["model_id"] in spec["required_metric_models"], f"{label} model changed", errors)
        _check(
            row["unit"] == spec["metric_units"][metric],
            f"{label} has the wrong unit",
            errors,
        )
        value = row["value"]
        if metric in spec["historical_value_metrics"]:
            _check(
                _finite_number(value) and value > 0,
                f"{label} registered historical value must be finite and positive",
                errors,
            )
        elif value is not None:
            _check(_finite_number(value), f"{label} value must be finite or null", errors)
            if _finite_number(value) and not metric.endswith("_delta_vs_baseline"):
                _check(value > 0, f"{label} physical metric must be positive", errors)

        if value is None:
            source_fields = (
                "source_file_id",
                "source_sha256",
                "source_row_key",
                "scope",
                "support_label",
            )
            for field in source_fields:
                _check(
                    row[field] is None,
                    f"{label}.{field} must be null for a missing value",
                    errors,
                )
            _check(
                row["evidence_status"] == "evidence_insufficient",
                f"{label} missing value needs evidence_insufficient",
                errors,
            )
            _check(
                isinstance(row["null_reason"], str) and bool(row["null_reason"]),
                f"{label} missing value needs a null_reason",
                errors,
            )
            continue

        _check(
            row["evidence_status"] == "registered_historical_reference",
            f"{label} status changed",
            errors,
        )
        _check(
            row["null_reason"] is None,
            f"{label} sourced value cannot have a null reason",
            errors,
        )
        source_id = row["source_file_id"]
        _check(
            isinstance(source_id, str) and bool(source_id),
            f"{label} sourced value needs a source_file_id",
            errors,
        )
        registered_source = registry.get(source_id) if isinstance(source_id, str) else None
        _check(
            isinstance(registered_source, dict),
            f"{label} source is not registered",
            errors,
        )
        if isinstance(registered_source, dict):
            _check(
                row["source_sha256"] == registered_source.get("sha256"),
                f"{label} source hash tamper detected",
                errors,
            )
        if metric in spec["historical_value_metrics"]:
            _check(
                source_id == "P12_CANDIDATES"
                and row["source_sha256"] == spec["source_hashes"]["P12_CANDIDATES"],
                f"{label} O13 historical value source changed",
                errors,
            )
        if source_id == "P12_CANDIDATES":
            _check(row["scope"] == spec["historical_scope"], f"{label} scope changed", errors)
            if candidate is not None:
                _check(
                    row["source_row_key"] == candidate["source_row_key"],
                    f"{label} source row is not the same structure",
                    errors,
                )
        else:
            _check(
                isinstance(row["scope"], str) and bool(row["scope"]),
                f"{label} registered source scope is required",
                errors,
            )
            if candidate is not None:
                allowed_row_keys = {
                    f"structure_id={structure_id}"
                    for structure_id in candidate["measured_geometry_match"]["structure_ids"]
                }
                _check(
                    row["source_row_key"] in allowed_row_keys,
                    f"{label} source row is not an exact measured-geometry match",
                    errors,
                )
        if candidate is not None:
            _check(
                row["support_label"] == candidate["support_label"],
                f"{label} support label changed",
                errors,
            )

    duplicate_keys = [key for key, count in Counter(keys).items() if count > 1]
    _check(not duplicate_keys, "duplicate candidate/model/metric rows", errors)
    expected_keys = {
        (candidate_id, model_id, metric)
        for candidate_id in candidates
        for model_id in spec["required_metric_models"]
        for metric in spec["required_metrics"]
    }
    missing_keys = expected_keys.difference(keys)
    _check(not missing_keys, "required candidate/model/metric matrix rows are missing", errors)


def delivery_errors(payload: Any, contract_path: Path = DEFAULT_CONTRACT_PATH) -> list[str]:
    """Return all observable contract violations; never switch to a success path."""
    contract, spec = _load_contract(contract_path)
    schema = contract
    errors: list[str] = []
    if not _check_fields(
        payload,
        ["decision", "candidates", "metrics", "source_registry"],
        "bundle",
        errors,
    ):
        return errors
    _validate_source_registry(payload["source_registry"], spec, errors)
    _validate_decision(payload["decision"], schema, errors)
    candidates = _validate_candidates(payload["candidates"], schema, spec, errors)
    _validate_metrics(
        payload["metrics"], candidates, payload["source_registry"], schema, spec, errors
    )
    return errors


def validate_delivery(payload: Any, contract_path: Path = DEFAULT_CONTRACT_PATH) -> None:
    """Raise ContractViolationError unless payload is a valid synthetic failure delivery."""
    errors = delivery_errors(payload, contract_path)
    if errors:
        raise ContractViolationError("\n".join(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="Synthetic JSON bundle to validate"
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    args = parser.parse_args(argv)
    try:
        with args.input.open(encoding="utf-8") as handle:
            payload = json.load(
                handle,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON number: {value}")
                ),
            )
        validate_delivery(payload, contract_path=args.contract)
    except (OSError, ValueError) as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1
    print("PASS: synthetic failure-delivery contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
