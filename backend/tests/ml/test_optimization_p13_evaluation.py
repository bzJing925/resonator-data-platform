from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.ml.optimization_p13.contracts import ContractError, load_protocol
from app.ml.optimization_p13.evaluation import (
    SCOPES,
    assert_same_evaluation_rows,
    compare_candidate_to_p6c,
    evaluate_acceptance,
    evaluate_metric_bundle,
    evaluation_row_differences,
    threshold_at_most,
    unit_design_group_mae,
)

BACKEND = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = (
    BACKEND
    / "surrogate_data/optimization_p13_20261027_r01/contracts/protocol.json"
)


def protocol():
    return load_protocol(PROTOCOL_PATH)


def metric_row(unit, error, structure=None, group=None):
    scope = unit.scope
    family = unit.family
    branch = unit.branch_id if family != "ordinary" else "pf0_eg1_fl1"
    structure = structure or f"s_{unit.unit_id}"
    return {
        "unit_id": unit.unit_id,
        "structure_id": structure,
        "design_group_id": group or f"d_{unit.unit_id}",
        "scope": scope,
        "fold": unit.unit_id,
        "family": family,
        "role": "evaluation",
        "branch_id": branch,
        "actual": 10.0,
        "prediction": 10.0 + error,
        "has_pf": 0,
        "target": "k2eff_pct",
        "unit": "percent_number",
        "source_split_sha256": f"{scope}_split",
        "source_structure_sha256": "p3_structure",
    }


def synthetic_metrics():
    capacities, rows = [], []
    for scope_index, scope in enumerate(SCOPES, start=1):
        ordinary_id = f"{scope}__ordinary__all_branches__fold_0"
        ordinary = {
            "unit_id": ordinary_id,
            "scope": scope,
            "family": "ordinary",
            "branch_id": "all_branches",
            "valid": True,
        }
        capacities.append(ordinary)
        rows.append(metric_row(type("Unit", (), ordinary)(), scope_index))
        boundary_specs = [
            ("eg_upper", "pf0_eg1_fl0", "top_1", 10.0),
            ("eg_upper", "pf0_eg1_fl1", "top_1", 20.0),
            ("fl_lower", "pf0_eg1_fl1", "bottom_1", 30.0),
            ("fl_lower", "pf0_eg0_fl1", "bottom_1", 50.0),
            ("joint_eg_upper_fl_lower", "pf0_eg1_fl1", "top_1__bottom_1", 60.0),
        ]
        for family, branch, variant, error in boundary_specs:
            unit_id = f"{scope}__{family}__{branch}__{variant}"
            unit = {
                "unit_id": unit_id,
                "scope": scope,
                "family": family,
                "branch_id": branch,
                "valid": True,
            }
            capacities.append(unit)
            rows.append(metric_row(type("Unit", (), unit)(), error))
    return pd.DataFrame(rows), pd.DataFrame(capacities)


def test_unit_mae_is_design_group_weighted_and_missing_prediction_fails():
    rows = pd.DataFrame(
        [
            {
                "unit_id": "u1",
                "structure_id": "a1",
                "design_group_id": "a",
                "role": "evaluation",
                "actual": 10.0,
                "prediction": 10.0,
            },
            {
                "unit_id": "u1",
                "structure_id": "a2",
                "design_group_id": "a",
                "role": "evaluation",
                "actual": 10.0,
                "prediction": 20.0,
            },
            {
                "unit_id": "u1",
                "structure_id": "b1",
                "design_group_id": "b",
                "role": "evaluation",
                "actual": 10.0,
                "prediction": 20.0,
            },
        ]
    )
    result = unit_design_group_mae(rows)
    assert result["status"] == "PASS"
    assert result["mae"] == pytest.approx(7.5)
    assert result["error_unit"] == "percentage_points"
    rows.loc[2, "prediction"] = None
    result = unit_design_group_mae(rows)
    assert result["status"] == "FAILED"
    assert result["mae"] is None
    assert result["n_structures"] == 3
    assert result["invalid_reason"] == "MISSING_OR_INVALID_PREDICTION"


