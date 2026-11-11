from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import platform
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
ROW_KEYS = (
    "unit_id",
    "structure_id",
    "design_group_id",
    "scope",
    "fold",
    "role",
    "branch_id",
)
SCOPES = ("structure_holdout", "area_edges", "eg_upper", "fl_upper")
BOUNDARY_FAMILIES = ("eg_upper", "fl_lower", "joint_eg_upper_fl_lower")
FROZEN_HASHES = {
    "contracts/protocol.json": "bc0f16a4791d1968cac10afafd5fd14a32abfa7bd7d0329b2591ff825d88f05c",
    "contracts/schema.json": "767b853871cf29e528cacf414cfd9596ef89c283c5be50c7da1a30a860c257d1",
    "verification/contract_report.json": (
        "4e5b4bca62f34ddec1fc3942b0509a80e207b6cb85eda7a87d14f9a9fe541880"
    ),
    "verification/t06_artifact_manifest.json": (
        "65b7527da0b8ca44e264f333a49078ae55cbabb6200ee64f4b2f83bcbd9458c9"
    ),
    "evaluation/splits.csv": "fd000bd582f01286d6d862d1dddffa16199146e7acf07802ab2d8a27050a71a9",
    "evaluation/capacity.csv": "77cd9e9b664fa2e0e39a6ebd32c50579ee407adb13cd938d2450e6951896d60f",
    "evaluation/t04_artifact_manifest.json": (
        "b070da607d3bc79d0ffc9a7b4c6728ccebee55697661b304f4959cccf0872a3a"
    ),
    "m1/inner_predictions.csv": (
        "f6f95d592c68b31d00f3384ff308153eb7c819b70df70e8e313baaa85fab5273"
    ),
    "m1/control_predictions.csv": (
        "f6d98d69abc501fa9775ab770ef99e64af3d16ce746eb60920f64b26725b1028"
    ),
    "m1/model_config.json": "cbca4e01bcc122a1edca086be0a63f6ec5f81094dc6a8d36583c4e2b108cbcd1",
    "m1/artifact_manifest.json": "2dfe81db5902e38440b45b7babec748554755b2f8d25b27c13491aa7b9b27799",
    "m2/inner_predictions.csv": (
        "eb99d80071db5e85b900bb8a37910911e3d7857281b4212fcb29d84843d1c06a"
    ),
    "m2/control_predictions.csv": (
        "2128171868a01cbd3fb4a875de60aeeeba9d53178808cd593719131114c86f5a"
    ),
    "m2/model_config.json": "c50519d764566e47c9028a11f52f37257dbc4e21eaba534c5d282f5c92d0622b",
    "m2/aggregation_audit.csv": "845e4b63a25c3d0a9ed9494a1f76b74f8b52d344dc2714f1f20a5ac96f6603db",
    "m2/artifact_manifest.json": "f840ac4aad5321569d9db606a20510e7e9a1d4a2a0db49df0f4040f44c77722c",
}
SOURCE_HASHES = {
    "app/ml/optimization_p13/contracts.py": (
        "b472ee144b3e146e489afd0e522b42d21bd29a359bdcbe909eb1bf10d446c62e"
    ),
    "app/ml/optimization_p13/evaluation.py": (
        "a2ce8d81e23fd083b028a0fcc7873c429c56d99e757d0bf43ea8f4a0108a6fdf"
    ),
    "app/ml/optimization_p13/m1.py": (
        "15144ed4a51bb5557286d3ce592514fd5e63fee681c1ab4d90c73c9a4487701f"
    ),
    "app/ml/optimization_p13/m2.py": (
        "7f6bd0eb1db38d5058fc0ca5a5b78a93e70308866a4ff14b8d537768e9e56261"
    ),
    "scripts/run_optimization_p13_m1.py": (
        "7b20906dc771e9038b5ee9d48ad6a79e57234c2625ba54ead24893938dfe38e1"
    ),
    "scripts/run_optimization_p13_t08_m2.py": (
        "7432e8ab9ec73e4a6cff7f0d2f1f021749bb23dc38b12b0ad5e8fa764de5af84"
    ),
    "tests/ml/test_optimization_p13_m1.py": (
        "ffed338a6711f7c5182ae58c7684a3d87ed1d6376776ac94e41263ad30e1f1da"
    ),
    "tests/ml/test_optimization_p13_m2.py": (
        "bf2e4b454d84e855caea36562bb42beccda7167c5ce62794613ec130ed83c8c5"
    ),
}
ARTIFACT_NAMES = (
    "model_gate.json",
    "model_units.csv",
    "model_metric_differences.csv",
    "model_false_positive_events.csv",
    "t09_validation.json",
)
EVENT_COLUMNS = [
    "model_family",
    "config_id",
    *ROW_KEYS,
    "actual",
    "prediction",
    "baseline_k2eff_pct",
    "predicted_uplift_pp",
    "measured_uplift_pp",
    "target",
    "unit",
    "event_type",
]


