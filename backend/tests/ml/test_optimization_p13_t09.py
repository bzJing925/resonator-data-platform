from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from scripts.run_optimization_p13_t09 import (
    SCOPES,
    EvaluationError,
    acceptance_condition,
    assert_complete_prediction_rows,
    compute_metric_bundle,
    evaluate_acceptance,
    false_positive_events,
    ordinary_scope_mae,
    validate_isolation,
    validate_m2_predictions,
)


def prediction_row(
    index=0,
    *,
    scope="structure_holdout",
    unit_id="structure_holdout__ordinary__all_branches__fold_0",
    family="ordinary",
    design_group_id="d_a",
    actual=10.0,
    prediction=11.0,
):
    return {
        "unit_id": unit_id,
        "structure_id": f"s_{index}",
        "design_group_id": design_group_id,
        "scope": scope,
        "fold": unit_id,
        "role": "evaluation",
        "branch_id": "all_branches" if family == "ordinary" else "pf0_eg1_fl1",
        "actual": actual,
        "prediction": prediction,
        "valid_prediction": True,
        "invalid_reason": None,
        "null_reason": None,
        "source_split_sha256": "split",
        "source_structure_sha256": "structure",
        "protocol_sha256": "bc0f16a4791d1968cac10afafd5fd14a32abfa7bd7d0329b2591ff825d88f05c",
        "source_file_id": "P3_SPLIT",
        "source_split_file_id": "P3_SPLIT",
        "source_structure_file_id": "P3_STRUCTURES",
        "target": "k2eff_pct",
        "unit": "percent_number",
        "family": family,
        "has_pf": 0,
    }


def complete_frames():
    evaluation = pd.DataFrame([prediction_row()])
    control = evaluation.copy()
    rows = evaluation.copy()
    return rows, evaluation, control


def test_ordinary_mae_weights_design_groups_then_scopes_not_folds_or_rows():
    rows = []
    specifications = {
        "structure_holdout": [("d_a", [0.0, 2.0]), ("d_b", [3.0]), ("d_c", [9.0])],
        "area_edges": [("d_d", [2.0])],
        "eg_upper": [("d_e", [4.0])],
        "fl_upper": [("d_f", [6.0])],
    }
    index = 0
    for scope, groups in specifications.items():
        for group, errors in groups:
            for error in errors:
                unit_id = f"{scope}__ordinary__all_branches__fold_{index % 2}"
                rows.append(
                    prediction_row(
                        index,
                        scope=scope,
                        unit_id=unit_id,
                        design_group_id=group,
                        actual=10.0,
                        prediction=10.0 + error,
                    )
                )
                index += 1
    expected = ((1 + 3 + 9) / 3 + 2 + 4 + 6) / 4
    assert ordinary_scope_mae(pd.DataFrame(rows)) == pytest.approx(expected)


def metric_inputs():
    capacities = []
    rows = []
    index = 0
    for scope in SCOPES:
        for fold in range(4):
            unit_id = f"{scope}__ordinary__all_branches__fold_{fold}"
            capacities.append(capacity_row(unit_id, scope, "ordinary", "all_branches", True))
            rows.append(
                prediction_row(
                    index,
                    scope=scope,
                    unit_id=unit_id,
                    design_group_id=f"d_{index}",
                    actual=10,
                    prediction=10,
                )
            )
            index += 1
    boundary_counts = {"structure_holdout": 9, "area_edges": 10, "eg_upper": 9, "fl_upper": 9}
    for scope, count in boundary_counts.items():
        for unit_index in range(count):
            unit_id = f"{scope}__eg_upper__pf0_eg1_fl1__top_{unit_index}"
            capacities.append(capacity_row(unit_id, scope, "eg_upper", "pf0_eg1_fl1", True))
            error = SCOPES.index(scope) + 1 if unit_index == 0 else 0
            rows.append(
                prediction_row(
                    index,
                    scope=scope,
                    unit_id=unit_id,
                    family="eg_upper",
                    design_group_id=f"d_{index}",
                    actual=10,
                    prediction=10 + error,
                )
            )
            index += 1
    invalid_id = "structure_holdout__eg_upper__pf0_eg1_fl0__top_invalid"
    capacities.append(
        capacity_row(invalid_id, "structure_holdout", "eg_upper", "pf0_eg1_fl0", False)
    )
    return pd.DataFrame(rows), pd.DataFrame(capacities)


