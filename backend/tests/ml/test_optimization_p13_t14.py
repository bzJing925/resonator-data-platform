from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.run_optimization_p13_t14 import (
    CANONICAL_EMPTY_INDICES_PAYLOAD,
    CANONICAL_EMPTY_INDICES_SHA256,
    FORMAL_OUTPUTS,
    T14Error,
    audit_no_random_fit_or_prediction,
    build_bootstrap_indices,
    build_candidate_scores,
    build_robustness_report,
    compute_capacity_audit,
    validate_outputs,
    write_json,
)


def shortlist():
    rows = []
    for index in range(20):
        negative = index >= 17
        rows.append(
            {
                "candidate_id": f"c_{index:02d}",
                "control_type": "negative_control" if negative else "nonnegative_control",
                "eg_um": str(index / 10),
                "fl_um": "1.5",
                "area_um2": "5500",
                "has_pf": "0",
                "included": str(not negative),
                "included_as_failure_control": str(not negative),
                "recommendation_eligible": "False",
                "reason": "original exclusion" if negative else "no_eligible_models",
                "exclusion_reason": "original exclusion" if negative else "",
                "risk_flags": "original exclusion" if negative else "existing risk",
                "new_candidate": "False",
                "a_or_b_eligible": "False",
            }
        )
    return pd.DataFrame(rows)


def protocol():
    return {
        "robustness": {
            "bootstrap": {"replicates": 1000, "seed": 20261027},
            "baseline_resampling": {
                "coordinate_records_available_under_o01": False,
                "coordinate_indices_generated": False,
                "same_round_candidate_baseline_delta_available": False,
                "null_reason": "missing E1 coordinates",
            },
            "conservative_value": {"available_under_o02": False},
        },
        "models": {"M2": {"aggregation_estimand_match": False}},
    }


def capacity_audit(count=13):
    return {
        "insufficient_key_count": count,
        "insufficient_keys": [{"scope": f"s_{index}"} for index in range(count)],
    }


def report():
    return build_robustness_report(
        protocol(),
        {"eligible_models": []},
        capacity_audit(),
        {"rng_call_count": 0, "refit_call_count": 0},
        {"O09_SELECTION": "selection", "O12_SHORTLIST": "shortlist"},
    )


def write_outputs(path: Path):
    path.mkdir()
    write_json(
        path / "bootstrap_indices.json",
        build_bootstrap_indices({"eligible_models": []}, protocol()),
    )
    build_candidate_scores(shortlist()).to_csv(path / "candidate_scores.csv", index=False)
    write_json(path / "robustness_report.json", report())
    return {"shortlist": shortlist()}


def test_canonical_empty_indices_is_rejected_without_seed_consumption():
    result = build_bootstrap_indices({"eligible_models": []}, protocol())
    assert result["status"] == "REJECTED"
    assert not result["generation_executed"]
    assert result["replicate_count"] == 0 and result["indices"] == []
    assert result["planned_replicates"] == 1000
    assert result["planned_seed"] == 20261027 and not result["seed_consumed"]
    assert CANONICAL_EMPTY_INDICES_PAYLOAD == '{"indices":[]}'
    assert result["canonical_empty_indices_payload_sha256"] == CANONICAL_EMPTY_INDICES_SHA256
    assert result["random_draw_count"] == 0


def test_candidate_scores_preserve_o12_and_leave_numeric_evidence_empty():
    source = shortlist()
    result = build_candidate_scores(source)
    assert result[list(source.columns)].equals(source)
    for field in (
        "conservative_value",
        "error_margin",
        "extrapolation_distance",
        "testable_max_distance",
        "neighborhood_result",
        "a_or_b_grade",
    ):
        assert result[field].eq("").all()
    assert result.conservative_value_null_reason.str.contains("no_eligible_models").all()
    assert result.conservative_value_null_reason.str.contains("P3_AGGREGATION").all()
    assert result.conservative_value_null_reason.str.contains("E1_COORDINATE").all()
    assert result.conservative_value_null_reason.str.contains("13_KEYS").all()
    assert result.neighborhood_status.eq("unavailable").all()
    assert result.a_or_b_eligible.eq("False").all()


