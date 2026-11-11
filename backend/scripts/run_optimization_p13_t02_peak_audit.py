# ruff: noqa: E402, E501
"""Audit P1203 with frozen P3/P6C inference only; never fit or select a model."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import os
import platform
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE))

from app.ml.optimization_baselines import predict_bundle
from app.ml.optimization_p12 import fine_grid
from app.ml.optimization_p13.audit import (
    AuditError,
    HashMismatchError,
    InferenceMismatchError,
    decimal_delta,
    decompose_trend_gp,
    make_geometry_frame,
    nine_point_neighborhood,
    read_json,
    sha256,
    verify_expected_hash,
    verify_numeric_match,
)

P1203_ID = "P1203_peak_k2eff_pct_p6c_EG3.0_FL0.025_A5500_PF0"
MODEL_KEY = ("structure_holdout", "trend_gp_log_area", "k2eff_pct")
GEOMETRY = ["eg_um", "fl_um", "area_um2", "has_pf"]
SOURCE_FILES = (
    BE / "app/ml/optimization_p13/audit.py",
    BE / "scripts/run_optimization_p13_t02_peak_audit.py",
    BE / "tests/ml/test_optimization_p13_audit.py",
    BE / "tests/ml/test_optimization_p13_t02_peak_audit.py",
)
USED_IDS = {
    "DOC_2026_10_27_SURROGATE_P13_AGENT_EXECUTION_ORDER",
    "DOC_2026_10_27_SURROGATE_NO_FAB_K2_PLUS_ONE_PLAN",
    "P3_STRUCTURES",
    "P3_SPLIT_STRUCTURE_HOLDOUT",
    "P3_MODEL_BUNDLE",
    "P3_MODEL_METADATA",
    "P6C_SELECTION",
    "P6C_FIT_RECEIPTS",
    "P6C_MODELS",
    "P12_OUTPUTS_MANIFEST",
    "P12_CANDIDATES",
    "P12_EG_EXTENSION_BEHAVIOR",
    "P12_GRID_PREDICTIONS",
    "P12_PEAK_SEARCHES",
    "P12_PROTOCOL",
    "P12_VERIFICATION",
    "SOURCE_OPTIMIZATION_DATA",
    "SOURCE_OPTIMIZATION_BASELINES",
    "SOURCE_OPTIMIZATION_BOUNDARY_PILOT",
    "SOURCE_OPTIMIZATION_ABLATION",
    "SOURCE_OPTIMIZATION_P12",
    "SOURCE_RUN_OPTIMIZATION_P12",
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def geometry_key(row) -> tuple[float, float, float, int]:
    return (
        round(float(row["eg_um"]), 12),
        round(float(row["fl_um"]), 12),
        round(float(row["area_um2"]), 12),
        int(row["has_pf"]),
    )


def geometry_from_saved(saved: pd.DataFrame) -> pd.DataFrame:
    points = list(saved[GEOMETRY].itertuples(index=False, name=None))
    frame = make_geometry_frame(points)
    if "branch_id" in saved:
        expected = saved.branch_id.reset_index(drop=True)
        if not frame.branch_id.equals(expected):
            raise InferenceMismatchError("inference/code mismatch: saved branch identity")
    return frame


def common_reference(row, kind: str, file_id: str, row_number: int, sha256_value: str) -> dict:
    if kind == "extension":
        p3_prediction = row.k2eff_pct_p3
        gp_prediction = row.k2eff_pct_p6c
        lower = upper = log_scale = None
        support = nearest_id = None
        distance = None
    else:
        prefix = "k2eff_pct_p3" if kind == "grid" else "k2eff_pct_prediction"
        lower_name = "k2eff_pct_p3_lower" if kind == "grid" else "k2eff_pct_lower"
        upper_name = "k2eff_pct_p3_upper" if kind == "grid" else "k2eff_pct_upper"
        p3_prediction = getattr(row, prefix)
        gp_prediction = row.k2eff_pct_p6c
        lower = getattr(row, lower_name)
        upper = getattr(row, upper_name)
        log_scale = row.k2eff_pct_p6c_log_scale
        support = getattr(row, "p3_support", None)
        nearest_id = getattr(row, "nearest_structure_id", None)
        distance = getattr(row, "nearest_scaled_distance", None)
    return {
        "kind": kind,
        "file_id": file_id,
        "sha256": sha256_value,
        "row_number": row_number,
        "p3_prediction": p3_prediction,
        "p3_lower": lower,
        "p3_upper": upper,
        "gp_prediction": gp_prediction,
        "gp_log_scale": log_scale,
        "p3_support": support,
        "nearest_structure_id": nearest_id,
        "nearest_scaled_distance": distance,
    }


def selected_values(row: pd.Series) -> dict:
    keys = (
        "eg_um",
        "fl_um",
        "area_um2",
        "has_pf",
        "branch_id",
        "p3_prediction",
        "p3_lower",
        "p3_upper",
        "p3_support",
        "p3_nearest_structure_id",
        "p3_nearest_scaled_distance",
        "gp_prediction",
        "gp_trend_log",
        "gp_residual_log",
        "gp_total_log",
        "gp_trend_only",
        "gp_residual_factor",
        "gp_log_scale",
        "gp_minus_baseline",
        "gp_minus_target",
        "p3_minus_baseline",
        "p3_minus_target",
        "gp_above_baseline",
        "gp_meets_target",
        "p3_above_baseline",
        "p3_meets_target",
        "support_label",
    )
    result = {}
    for key in keys:
        value = row[key]
        if isinstance(value, (np.bool_, bool)):
            result[key] = bool(value)
        elif isinstance(value, (np.integer, int)):
            result[key] = int(value)
        elif isinstance(value, (np.floating, float)):
            result[key] = float(value)
        else:
            result[key] = value
    return result


def execute(run: Path, attempt: str) -> int:
    if not attempt.startswith("attempt_") or Path(attempt).name != attempt:
        raise AuditError("Invalid attempt identifier")
    audit = run / "audit"
    input_manifest_path = audit / "input_manifest.json"
    conflicts_path = audit / "version_conflicts.csv"
    t01_artifact_path = audit / "artifact_manifest.json"
    formal = (
        audit / "peak_audit.csv",
        audit / "peak_audit.json",
        audit / "peak_artifact_manifest.json",
    )
    if any(path.exists() for path in formal):
        raise AuditError("Refusing to overwrite formal T02 outputs")
    work = audit / "peak_work" / attempt
    logs = audit / "peak_logs"
    if work.exists():
        raise AuditError(f"Refusing to overwrite T02 attempt evidence: {work}")
    work.mkdir(parents=True)
    logs.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    thread_names = (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    )
    for name in thread_names:
        env[name] = "1"
    commands = []

    def run_checked(command: list[str], log: Path) -> None:
        completed = subprocess.run(command, cwd=BE, env=env, capture_output=True, text=True)
        log.write_text(completed.stdout + completed.stderr)
        record = {
            "command": shlex.join(command),
            "cwd": str(BE),
            "exit_code": completed.returncode,
            "log": str(log),
        }
        commands.append(record)
        if completed.returncode:
            raise AuditError(f"Command failed: {shlex.join(command)}")

    run_checked(
        [
            "uv",
            "run",
            "--no-sync",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(work / "pytest_tmp"),
            "tests/ml/test_optimization_p13_audit.py",
            "tests/ml/test_optimization_p13_t02_peak_audit.py",
        ],
        logs / f"peak_pytest_{attempt}.log",
    )
    run_checked(
        [
            "uv",
            "run",
            "--no-sync",
            "ruff",
            "check",
            "--no-cache",
            "app/ml/optimization_p13/audit.py",
            "scripts/run_optimization_p13_t01_audit.py",
            "scripts/run_optimization_p13_t02_peak_audit.py",
            "tests/ml/test_optimization_p13_audit.py",
            "tests/ml/test_optimization_p13_t02_peak_audit.py",
        ],
        logs / f"peak_ruff_{attempt}.log",
    )

    o01 = read_json(input_manifest_path)
    t01_artifact = read_json(t01_artifact_path)
    if o01["status"] != "PASS" or t01_artifact["status"] != "PASS":
        raise AuditError("O01/T01 is not PASS")
    registered = {record["file_id"]: record for record in o01["inputs"]}
    if t01_artifact["inputs"] != {key: value["sha256"] for key, value in registered.items()}:
        raise AuditError("T01 artifact inputs differ from O01")
    t01_outputs = {entry["path"]: entry for entry in t01_artifact["outputs"]}
    verify_expected_hash(input_manifest_path, t01_outputs["audit/input_manifest.json"]["sha256"])
    verify_expected_hash(conflicts_path, t01_outputs["audit/version_conflicts.csv"]["sha256"])
    o01_protection = {
        "input_manifest_sha256": sha256(input_manifest_path),
        "version_conflicts_sha256": sha256(conflicts_path),
        "artifact_manifest_sha256": sha256(t01_artifact_path),
    }
    if not USED_IDS <= set(registered):
        raise AuditError(f"Unregistered T02 inputs: {sorted(USED_IDS - set(registered))}")
    input_hashes = dict(USED_IDS and {key: registered[key]["sha256"] for key in sorted(USED_IDS)})
    input_paths = {key: registered[key]["absolute_path"] for key in sorted(USED_IDS)}
    for key in sorted(USED_IDS):
        verify_expected_hash(Path(input_paths[key]), input_hashes[key])
    input_hashes.update(
        {
            "O01_INPUT_MANIFEST": o01_protection["input_manifest_sha256"],
            "O01_VERSION_CONFLICTS": o01_protection["version_conflicts_sha256"],
            "O01_ARTIFACT_MANIFEST": o01_protection["artifact_manifest_sha256"],
        }
    )
    input_paths.update(
        {
            "O01_INPUT_MANIFEST": str(input_manifest_path),
            "O01_VERSION_CONFLICTS": str(conflicts_path),
            "O01_ARTIFACT_MANIFEST": str(t01_artifact_path),
        }
    )

    baseline = o01["baseline"]["baseline_k2eff_pct"]
    target = o01["baseline"]["threshold_k2eff_pct"]
    if Decimal(decimal_delta(target, baseline)) != Decimal("1"):
        raise AuditError("O01 target is not baseline plus one percentage point")
    structures = pd.read_csv(input_paths["P3_STRUCTURES"])
    baseline_rows = structures[
        structures.marks.eq("E1-1")
        & structures.ports.eq("S22")
        & structures.area_um2.eq(5500)
        & structures.eg_um.eq(2)
        & structures.fl_um.eq(0)
    ]
    if len(baseline_rows) != 1 or str(baseline_rows.iloc[0].k2eff_pct) != baseline:
        raise AuditError("P3 structure baseline differs from O01")
    positive_fl = structures[(structures.has_pf.eq(0)) & (structures.fl_um.gt(0))]
    min_positive_fl = float(positive_fl.fl_um.min())
    if min_positive_fl != 0.5:
        raise AuditError("Measured PF=0 positive-FL minimum differs from 0.5")

    models = joblib.load(input_paths["P6C_MODELS"])
    bundle = joblib.load(input_paths["P3_MODEL_BUNDLE"])
    model = models[MODEL_KEY]
    selection = read_json(Path(input_paths["P6C_SELECTION"]))
    fit_receipts = read_json(Path(input_paths["P6C_FIT_RECEIPTS"]))
    p3_metadata = read_json(Path(input_paths["P3_MODEL_METADATA"]))
    if selection["structure_holdout"]["selected"] != "trend_gp_log_area":
        raise AuditError("P6C structure_holdout recipe differs from P12")
    if model.recipe != "trend_gp_log_area" or model.target != "k2eff_pct":
        raise AuditError("Frozen GP model identity mismatch")
    if p3_metadata["selected_models"]["k2eff_pct"] != "extra_trees_log":
        raise AuditError("P3 k2 model identity mismatch")
    if not read_json(Path(input_paths["P12_VERIFICATION"]))["all_passed"]:
        raise AuditError("P12 verification is not PASS")

    def predict(frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.reset_index(drop=True)
        p3_all = predict_bundle(bundle, frame)
        p3 = p3_all[p3_all.target.eq("k2eff_pct")].set_index("input_index").sort_index()
        decomposition = decompose_trend_gp(model, frame)
        result = frame.copy()
        result["p3_prediction"] = p3.prediction.to_numpy()
        result["p3_lower"] = p3.lower.to_numpy()
        result["p3_upper"] = p3.upper.to_numpy()
        result["p3_support"] = p3.support.to_numpy()
        result["p3_nearest_structure_id"] = p3.nearest_structure_id.to_numpy()
        result["p3_nearest_scaled_distance"] = p3.nearest_scaled_distance.to_numpy()
        for name, values in decomposition.items():
            result["gp_" + name] = values
        result["gp_log_scale"] = decomposition["std"]
        return result

    candidates = pd.read_csv(input_paths["P12_CANDIDATES"])
    grid = pd.read_csv(input_paths["P12_GRID_PREDICTIONS"])
    extension = pd.read_csv(input_paths["P12_EG_EXTENSION_BEHAVIOR"])
    comparisons = []

    def compare(prediction: pd.DataFrame, saved: pd.DataFrame, mapping: dict[str, str], label: str) -> None:
        for actual, expected in mapping.items():
            comparisons.append(verify_numeric_match(prediction[actual], saved[expected], f"{label}:{expected}"))

    candidate_prediction = predict(geometry_from_saved(candidates))
    candidate_mapping = {
        "p3_prediction": "k2eff_pct_prediction",
        "p3_lower": "k2eff_pct_lower",
        "p3_upper": "k2eff_pct_upper",
        "gp_prediction": "k2eff_pct_p6c",
        "gp_log_scale": "k2eff_pct_p6c_log_scale",
        "p3_nearest_scaled_distance": "nearest_scaled_distance",
    }
    compare(candidate_prediction, candidates, candidate_mapping, "candidates")
    for column in ("p3_support", "p3_nearest_structure_id"):
        expected_column = "p3_support" if column == "p3_support" else "nearest_structure_id"
        if not candidate_prediction[column].equals(candidates[expected_column]):
            raise InferenceMismatchError(f"inference/code mismatch: candidates:{expected_column}")

    regenerated_grid = fine_grid()
    for column in GEOMETRY:
        comparisons.append(verify_numeric_match(regenerated_grid[column], grid[column], f"grid_geometry:{column}"))
    if not regenerated_grid.branch_id.equals(grid.branch_id):
        raise InferenceMismatchError("inference/code mismatch: grid branch identity")
    grid_prediction = predict(geometry_from_saved(grid))
    grid_mapping = {
        "p3_prediction": "k2eff_pct_p3",
        "p3_lower": "k2eff_pct_p3_lower",
        "p3_upper": "k2eff_pct_p3_upper",
        "gp_prediction": "k2eff_pct_p6c",
        "gp_log_scale": "k2eff_pct_p6c_log_scale",
    }
    compare(grid_prediction, grid, grid_mapping, "grid")
    extension_prediction = predict(geometry_from_saved(extension))
    compare(
        extension_prediction,
        extension,
        {"p3_prediction": "k2eff_pct_p3", "gp_prediction": "k2eff_pct_p6c"},
        "extension",
    )
    p1203_index = candidates.index[candidates.candidate_id.eq(P1203_ID)]
    if len(p1203_index) != 1:
        raise AuditError("P1203 candidate identity is not unique")
    p1203_index = int(p1203_index[0])
    p1203_saved = candidates.iloc[p1203_index]
    p1203_prediction = candidate_prediction.iloc[p1203_index]
    p1203_checks = [
        item for item in comparisons if item["label"].startswith("candidates:")
    ]
    frozen_verification = {
        "status": "PASS",
        "difference_classification": "floating_point_reproduction_within_tolerance_not_scientific_drift",
        "exact_zero_required": False,
        "all_close": all(item["all_close"] for item in comparisons),
        "candidate_rows_verified": len(candidates),
        "grid_rows_verified": len(grid),
        "extension_rows_verified": len(extension),
        "candidate_numeric_fields_verified": len(candidate_mapping) * len(candidates),
        "grid_numeric_fields_verified": len(grid_mapping) * len(grid),
        "max_abs_difference": max(item["max_abs_difference"] for item in comparisons),
        "max_relative_difference": max(item["max_relative_difference"] for item in comparisons),
        "comparisons": comparisons,
        "p1203": {
            "candidate_id": P1203_ID,
            "candidate_csv_row": p1203_index + 2,
            "saved_gp_k2eff_pct": float(p1203_saved.k2eff_pct_p6c),
            "recomputed_gp_k2eff_pct": float(p1203_prediction.gp_prediction),
            "gp_abs_difference": abs(float(p1203_prediction.gp_prediction - p1203_saved.k2eff_pct_p6c)),
            "saved_p3_k2eff_pct": float(p1203_saved.k2eff_pct_prediction),
            "recomputed_p3_k2eff_pct": float(p1203_prediction.p3_prediction),
            "p3_abs_difference": abs(float(p1203_prediction.p3_prediction - p1203_saved.k2eff_pct_prediction)),
            "p3_support": p1203_prediction.p3_support,
            "nearest_structure_id": p1203_prediction.p3_nearest_structure_id,
            "nearest_scaled_distance": float(p1203_prediction.p3_nearest_scaled_distance),
            "candidate_checks": p1203_checks,
        },
    }
    if not frozen_verification["all_close"]:
        raise InferenceMismatchError("inference/code mismatch: P12 replay exceeds tolerance")
    frozen_path = work / "frozen_inference_verification.json"
    write_json(frozen_path, frozen_verification)
    if read_json(frozen_path)["status"] != "PASS":
        raise AuditError("Could not read back frozen inference verification")

    reference_map = {}
    for kind, saved, file_id in (
        ("grid", grid, "P12_GRID_PREDICTIONS"),
        ("extension", extension, "P12_EG_EXTENSION_BEHAVIOR"),
        ("candidates", candidates, "P12_CANDIDATES"),
    ):
        for index, row in saved.iterrows():
            reference_map[geometry_key(row)] = common_reference(
                row, kind, file_id, index + 2, registered[file_id]["sha256"]
            )

    def support_label(row) -> str:
        labels = [row.p3_support]
        if row.eg_um > 2:
            labels.append("eg_outside_measured_range")
        if row.fl_um == 0:
            labels.append("separate_fl0_branch_no_cross_branch_continuity_assumed")
        elif row.fl_um < min_positive_fl:
            labels.extend(
                [
                    "positive_fl_below_measured_minimum_0.5",
                    "positive_fl_branch_lower_edge_extrapolation",
                ]
            )
        if row.eg_um > 3:
            labels.append("beyond_p12_main_grid_audit_only_not_candidate")
        labels.append("unmeasured_computation_not_model_qualification")
        return ";".join(labels)

    def make_profile(name: str, points, deltas=None) -> list[dict]:
        points = [
            point if len(point) == 4 else (point[0], point[1], 5500.0, 0)
            for point in points
        ]
        prediction = predict(make_geometry_frame(points))
        rows = []
        for index, row in prediction.iterrows():
            key = geometry_key(row)
            reference = reference_map.get(key)
            candidate_id = P1203_ID if key == (3.0, 0.025, 5500.0, 0) else None
            delta = deltas[index] if deltas is not None else (None, None)
            rows.append(
                {
                    "audit_row_id": f"T02_{name}_{index:03d}",
                    "profile": name,
                    "profile_index": index,
                    "candidate_id": candidate_id,
                    "structure_id": None,
                    "design_group_id": None,
                    "null_identity_reason": "unmeasured audit geometry; no measured structure/design group assigned",
                    "scope": "structure_holdout",
                    "fold": "outer_full_fit_not_inner_fold",
                    "role": "retrospective_frozen_inference_audit",
                    "branch_id": row.branch_id,
                    "gp_model_id": "P6C:structure_holdout:trend_gp_log_area:k2eff_pct",
                    "p3_model_id": "P3:optimization_scalar_p3_v1:extra_trees_log:k2eff_pct",
                    "eg_um": float(row.eg_um),
                    "fl_um": float(row.fl_um),
                    "area_um2": float(row.area_um2),
                    "has_pf": int(row.has_pf),
                    "target": "k2eff_pct",
                    "unit": "percent_number",
                    "difference_unit": "percentage_point",
                    "measured": False,
                    "trusted_for_unmeasured_optimization": False,
                    "p3_prediction": float(row.p3_prediction),
                    "p3_lower": float(row.p3_lower),
                    "p3_upper": float(row.p3_upper),
                    "p3_support": row.p3_support,
                    "p3_nearest_structure_id": row.p3_nearest_structure_id,
                    "p3_nearest_scaled_distance": float(row.p3_nearest_scaled_distance),
                    "gp_prediction": float(row.gp_prediction),
                    "gp_trend_log": float(row.gp_trend_log),
                    "gp_residual_log": float(row.gp_residual_log),
                    "gp_total_log": float(row.gp_total_log),
                    "gp_trend_only": float(row.gp_trend_only),
                    "gp_residual_factor": float(row.gp_residual_factor),
                    "gp_log_scale": float(row.gp_log_scale),
                    "gp_minus_baseline": float(row.gp_prediction) - float(baseline),
                    "gp_minus_target": float(row.gp_prediction) - float(target),
                    "p3_minus_baseline": float(row.p3_prediction) - float(baseline),
                    "p3_minus_target": float(row.p3_prediction) - float(target),
                    "gp_above_baseline": bool(row.gp_prediction > float(baseline)),
                    "gp_meets_target": bool(row.gp_prediction >= float(target)),
                    "p3_above_baseline": bool(row.p3_prediction > float(baseline)),
                    "p3_meets_target": bool(row.p3_prediction >= float(target)),
                    "support_label": support_label(row),
                    "p6c_model_file_id": "P6C_MODELS",
                    "p6c_model_sha256": input_hashes["P6C_MODELS"],
                    "p3_model_file_id": "P3_MODEL_BUNDLE",
                    "p3_model_sha256": input_hashes["P3_MODEL_BUNDLE"],
                    "baseline_file_id": "P3_STRUCTURES",
                    "baseline_sha256": input_hashes["P3_STRUCTURES"],
                    "p12_reference_file_id": reference["file_id"] if reference else None,
                    "p12_reference_sha256": reference["sha256"] if reference else None,
                    "p12_reference_row_number": reference["row_number"] if reference else None,
                    "p12_reference_kind": reference["kind"] if reference else "new_t02_audit_point",
                    "p12_gp_prediction": reference["gp_prediction"] if reference else None,
                    "p12_p3_prediction": reference["p3_prediction"] if reference else None,
                    "p12_p3_lower": reference["p3_lower"] if reference else None,
                    "p12_p3_upper": reference["p3_upper"] if reference else None,
                    "p12_gp_log_scale": reference["gp_log_scale"] if reference else None,
                    "gp_abs_diff_vs_p12": abs(float(row.gp_prediction) - float(reference["gp_prediction"])) if reference else None,
                    "p3_abs_diff_vs_p12": abs(float(row.p3_prediction) - float(reference["p3_prediction"])) if reference else None,
                    "neighborhood_delta_eg_um": delta[0],
                    "neighborhood_delta_fl_um": delta[1],
                }
            )
        return rows

    fl_points = [(3.0, float(Decimal("0.5") - Decimal("0.025") * i), 5500.0, 0) for i in range(20)]
    fl_points += [
        (3.0, fl, 5500.0, 0)
        for fl in (0.02, 0.015, 0.01, 0.005, 0.001, 0.0005, 0.0001, 0.00005, 0.00001)
    ]
    eg_values = [float(Decimal("0.025") * i) for i in range(121)] + [3.25, 3.5, 4.0]
    neighborhood_points = nine_point_neighborhood(3.0, 0.025)
    neighborhood_deltas = [
        (round(eg - 3.0, 12), round(fl - 0.025, 12)) for eg, fl in neighborhood_points
    ]
    profile_rows = []
    profile_rows += make_profile("fl_positive_lower_edge", fl_points)
    profile_rows += make_profile("fl_zero_branch_anchor", [(3.0, 0.0, 5500.0, 0)])
    profile_rows += make_profile("eg_trend_fl0", [(eg, 0.0, 5500.0, 0) for eg in eg_values])
    profile_rows += make_profile(
        "eg_trend_fl_positive", [(eg, 0.025, 5500.0, 0) for eg in eg_values]
    )
    profile_rows += make_profile("neighborhood_9", neighborhood_points, neighborhood_deltas)
    frame = pd.DataFrame(profile_rows)

    def row_at(profile: str, eg: float, fl: float) -> pd.Series:
        selected = frame[
            frame.profile.eq(profile)
            & frame.eg_um.round(12).eq(round(eg, 12))
            & frame.fl_um.round(12).eq(round(fl, 12))
        ]
        if len(selected) != 1:
            raise AuditError(f"Profile lookup failed: {profile} EG={eg} FL={fl}")
        return selected.iloc[0]

    p1203_row = row_at("fl_positive_lower_edge", 3.0, 0.025)
    zero_row = row_at("fl_zero_branch_anchor", 3.0, 0.0)
    fl_selected = [
        selected_values(row_at("fl_positive_lower_edge", 3.0, fl))
        for fl in (0.5, 0.1, 0.05, 0.025, 0.01, 0.001, 0.00001)
    ]
    eg_selected = {
        profile: [selected_values(row_at(profile, eg, fl)) for eg in (2.0, 2.5, 3.0, 3.25, 3.5, 4.0)]
        for profile, fl in (("eg_trend_fl0", 0.0), ("eg_trend_fl_positive", 0.025))
    }

    def eg_deltas(profile: str, fl: float) -> list[dict]:
        start = row_at(profile, 3.0, fl)
        result = []
        for eg in (3.25, 3.5, 4.0):
            end = row_at(profile, eg, fl)
            result.append(
                {
                    "from_eg": 3.0,
                    "to_eg": eg,
                    "branch_id": end.branch_id,
                    "delta_gp_prediction": float(end.gp_prediction - start.gp_prediction),
                    "delta_gp_total_log": float(end.gp_total_log - start.gp_total_log),
                    "delta_gp_trend_log": float(end.gp_trend_log - start.gp_trend_log),
                    "delta_gp_residual_log": float(end.gp_residual_log - start.gp_residual_log),
                    "delta_p3_prediction": float(end.p3_prediction - start.p3_prediction),
                }
            )
        return result

    neighborhood_rows = frame[frame.profile.eq("neighborhood_9")]
    neighborhood_summary = {
        "n": len(neighborhood_rows),
        "gp_above_baseline_count": int(neighborhood_rows.gp_above_baseline.sum()),
        "gp_meets_target_count": int(neighborhood_rows.gp_meets_target.sum()),
        "p3_above_baseline_count": int(neighborhood_rows.p3_above_baseline.sum()),
        "p3_meets_target_count": int(neighborhood_rows.p3_meets_target.sum()),
        "gp_min": float(neighborhood_rows.gp_prediction.min()),
        "gp_max": float(neighborhood_rows.gp_prediction.max()),
        "baseline_signal_disappears_at": [
            f"EG={row.eg_um},FL={row.fl_um}"
            for row in neighborhood_rows.itertuples()
            if not row.gp_above_baseline
        ],
        "rows": [selected_values(row) for _, row in neighborhood_rows.iterrows()],
        "interpretation": "audit sensitivity only; not an A/B/C grade or model-selection rule",
    }
    with Path(input_paths["P12_PEAK_SEARCHES"]).open(newline="") as handle:
        peak_search_rows = list(csv.DictReader(handle))
    continuous = []
    for row in peak_search_rows:
        if row["target"] == "k2eff_pct" and row["model"] == "p6c" and row["method"] == "nelder_mead":
            continuous.append(
                {
                    "start_eg": float(row["start_eg"]),
                    "start_fl": float(row["start_fl"]),
                    "reported_eg": float(row["eg_um"]),
                    "reported_fl": float(row["fl_um"]),
                    "value": row["value"],
                    "minus_baseline": decimal_delta(row["value"], baseline),
                    "minus_target": decimal_delta(row["value"], target),
                    "candidate_geometry_valid": False,
                    "reason": "P12 positive-FL continuous refinement is a branch-limit diagnostic, not a valid FL=0 candidate",
                }
            )
    continuous_max = max((Decimal(row["value"]) for row in continuous), default=None)
    p1203_document = selected_values(p1203_row)
    p1203_document.update(
        {
            "candidate_id": P1203_ID,
            "gp_minus_baseline_exact": decimal_delta(p1203_row.gp_prediction, baseline),
            "gp_minus_target_exact": decimal_delta(p1203_row.gp_prediction, target),
            "p3_minus_baseline_exact": decimal_delta(p1203_row.p3_prediction, baseline),
            "p3_minus_target_exact": decimal_delta(p1203_row.p3_prediction, target),
            "gp_minus_p3_exact": decimal_delta(p1203_row.gp_prediction, p1203_row.p3_prediction),
            "fl_positive_0.025_minus_fl0_gp_exact": decimal_delta(
                p1203_row.gp_prediction, zero_row.gp_prediction
            ),
            "fl_positive_0.025_minus_fl0_p3_exact": decimal_delta(
                p1203_row.p3_prediction, zero_row.p3_prediction
            ),
            "trend_residual_formula": "total_log=trend_log+residual_log; prediction=exp(total_log)=trend_only*residual_factor",
            "gp_std_note": "residual GP standard deviation only; trend-parameter uncertainty is excluded",
            "p12_replay": frozen_verification["p1203"],
        }
    )
    profile_counts = {
        key: int(value) for key, value in frame.profile.value_counts().sort_index().items()
    }
    document = {
        "schema": "aln_optimization_p13_peak_audit_v1",
        "task_id": "T02",
        "attempt_id": attempt,
        "run_id": o01["run_id"],
        "status": "PASS",
        "created_at": now(),
        "baseline_k2eff_pct": baseline,
        "target_k2eff_pct": target,
        "target_rule": "full P3 CSV baseline + 1 percentage point",
        "model_identities": {
            "gp": {
                "model_key": list(MODEL_KEY),
                "scope": "structure_holdout",
                "fold": "outer_full_fit_not_inner_fold",
                "fit_roles": ["train", "validation"],
                "fit_structure_count": len(fit_receipts["structure_holdout"]["fit"]),
                "model_file_id": "P6C_MODELS",
                "model_sha256": input_hashes["P6C_MODELS"],
                "selection_file_id": "P6C_SELECTION",
                "new_selection_performed": False,
            },
            "p3": {
                "schema_version": p3_metadata["schema_version"],
                "model": "extra_trees_log",
                "scope": "structure_holdout",
                "fold": "outer_full_fit_not_inner_fold",
                "fit_roles": p3_metadata["training_roles"],
                "model_file_id": "P3_MODEL_BUNDLE",
                "model_sha256": input_hashes["P3_MODEL_BUNDLE"],
                "new_selection_performed": False,
            },
        },
        "model_training_performed": False,
        "model_selection_performed": False,
        "performance_acceptance_evaluated": False,
        "measured": False,
        "trusted_for_unmeasured_optimization": False,
        "frozen_inference_verification": frozen_verification,
        "p1203": p1203_document,
        "fl_audit": {
            "measured_positive_fl_min": min_positive_fl,
            "zero_branch": selected_values(zero_row),
            "positive_branch_selected_rows": fl_selected,
            "positive_0.025_minus_zero_gp": p1203_document["fl_positive_0.025_minus_fl0_gp_exact"],
            "positive_0.025_minus_zero_p3": p1203_document["fl_positive_0.025_minus_fl0_p3_exact"],
            "branch_continuity_assumed": False,
            "physical_jump_asserted": False,
        },
        "eg_audit": {
            "selected_rows": eg_selected,
            "fl0_extension_deltas": eg_deltas("eg_trend_fl0", 0.0),
            "positive_extension_deltas": eg_deltas("eg_trend_fl_positive", 0.025),
            "extension_note": "EG>3 points are audit profiles only, not a new candidate search or recommendation",
        },
        "neighborhood_audit": neighborhood_summary,
        "continuous_peak_evidence": {
            "rows": continuous,
            "max_value": str(continuous_max) if continuous_max is not None else None,
            "max_minus_target": decimal_delta(continuous_max, target) if continuous_max is not None else None,
            "all_below_plus_one_target": all(Decimal(row["value"]) < Decimal(target) for row in continuous),
            "note": "Refinement locates a model-surface peak only; denser grids are not higher-performance evidence",
        },
        "profile_counts": profile_counts,
        "row_count": len(frame),
        "null_semantics": "CSV empty cells and JSON null denote unavailable/not-applicable identity or reference values, never numeric zero",
        "code_errors": [],
        "scientific_support": {
            "label": "outside_training_bounds;positive_fl_below_measured_minimum;single_gp_signal;branch_sensitive;insufficient_for_plus_one_claim",
            "p1203_gp_above_measured_baseline": bool(p1203_row.gp_above_baseline),
            "p1203_gp_meets_plus_one_target": bool(p1203_row.gp_meets_target),
            "p1203_p3_meets_plus_one_target": bool(p1203_row.p3_meets_target),
            "p1203_model_disagreement_percentage_points": p1203_document["gp_minus_p3_exact"],
            "conclusion": "P1203 has a single-GP above-baseline signal but is below the +1 target and outside measured support; no model qualification or performance-pass claim is made",
        },
        "input_sha256": input_hashes,
        "input_paths": input_paths,
        "o01_protection": o01_protection,
        "random_processes": [],
    }
    temp_csv = work / "peak_audit.csv"
    temp_json = work / "peak_audit.json"
    frame.to_csv(temp_csv, index=False)
    write_json(temp_json, document)

    def validate_peak_outputs(csv_path: Path, json_path: Path) -> dict:
        with csv_path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        doc = read_json(json_path)
        if len(rows) != doc["row_count"] or len({row["audit_row_id"] for row in rows}) != len(rows):
            raise AuditError("Peak CSV row count or identity validation failed")
        required = (
            "audit_row_id",
            "profile",
            "branch_id",
            "gp_model_id",
            "p3_model_id",
            "p3_prediction",
            "gp_prediction",
            "gp_trend_log",
            "gp_residual_log",
            "gp_total_log",
            "p6c_model_sha256",
            "p3_model_sha256",
            "baseline_sha256",
            "support_label",
        )
        for row in rows:
            if any(not row[key] for key in required):
                raise AuditError(f"Missing required peak field: {row['audit_row_id']}")
            trend = float(row["gp_trend_log"])
            residual = float(row["gp_residual_log"])
            total = float(row["gp_total_log"])
            prediction = float(row["gp_prediction"])
            verify_numeric_match(total, trend + residual, f"CSV decomposition:{row['audit_row_id']}")
            verify_numeric_match(prediction, np.exp(total), f"CSV exponential:{row['audit_row_id']}")
            if row["p12_reference_file_id"]:
                verify_numeric_match(
                    float(row["gp_abs_diff_vs_p12"]),
                    abs(prediction - float(row["p12_gp_prediction"])),
                    f"CSV P12 GP difference:{row['audit_row_id']}",
                )
        if sum(row["profile"] == "neighborhood_9" for row in rows) != 9:
            raise AuditError("Peak neighborhood does not contain nine rows")
        if doc["p1203"]["gp_minus_target_exact"] != decimal_delta(
            p1203_row.gp_prediction, target
        ):
            raise AuditError("P1203 full-precision target difference changed")
        if doc["code_errors"] or doc["model_training_performed"] or doc["model_selection_performed"]:
            raise AuditError("Invalid T02 code/model flags")
        for path, expected in (
            (input_manifest_path, o01_protection["input_manifest_sha256"]),
            (conflicts_path, o01_protection["version_conflicts_sha256"]),
            (t01_artifact_path, o01_protection["artifact_manifest_sha256"]),
        ):
            verify_expected_hash(path, expected)
        return {
            "status": "PASS",
            "rows_verified": len(rows),
            "profile_counts": profile_counts,
            "frozen_p12_max_abs_difference": frozen_verification["max_abs_difference"],
            "p1203_gp_minus_target_exact": doc["p1203"]["gp_minus_target_exact"],
            "o01_files_unchanged": True,
        }

    validation = validate_peak_outputs(temp_csv, temp_json)
    validation_path = logs / f"peak_validation_{attempt}.json"
    write_json(validation_path, validation)
    main_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t02_peak_audit.py --run "
        + str(run)
        + " --attempt "
        + attempt
    )
    commands.insert(0, {"command": main_command, "cwd": str(BE), "exit_code": 0})
    prior_attempts = []
    for failure_path in sorted((audit / "peak_work").glob("attempt_*/failure.json")):
        failure = read_json(failure_path)
        failed_command = failure["failed_command"]
        commands.append(failed_command)
        evidence = []
        for evidence_path in (failure_path, Path(failed_command["log"])):
            evidence.append({"path": str(evidence_path), "sha256": sha256(evidence_path)})
        prior_attempts.append(
            {"attempt_id": failure_path.parent.name, "status": failure["status"], "evidence": evidence}
        )
    outputs = []
    for path, relative in (
        (temp_csv, "audit/peak_audit.csv"),
        (temp_json, "audit/peak_audit.json"),
        (validation_path, f"audit/peak_logs/peak_validation_{attempt}.json"),
        (logs / f"peak_pytest_{attempt}.log", f"audit/peak_logs/peak_pytest_{attempt}.log"),
        (logs / f"peak_ruff_{attempt}.log", f"audit/peak_logs/peak_ruff_{attempt}.log"),
    ):
        outputs.append({"path": relative, "kind": "file", "sha256": sha256(path), "bytes": path.stat().st_size})
    packages = {
        name: importlib.metadata.version(name)
        for name in ("numpy", "scipy", "pandas", "scikit-learn", "joblib", "pytest", "ruff")
    }
    artifact = {
        "schema": "aln_optimization_p13_peak_artifact_manifest_v1",
        "task_id": "T02",
        "attempt_id": attempt,
        "run_id": o01["run_id"],
        "status": "PASS",
        "inputs": input_hashes,
        "input_paths": input_paths,
        "outputs": outputs,
        "source_sha256": {str(path): sha256(path) for path in SOURCE_FILES},
        "command": main_command,
        "commands": commands,
        "attempts": [
            *prior_attempts,
            {"attempt_id": attempt, "status": "PASS", "evidence": "audit/peak_artifact_manifest.json"},
        ],
        "exit_code": 0,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "packages": packages,
            "uv": subprocess.check_output(["uv", "--version"], text=True).strip(),
            "git": subprocess.check_output(["/opt/homebrew/bin/git", "--version"], text=True).strip(),
            "cpu_count": os.cpu_count(),
            "numeric_threads": {name: env[name] for name in thread_names},
            "python_dont_write_bytecode": "1",
        },
        "validation": validation,
        "frozen_inference_verification": {
            key: value for key, value in frozen_verification.items() if key != "comparisons"
        },
        "o01_protection": o01_protection,
        "model_training_performed": False,
        "model_selection_performed": False,
        "performance_acceptance_evaluated": False,
        "random_processes": [],
        "created_at": now(),
        "notes": [
            "Only frozen P3/P6C inference was called; no fit, retraining, or model selection occurred",
            "P12 candidates/grid/extension were verified before new audit profiles were generated",
            "Scientific support limitations are not software failures and are not reported as performance PASS",
            "Temporary outputs were validated before promotion; T01 formal files remain byte-identical",
        ],
    }
    temp_artifact = work / "peak_artifact_manifest.json"
    write_json(temp_artifact, artifact)
    if read_json(temp_artifact)["outputs"] != outputs:
        raise AuditError("Temporary peak artifact manifest could not be read back")
    if any(path.exists() for path in formal):
        raise AuditError("Refusing to overwrite formal T02 outputs")
    temp_csv.rename(audit / "peak_audit.csv")
    temp_json.rename(audit / "peak_audit.json")
    temp_artifact.rename(audit / "peak_artifact_manifest.json")
    print(json.dumps({"status": "PASS", "rows": len(frame), "O03": str(audit / "peak_audit.json")}, ensure_ascii=False))
    return 0


def validate_peak_artifact(run: Path) -> dict:
    manifest = read_json(run / "audit/peak_artifact_manifest.json")
    if manifest["task_id"] != "T02" or manifest["status"] != "PASS":
        raise AuditError("Invalid peak artifact identity/status")
    for file_id, expected in manifest["inputs"].items():
        verify_expected_hash(Path(manifest["input_paths"][file_id]), expected)
    for output in manifest["outputs"]:
        path = run / output["path"]
        verify_expected_hash(path, output["sha256"])
        if path.stat().st_size != output["bytes"]:
            raise AuditError(f"Peak output byte count mismatch: {path}")
    for source, expected in manifest["source_sha256"].items():
        verify_expected_hash(Path(source), expected)
    protection = manifest["o01_protection"]
    for relative, key in (
        ("audit/input_manifest.json", "input_manifest_sha256"),
        ("audit/version_conflicts.csv", "version_conflicts_sha256"),
        ("audit/artifact_manifest.json", "artifact_manifest_sha256"),
    ):
        verify_expected_hash(run / relative, protection[key])
    document = read_json(run / "audit/peak_audit.json")
    with (run / "audit/peak_audit.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != document["row_count"] or document["code_errors"]:
        raise AuditError("Peak final document mismatch")
    return {
        "status": "PASS",
        "outputs_verified": len(manifest["outputs"]),
        "inputs_verified": len(manifest["inputs"]),
        "sources_verified": len(manifest["source_sha256"]),
        "rows_verified": len(rows),
        "frozen_p12_max_abs_difference": manifest["validation"]["frozen_p12_max_abs_difference"],
        "o01_files_unchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=BE / "surrogate_data/optimization_p13_20261027_r01")
    parser.add_argument("--attempt", default="attempt_001")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(validate_peak_artifact(args.run.resolve()), ensure_ascii=False))
        return 0
    try:
        return execute(args.run.resolve(), args.attempt)
    except Exception as error:
        status = "BLOCKED" if isinstance(error, HashMismatchError) else "FAILED"
        category = "inference_or_code" if isinstance(error, InferenceMismatchError) else "input_or_code"
        work = args.run.resolve() / "audit/peak_work" / args.attempt
        logs = args.run.resolve() / "audit/peak_logs"
        logs.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        log = logs / f"peak_failure_{args.attempt}.log"
        if not log.exists():
            log.write_text(f"status={status}\ncategory={category}\nerror={error}\n")
        failure = {
            "task_id": "T02",
            "status": status,
            "error_category": category,
            "error": str(error),
            "failed_command": {
                "command": "uv run --no-sync python scripts/run_optimization_p13_t02_peak_audit.py --run "
                + str(args.run.resolve())
                + " --attempt "
                + args.attempt,
                "cwd": str(BE),
                "exit_code": 2,
                "log": str(log),
            },
        }
        if not (work / "failure.json").exists():
            write_json(work / "failure.json", failure)
        print(f"T02 {status} ({category}): {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