def capacity_row(unit_id, scope, family, branch_id, valid):
    return {
        "unit_id": unit_id,
        "scope": scope,
        "family": family,
        "branch_id": branch_id,
        "valid": valid,
        "invalid_reason": None if valid else "synthetic_invalid",
        "counts_toward_hard_capacity": valid and family != "ordinary",
    }


def test_boundary_mae_and_worst_unit_use_valid_pf0_units_and_equal_scopes():
    rows, capacity = metric_inputs()
    bundle = compute_metric_bundle(rows, capacity)
    expected_boundary = (1 / 9 + 2 / 10 + 3 / 9 + 4 / 9) / 4
    assert bundle["ordinary_mae"] == 0
    assert bundle["boundary_mae"] == pytest.approx(expected_boundary)
    assert bundle["worst_unit_mae"] == 4
    assert bundle["worst_unit_id"] == "fl_upper__eg_upper__pf0_eg1_fl1__top_0"
    assert bundle["valid_boundary_unit_count"] == 37
    assert len(bundle["unit_metrics"]) == 54


def test_common_row_deletion_fails_against_o05():
    rows, evaluation, control = complete_frames()
    with pytest.raises(EvaluationError, match="INCOMPLETE_OR_UNEXPECTED_O05_ROWS"):
        assert_complete_prediction_rows(
            rows.iloc[0:0], evaluation, control.iloc[0:0], model_family="M1"
        )


def test_null_or_invalid_prediction_fails_instead_of_dropping_row():
    rows, evaluation, control = complete_frames()
    rows.loc[0, "prediction"] = np.nan
    with pytest.raises(EvaluationError, match="NULL_OR_NONFINITE_PREDICTION_PRESENT"):
        assert_complete_prediction_rows(rows, evaluation, control, model_family="M1")
    rows, evaluation, control = complete_frames()
    rows.loc[0, "valid_prediction"] = False
    rows.loc[0, "invalid_reason"] = "FIT_UNAVAILABLE"
    with pytest.raises(EvaluationError, match="INVALID_PREDICTION_PRESENT"):
        assert_complete_prediction_rows(rows, evaluation, control, model_family="M1")


def test_actual_or_source_mismatch_fails():
    rows, evaluation, control = complete_frames()
    control.loc[0, "actual"] = 99
    with pytest.raises(EvaluationError, match="ACTUAL_MISMATCH_WITH_P6C"):
        assert_complete_prediction_rows(rows, evaluation, control, model_family="M1")
    rows, evaluation, control = complete_frames()
    evaluation.loc[0, "source_split_sha256"] = "changed"
    with pytest.raises(EvaluationError, match="O05_METADATA_MISMATCH"):
        assert_complete_prediction_rows(rows, evaluation, control, model_family="M1")


def test_zero_control_and_numerical_critical_never_pass():
    boundary = acceptance_condition(0, 0, 0.9, boundary=True)
    assert boundary["status"] == "evidence_insufficient"
    ordinary = acceptance_condition(0, 0, 1.05, boundary=False)
    assert ordinary["status"] == "PASS"
    exact = acceptance_condition(0.9, 1, 0.9, boundary=True)
    assert exact["status"] == "numerical_critical"
    inside = acceptance_condition(0.8, 1, 0.9, boundary=True)
    assert inside["status"] == "PASS"
    outside = acceptance_condition(1.0, 1, 0.9, boundary=True)
    assert outside["status"] == "REJECTED"


def acceptance_protocol():
    return {
        "evaluation": {
            "acceptance": {
                "boundary_improvement_fraction": "candidate <= 0.90 * P6C",
                "ordinary_noninferiority_fraction": "candidate <= 1.05 * P6C",
                "worst_unit_noninferiority_fraction": "candidate <= 1.05 * P6C",
            }
        },
        "numerical_reproduction": {"prediction_atol": 1e-12, "prediction_rtol": 1e-12},
    }