class EvaluationError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def file_record(path: Path, file_id: str | None = None) -> dict:
    result = {
        "absolute_path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    if file_id is not None:
        result["file_id"] = file_id
    return result


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise EvaluationError(reason)


def true_mask(series: pd.Series) -> pd.Series:
    return series.map(lambda value: value is True or str(value) == "True")


def mean(values: list[float]) -> float:
    require(
        bool(values) and all(math.isfinite(value) for value in values), "EMPTY_OR_NONFINITE_MEAN"
    )
    return float(sum(values) / len(values))


def validate_frozen_inputs(run: Path) -> list[dict]:
    records = []
    for relative, expected in FROZEN_HASHES.items():
        actual = sha256(run / relative)
        require(actual == expected, f"FROZEN_INPUT_HASH_MISMATCH:{relative}")
        records.append(file_record(run / relative, relative))
    for relative, expected in SOURCE_HASHES.items():
        actual = sha256(BACKEND / relative)
        require(actual == expected, f"FROZEN_SOURCE_HASH_MISMATCH:{relative}")
    return records


def validate_author_manifests(run: Path) -> dict:
    reports = {}
    for family in ("m1", "m2"):
        manifest_path = run / family / "artifact_manifest.json"
        manifest = read_json(manifest_path)
        require(manifest["status"] == "PASS", f"AUTHOR_MANIFEST_NOT_PASS:{family}")
        checked = []
        for record in manifest["inputs"]:
            path = Path(record["absolute_path"])
            expected = (
                record.get("expected_sha256") or record.get("actual_sha256") or record["sha256"]
            )
            require(path.is_file(), f"MANIFEST_INPUT_MISSING:{path}")
            require(sha256(path) == expected, f"MANIFEST_INPUT_HASH_MISMATCH:{path}")
            require(path.stat().st_size == record["bytes"], f"MANIFEST_INPUT_SIZE_MISMATCH:{path}")
            checked.append(str(path))
        for record in manifest["outputs"]:
            path = Path(record.get("absolute_path", run / record.get("path", "")))
            require(path.is_file(), f"MANIFEST_OUTPUT_MISSING:{path}")
            require(sha256(path) == record["sha256"], f"MANIFEST_OUTPUT_HASH_MISMATCH:{path}")
            require(path.stat().st_size == record["bytes"], f"MANIFEST_OUTPUT_SIZE_MISMATCH:{path}")
        for source_name, expected in manifest["source_sha256"].items():
            path = Path(source_name)
            if not path.is_absolute():
                path = BACKEND / path
            require(sha256(path) == expected, f"MANIFEST_SOURCE_HASH_MISMATCH:{path}")
        reports[family] = {
            "status": "PASS",
            "attempt_id": manifest["attempt_id"],
            "input_count": len(manifest["inputs"]),
            "output_count": len(manifest["outputs"]),
            "source_count": len(manifest["source_sha256"]),
            "manifest_sha256": sha256(manifest_path),
        }
    return reports


def assert_complete_prediction_rows(
    rows: pd.DataFrame,
    evaluation: pd.DataFrame,
    control: pd.DataFrame,
    *,
    model_family: str,
) -> dict:
    required = {
        *ROW_KEYS,
        "actual",
        "prediction",
        "valid_prediction",
        "invalid_reason",
        "null_reason",
        "source_split_sha256",
        "source_structure_sha256",
        "protocol_sha256",
        "target",
        "unit",
        "family",
        "has_pf",
    }
    require(required <= set(rows.columns), "MISSING_PREDICTION_FIELDS")
    require(not rows.duplicated(list(ROW_KEYS)).any(), "DUPLICATE_EVALUATION_ROW")
    expected = evaluation[list(ROW_KEYS)]
    require(not expected.duplicated().any(), "DUPLICATE_O05_EVALUATION_ROW")
    row_keys = set(rows[list(ROW_KEYS)].itertuples(index=False, name=None))
    expected_keys = set(expected.itertuples(index=False, name=None))
    control_keys = set(control[list(ROW_KEYS)].itertuples(index=False, name=None))
    require(row_keys == expected_keys, "INCOMPLETE_OR_UNEXPECTED_O05_ROWS")
    require(control_keys == expected_keys, "INCOMPLETE_OR_UNEXPECTED_CONTROL_ROWS")
    require(len(rows) == len(evaluation) == len(control), "EVALUATION_ROW_COUNT_MISMATCH")
    joined = rows.merge(
        evaluation,
        on=list(ROW_KEYS),
        how="inner",
        suffixes=("", "_o05"),
        validate="one_to_one",
    )
    require(len(joined) == len(evaluation), "O05_ROW_IDENTITY_MISMATCH")
    for field in (
        "family",
        "source_split_sha256",
        "source_structure_sha256",
        "source_split_file_id",
        "source_structure_file_id",
    ):
        require(joined[field].equals(joined[f"{field}_o05"]), f"O05_METADATA_MISMATCH:{field}")
    if model_family == "M2":
        require(
            joined.source_file_id.eq(joined.source_structure_file_id).all(),
            "M2_NATIVE_LABEL_SOURCE_MISMATCH",
        )
    else:
        require(
            joined.source_file_id.equals(joined.source_file_id_o05),
            "O05_METADATA_MISMATCH:source_file_id",
        )
    controlled = rows.merge(
        control[list(ROW_KEYS) + ["actual", "target", "unit"]],
        on=list(ROW_KEYS),
        how="inner",
        suffixes=("", "_control"),
        validate="one_to_one",
    )
    require(controlled.actual.equals(controlled.actual_control), "ACTUAL_MISMATCH_WITH_P6C")
    require(controlled.unit.eq("percent_number").all(), "K2_UNIT_MUST_BE_PERCENT_NUMBER")
    expected_target = "k2eff_pct_native" if model_family == "M2" else "k2eff_pct"
    require(controlled.target.eq(expected_target).all(), "PREDICTION_TARGET_MISMATCH")
    require(controlled.target_control.eq("k2eff_pct").all(), "CONTROL_TARGET_MISMATCH")
    protocol_hashes = set(rows.protocol_sha256) | set(control.protocol_sha256)
    require(protocol_hashes == {FROZEN_HASHES["contracts/protocol.json"]}, "O02_ROW_HASH_MISMATCH")
    valid = true_mask(rows.valid_prediction)
    prediction = pd.to_numeric(rows.prediction, errors="coerce").to_numpy(dtype=float)
    actual = pd.to_numeric(rows.actual, errors="coerce").to_numpy(dtype=float)
    require(bool(valid.all()), "INVALID_PREDICTION_PRESENT")
    require(bool(np.isfinite(prediction).all()), "NULL_OR_NONFINITE_PREDICTION_PRESENT")
    require(bool(np.isfinite(actual).all()), "NULL_OR_NONFINITE_ACTUAL_PRESENT")
    require(bool(((prediction > 0) & (prediction < 100)).all()), "NONPHYSICAL_PREDICTION_PRESENT")
    require(bool(rows.invalid_reason.isna().all()), "VALID_ROWS_HAVE_INVALID_REASON")
    require(bool(rows.null_reason.isna().all()), "VALID_ROWS_HAVE_NULL_REASON")
    return {
        "status": "PASS",
        "row_count": len(rows),
        "expected_row_count": len(evaluation),
        "duplicate_row_count": 0,
        "missing_row_count": 0,
        "unexpected_row_count": 0,
        "valid_prediction_count": int(valid.sum()),
        "invalid_prediction_count": int((~valid).sum()),
        "null_prediction_count": int(rows.prediction.isna().sum()),
        "actual_mismatch_count": 0,
        "source_hash_mismatch_count": 0,
        "target": expected_target,
    }


def design_group_mae(rows: pd.DataFrame) -> float:
    require(not rows.empty, "EMPTY_METRIC_ROWS")
    error = pd.to_numeric(rows.prediction, errors="coerce") - pd.to_numeric(
        rows.actual, errors="coerce"
    )
    require(bool(np.isfinite(error.to_numpy(dtype=float)).all()), "NONFINITE_METRIC_ERROR")
    group_errors = error.abs().groupby(rows.design_group_id).mean()
    require(bool(group_errors.notna().all()), "NONFINITE_DESIGN_GROUP_ERROR")
    return float(group_errors.mean())


def ordinary_scope_mae(rows: pd.DataFrame) -> float:
    scope_values = []
    for scope in SCOPES:
        scope_rows = rows[rows.scope.eq(scope) & rows.family.eq("ordinary")]
        require(not scope_rows.empty, f"MISSING_ORDINARY_SCOPE:{scope}")
        absolute_error = (
            pd.to_numeric(scope_rows.prediction, errors="coerce")
            - pd.to_numeric(scope_rows.actual, errors="coerce")
        ).abs()
        group_errors = absolute_error.groupby(scope_rows.design_group_id).mean()
        scope_values.append(float(group_errors.mean()))
    return mean(scope_values)


def compute_metric_bundle(rows: pd.DataFrame, capacity: pd.DataFrame) -> dict:
    require(not capacity.unit_id.duplicated().any(), "DUPLICATE_CAPACITY_UNIT")
    valid_capacity_ids = set(capacity.loc[true_mask(capacity.valid), "unit_id"])
    require(valid_capacity_ids == set(rows.unit_id), "CAPACITY_PREDICTION_UNIT_MISMATCH")
    unit_metrics = []
    for unit in capacity.itertuples(index=False):
        unit_rows = rows[rows.unit_id.eq(unit.unit_id)]
        valid = unit.valid is True or str(unit.valid) == "True"
        record = {
            "unit_id": unit.unit_id,
            "scope": unit.scope,
            "family": unit.family,
            "branch_id": unit.branch_id,
            "valid_capacity_unit": valid,
            "counts_toward_hard_capacity": unit.counts_toward_hard_capacity is True
            or str(unit.counts_toward_hard_capacity) == "True",
            "capacity_invalid_reason": None
            if pd.isna(unit.invalid_reason)
            else unit.invalid_reason,
            "row_count": len(unit_rows),
            "design_group_count": int(unit_rows.design_group_id.nunique()),
            "is_ordinary": unit.family == "ordinary",
            "is_pf0_boundary": unit.family != "ordinary" and valid,
            "mae": None,
        }
        if valid:
            require(not unit_rows.empty, f"MISSING_VALID_UNIT:{unit.unit_id}")
            require(unit_rows.fold.eq(unit.unit_id).all(), "FOLD_UNIT_MISMATCH")
            require(unit_rows.scope.eq(unit.scope).all(), "UNIT_SCOPE_MISMATCH")
            require(unit_rows.family.eq(unit.family).all(), "UNIT_FAMILY_MISMATCH")
            if unit.family != "ordinary":
                require(unit_rows.branch_id.eq(unit.branch_id).all(), "BOUNDARY_BRANCH_MISMATCH")
                require(unit_rows.has_pf.eq(0).all(), "PF0_BOUNDARY_REQUIRED")
            record["mae"] = design_group_mae(unit_rows)
        else:
            require(unit_rows.empty, f"INVALID_CAPACITY_UNIT_HAS_PREDICTIONS:{unit.unit_id}")
        unit_metrics.append(record)
    valid_units = [row for row in unit_metrics if row["valid_capacity_unit"]]
    require(len(valid_units) == 53, "VALID_UNIT_COUNT_CHANGED")
    ordinary_scope_metrics = []
    for scope in SCOPES:
        scope_rows = rows[rows.scope.eq(scope) & rows.family.eq("ordinary")]
        require(not scope_rows.empty, f"MISSING_ORDINARY_SCOPE:{scope}")
        absolute_error = (
            pd.to_numeric(scope_rows.prediction, errors="coerce")
            - pd.to_numeric(scope_rows.actual, errors="coerce")
        ).abs()
        group_errors = absolute_error.groupby(scope_rows.design_group_id).mean()
        ordinary_scope_metrics.append(
            {
                "scope": scope,
                "mae": float(group_errors.mean()),
                "unit_count": int(scope_rows.unit_id.nunique()),
                "design_group_count": len(group_errors),
            }
        )
    boundary_branch_metrics = []
    for scope in SCOPES:
        for family in BOUNDARY_FAMILIES:
            branches = sorted(
                {
                    row["branch_id"]
                    for row in valid_units
                    if row["scope"] == scope and row["family"] == family
                }
            )
            for branch in branches:
                values = [
                    row["mae"]
                    for row in valid_units
                    if row["scope"] == scope
                    and row["family"] == family
                    and row["branch_id"] == branch
                ]
                boundary_branch_metrics.append(
                    {"scope": scope, "family": family, "branch_id": branch, "mae": mean(values)}
                )
    boundary_family_metrics = []
    for scope in SCOPES:
        for family in BOUNDARY_FAMILIES:
            values = [
                row["mae"]
                for row in boundary_branch_metrics
                if row["scope"] == scope and row["family"] == family
            ]
            if values:
                boundary_family_metrics.append(
                    {"scope": scope, "family": family, "mae": mean(values)}
                )
    boundary_scope_metrics = []
    for scope in SCOPES:
        values = [row["mae"] for row in boundary_family_metrics if row["scope"] == scope]
        require(bool(values), f"MISSING_BOUNDARY_SCOPE:{scope}")
        boundary_scope_metrics.append({"scope": scope, "mae": mean(values)})
    boundary_units = [row for row in valid_units if row["family"] != "ordinary"]
    require(bool(boundary_units), "NO_VALID_BOUNDARY_UNIT")
    worst = max(boundary_units, key=lambda row: (row["mae"], row["unit_id"]))
    for row in unit_metrics:
        row["is_worst_valid_boundary_unit"] = row["unit_id"] == worst["unit_id"]
    return {
        "status": "PASS",
        "ordinary_mae": mean([row["mae"] for row in ordinary_scope_metrics]),
        "boundary_mae": mean([row["mae"] for row in boundary_scope_metrics]),
        "worst_unit_mae": worst["mae"],
        "worst_unit_id": worst["unit_id"],
        "unit_metrics": unit_metrics,
        "ordinary_scope_metrics": ordinary_scope_metrics,
        "boundary_branch_metrics": boundary_branch_metrics,
        "boundary_family_metrics": boundary_family_metrics,
        "boundary_scope_metrics": boundary_scope_metrics,
        "valid_unit_count": len(valid_units),
        "valid_boundary_unit_count": len(boundary_units),
    }


def acceptance_condition(
    candidate: float,
    control: float,
    multiplier: float,
    *,
    boundary: bool,
    atol: float = 1e-12,
    rtol: float = 1e-12,
) -> dict:
    if not all(math.isfinite(value) for value in (candidate, control, multiplier)):
        return {"status": "FAILED", "reason": "NONFINITE_ACCEPTANCE_INPUT"}
    if candidate < 0 or control < 0 or multiplier < 0:
        return {"status": "FAILED", "reason": "NEGATIVE_ACCEPTANCE_INPUT"}
    if control == 0:
        if boundary:
            return {
                "status": "evidence_insufficient",
                "reason": "ZERO_CONTROL_RELATIVE_IMPROVEMENT_UNDEFINED",
                "limit": None,
            }
        return {
            "status": "PASS" if candidate == 0 else "REJECTED",
            "reason": "ZERO_CONTROL_REQUIRES_ZERO_CANDIDATE",
            "limit": 0.0,
        }
    limit = multiplier * control
    difference = abs(candidate - limit)
    tolerance = atol + rtol * abs(limit)
    if difference <= tolerance:
        status = "numerical_critical"
    else:
        status = "PASS" if candidate <= limit else "REJECTED"
    return {
        "status": status,
        "candidate": candidate,
        "control": control,
        "difference": candidate - control,
        "multiplier": multiplier,
        "limit": limit,
        "margin": limit - candidate,
        "threshold_distance": difference,
        "threshold_tolerance": tolerance,
        "ratio": candidate / control,
    }


def evaluate_acceptance(candidate: dict, control: dict, model_family: str, protocol: dict) -> dict:
    acceptance = protocol["evaluation"]["acceptance"]
    tolerances = protocol["numerical_reproduction"]
    specifications = (
        ("boundary_improvement", "boundary_mae", "boundary_improvement_fraction", True),
        ("ordinary_noninferiority", "ordinary_mae", "ordinary_noninferiority_fraction", False),
        (
            "worst_unit_noninferiority",
            "worst_unit_mae",
            "worst_unit_noninferiority_fraction",
            False,
        ),
    )
    conditions = {}
    for name, metric, rule, boundary in specifications:
        text = acceptance[rule]
        match = re.fullmatch(r"candidate\s*<=\s*([0-9.]+)\s*\*\s*P6C", text)
        require(match is not None, f"UNKNOWN_ACCEPTANCE_RULE:{text}")
        conditions[name] = acceptance_condition(
            candidate[metric],
            control[metric],
            float(match.group(1)),
            boundary=boundary,
            atol=tolerances["prediction_atol"],
            rtol=tolerances["prediction_rtol"],
        )
    three_condition_pass = all(value["status"] == "PASS" for value in conditions.values())
    if model_family == "M2":
        return {
            "three_condition_status": "PASS" if three_condition_pass else "REJECTED",
            "scientific_status": "REJECTED",
            "qualified": False,
            "scientific_rejection_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
            "conditions": conditions,
        }
    return {
        "three_condition_status": "PASS" if three_condition_pass else "REJECTED",
        "scientific_status": "PASS" if three_condition_pass else "REJECTED",
        "qualified": three_condition_pass,
        "scientific_rejection_reason": None
        if three_condition_pass
        else "ACCEPTANCE_CONDITION_NOT_MET",
        "conditions": conditions,
    }


def validate_isolation(splits: pd.DataFrame, protocol: dict) -> dict:
    fit = splits[splits.role.eq("fit")]
    evaluation = splits[splits.role.eq("evaluation")]
    overlaps = []
    for unit_id, fit_rows in fit.groupby("unit_id"):
        evaluation_groups = set(evaluation.loc[evaluation.unit_id.eq(unit_id), "design_group_id"])
        if set(fit_rows.design_group_id) & evaluation_groups:
            overlaps.append(unit_id)
    require(not overlaps, "FIT_EVALUATION_DESIGN_GROUP_LEAKAGE")
    baseline_id = protocol["baseline"]["design_group_id"]
    baseline_rows = splits[splits.design_group_id.eq(baseline_id)]
    require(not baseline_rows.empty, "E1_BASELINE_ABSENT")
    require(baseline_rows.role.eq("baseline_only").all(), "E1_BASELINE_NOT_BASELINE_ONLY")
    require(not baseline_rows.role.isin(["fit", "evaluation"]).any(), "E1_BASELINE_IN_INNER_LOOP")
    outer_roles = {"outer_calibration", "outer_test"}
    outer_absorbed = splits[
        splits.role.isin(["fit", "evaluation"]) & splits.original_role.isin(outer_roles)
    ]
    require(outer_absorbed.empty, "OUTER_ROWS_ABSORBED_IN_INNER_LOOP")
    return {
        "status": "PASS",
        "fit_row_count": len(fit),
        "evaluation_row_count": len(evaluation),
        "fit_unit_count": int(fit.unit_id.nunique()),
        "evaluation_unit_count": int(evaluation.unit_id.nunique()),
        "fit_evaluation_group_overlap_unit_count": len(overlaps),
        "baseline_design_group_id": baseline_id,
        "baseline_row_count": len(baseline_rows),
        "baseline_roles": sorted(baseline_rows.role.unique().tolist()),
        "outer_absorbed_row_count": len(outer_absorbed),
    }


def validate_m2_predictions(rows: pd.DataFrame, protocol: dict) -> dict:
    required = {
        "pred_a",
        "pred_z",
        "pred_fs_ghz",
        "pred_fp_ghz",
        "pred_frequency_ratio",
        "pred_k2eff_pct_native",
        "frequency_valid",
        "native_k2_valid",
        "aggregation_estimand_match",
        "qualified",
        "p3_comparable_prediction",
        "p3_comparable_null_reason",
    }
    require(required <= set(rows.columns), "M2_REQUIRED_FIELDS_MISSING")
    require(rows.target.eq("k2eff_pct_native").all(), "M2_TARGET_NOT_NATIVE")
    fs = pd.to_numeric(rows.pred_fs_ghz, errors="coerce").to_numpy(dtype=float)
    fp = pd.to_numeric(rows.pred_fp_ghz, errors="coerce").to_numpy(dtype=float)
    ratio = pd.to_numeric(rows.pred_frequency_ratio, errors="coerce").to_numpy(dtype=float)
    native = pd.to_numeric(rows.pred_k2eff_pct_native, errors="coerce").to_numpy(dtype=float)
    prediction = pd.to_numeric(rows.prediction, errors="coerce").to_numpy(dtype=float)
    require(
        bool(np.isfinite(np.column_stack((fs, fp, ratio, native, prediction))).all()),
        "M2_NONFINITE_OUTPUT",
    )
    require(bool(((fs > 0) & (fp > fs) & (ratio > 0) & (ratio < 1)).all()), "M2_INVALID_FREQUENCY")
    tolerance = protocol["numerical_reproduction"]["formula_consistency_atol"]
    require(
        bool(np.allclose(ratio, fs / fp, atol=tolerance, rtol=0)), "M2_FREQUENCY_RATIO_MISMATCH"
    )
    formula = 25 * np.pi**2 * ratio * (1 - ratio)
    max_formula_difference = float(np.max(np.abs(native - formula)))
    require(max_formula_difference <= tolerance, "M2_NATIVE_K2_FORMULA_MISMATCH")
    require(bool(np.allclose(native, prediction, atol=0, rtol=0)), "M2_PREDICTION_NOT_NATIVE_K2")
    require(bool((native > 0).all() and (native < 100).all()), "M2_INVALID_NATIVE_K2")
    require(bool(true_mask(rows.frequency_valid).all()), "M2_FREQUENCY_VALID_FLAG_FALSE")
    require(bool(true_mask(rows.native_k2_valid).all()), "M2_NATIVE_K2_VALID_FLAG_FALSE")
    require(bool((~true_mask(rows.aggregation_estimand_match)).all()), "M2_AGGREGATION_OVERRIDE")
    require(bool((~true_mask(rows.qualified)).all()), "M2_QUALIFIED_FLAG_TRUE")
    require(bool(rows.p3_comparable_prediction.isna().all()), "M2_P3_COMPARABLE_NOT_NULL")
    require(
        rows.p3_comparable_null_reason.eq("P3_AGGREGATION_ESTIMAND_MISMATCH").all(),
        "M2_P3_COMPARABLE_REASON_MISMATCH",
    )
    return {
        "status": "PASS",
        "row_count": len(rows),
        "frequency_valid_count": int(true_mask(rows.frequency_valid).sum()),
        "native_k2_valid_count": int(true_mask(rows.native_k2_valid).sum()),
        "max_native_formula_abs_difference_pp": max_formula_difference,
        "aggregation_estimand_match": False,
        "qualified_row_count": int(true_mask(rows.qualified).sum()),
        "p3_comparable_null_count": int(rows.p3_comparable_prediction.isna().sum()),
    }


def validate_aggregation_audit(
    audit: pd.DataFrame, m2: pd.DataFrame, control: pd.DataFrame, protocol: dict
) -> dict:
    inner = audit[audit.row_kind.eq("inner_prediction")].copy()
    baseline = audit[audit.row_kind.eq("baseline_observation")]
    require(len(audit) == len(m2) + 1 and len(inner) == len(m2), "M2_AUDIT_ROW_COUNT_MISMATCH")
    require(len(baseline) == 1, "M2_BASELINE_AUDIT_COUNT_MISMATCH")
    audit_row_keys = [field for field in ROW_KEYS if field != "role"]
    audit_keys = ["config_id", *audit_row_keys]
    require(not inner.duplicated(audit_keys).any(), "M2_AUDIT_DUPLICATE_ROW")
    joined = inner.merge(
        m2,
        on=audit_keys,
        how="inner",
        suffixes=("_audit", ""),
        validate="one_to_one",
    )
    require(len(joined) == len(m2), "M2_AUDIT_PREDICTION_ROW_MISMATCH")
    controlled = inner.merge(
        control[audit_row_keys + ["actual"]],
        on=audit_row_keys,
        how="inner",
        validate="many_to_one",
    )
    require(len(controlled) == len(inner), "M2_AUDIT_CONTROL_ROW_MISMATCH")
    require(
        np.allclose(
            controlled.observed_p3_k2eff_pct,
            controlled.actual,
            atol=0,
            rtol=0,
        ),
        "M2_AUDIT_ACTUAL_MISMATCH",
    )
    fs = joined.observed_fs_ghz.to_numpy(dtype=float)
    fp = joined.observed_fp_ghz.to_numpy(dtype=float)
    require(bool(((fs > 0) & (fp > fs)).all()), "M2_AUDIT_INVALID_OBSERVED_FREQUENCY")
    observed_native = 25 * np.pi**2 * (fs / fp) * (1 - fs / fp)
    tolerance = protocol["numerical_reproduction"]["formula_consistency_atol"]
    require(
        bool(
            np.allclose(
                observed_native,
                joined.observed_k2_from_aggregated_frequency_pct,
                atol=tolerance,
                rtol=0,
            )
        ),
        "M2_AUDIT_OBSERVED_NATIVE_FORMULA_MISMATCH",
    )
    require(
        bool(
            np.allclose(
                joined.observed_aggregation_delta_pp,
                joined.observed_k2_from_aggregated_frequency_pct - joined.observed_p3_k2eff_pct,
                atol=tolerance,
                rtol=0,
            )
        ),
        "M2_AUDIT_DELTA_MISMATCH",
    )
    require(
        bool(
            np.allclose(
                joined.pred_k2eff_pct_native,
                joined.prediction,
                atol=0,
                rtol=0,
            )
        ),
        "M2_AUDIT_PREDICTION_MISMATCH",
    )
    require(
        bool((~true_mask(joined.aggregation_estimand_match)).all()), "M2_AUDIT_AGGREGATION_OVERRIDE"
    )
    require(
        joined.p3_k2_statistic.eq("median_of_coordinate_block_medians_of_per_record_k2").all(),
        "M2_AUDIT_P3_STATISTIC_CHANGED",
    )
    require(
        joined.frequency_statistic.eq(
            "separate_median_of_coordinate_block_medians_for_fs_and_fp"
        ).all(),
        "M2_AUDIT_FREQUENCY_STATISTIC_CHANGED",
    )
    require(
        bool(joined.pred_k2eff_pct_p3_comparable.isna().all()), "M2_AUDIT_P3_COMPARABLE_NOT_NULL"
    )
    baseline_row = baseline.iloc[0]
    baseline_p3 = float(baseline_row.observed_p3_k2eff_pct)
    baseline_native = float(baseline_row.observed_k2_from_aggregated_frequency_pct)
    require(baseline_p3 == float(protocol["baseline"]["k2eff_pct"]), "M2_BASELINE_P3_MISMATCH")
    require(
        math.isclose(
            baseline_native,
            protocol["models"]["M2"]["aggregation_evidence"]["e1_baseline_audit"][
                "k2_from_aggregated_frequency_pct"
            ],
            abs_tol=tolerance,
            rel_tol=0,
        ),
        "M2_BASELINE_NATIVE_MISMATCH",
    )
    return {
        "status": "PASS",
        "inner_row_count": len(inner),
        "baseline_observation_count": len(baseline),
        "p3_estimand": "median_of_coordinate_block_medians_of_per_record_k2",
        "native_estimand": "k2_from_separately_aggregated_fs_and_fp",
        "aggregation_estimand_match": False,
        "qualified_model_set_eligible": False,
        "baseline_p3_k2eff_pct": baseline_p3,
        "baseline_native_k2eff_pct": baseline_native,
        "baseline_aggregation_delta_pp": baseline_native - baseline_p3,
        "inner_abs_aggregation_delta_pp_median": float(
            inner.observed_abs_aggregation_delta_pp.median()
        ),
        "inner_abs_aggregation_delta_pp_max": float(inner.observed_abs_aggregation_delta_pp.max()),
        "scientific_rejection_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
    }


def audit_m2_inference_source(run: Path) -> dict:
    m2_path = BACKEND / "app/ml/optimization_p13/m2.py"
    runner_path = BACKEND / "scripts/run_optimization_p13_t08_m2.py"
    m2_tree = ast.parse(m2_path.read_text())
    model = next(
        node
        for node in m2_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "M2FrequencyModel"
    )
    methods = {node.name: node for node in model.body if isinstance(node, ast.FunctionDef)}
    inference_names = ("_raw_features", "predict", "predict_frame")
    inference_text = "\n".join(
        ast.get_source_segment(m2_path.read_text(), methods[name]) or "" for name in inference_names
    )
    require(
        "fs_ghz" not in inference_text and "fp_ghz" not in inference_text,
        "M2_INFERENCE_FREQUENCY_REFERENCE",
    )
    require("GEOMETRY_FIELDS" in inference_text, "M2_GEOMETRY_INTERFACE_NOT_FOUND")
    runner_text = runner_path.read_text()
    require(
        "geometry = branch_evaluation[list(GEOMETRY_FIELDS)]" in runner_text
        and "model.predict_frame(geometry)" in runner_text,
        "M2_RUNNER_GEOMETRY_PROJECTION_NOT_VERIFIED",
    )
    return {
        "status": "PASS",
        "m2_sha256": sha256(m2_path),
        "runner_sha256": sha256(runner_path),
        "inference_methods_checked": list(inference_names),
        "inference_observed_frequency_references": False,
        "inference_geometry_fields": ["eg_um", "fl_um", "area_um2", "has_pf"],
        "runner_geometry_projection_verified": True,
        "fit_frequency_supervision_not_used_as_inference_input": True,
        "run_manifest_sha256": sha256(run / "m2/artifact_manifest.json"),
    }


def false_positive_events(
    rows: pd.DataFrame, *, model_family: str, config_id: str, baseline: float
) -> pd.DataFrame:
    result = rows.loc[
        (rows.prediction - baseline >= 1) & (rows.actual - baseline < 1),
        list(ROW_KEYS) + ["actual", "prediction", "target", "unit"],
    ].copy()
    result.insert(0, "config_id", config_id)
    result.insert(0, "model_family", model_family)
    result["baseline_k2eff_pct"] = baseline
    result["predicted_uplift_pp"] = result.prediction - baseline
    result["measured_uplift_pp"] = result.actual - baseline
    result["event_type"] = "PREDICTED_UPLIFT_GE1_MEASURED_UPLIFT_LT1"
    return result[EVENT_COLUMNS]


def compare_controls(m1_control: pd.DataFrame, m2_control: pd.DataFrame, protocol: dict) -> dict:
    joined = m1_control.merge(
        m2_control,
        on=list(ROW_KEYS),
        how="outer",
        suffixes=("_m1", "_m2"),
        indicator=True,
        validate="one_to_one",
    )
    require(joined._merge.eq("both").all(), "P6C_CONTROL_ROW_KEY_MISMATCH")
    actual_difference = float((joined.actual_m1 - joined.actual_m2).abs().max())
    prediction_difference = float((joined.prediction_m1 - joined.prediction_m2).abs().max())
    tolerance = protocol["numerical_reproduction"]["prediction_atol"] + protocol[
        "numerical_reproduction"
    ]["prediction_rtol"] * float(joined.prediction_m1.abs().max())
    require(actual_difference == 0, "P6C_CONTROL_ACTUAL_MISMATCH")
    require(prediction_difference <= tolerance, "P6C_CONTROL_PREDICTION_MISMATCH")
    return {
        "status": "PASS",
        "row_count": len(joined),
        "actual_max_abs_difference_pp": actual_difference,
        "prediction_max_abs_difference_pp": prediction_difference,
        "comparison_tolerance_pp": tolerance,
    }


def unit_table(
    metric_bundles: dict[str, dict], model_lookup: dict[str, tuple[str, str]]
) -> pd.DataFrame:
    records = []
    for config_id, bundle in metric_bundles.items():
        family, target = model_lookup[config_id]
        for unit in bundle["unit_metrics"]:
            records.append(
                {
                    "model_family": family,
                    "config_id": config_id,
                    **unit,
                    "target": target,
                    "unit": "percent_number",
                    "metric_role": "descriptive_only" if family == "M2" else "scientific_metric",
                }
            )
    return pd.DataFrame(records)


def difference_table(configurations: list[dict]) -> pd.DataFrame:
    metric_names = {
        "boundary_improvement": "boundary_mae",
        "ordinary_noninferiority": "ordinary_mae",
        "worst_unit_noninferiority": "worst_unit_mae",
    }
    records = []
    for config in configurations:
        for condition, metric in metric_names.items():
            value = config["acceptance"]["conditions"][condition]
            records.append(
                {
                    "model_family": config["model_family"],
                    "config_id": config["config_id"],
                    "metric": metric,
                    "condition": condition,
                    "candidate_mae_pp": value["candidate"],
                    "p6c_mae_pp": value["control"],
                    "candidate_minus_p6c_pp": value["difference"],
                    "candidate_over_p6c": value["ratio"],
                    "allowed_p6c_multiplier": value["multiplier"],
                    "acceptance_limit_pp": value["limit"],
                    "acceptance_margin_pp": value["margin"],
                    "threshold_distance_pp": value["threshold_distance"],
                    "threshold_tolerance_pp": value["threshold_tolerance"],
                    "condition_status": value["status"],
                    "metric_role": "descriptive_only"
                    if config["model_family"] == "M2"
                    else "scientific_metric",
                }
            )
    return pd.DataFrame(records)


def evaluate(run: Path, work: Path) -> dict:
    input_records = validate_frozen_inputs(run)
    manifest_audit = validate_author_manifests(run)
    protocol = read_json(run / "contracts/protocol.json")
    splits = pd.read_csv(run / "evaluation/splits.csv")
    evaluation = splits[splits.role.eq("evaluation")].copy()
    capacity = pd.read_csv(run / "evaluation/capacity.csv")
    require(len(evaluation) == 2882, "O05_EVALUATION_ROW_COUNT_CHANGED")
    require(len(capacity) == 112 and true_mask(capacity.valid).sum() == 53, "O05_CAPACITY_CHANGED")
    isolation = validate_isolation(splits, protocol)
    m1 = pd.read_csv(run / "m1/inner_predictions.csv")
    m2 = pd.read_csv(run / "m2/inner_predictions.csv")
    m1_control = pd.read_csv(run / "m1/control_predictions.csv")
    m2_control = pd.read_csv(run / "m2/control_predictions.csv")
    aggregation_audit = pd.read_csv(run / "m2/aggregation_audit.csv")
    control_comparison = compare_controls(m1_control, m2_control, protocol)
    control_row_audit = assert_complete_prediction_rows(
        m1_control, evaluation, m2_control, model_family="P6C"
    )
    control_bundle = compute_metric_bundle(m1_control, capacity)
    control_config = read_json(run / "m1/model_config.json")["control"]
    require(control_config["model_family"] == "P6C", "P6C_CONTROL_CONFIG_CHANGED")
    require(control_config["recipe"] == m1_control.config_id.iloc[0], "P6C_RECIPE_CHANGED")
    configurations = []
    row_audits = {}
    metric_bundles = {"trend_gp_log_area": control_bundle}
    model_lookup = {"trend_gp_log_area": ("P6C", "k2eff_pct")}
    m2_physical_audit = None
    aggregation_report = None
    for family, predictions, config_name in (
        ("M1", m1, "m1/model_config.json"),
        ("M2", m2, "m2/model_config.json"),
    ):
        model_config = read_json(run / config_name)
        config_order = [value["config_id"] for value in model_config["configurations"]]
        require(
            config_order == predictions.config_id.drop_duplicates().tolist(),
            "CONFIG_ORDER_MISMATCH",
        )
        require(len(config_order) == (12 if family == "M1" else 10), "CONFIG_COUNT_CHANGED")
        for config_id in config_order:
            rows = predictions[predictions.config_id.eq(config_id)].copy()
            row_audit = assert_complete_prediction_rows(
                rows, evaluation, m1_control, model_family=family
            )
            row_audits[config_id] = row_audit
            if family == "M2":
                physical = validate_m2_predictions(rows, protocol)
                if m2_physical_audit is None:
                    m2_physical_audit = physical
                else:
                    for field in (
                        "row_count",
                        "frequency_valid_count",
                        "native_k2_valid_count",
                        "qualified_row_count",
                        "p3_comparable_null_count",
                    ):
                        m2_physical_audit[field] += physical[field]
                    m2_physical_audit["max_native_formula_abs_difference_pp"] = max(
                        m2_physical_audit["max_native_formula_abs_difference_pp"],
                        physical["max_native_formula_abs_difference_pp"],
                    )
            bundle = compute_metric_bundle(rows, capacity)
            acceptance = evaluate_acceptance(bundle, control_bundle, family, protocol)
            configurations.append(
                {
                    "model_family": family,
                    "config_id": config_id,
                    "target": rows.target.iloc[0],
                    "unit": "percentage_points_for_errors",
                    "metric_role": "descriptive_only" if family == "M2" else "scientific_metric",
                    "ordinary_mae": bundle["ordinary_mae"],
                    "boundary_mae": bundle["boundary_mae"],
                    "worst_unit_mae": bundle["worst_unit_mae"],
                    "worst_unit_id": bundle["worst_unit_id"],
                    "valid_unit_count": bundle["valid_unit_count"],
                    "valid_boundary_unit_count": bundle["valid_boundary_unit_count"],
                    "acceptance": acceptance,
                    "scientific_status": acceptance["scientific_status"],
                    "qualified": acceptance["qualified"],
                    "scientific_rejection_reason": acceptance["scientific_rejection_reason"],
                }
            )
            metric_bundles[config_id] = bundle
            model_lookup[config_id] = (family, rows.target.iloc[0])
    require(m2_physical_audit is not None, "M2_PHYSICAL_AUDIT_MISSING")
    aggregation_report = validate_aggregation_audit(aggregation_audit, m2, m1_control, protocol)
    inference_audit = audit_m2_inference_source(run)
    baseline = float(protocol["baseline"]["k2eff_pct"])
    event_frames = []
    event_summary = []
    for config in configurations:
        source = m1 if config["model_family"] == "M1" else m2
        rows = source[source.config_id.eq(config["config_id"])]
        events = false_positive_events(
            rows,
            model_family=config["model_family"],
            config_id=config["config_id"],
            baseline=baseline,
        )
        event_frames.append(events)
        predicted_uplift_count = int((rows.prediction - baseline >= 1).sum())
        event_summary.append(
            {
                "model_family": config["model_family"],
                "config_id": config["config_id"],
                "evaluable_row_count": len(rows),
                "predicted_uplift_ge1_event_count": predicted_uplift_count,
                "false_positive_event_count": len(events),
                "status": "EVENTS_FOUND" if len(events) else "evidence_insufficient",
                "zero_risk_claimed": False,
                "reason": None
                if len(events)
                else "NO_FALSE_POSITIVE_EVENTS_DO_NOT_IMPLY_ZERO_RISK"
                if predicted_uplift_count
                else "NO_EVALUABLE_PREDICTED_UPLIFT_EVENTS",
            }
        )
    all_events = pd.concat(event_frames, ignore_index=True)
    units = unit_table(metric_bundles, model_lookup)
    differences = difference_table(configurations)
    require(len(units) == 23 * 112, "UNIT_OUTPUT_ROW_COUNT_MISMATCH")
    require(len(differences) == 22 * 3, "DIFFERENCE_OUTPUT_ROW_COUNT_MISMATCH")
    m1_pass = [
        config["config_id"]
        for config in configurations
        if config["model_family"] == "M1" and config["scientific_status"] == "PASS"
    ]
    m2_rejected = all(
        config["scientific_status"] == "REJECTED" and not config["qualified"]
        for config in configurations
        if config["model_family"] == "M2"
    )
    require(m2_rejected, "M2_SCIENTIFIC_REJECTION_NOT_ENFORCED")
    gate = {
        "schema": "aln_optimization_p13_t09_model_gate_v1",
        "task_id": "T09",
        "gate_id": "G3",
        "attempt_id": "attempt_001",
        "run_id": run.name.rsplit("_", 1)[-1],
        "status": "PASS",
        "status_meaning": "Independent recomputation complete; not a final model selection",
        "model_selection_performed": False,
        "o09_performed": False,
        "tasks_started": ["T09"],
        "tasks_not_started": ["T10", "T11", "T12"],
        "row_count_per_configuration": len(evaluation),
        "configuration_count": len(configurations),
        "control": {
            "model_family": "P6C",
            "config_id": "trend_gp_log_area",
            "ordinary_mae": control_bundle["ordinary_mae"],
            "boundary_mae": control_bundle["boundary_mae"],
            "worst_unit_mae": control_bundle["worst_unit_mae"],
            "worst_unit_id": control_bundle["worst_unit_id"],
            "row_audit": control_row_audit,
            "cross_control_comparison": control_comparison,
        },
        "configurations": configurations,
        "m1_all_three_conditions_pass_config_ids": m1_pass,
        "m1_any_all_three_conditions_pass": bool(m1_pass),
        "m2_all_configurations_rejected": m2_rejected,
        "m2_aggregation_evidence": aggregation_report,
        "false_positive_audit": {
            "baseline_k2eff_pct": baseline,
            "target_k2eff_pct": float(protocol["baseline"]["target_k2eff_pct"]),
            "definition": "predicted_uplift_pp>=1 and measured_uplift_pp<1",
            "event_row_count": len(all_events),
            "per_configuration": event_summary,
            "zero_risk_claimed": False,
        },
        "qualified_model_set": m1_pass,
        "qualified_model_set_is_not_final_selection": True,
    }
    validation = {
        "schema": "aln_optimization_p13_t09_validation_v1",
        "task_id": "T09",
        "attempt_id": "attempt_001",
        "status": "PASS",
        "input_records": input_records,
        "author_manifest_audit": manifest_audit,
        "row_audits": row_audits,
        "isolation_audit": isolation,
        "m2_physical_audit": m2_physical_audit,
        "m2_aggregation_audit": aggregation_report,
        "m2_inference_source_audit": inference_audit,
        "metric_definitions": {
            "ordinary": (
                "design-group MAE over four ordinary folds within scope, "
                "then four scopes equally"
            ),
            "boundary": (
                "design groups -> valid PF0 units -> branches -> families -> scopes, "
                "all equally"
            ),
            "worst_unit": "maximum design-group MAE over all valid PF0 boundary units",
            "missing_prediction_rule": (
                "no row deletion; null, invalid, or nonfinite prediction fails evaluation"
            ),
        },
        "output_row_counts": {
            "model_units.csv": len(units),
            "model_metric_differences.csv": len(differences),
            "model_false_positive_events.csv": len(all_events),
        },
    }
    output = work / "outputs"
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "model_gate.json", gate)
    units.to_csv(output / "model_units.csv", index=False)
    differences.to_csv(output / "model_metric_differences.csv", index=False)
    all_events.to_csv(output / "model_false_positive_events.csv", index=False)
    write_json(output / "t09_validation.json", validation)
    return validate_temporary_outputs(output)