def test_metric_hierarchy_matches_o02_equal_weights():
    rows, capacity = synthetic_metrics()
    result = evaluate_metric_bundle(rows, capacity)
    assert result["status"] == "PASS"
    assert result["ordinary_mae"] == pytest.approx(2.5)
    assert result["boundary_mae"] == pytest.approx((15 + 40 + 60) / 3)
    assert result["worst_unit_mae"] == pytest.approx(60.0)
    assert result["worst_unit_id"].endswith("top_1__bottom_1")
    assert result["evaluation_unit_count"] == 24
    assert result["ordinary_overall_scope_count"] == 4
    assert result["boundary_overall_scope_count"] == 4
    assert len(result["boundary_branch_metrics"]) == 20
    assert len(result["boundary_family_metrics"]) == 12
    assert len(result["boundary_scope_metrics"]) == 4


def test_missing_prediction_or_unit_cannot_improve_bundle():
    rows, capacity = synthetic_metrics()
    rows.loc[10, "prediction"] = None
    result = evaluate_metric_bundle(rows, capacity)
    assert result["status"] == "FAILED"
    assert result["ordinary_mae"] is None
    assert result["boundary_mae"] is None
    assert any("MISSING_OR_INVALID_PREDICTION" in reason for reason in result["invalid_reasons"])
    acceptance = evaluate_acceptance(result, result, protocol())
    assert acceptance["status"] == "FAILED"
    assert acceptance["qualified"] is False
    rows, capacity = synthetic_metrics()
    missing_unit = rows.unit_id.iloc[10]
    result = evaluate_metric_bundle(rows[rows.unit_id.ne(missing_unit)], capacity)
    assert result["status"] == "FAILED"
    assert any("MISSING_EVALUATION_UNIT" in reason for reason in result["invalid_reasons"])


def test_invalid_actual_and_non_pf0_boundary_are_rejected():
    rows, capacity = synthetic_metrics()
    rows.loc[0, "actual"] = None
    result = evaluate_metric_bundle(rows, capacity)
    assert result["status"] == "FAILED"
    assert any("ACTUAL_INVALID" in reason for reason in result["invalid_reasons"])
    rows, capacity = synthetic_metrics()
    boundary_index = rows.family.ne("ordinary").idxmax()
    rows.loc[boundary_index, "has_pf"] = 1
    with pytest.raises(ContractError) as error:
        evaluate_metric_bundle(rows, capacity)
    assert error.value.reason == "PF0_BOUNDARY_REQUIRED"


def test_same_evaluation_rows_and_source_hashes_are_enforced():
    candidate, _ = synthetic_metrics()
    control = candidate.copy()
    assert_same_evaluation_rows(candidate, control)
    assert evaluation_row_differences(candidate, control) == []
    with pytest.raises(ContractError) as error:
        assert_same_evaluation_rows(candidate.iloc[1:], control)
    assert error.value.reason == "SAME_EVALUATION_ROWS_REQUIRED"
    changed = control.copy()
    changed.loc[0, "source_structure_sha256"] = "different"
    differences = evaluation_row_differences(candidate, changed)
    assert len(differences) == 1
    assert differences[0]["side"] == "source_hash_mismatch"


def bundle(ordinary, boundary, worst):
    return {
        "status": "PASS",
        "ordinary_mae": ordinary,
        "boundary_mae": boundary,
        "worst_unit_mae": worst,
    }


def test_acceptance_pass_and_exact_threshold_is_numerical_critical():
    control = bundle(100.0, 100.0, 100.0)
    candidate = bundle(104.0, 89.0, 104.0)
    result = evaluate_acceptance(candidate, control, protocol())
    assert result["status"] == "PASS"
    assert result["qualified"] is True
    candidate["boundary_mae"] = 90.0
    result = evaluate_acceptance(candidate, control, protocol())
    assert result["qualified"] is False
    assert result["status"] == "evidence_insufficient"
    assert result["conditions"]["boundary_improvement"]["status"] == "numerical_critical"


