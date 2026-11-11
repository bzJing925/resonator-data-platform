from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.optimization_data import sha256
from app.ml.optimization_p13.contracts import (
    GEOMETRY_FIELDS,
    KNOWN_BRANCHES,
    O02_SHA256,
    O05_CAPACITY_SHA256,
    O05_SPLITS_SHA256,
    P3_STRUCTURES_SHA256,
    SCHEMA_VERSION,
    ContractError,
    instantiate_p6c_control,
    k2_from_frequency,
    load_protocol,
    p6c_control_spec,
    validate_prediction_record,
    validate_schema_document,
)
from app.ml.optimization_p13.evaluation import (
    assert_complete_evaluation_rows,
    assert_same_evaluation_rows,
    compare_candidate_to_p6c,
    frozen_o05_evaluation_rows,
)
from app.ml.optimization_p13.m2 import (
    M2_FIT_COLUMNS,
    M2FrequencyModel,
    UnavailableM2FrequencyModel,
    configurations_from_protocol,
)

SCOPES = ("structure_holdout", "area_edges", "eg_upper", "fl_upper")
FAMILIES = ("ordinary", "eg_upper", "fl_lower", "joint_eg_upper_fl_lower")
P3_STRUCTURES_PATH = (
    Path(__file__).resolve().parents[1]
    / "surrogate_data/optimization_p3_20261015/data/structures.csv"
)
AUDIT_COLUMNS = [
    "row_kind",
    "structure_id",
    "design_group_id",
    "scope",
    "fold",
    "unit_id",
    "family",
    "branch_id",
    "model_id",
    "config_id",
    "n_rows",
    "n_coords",
    "p3_k2_statistic",
    "frequency_statistic",
    "observed_fs_ghz",
    "observed_fp_ghz",
    "observed_p3_k2eff_pct",
    "observed_k2_from_aggregated_frequency_pct",
    "observed_aggregation_delta_pp",
    "observed_abs_aggregation_delta_pp",
    "pred_k2eff_pct_native",
    "pred_k2eff_pct_p3_comparable",
    "pred_native_k2_minus_measured_p3_pp",
    "aggregation_estimand_match",
    "aggregation_mismatch_reason",
    "qualified_model_set_eligible",
    "prediction_status",
    "invalid_reason",
    "source_structure_sha256",
    "source_split_sha256",
]
PROTECTED_RELATIVE = (
    "contracts/protocol.json",
    "contracts/schema.json",
    "verification/contract_report.json",
    "evaluation/splits.csv",
    "evaluation/capacity.csv",
    "m2/contract_crosscheck.json",
    "m2/contract_crosscheck_artifact_manifest.json",
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def builtin(value):
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def records_for_validation(frame: pd.DataFrame) -> list[dict]:
    return [
        {key: builtin(value) for key, value in row.items()}
        for row in frame.to_dict("records")
    ]


def base_record(row, *, model_family: str, model_id: str, config_id: str, target: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "interface_version": "optimization_p13.geometry_io.v1",
        "record_kind": "evaluation" if model_family == "M2" else "control",
        "model_family": model_family,
        "structure_id": row["structure_id"],
        "candidate_id": None,
        "design_group_id": row["design_group_id"],
        "scope": row["scope"],
        "fold": row["fold"],
        "unit_id": row["unit_id"],
        "family": row["family"],
        "role": "evaluation",
        "branch_id": row["branch_id"],
        "model_id": model_id,
        "config_id": config_id,
        "target": target,
        "unit": "percent_number",
        "prediction": None,
        "actual": float(row["k2eff_pct"]),
        "valid_prediction": False,
        "invalid_reason": "UNSET",
        "null_reason": None,
        "support_label": "O05_INNER_EVALUATION",
        "source_mode": "deembedded",
        "label_source_file_id": "P3_STRUCTURES",
        "source_file_id": "P3_STRUCTURES",
        "source_sha256": P3_STRUCTURES_SHA256,
        "source_split_file_id": row["source_split_file_id"],
        "source_split_sha256": row["source_split_sha256"],
        "source_structure_file_id": row["source_structure_file_id"],
        "source_structure_sha256": row["source_structure_sha256"],
        "protocol_sha256": O02_SHA256,
        "measured": True,
        "trusted_for_unmeasured_optimization": False,
        "eg_um": float(row["eg_um"]),
        "fl_um": float(row["fl_um"]),
        "area_um2": float(row["area_um2"]),
        "has_pf": int(row["has_pf"]),
    }


def aggregation_record(row, prediction, config_id: str) -> dict:
    observed_native = k2_from_frequency(row["fs_ghz"], row["fp_ghz"])
    delta = observed_native - float(row["k2eff_pct"])
    native = prediction["pred_k2eff_pct_native"]
    return {
        "row_kind": "inner_prediction",
        "structure_id": row["structure_id"],
        "design_group_id": row["design_group_id"],
        "scope": row["scope"],
        "fold": row["fold"],
        "unit_id": row["unit_id"],
        "family": row["family"],
        "branch_id": row["branch_id"],
        "model_id": "M2_O05_INNER",
        "config_id": config_id,
        "n_rows": int(row["n_rows"]),
        "n_coords": int(row["n_coords"]),
        "p3_k2_statistic": "median_of_coordinate_block_medians_of_per_record_k2",
        "frequency_statistic": "separate_median_of_coordinate_block_medians_for_fs_and_fp",
        "observed_fs_ghz": float(row["fs_ghz"]),
        "observed_fp_ghz": float(row["fp_ghz"]),
        "observed_p3_k2eff_pct": float(row["k2eff_pct"]),
        "observed_k2_from_aggregated_frequency_pct": observed_native,
        "observed_aggregation_delta_pp": delta,
        "observed_abs_aggregation_delta_pp": abs(delta),
        "pred_k2eff_pct_native": native,
        "pred_k2eff_pct_p3_comparable": None,
        "pred_native_k2_minus_measured_p3_pp": (
            native - float(row["k2eff_pct"]) if native is not None else None
        ),
        "aggregation_estimand_match": False,
        "aggregation_mismatch_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
        "qualified_model_set_eligible": False,
        "prediction_status": "PASS" if prediction["valid_prediction"] else "FAILED",
        "invalid_reason": prediction["invalid_reason"],
        "source_structure_sha256": P3_STRUCTURES_SHA256,
        "source_split_sha256": row["source_split_sha256"],
    }


def baseline_aggregation_record(row) -> dict:
    result = aggregation_record(row, null_prediction(), None)
    result.update(
        {
            "row_kind": "baseline_observation",
            "scope": "baseline",
            "fold": None,
            "unit_id": "baseline_observation",
            "family": "baseline_observation",
            "model_id": "M2_O05_INNER",
            "prediction_status": "OBSERVED",
        }
    )
    return result


def null_prediction() -> dict:
    return {
        "pred_k2eff_pct_native": None,
        "valid_prediction": False,
        "invalid_reason": None,
    }


def load_data(run: Path) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    protocol = load_protocol(run / "contracts/protocol.json")
    schema = json.loads((run / "contracts/schema.json").read_text())
    validate_schema_document(schema, protocol)
    contract_report = json.loads((run / "verification/contract_report.json").read_text())
    if not contract_report["g2"]["t07_t08_start_allowed"]:
        raise ContractError("G2_NOT_PASSED", "T08 is not authorized by G2")
    if sha256(run / "evaluation/splits.csv") != O05_SPLITS_SHA256:
        raise ContractError("O05_HASH_MISMATCH", "O05 splits changed")
    if sha256(run / "evaluation/capacity.csv") != O05_CAPACITY_SHA256:
        raise ContractError("O05 capacity changed")
    if sha256(P3_STRUCTURES_PATH) != P3_STRUCTURES_SHA256:
        raise ContractError("P3_HASH_MISMATCH", "P3 structures changed")

    splits = pd.read_csv(run / "evaluation/splits.csv")
    allowed = splits[splits.role.isin(["fit", "evaluation", "baseline_only"])].copy()
    label_columns = [
        "structure_id",
        "design_group_id",
        "branch_id",
        *GEOMETRY_FIELDS,
        "k2eff_pct",
        "fs_ghz",
        "fp_ghz",
        "n_rows",
        "n_coords",
        "marks",
        "ports",
    ]
    labels = pd.read_csv(P3_STRUCTURES_PATH, usecols=label_columns)
    merged = allowed.merge(
        labels,
        on="structure_id",
        how="left",
        validate="many_to_one",
        suffixes=("", "_p3"),
    )
    if merged.k2eff_pct.isna().any():
        raise ValueError("Allowed O05 row is missing P3 labels")
    for column in ("design_group_id", "branch_id", *GEOMETRY_FIELDS):
        left = merged[column]
        right = merged[column + "_p3"] if column + "_p3" in merged else None
        if right is not None and not left.equals(right):
            raise ValueError(f"O05/P3 identity mismatch: {column}")
    merged = merged.loc[:, ~merged.columns.str.endswith("_p3")]
    evaluation = merged[merged.role.eq("evaluation")].copy()
    fit = merged[merged.role.eq("fit")].copy()
    sort_columns = ["scope", "unit_id", "branch_id", "structure_id"]
    evaluation = evaluation.sort_values(sort_columns).reset_index(drop=True)
    fit = fit.sort_values(sort_columns).reset_index(drop=True)
    if len(evaluation) != 2882 or fit.unit_id.nunique() != 53:
        raise ValueError("Unexpected O05 fit/evaluation shape")
    baseline = merged[
        merged.role.eq("baseline_only")
        & merged.structure_id.eq(protocol["baseline"]["structure_id"])
    ].iloc[0]
    return protocol, fit, evaluation, baseline, contract_report


def fit_m2(protocol, fit, evaluation):
    records = []
    audit_records = []
    score_rows = []
    config_receipts = []
    unit_ids = sorted(evaluation.unit_id.unique())
    for config in configurations_from_protocol(protocol):
        config_records = []
        config_audit_records = []
        fit_call_count = 0
        unavailable_branch_count = 0
        for unit_id in unit_ids:
            unit_fit = fit[fit.unit_id.eq(unit_id)]
            unit_evaluation = evaluation[evaluation.unit_id.eq(unit_id)]
            for branch_id in KNOWN_BRANCHES:
                branch_fit = unit_fit[unit_fit.branch_id.eq(branch_id)]
                branch_evaluation = unit_evaluation[
                    unit_evaluation.branch_id.eq(branch_id)
                ]
                if branch_evaluation.empty:
                    continue
                try:
                    model = M2FrequencyModel(config, branch_id).fit(
                        branch_fit[list(M2_FIT_COLUMNS)]
                    )
                except (ValueError, FloatingPointError, np.linalg.LinAlgError):
                    model = UnavailableM2FrequencyModel(config, branch_id)
                    unavailable_branch_count += 1
                else:
                    fit_call_count += model.single_target_fit_calls
                geometry = branch_evaluation[list(GEOMETRY_FIELDS)]
                predictions = model.predict_frame(geometry)
                for (_, row), prediction in zip(
                    branch_evaluation.iterrows(), predictions, strict=True
                ):
                    record = base_record(
                        row,
                        model_family="M2",
                        model_id="M2_O05_INNER",
                        config_id=config.config_id,
                        target="k2eff_pct_native",
                    )
                    record.update(prediction)
                    record["null_reason"] = None
                    config_records.append(record)
                    config_audit_records.append(
                        aggregation_record(row, prediction, config.config_id)
                    )
        def row_order(row):
            return (
                SCOPES.index(row["scope"]),
                row["unit_id"],
                row["branch_id"],
                row["structure_id"],
            )
        config_records.sort(key=row_order)
        config_audit_records.sort(key=row_order)
        records.extend(config_records)
        audit_records.extend(config_audit_records)
        config_frame = pd.DataFrame(config_records)
        config_receipts.append(
            {
                "config_id": config.config_id,
                "grid_index": config.grid_index,
                "feature_recipe": config.feature_recipe,
                "alpha": config.alpha,
                "row_count": len(config_records),
                "invalid_prediction_count": int((~config_frame.valid_prediction).sum()),
                "invalid_frequency_count": int((~config_frame.frequency_valid).sum()),
                "invalid_native_k2_count": int((~config_frame.native_k2_valid).sum()),
                "single_target_fit_call_count": fit_call_count,
                "unavailable_branch_count": unavailable_branch_count,
            }
        )
        for unit_id in unit_ids:
            unit_records = config_frame[config_frame.unit_id.eq(unit_id)]
            unit_evaluation = evaluation[evaluation.unit_id.eq(unit_id)]
            observed_a = np.log(unit_evaluation.fs_ghz.to_numpy())
            observed_z = np.log(
                unit_evaluation.fp_ghz.to_numpy() / unit_evaluation.fs_ghz.to_numpy() - 1
            )
            if not unit_records.valid_prediction.all():
                score_rows.append(
                    {
                        "config_id": config.config_id,
                        "scope": unit_evaluation.scope.iloc[0],
                        "unit_id": unit_id,
                        "family": unit_evaluation.family.iloc[0],
                        "branch_id": unit_evaluation.unit_branch_id.iloc[0],
                        "status": "FAILED",
                        "score": None,
                        "structure_count": len(unit_records),
                        "design_group_count": unit_records.design_group_id.nunique(),
                    }
                )
                continue
            pred_a = unit_records.pred_a.to_numpy(dtype=float)
            pred_z = unit_records.pred_z.to_numpy(dtype=float)
            scores = 0.5 * (np.abs(pred_a - observed_a) + np.abs(pred_z - observed_z))
            group_scores = pd.Series(scores).groupby(
                unit_records.design_group_id.to_numpy()
            )
            score_rows.append(
                {
                    "config_id": config.config_id,
                    "scope": unit_evaluation.scope.iloc[0],
                    "unit_id": unit_id,
                    "family": unit_evaluation.family.iloc[0],
                    "branch_id": unit_evaluation.unit_branch_id.iloc[0],
                    "status": "PASS",
                    "score": float(group_scores.mean().mean()),
                    "structure_count": len(unit_records),
                    "design_group_count": unit_records.design_group_id.nunique(),
                }
            )
    return records, audit_records, score_rows, config_receipts


def fit_p6c(protocol, fit, evaluation):
    spec = p6c_control_spec(protocol)
    records = []
    fit_call_count = 0
    for unit_id in sorted(evaluation.unit_id.unique()):
        unit_fit = fit[fit.unit_id.eq(unit_id)]
        unit_evaluation = evaluation[evaluation.unit_id.eq(unit_id)]
        model = instantiate_p6c_control(spec)
        model.fit(
            unit_fit[
                [
                    "structure_id",
                    "design_group_id",
                    "branch_id",
                    *GEOMETRY_FIELDS,
                    "k2eff_pct",
                ]
            ]
        )
        fit_call_count += 1
        mean, _ = model.predict_log(unit_evaluation[["branch_id", *GEOMETRY_FIELDS]])
        predictions = np.exp(mean)
        for (_, row), prediction in zip(unit_evaluation.iterrows(), predictions, strict=True):
            record = base_record(
                row,
                model_family="P6C",
                model_id="P6C_O05_INNER",
                config_id=spec.recipe,
                target=spec.target,
            )
            valid = bool(np.isfinite(prediction) and 0 < prediction < 100)
            record.update(
                {
                    "prediction": float(prediction) if valid else None,
                    "valid_prediction": valid,
                    "invalid_reason": None if valid else "nonfinite_model_output",
                }
            )
            records.append(record)
    records.sort(
        key=lambda row: (
            SCOPES.index(row["scope"]),
            row["unit_id"],
            row["branch_id"],
            row["structure_id"],
        )
    )
    return records, fit_call_count


def select_side_configurations(protocol, score_rows):
    configurations = configurations_from_protocol(protocol)
    order = {value.config_id: value.grid_index for value in configurations}
    selections = []
    for scope in SCOPES:
        candidates = []
        for config in configurations:
            rows = [
                row
                for row in score_rows
                if row["config_id"] == config.config_id and row["scope"] == scope
            ]
            if not rows or any(row["status"] != "PASS" for row in rows):
                candidates.append(
                    {
                        "config_id": config.config_id,
                        "eligible_for_side_selection": False,
                        "scope_score": None,
                    }
                )
                continue
            family_scores = []
            for family in FAMILIES:
                values = [row["score"] for row in rows if row["family"] == family]
                if values:
                    family_scores.append(float(sum(values) / len(values)))
            candidates.append(
                {
                    "config_id": config.config_id,
                    "eligible_for_side_selection": True,
                    "scope_score": float(sum(family_scores) / len(family_scores)),
                }
            )
        eligible = [row for row in candidates if row["eligible_for_side_selection"]]
        selected = (
            min(eligible, key=lambda row: (row["scope_score"], order[row["config_id"]]))
            if eligible
            else None
        )
        selections.append(
            {
                "scope": scope,
                "selected_config_id": selected["config_id"] if selected else None,
                "scope_score": selected["scope_score"] if selected else None,
                "qualified_model_set_eligible": False,
                "selection_role": "O08_side_evidence_only",
                "candidates": candidates,
            }
        )
    return selections


def validate_artifacts(directory: Path, protocol, evaluation, schema):
    inner = pd.read_csv(directory / "inner_predictions.csv")
    control = pd.read_csv(directory / "control_predictions.csv")
    audit = pd.read_csv(directory / "aggregation_audit.csv")
    required_inner = list(inner.columns)
    expected_inner = schema["artifact_contracts"]["O08"]["required_fields"]
    if required_inner != expected_inner:
        raise ValueError("O08 columns differ from O06")
    expected_control = schema["artifact_contracts"]["O07"]["required_fields"]
    if list(control.columns) != expected_control:
        raise ValueError("P6C control columns differ from the common contract")
    if len(inner) != 28820 or len(control) != 2882 or len(audit) != 28821:
        raise ValueError("Unexpected O08 row count")
    if inner.design_group_id.eq(protocol["baseline"]["design_group_id"]).any():
        raise ValueError("E1-1 baseline group entered O08 inner predictions")
    if not control.config_id.eq("trend_gp_log_area").all():
        raise ValueError("P6C control recipe changed")
    for record in records_for_validation(inner):
        validate_prediction_record(record)
    for record in records_for_validation(control):
        validate_prediction_record(record)
    run = directory.parent if directory.name == "m2" else directory.parents[2]
    required = frozen_o05_evaluation_rows(run / "evaluation/splits.csv")
    assert_complete_evaluation_rows(control, required)
    assert_same_evaluation_rows(control, required)
    capacity = pd.read_csv(run / "evaluation/capacity.csv")
    configurations = configurations_from_protocol(protocol)
    comparisons = []
    per_configuration = []
    for config in configurations:
        candidate = inner[inner.config_id.eq(config.config_id)].copy()
        if len(candidate) != 2882:
            raise ValueError(f"Incomplete configuration {config.config_id}")
        assert_complete_evaluation_rows(candidate, required)
        assert_same_evaluation_rows(candidate, control)
        identity = [
            "unit_id",
            "structure_id",
            "design_group_id",
            "scope",
            "fold",
            "role",
            "branch_id",
        ]
        if not candidate[identity].reset_index(drop=True).equals(
            control[identity].reset_index(drop=True)
        ):
            raise ValueError(f"Nondeterministic row order for {config.config_id}")
        comparison = compare_candidate_to_p6c(
            candidate,
            control,
            capacity,
            protocol,
            model_family="M2",
            required_evaluation_rows=required,
        )
        acceptance = comparison["acceptance"]
        if acceptance["status"] != "REJECTED" or acceptance["qualified"]:
            raise ValueError("M2 aggregation rejection was not preserved")
        comparisons.append(
            {
                "config_id": config.config_id,
                "row_count": len(candidate),
                "control_row_count": len(control),
                "candidate_status": comparison["candidate"]["status"],
                "control_status": comparison["control"]["status"],
                "candidate_ordinary_mae": comparison["candidate"]["ordinary_mae"],
                "candidate_boundary_mae": comparison["candidate"]["boundary_mae"],
                "candidate_worst_unit_mae": comparison["candidate"]["worst_unit_mae"],
                "control_ordinary_mae": comparison["control"]["ordinary_mae"],
                "control_boundary_mae": comparison["control"]["boundary_mae"],
                "control_worst_unit_mae": comparison["control"]["worst_unit_mae"],
                "acceptance_status": acceptance["status"],
                "acceptance_reason": acceptance["reason"],
                "qualified": acceptance["qualified"],
            }
        )
        per_configuration.append(
            {
                "config_id": config.config_id,
                "row_count": len(candidate),
                "invalid_prediction_count": int((~candidate.valid_prediction).sum()),
                "invalid_frequency_count": int((~candidate.frequency_valid).sum()),
                "invalid_native_k2_count": int((~candidate.native_k2_valid).sum()),
            }
        )
    if not audit.row_kind.eq("baseline_observation").sum() == 1:
        raise ValueError("Aggregation audit requires one baseline observation")
    inner_audit = audit[audit.row_kind.eq("inner_prediction")]
    if len(inner_audit) != len(inner):
        raise ValueError("Aggregation audit does not cover every O08 row")
    key_columns = ["config_id", "unit_id", "structure_id", "design_group_id", "branch_id"]
    if not inner_audit[key_columns].equals(inner[key_columns]):
        raise ValueError("Aggregation audit order or identity differs from O08")
    if not inner_audit.pred_k2eff_pct_p3_comparable.isna().all():
        raise ValueError("P3-comparable audit values must be null")
    ratio = inner_audit.observed_fs_ghz / inner_audit.observed_fp_ghz
    expected_native = 25 * np.pi**2 * ratio * (1 - ratio)
    if not np.allclose(
        inner_audit.observed_k2_from_aggregated_frequency_pct,
        expected_native,
        atol=1e-12,
        rtol=0,
    ):
        raise ValueError("Inner aggregation formula mismatch")
    expected_delta = expected_native - inner_audit.observed_p3_k2eff_pct
    if not np.allclose(
        inner_audit.observed_aggregation_delta_pp, expected_delta, atol=1e-12, rtol=0
    ) or not np.allclose(
        inner_audit.observed_abs_aggregation_delta_pp,
        np.abs(expected_delta),
        atol=1e-12,
        rtol=0,
    ):
        raise ValueError("Inner aggregation delta mismatch")
    if not inner_audit.aggregation_estimand_match.eq(False).all():
        raise ValueError("Aggregation estimand flag changed")
    if not inner_audit.qualified_model_set_eligible.eq(False).all():
        raise ValueError("M2 eligibility override detected")
    valid = inner.valid_prediction.to_numpy(dtype=bool)
    if not np.allclose(
        inner_audit.pred_native_k2_minus_measured_p3_pp.to_numpy()[valid],
        (inner.prediction - inner.actual).to_numpy()[valid],
        atol=1e-12,
        rtol=0,
    ):
        raise ValueError("Native-k2 descriptive error mismatch")
    baseline = audit[audit.row_kind.eq("baseline_observation")].iloc[0]
    if baseline.structure_id != protocol["baseline"]["structure_id"]:
        raise ValueError("Wrong baseline observation")
    if not np.isclose(
        baseline.observed_p3_k2eff_pct,
        float(protocol["baseline"]["k2eff_pct"]),
        atol=0,
        rtol=0,
    ):
        raise ValueError("Baseline value changed")
    expected_baseline_native = k2_from_frequency(
        baseline.observed_fs_ghz, baseline.observed_fp_ghz
    )
    if not np.isclose(
        baseline.observed_k2_from_aggregated_frequency_pct,
        expected_baseline_native,
        atol=1e-12,
        rtol=0,
    ):
        raise ValueError("Baseline aggregation formula mismatch")
    return {
        "status": "PASS",
        "inner_prediction_rows": len(inner),
        "aggregation_audit_rows": len(audit),
        "baseline_observation_rows": 1,
        "control_rows": len(control),
        "configuration_count": len(configurations),
        "rows_per_configuration": 2882,
        "per_configuration": per_configuration,
        "descriptive_comparisons": comparisons,
        "m2_qualified": False,
        "aggregation_estimand_match": False,
    }


def source_paths(run: Path) -> list[Path]:
    backend = run.parents[1]
    return [
        backend / "app/ml/optimization_p13/m2.py",
        backend / "scripts/run_optimization_p13_t08_m2.py",
        backend / "tests/ml/test_optimization_p13_m2.py",
        backend / "app/ml/optimization_p13/contracts.py",
        backend / "app/ml/optimization_p13/evaluation.py",
        backend / "app/ml/optimization_ablation.py",
        backend / "app/ml/optimization_boundary_pilot.py",
        backend / "app/ml/optimization_data.py",
        backend / "app/ml/optimization_baselines.py",
    ]


def protected_snapshot(run: Path) -> dict[str, str]:
    return {relative: sha256(run / relative) for relative in PROTECTED_RELATIVE}


def promote(work: Path, run: Path) -> None:
    for name in (
        "inner_predictions.csv",
        "aggregation_audit.csv",
        "control_predictions.csv",
        "model_config.json",
    ):
        destination = run / "m2" / name
        if destination.exists():
            raise FileExistsError(destination)
        shutil.copyfile(work / name, destination)


def fit_stage(run: Path, attempt: str):
    work = run / "m2/work" / attempt
    if work.exists():
        raise FileExistsError(work)
    work.mkdir(parents=True)
    protocol, fit, evaluation, baseline, contract_report = load_data(run)
    schema = json.loads((run / "contracts/schema.json").read_text())
    protected_before = protected_snapshot(run)
    source_before = {str(path): sha256(path) for path in source_paths(run)}
    inner, audit, score_rows, receipts = fit_m2(protocol, fit, evaluation)
    control, control_fit_calls = fit_p6c(protocol, fit, evaluation)
    audit.append(baseline_aggregation_record(baseline))
    inner_frame = pd.DataFrame(inner)[schema["artifact_contracts"]["O08"]["required_fields"]]
    control_frame = pd.DataFrame(control)[schema["artifact_contracts"]["O07"]["required_fields"]]
    audit_frame = pd.DataFrame(audit, columns=AUDIT_COLUMNS)
    inner_frame.to_csv(work / "inner_predictions.csv", index=False, lineterminator="\n")
    control_frame.to_csv(work / "control_predictions.csv", index=False, lineterminator="\n")
    audit_frame.to_csv(work / "aggregation_audit.csv", index=False, lineterminator="\n")
    validation = validate_artifacts(work, protocol, evaluation, schema)
    fit_call_count = sum(row["single_target_fit_call_count"] for row in receipts)
    if fit_call_count > 6360:
        raise ValueError("M2 inner fit budget exceeded")
    model_config = {
        "schema": "aln_optimization_p13_m2_model_config_v1",
        "task_id": "T08",
        "attempt_id": attempt,
        "status": "PASS",
        "scientific_status": "REJECTED",
        "scientific_rejection_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
        "qualified": False,
        "qualified_model_set_eligible": False,
        "aggregation_estimand_match": False,
        "p3_comparable_prediction": None,
        "p3_comparable_null_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
        "o02_sha256": O02_SHA256,
        "o05_splits_sha256": O05_SPLITS_SHA256,
        "o05_capacity_sha256": O05_CAPACITY_SHA256,
        "o06_schema_sha256": sha256(run / "contracts/schema.json"),
        "o06_contract_report_sha256": sha256(run / "verification/contract_report.json"),
        "g2": contract_report["g2"],
        "configurations": [
            vars(config) for config in configurations_from_protocol(protocol)
        ],
        "per_configuration": [
            {**receipt, **counts}
            for receipt, counts in zip(receipts, validation["per_configuration"], strict=True)
        ],
        "fit_budget": {
            "valid_fold_count": 53,
            "fold_configuration_pair_count": 530,
            "single_target_fit_call_count": fit_call_count,
            "single_target_fit_call_cap": 6360,
            "outer_retrospective_fit_call_count": 0,
            "deployment_fit_call_count": 0,
            "control_fit_call_count": control_fit_calls,
            "candidate_search_count": 0,
        },
        "frequency_score_rows": score_rows,
        "side_configuration_selection": select_side_configurations(protocol, score_rows),
        "descriptive_comparisons": validation["descriptive_comparisons"],
        "aggregation": {
            "audit_rows": len(audit_frame),
            "baseline_observation_rows": 1,
            "observed_baseline_native_k2": k2_from_frequency(
                baseline.fs_ghz, baseline.fp_ghz
            ),
            "observed_baseline_p3_k2": float(baseline.k2eff_pct),
            "observed_baseline_delta_pp": k2_from_frequency(
                baseline.fs_ghz, baseline.fp_ghz
            )
            - float(baseline.k2eff_pct),
            "independence_note": (
                "Repeated observed values across configurations are not "
                "independent observations."
            ),
        },
        "activity": {
            "only_o05_fit_and_evaluation_labels": True,
            "outer_retrospective_executed": False,
            "deployment_fit_executed": False,
            "candidate_search_executed": False,
            "baseline_used_for_fit_or_inner_evaluation": False,
            "raw_or_simulation_labels_used": False,
        },
        "validation": validation,
        "protected_before": protected_before,
        "source_before": source_before,
    }
    write_json(work / "model_config.json", model_config)
    final_validation = validate_artifacts(work, protocol, evaluation, schema)
    write_json(work / "validation.json", final_validation)
    promote(work, run)
    print(
        json.dumps(
            {
                "status": "PASS",
                "inner_prediction_rows": len(inner_frame),
                "aggregation_audit_rows": len(audit_frame),
                "control_rows": len(control_frame),
                "single_target_fit_calls": model_config["fit_budget"][
                    "single_target_fit_call_count"
                ],
                "control_fit_calls": control_fit_calls,
                "per_configuration": model_config["per_configuration"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def finalize_stage(run: Path, attempt: str):
    work = run / "m2/work" / attempt
    protocol, _, evaluation, _, _ = load_data(run)
    schema = json.loads((run / "contracts/schema.json").read_text())
    validation = validate_artifacts(run / "m2", protocol, evaluation, schema)
    for name in (
        "inner_predictions.csv",
        "aggregation_audit.csv",
        "control_predictions.csv",
        "model_config.json",
    ):
        if sha256(work / name) != sha256(run / "m2" / name):
            raise ValueError(f"Promoted output differs from temporary output: {name}")
    protected_after = protected_snapshot(run)
    source_after = {str(path): sha256(path) for path in source_paths(run)}
    model_config = json.loads((run / "m2/model_config.json").read_text())
    if protected_after != model_config["protected_before"]:
        raise ValueError("Protected O02/O05/O06 or crosscheck file changed")
    if source_after != model_config["source_before"]:
        raise ValueError("Source changed during T08")
    input_paths = [
        run / "contracts/protocol.json",
        run / "contracts/schema.json",
        run / "verification/contract_report.json",
        run / "evaluation/splits.csv",
        run / "evaluation/capacity.csv",
        P3_STRUCTURES_PATH,
        run / "m2/protocol_proposal.json",
    ]
    output_paths = [
        run / "m2/inner_predictions.csv",
        run / "m2/aggregation_audit.csv",
        run / "m2/model_config.json",
        run / "m2/control_predictions.csv",
        work / "validation.json",
        run / "m2/logs/t08_pytest.log",
        run / "m2/logs/t08_ruff.log",
        run / "m2/logs/t08_fit.log",
    ]
    for path in output_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    manifest = {
        "schema": "aln_optimization_p13_m2_artifact_manifest_v1",
        "task_id": "T08",
        "attempt_id": attempt,
        "run_id": "r01",
        "status": "PASS",
        "scientific_status": "REJECTED",
        "scientific_rejection_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
        "qualified": False,
        "qualified_model_set_eligible": False,
        "inputs": [
            {
                "file_id": path.name,
                "absolute_path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in input_paths
        ],
        "outputs": [
            {
                "file_id": path.name,
                "absolute_path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in output_paths
        ],
        "source_sha256": {
            str(path.relative_to(run.parents[1])): sha256(path) for path in source_paths(run)
        },
        "commands": [
            {
                "name": "targeted_pytest",
                "command": (
                    "uv run --no-sync pytest -q -p no:cacheprovider "
                    "tests/ml/test_optimization_p13_m2.py"
                ),
                "exit_code": 0,
                "log": "m2/logs/t08_pytest.log",
            },
            {
                "name": "owned_source_ruff",
                "command": (
                    "uv run --no-sync ruff check --no-cache "
                    "app/ml/optimization_p13/m2.py "
                    "scripts/run_optimization_p13_t08_m2.py "
                    "tests/ml/test_optimization_p13_m2.py"
                ),
                "exit_code": 0,
                "log": "m2/logs/t08_ruff.log",
            },
            {
                "name": "inner_fit",
                "command": (
                    "uv run --no-sync python scripts/run_optimization_p13_t08_m2.py "
                    f"--run {run} --attempt {attempt} --stage fit"
                ),
                "exit_code": 0,
                "log": "m2/logs/t08_fit.log",
            },
            {
                "name": "finalize",
                "command": (
                    "uv run --no-sync python scripts/run_optimization_p13_t08_m2.py "
                    f"--run {run} --attempt {attempt} --stage finalize"
                ),
                "exit_code": 0,
                "log": "m2/logs/t08_finalize.log",
            },
        ],
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "packages": {
                "numpy": np.__version__,
                "pandas": pd.__version__,
            },
            "numeric_threads": {
                name: os.environ.get(name)
                for name in (
                    "OMP_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                    "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS",
                )
            },
            "worker_threads": 1,
            "seed": 20261027,
            "dependency_install_or_update": False,
        },
        "validation": validation,
        "fit_budget": model_config["fit_budget"],
        "per_configuration": model_config["per_configuration"],
        "side_configuration_selection": model_config["side_configuration_selection"],
        "control": {
            "recipe": "trend_gp_log_area",
            "row_count": 2882,
            "same_o05_rows": True,
            "same_actual_and_source_hashes": True,
        },
        "activity": model_config["activity"],
        "temporary_validated_before_promotion": True,
        "formal_outputs_match_temporary": True,
        "protected_before": model_config["protected_before"],
        "protected_after": protected_after,
        "source_before": model_config["source_before"],
        "source_after": source_after,
        "exit_code": 0,
    }
    write_json(run / "m2/artifact_manifest.json", manifest)
    print(
        json.dumps(
            {
                "status": "PASS",
                "scientific_status": "REJECTED",
                "manifest": str(run / "m2/artifact_manifest.json"),
                "manifest_sha256": sha256(run / "m2/artifact_manifest.json"),
                "validation": validation["status"],
            },
            indent=2,
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--stage", choices=["fit", "finalize"], required=True)
    args = parser.parse_args()
    try:
        if args.stage == "fit":
            fit_stage(args.run, args.attempt)
        else:
            finalize_stage(args.run, args.attempt)
    except Exception as error:
        failure = args.run / "failures/T08" / args.attempt
        failure.mkdir(parents=True, exist_ok=True)
        write_json(
            failure / "failure.json",
            {
                "task_id": "T08",
                "attempt_id": args.attempt,
                "stage": args.stage,
                "status": "FAILED",
                "error_type": type(error).__name__,
                "error": str(error),
                "scientific_status": "REJECTED",
            },
        )
        raise


if __name__ == "__main__":
    main()