def validate_temporary_outputs(output: Path) -> dict:
    require(all((output / name).is_file() for name in ARTIFACT_NAMES), "TEMPORARY_OUTPUT_MISSING")
    gate = read_json(output / "model_gate.json")
    validation = read_json(output / "t09_validation.json")
    units = pd.read_csv(output / "model_units.csv")
    differences = pd.read_csv(output / "model_metric_differences.csv")
    events = pd.read_csv(output / "model_false_positive_events.csv")
    require(gate["status"] == "PASS" and gate["task_id"] == "T09", "MODEL_GATE_EXECUTION_FAILED")
    require(
        not gate["model_selection_performed"] and not gate["o09_performed"],
        "MODEL_SELECTION_DETECTED",
    )
    require(len(gate["configurations"]) == 22, "MODEL_GATE_CONFIG_COUNT_MISMATCH")
    require(len(units) == 23 * 112, "MODEL_UNITS_ROW_COUNT_MISMATCH")
    require(not units.duplicated(["config_id", "unit_id"]).any(), "MODEL_UNITS_DUPLICATE")
    require(len(differences) == 66, "MODEL_DIFFERENCE_ROW_COUNT_MISMATCH")
    require(not differences.duplicated(["config_id", "metric"]).any(), "MODEL_DIFFERENCE_DUPLICATE")
    require(list(events.columns) == EVENT_COLUMNS, "FALSE_POSITIVE_SCHEMA_MISMATCH")
    require(
        bool(((events.predicted_uplift_pp >= 1) & (events.measured_uplift_pp < 1)).all()),
        "FALSE_POSITIVE_DEFINITION_MISMATCH",
    )
    require(validation["status"] == "PASS", "VALIDATION_STATUS_NOT_PASS")
    for config in gate["configurations"]:
        require(
            config["scientific_status"] in {"PASS", "REJECTED", "FAILED"},
            "INVALID_SCIENTIFIC_STATUS",
        )
        selected = units[units.config_id.eq(config["config_id"])]
        worst = selected[selected.is_worst_valid_boundary_unit]
        require(len(worst) == 1, "WORST_UNIT_MARKER_MISMATCH")
        require(
            math.isclose(worst.mae.iloc[0], config["worst_unit_mae"], abs_tol=1e-12, rel_tol=1e-12),
            "WORST_UNIT_VALUE_MISMATCH",
        )
        for condition, value in config["acceptance"]["conditions"].items():
            row = differences[
                differences.config_id.eq(config["config_id"]) & differences.condition.eq(condition)
            ]
            require(
                len(row) == 1 and row.condition_status.iloc[0] == value["status"],
                "DIFFERENCE_STATUS_MISMATCH",
            )
            require(
                math.isclose(
                    row.candidate_mae_pp.iloc[0], value["candidate"], abs_tol=1e-12, rel_tol=1e-12
                ),
                "DIFFERENCE_VALUE_MISMATCH",
            )
        if config["model_family"] == "M2":
            require(
                config["scientific_status"] == "REJECTED" and not config["qualified"],
                "M2_NOT_REJECTED",
            )
    require(
        set(gate["qualified_model_set"]) == set(gate["m1_all_three_conditions_pass_config_ids"]),
        "QUALIFIED_SET_MISMATCH",
    )
    return {
        "status": "PASS",
        "files": [file_record(output / name, name) for name in ARTIFACT_NAMES],
        "configuration_count": 22,
        "unit_row_count": len(units),
        "difference_row_count": len(differences),
        "false_positive_event_row_count": len(events),
        "m1_all_three_conditions_pass_config_ids": gate["m1_all_three_conditions_pass_config_ids"],
    }