def test_zero_control_rules_do_not_divide_or_pass_boundary_improvement():
    control = bundle(0.0, 0.0, 0.0)
    candidate = bundle(0.0, 0.0, 0.0)
    result = evaluate_acceptance(candidate, control, protocol())
    assert result["qualified"] is False
    assert result["status"] == "evidence_insufficient"
    assert result["conditions"]["boundary_improvement"]["status"] == "evidence_insufficient"
    assert result["conditions"]["ordinary_noninferiority"]["status"] == "PASS"
    assert result["conditions"]["worst_unit_noninferiority"]["status"] == "PASS"
    candidate["worst_unit_mae"] = 1.0
    result = evaluate_acceptance(candidate, control, protocol())
    assert result["status"] == "REJECTED"
    assert result["conditions"]["worst_unit_noninferiority"]["status"] == "FAILED"


def test_noninferiority_and_m2_aggregation_contract():
    control = bundle(100.0, 100.0, 100.0)
    candidate = bundle(106.0, 89.0, 104.0)
    result = evaluate_acceptance(candidate, control, protocol())
    assert result["qualified"] is False
    assert result["conditions"]["ordinary_noninferiority"]["status"] == "FAILED"
    candidate = bundle(0.0, 0.0, 0.0)
    result = evaluate_acceptance(candidate, control, protocol(), model_family="M2")
    assert result["qualified"] is False
    assert result["status"] == "REJECTED"
    assert result["reason"] == "P3_AGGREGATION_ESTIMAND_MISMATCH"
    assert result["aggregation_estimand_match"] is False
    assert result["eligibility_override_allowed"] is False


def test_threshold_at_most_uses_frozen_tolerance():
    assert threshold_at_most(90.0, 90.0, protocol())["status"] == "numerical_critical"
    assert threshold_at_most(89.999999, 90.0, protocol())["status"] == "PASS"
    assert threshold_at_most(90.000001, 90.0, protocol())["status"] == "FAILED"


def test_compare_candidate_to_p6c_uses_same_rows_and_fixed_recipe():
    candidate, capacity = synthetic_metrics()
    control = candidate.copy()
    control["prediction"] = control["actual"]
    result = compare_candidate_to_p6c(
        candidate, control, capacity, protocol(), required_evaluation_rows=candidate
    )
    assert result["same_evaluation_rows"] is True
    assert result["p6c_recipe"] == "trend_gp_log_area"
    assert result["candidate"]["status"] == "PASS"
    assert result["control"]["status"] == "PASS"
    assert result["acceptance"]["qualified"] is False
    assert result["acceptance"]["status"] == "REJECTED"


def test_null_design_group_is_rejected_instead_of_dropped():
    rows, capacity = synthetic_metrics()
    rows.loc[0, "design_group_id"] = None
    with pytest.raises(ContractError) as error:
        evaluate_metric_bundle(rows, capacity)
    assert error.value.reason == "IDENTITY_REQUIRED"


def test_same_rows_also_requires_identical_actual_labels():
    candidate, _ = synthetic_metrics()
    control = candidate.copy()
    control.loc[0, "actual"] = control.loc[0, "actual"] + 1.0
    differences = evaluation_row_differences(candidate, control)
    assert len(differences) == 1
    assert differences[0]["side"] == "actual_mismatch"
    with pytest.raises(ContractError) as error:
        assert_same_evaluation_rows(candidate, control)
    assert error.value.reason == "SAME_EVALUATION_ROWS_REQUIRED"