def test_m2_is_rejected_even_when_all_three_numeric_conditions_pass():
    candidate = {"boundary_mae": 0.5, "ordinary_mae": 1.0, "worst_unit_mae": 1.0}
    control = {"boundary_mae": 1.0, "ordinary_mae": 1.0, "worst_unit_mae": 1.0}
    m1 = evaluate_acceptance(candidate, control, "M1", acceptance_protocol())
    m2 = evaluate_acceptance(candidate, control, "M2", acceptance_protocol())
    assert m1["scientific_status"] == "PASS" and m1["qualified"]
    assert m2["three_condition_status"] == "PASS"
    assert m2["scientific_status"] == "REJECTED" and not m2["qualified"]
    assert m2["scientific_rejection_reason"] == "P3_AGGREGATION_ESTIMAND_MISMATCH"


def m2_rows():
    rows, _, _ = complete_frames()
    rows["target"] = "k2eff_pct_native"
    rows["pred_a"] = math.log(5.0)
    rows["pred_z"] = math.log(0.1)
    rows["pred_fs_ghz"] = 5.0
    rows["pred_fp_ghz"] = 5.5
    rows["pred_frequency_ratio"] = 5.0 / 5.5
    rows["pred_k2eff_pct_native"] = 25 * np.pi**2 * (5 / 5.5) * (1 - 5 / 5.5)
    rows["prediction"] = rows.pred_k2eff_pct_native
    rows["frequency_valid"] = True
    rows["native_k2_valid"] = True
    rows["aggregation_estimand_match"] = False
    rows["qualified"] = False
    rows["p3_comparable_prediction"] = np.nan
    rows["p3_comparable_null_reason"] = "P3_AGGREGATION_ESTIMAND_MISMATCH"
    return rows


def m2_protocol():
    return {"numerical_reproduction": {"formula_consistency_atol": 1e-12}}


def test_m2_native_formula_and_frequency_are_checked():
    rows = m2_rows()
    report = validate_m2_predictions(rows, m2_protocol())
    assert report["aggregation_estimand_match"] is False
    rows.loc[0, "pred_k2eff_pct_native"] += 0.01
    with pytest.raises(EvaluationError, match="M2_NATIVE_K2_FORMULA_MISMATCH"):
        validate_m2_predictions(rows, m2_protocol())
    rows = m2_rows()
    rows.loc[0, "pred_fs_ghz"] = 6.0
    with pytest.raises(EvaluationError, match="M2_INVALID_FREQUENCY"):
        validate_m2_predictions(rows, m2_protocol())
    rows = m2_rows()
    rows.loc[0, "target"] = "k2eff_pct"
    with pytest.raises(EvaluationError, match="M2_TARGET_NOT_NATIVE"):
        validate_m2_predictions(rows, m2_protocol())


def test_false_positive_definition_and_no_event_is_not_zero_risk():
    rows = pd.DataFrame(
        [
            prediction_row(0, actual=10.5, prediction=11.0),
            prediction_row(1, actual=11.0, prediction=12.0),
            prediction_row(2, actual=9.0, prediction=10.0),
        ]
    )
    events = false_positive_events(rows, model_family="M1", config_id="test", baseline=10.0)
    assert events.structure_id.tolist() == ["s_0"]
    assert events.predicted_uplift_pp.iloc[0] == 1
    assert events.measured_uplift_pp.iloc[0] == 0.5
    empty = false_positive_events(rows.iloc[2:], model_family="M1", config_id="test", baseline=10.0)
    assert empty.empty


def test_isolation_rejects_group_leakage_and_outer_absorption():
    splits = pd.DataFrame(
        [
            {**prediction_row(0), "role": "fit", "original_role": "train"},
            {
                **prediction_row(1),
                "design_group_id": "d_b",
                "role": "evaluation",
                "original_role": "validation",
            },
            {
                **prediction_row(2),
                "design_group_id": "d_71c5a74d95544080",
                "role": "baseline_only",
                "original_role": "baseline",
            },
        ]
    )
    protocol = {"baseline": {"design_group_id": "d_71c5a74d95544080"}}
    assert validate_isolation(splits, protocol)["status"] == "PASS"
    leaked = splits.copy()
    leaked.loc[1, "design_group_id"] = "d_a"
    with pytest.raises(EvaluationError, match="FIT_EVALUATION_DESIGN_GROUP_LEAKAGE"):
        validate_isolation(leaked, protocol)
    outer = splits.copy()
    outer.loc[0, "original_role"] = "outer_test"
    with pytest.raises(EvaluationError, match="OUTER_ROWS_ABSORBED_IN_INNER_LOOP"):
        validate_isolation(outer, protocol)