def test_real_output_contract_rejects_zero_substituted_for_null(tmp_path):
    bundle = write_outputs(tmp_path / "outputs")
    path = tmp_path / "outputs/candidate_scores.csv"
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.loc[0, "conservative_value"] = "0"
    frame.to_csv(path, index=False)
    with pytest.raises(T14Error, match="ZERO_OR_VALUE_SUBSTITUTED_FOR_NULL"):
        validate_outputs(tmp_path / "outputs", bundle)


def test_real_output_contract_rejects_changed_o12_value(tmp_path):
    bundle = write_outputs(tmp_path / "outputs")
    path = tmp_path / "outputs/candidate_scores.csv"
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.loc[0, "eg_um"] = "999"
    frame.to_csv(path, index=False)
    with pytest.raises(T14Error, match="O12_VALUES_OR_ORDER_CHANGED"):
        validate_outputs(tmp_path / "outputs", bundle)


def test_full_output_validation_checks_null_reasons_and_zero_activity(tmp_path):
    bundle = write_outputs(tmp_path / "outputs")
    result = validate_outputs(tmp_path / "outputs", bundle)
    assert result["status"] == "PASS" and result["scientific_status"] == "REJECTED"
    assert result["failure_control_count"] == 17
    assert result["negative_control_excluded_count"] == 3
    assert result["random_draw_count"] == 0
    assert result["neighborhood_prediction_count"] == 0
    assert result["capacity_insufficient_key_count"] == 13
    assert set(FORMAL_OUTPUTS) == {
        "bootstrap_indices.json",
        "candidate_scores.csv",
        "robustness_report.json",
    }


def test_capacity_recomputation_uses_unique_groups_and_valid_units():
    splits = pd.DataFrame(
        [
            {
                "role": "evaluation",
                "family": "eg_upper",
                "scope": "s",
                "branch_id": "b",
                "unit_id": "u1",
                "design_group_id": "d1",
            },
            {
                "role": "evaluation",
                "family": "eg_upper",
                "scope": "s",
                "branch_id": "b",
                "unit_id": "u1",
                "design_group_id": "d1",
            },
            {
                "role": "evaluation",
                "family": "eg_upper",
                "scope": "s",
                "branch_id": "b",
                "unit_id": "u2",
                "design_group_id": "d2",
            },
            {
                "role": "evaluation",
                "family": "ordinary",
                "scope": "s",
                "branch_id": "b",
                "unit_id": "u3",
                "design_group_id": "d3",
            },
        ]
    )
    capacity = pd.DataFrame({"counts_toward_hard_capacity": [True] * 37})
    protocol = {
        "evaluation": {
            "hard_capacity": {
                "minimum_independent_design_groups": 20,
                "minimum_valid_boundary_units": 2,
                "known_insufficient_keys": 1,
                "key": "scope/family/branch",
                "deduplication": "unique groups",
            }
        }
    }
    result = compute_capacity_audit(splits, capacity, protocol)
    assert result["insufficient_key_count"] == 1
    assert result["insufficient_keys"][0]["independent_design_group_count"] == 2
    assert result["insufficient_keys"][0]["valid_unit_count"] == 2


def test_static_audit_rejects_rng_fit_and_prediction_calls(tmp_path):
    for source_text in (
        "np.random.default_rng(20261027)\n",
        "model.fit(rows)\n",
        "model.predict(rows)\n",
        "df.resample('D')\n",
    ):
        source = tmp_path / "forbidden.py"
        source.write_text(source_text)
        with pytest.raises(T14Error, match="RANDOM_FIT_OR_PREDICTION_CALL_DETECTED"):
            audit_no_random_fit_or_prediction(source)
    actual = audit_no_random_fit_or_prediction(
        Path(__file__).resolve().parents[2] / "scripts/run_optimization_p13_t14.py"
    )
    assert actual["rng_call_count"] == 0
    assert actual["refit_call_count"] == 0
    assert actual["neighborhood_prediction_call_count"] == 0