def unbalanced_ordinary_data():
    capacities, rows = [], []
    for scope in SCOPES:
        for fold in range(4):
            unit_id = f"{scope}__ordinary__all_branches__fold_{fold}"
            capacities.append(
                {
                    "unit_id": unit_id,
                    "scope": scope,
                    "family": "ordinary",
                    "branch_id": "all_branches",
                    "valid": True,
                }
            )
            specs = (
                [("large", 100, 1.0), ("small", 1, 50.0)]
                if scope == "structure_holdout" and fold == 0
                else [("single", 1, 1.0)]
            )
            for group, count, error in specs:
                for index in range(count):
                    rows.append(
                        {
                            "unit_id": unit_id,
                            "structure_id": f"{unit_id}_{group}_{index}",
                            "design_group_id": f"{scope}_{fold}_{group}",
                            "scope": scope,
                            "fold": unit_id,
                            "family": "ordinary",
                            "role": "evaluation",
                            "branch_id": "pf0_eg1_fl1",
                            "actual": 10.0,
                            "prediction": 10.0 + error,
                            "has_pf": 0,
                            "target": "k2eff_pct",
                            "unit": "percent_number",
                        }
                    )
        unit_id = f"{scope}__joint_eg_upper_fl_lower__pf0_eg1_fl1__top_1__bottom_1"
        capacities.append(
            {
                "unit_id": unit_id,
                "scope": scope,
                "family": "joint_eg_upper_fl_lower",
                "branch_id": "pf0_eg1_fl1",
                "valid": True,
            }
        )
        rows.append(
            {
                "unit_id": unit_id,
                "structure_id": unit_id,
                "design_group_id": unit_id,
                "scope": scope,
                "fold": unit_id,
                "family": "joint_eg_upper_fl_lower",
                "role": "evaluation",
                "branch_id": "pf0_eg1_fl1",
                "actual": 10.0,
                "prediction": 11.0,
                "has_pf": 0,
                "target": "k2eff_pct",
                "unit": "percent_number",
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(capacities)


def test_crosscheck_ordinary_scope_pool_design_groups_before_scope_mean():
    rows, capacity = unbalanced_ordinary_data()
    result = evaluate_metric_bundle(rows, capacity)
    structure = next(
        row for row in result["ordinary_scope_metrics"] if row["scope"] == "structure_holdout"
    )
    assert structure["mae"] == pytest.approx(10.8)
    assert result["ordinary_mae"] == pytest.approx(3.45)


def test_crosscheck_source_hash_coverage_required():
    candidate, _ = synthetic_metrics()
    control = candidate.copy().drop(columns=["source_structure_sha256"])
    differences = evaluation_row_differences(candidate, control)
    assert differences
    assert differences[0]["side"] == "source_hash_coverage_mismatch"


def frozen_o05_evaluation_rows():
    columns = [
        "unit_id",
        "structure_id",
        "design_group_id",
        "scope",
        "fold",
        "family",
        "role",
        "branch_id",
        "has_pf",
        "source_split_sha256",
        "source_structure_sha256",
    ]
    path = BACKEND / "surrogate_data/optimization_p13_20261027_r01/evaluation/splits.csv"
    rows = pd.read_csv(path, usecols=columns)
    rows = rows[rows.role.eq("evaluation")].copy()
    assert len(rows) == 2882
    rows["actual"] = 10.0
    rows["prediction"] = 10.0
    rows["target"] = "k2eff_pct"
    rows["unit"] = "percent_number"
    return rows


def test_crosscheck_joint_drop_requires_complete_o05_rows():
    evaluation = frozen_o05_evaluation_rows()
    control = evaluation.copy()
    drop_index = evaluation.index[0]
    with pytest.raises(ContractError) as error:
        compare_candidate_to_p6c(
            evaluation.drop(index=drop_index),
            control.drop(index=drop_index),
            pd.read_csv(
                BACKEND
                / "surrogate_data/optimization_p13_20261027_r01/evaluation/capacity.csv"
            ),
            protocol(),
        )
    assert error.value.reason == "COMPLETE_O05_ROWS_REQUIRED"


def test_crosscheck_native_target_descriptive_m2_rejected_only_by_aggregation():
    candidate = frozen_o05_evaluation_rows()
    candidate["target"] = "k2eff_pct_native"
    control = candidate.copy()
    control["target"] = "k2eff_pct"
    result = compare_candidate_to_p6c(
        candidate,
        control,
        pd.read_csv(
            BACKEND / "surrogate_data/optimization_p13_20261027_r01/evaluation/capacity.csv"
        ),
        protocol(),
        model_family="M2",
    )
    assert result["candidate"]["status"] == "PASS"
    assert result["acceptance"]["qualified"] is False
    assert result["acceptance"]["reason"] == "P3_AGGREGATION_ESTIMAND_MISMATCH"