def command_result(command: str, log: Path, exit_code: int) -> dict:
    require(log.is_file(), f"COMMAND_LOG_MISSING:{log}")
    return {
        "command": command,
        "cwd": str(BACKEND),
        "exit_code": exit_code,
        "log": str(log),
        "log_sha256": sha256(log),
    }


def environment() -> dict:
    from importlib.metadata import version

    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "pytest": version("pytest"),
            "ruff": version("ruff"),
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
        "worker_threads_per_process": 1,
        "dependency_install_or_update": False,
        "random_processes": [],
    }


def finalize(run: Path, work: Path, ruff_exit: int, pytest_exit: int, evaluate_exit: int) -> dict:
    require(
        ruff_exit == 0 and pytest_exit == 0 and evaluate_exit == 0,
        "T09_PREREQUISITE_COMMAND_FAILED",
    )
    temporary = validate_temporary_outputs(work / "outputs")
    verification = run / "verification"
    protected_before = {relative: sha256(run / relative) for relative in FROZEN_HASHES}
    source_before = {relative: sha256(BACKEND / relative) for relative in SOURCE_HASHES}
    for name in ARTIFACT_NAMES:
        formal = verification / name
        require(not formal.exists(), f"FORMAL_OUTPUT_ALREADY_EXISTS:{formal}")
        shutil.copyfile(work / "outputs" / name, formal)
        require(
            sha256(formal) == sha256(work / "outputs" / name), f"PROMOTION_HASH_MISMATCH:{name}"
        )
    require(
        validate_temporary_outputs(verification)["status"] == "PASS",
        "FORMAL_OUTPUT_VALIDATION_FAILED",
    )
    protected_after = {relative: sha256(run / relative) for relative in FROZEN_HASHES}
    source_after = {relative: sha256(BACKEND / relative) for relative in SOURCE_HASHES}
    require(protected_after == protected_before, "FROZEN_INPUT_CHANGED_DURING_T09")
    require(source_after == source_before, "FROZEN_SOURCE_CHANGED_DURING_T09")
    log_dir = work / "logs"
    run_relative = str(run)
    work_relative = str(work)
    commands = [
        command_result(
            "uv run --no-sync ruff check --no-cache "
            "scripts/run_optimization_p13_t09.py tests/ml/test_optimization_p13_t09.py",
            log_dir / "t09_ruff.log",
            ruff_exit,
        ),
        command_result(
            "uv run --no-sync pytest -q -p no:cacheprovider "
            f"--basetemp {work}/pytest_tmp tests/ml/test_optimization_p13_t09.py",
            log_dir / "t09_pytest.log",
            pytest_exit,
        ),
        command_result(
            "uv run --no-sync python scripts/run_optimization_p13_t09.py "
            f"--run {run_relative} --work {work_relative} --stage evaluate",
            log_dir / "t09_evaluate.log",
            evaluate_exit,
        ),
    ]
    finalize_log = log_dir / "t09_finalize.log"
    finalize_log.write_text(
        "T09 temporary outputs validated and promoted; formal outputs re-read: PASS\n"
    )
    finalize_command = (
        f"uv run --no-sync python scripts/run_optimization_p13_t09.py --run {run_relative} "
        f"--work {work_relative} --stage finalize --ruff-exit {ruff_exit} "
        f"--pytest-exit {pytest_exit} --evaluate-exit {evaluate_exit}"
    )
    commands.append(
        {
            "command": finalize_command,
            "cwd": str(BACKEND),
            "exit_code": 0,
            "log": str(finalize_log),
            "log_sha256": sha256(finalize_log),
        }
    )
    manifest = {
        "schema": "aln_optimization_p13_t09_artifact_manifest_v1",
        "task_id": "T09",
        "gate_id": "G3",
        "attempt_id": "attempt_001",
        "run_id": run.name.rsplit("_", 1)[-1],
        "status": "PASS",
        "status_meaning": "Independent recomputation complete; not a final model selection",
        "created_at": datetime.now(UTC).isoformat(),
        "command": finalize_command,
        "commands": commands,
        "environment": environment(),
        "inputs": [file_record(run / relative, relative) for relative in FROZEN_HASHES],
        "outputs": [file_record(verification / name, name) for name in ARTIFACT_NAMES],
        "source_sha256": {
            **{relative: sha256(BACKEND / relative) for relative in SOURCE_HASHES},
            "scripts/run_optimization_p13_t09.py": sha256(
                BACKEND / "scripts/run_optimization_p13_t09.py"
            ),
            "tests/ml/test_optimization_p13_t09.py": sha256(
                BACKEND / "tests/ml/test_optimization_p13_t09.py"
            ),
        },
        "temporary_output_validation": temporary,
        "formal_outputs_match_temporary": True,
        "protected_before": protected_before,
        "protected_after": protected_after,
        "source_before": source_before,
        "source_after": source_after,
        "model_selection_performed": False,
        "o09_performed": False,
        "tasks_not_started": ["T10", "T11", "T12"],
        "random_processes": [],
        "exit_code": 0,
    }
    manifest_path = verification / "t09_artifact_manifest.json"
    require(not manifest_path.exists(), "T09_MANIFEST_ALREADY_EXISTS")
    write_json(manifest_path, manifest)
    return {
        "status": "PASS",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "m1_all_three_conditions_pass_config_ids": temporary[
            "m1_all_three_conditions_pass_config_ids"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--stage", choices=("evaluate", "finalize"), required=True)
    parser.add_argument("--ruff-exit", type=int)
    parser.add_argument("--pytest-exit", type=int)
    parser.add_argument("--evaluate-exit", type=int)
    args = parser.parse_args()
    if args.stage == "evaluate":
        result = evaluate(args.run.resolve(), args.work.resolve())
    else:
        require(
            args.ruff_exit is not None
            and args.pytest_exit is not None
            and args.evaluate_exit is not None,
            "FINALIZE_EXIT_CODES_REQUIRED",
        )
        result = finalize(
            args.run.resolve(),
            args.work.resolve(),
            args.ruff_exit,
            args.pytest_exit,
            args.evaluate_exit,
        )
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
