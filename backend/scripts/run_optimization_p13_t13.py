from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import shutil
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
P12 = BACKEND / "surrogate_data/optimization_p12_20261026"
P3 = BACKEND / "surrogate_data/optimization_p3_20261015"
O02_SHA256 = "bc0f16a4791d1968cac10afafd5fd14a32abfa7bd7d0329b2591ff825d88f05c"
APPENDED_COLUMNS = [
    "included",
    "included_as_failure_control",
    "recommendation_eligible",
    "reason",
    "exclusion_reason",
    "geometry_valid",
    "geometry_invalid_reason",
    "measured_exact_match",
    "measured_match_count",
    "measured_match_structure_ids",
    "new_candidate",
    "a_or_b_eligible",
]


class T13Error(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise T13Error(reason)


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


def file_record(file_id: str, path: Path) -> dict:
    return {
        "file_id": file_id,
        "absolute_path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def false_mask(series: pd.Series) -> pd.Series:
    return series.map(lambda value: value is False or str(value).lower() == "false")


def true_mask(series: pd.Series) -> pd.Series:
    return series.map(lambda value: value is True or str(value).lower() == "true")


def input_paths(run: Path) -> dict[str, Path]:
    return {
        "O02_PROTOCOL": run / "contracts/protocol.json",
        "O09_SELECTION": run / "selection/selection.json",
        "O09_ELIGIBLE_MODELS": run / "selection/eligible_models.json",
        "O09_ARTIFACT_MANIFEST": run / "selection/artifact_manifest.json",
        "O11_SEARCH_PROTOCOL": run / "search/search_protocol.json",
        "O11_GRID_PREDICTIONS": run / "search/grid_predictions.csv",
        "O11_ARTIFACT_MANIFEST": run / "search/t12_artifact_manifest.json",
        "O01_INPUT_MANIFEST": run / "audit/input_manifest.json",
        "O01_ARTIFACT_MANIFEST": run / "audit/artifact_manifest.json",
        "P12_CANDIDATES": P12 / "candidates.csv",
        "P12_OUTPUTS_MANIFEST": P12 / "outputs_manifest.json",
        "P3_STRUCTURES": P3 / "data/structures.csv",
        "P3_DATA_OUTPUTS_MANIFEST": P3 / "data/outputs_manifest.json",
        "P3_DATA_AUDIT": P3 / "data/data_audit.json",
        "P3_DELIVERY_MANIFEST": P3 / "receipts/delivery_manifest.json",
    }


def validate_geometry(rows: pd.DataFrame) -> None:
    required = {"eg_um", "fl_um", "area_um2", "has_pf", "branch_id"}
    require(required <= set(rows.columns), "GEOMETRY_FIELDS_MISSING")
    numeric = rows[["eg_um", "fl_um", "area_um2", "has_pf"]].apply(pd.to_numeric, errors="coerce")
    values = numeric.to_numpy(dtype=float)
    require(bool(np.isfinite(values).all()), "NONFINITE_GEOMETRY")
    require(bool((numeric.eg_um >= 0).all() and (numeric.fl_um >= 0).all()), "NEGATIVE_GEOMETRY")
    require(bool((numeric.area_um2 > 0).all()), "NONPOSITIVE_AREA")
    require(bool(numeric.has_pf.isin([0, 1]).all()), "INVALID_PF")
    expected_branch = (
        "pf"
        + numeric.has_pf.astype(int).astype(str)
        + "_eg"
        + numeric.eg_um.gt(0).astype(int).astype(str)
        + "_fl"
        + numeric.fl_um.gt(0).astype(int).astype(str)
    )
    require(expected_branch.equals(rows.branch_id), "GEOMETRY_BRANCH_MISMATCH")


def validate_inputs(run: Path) -> dict:
    paths = input_paths(run)
    for file_id, path in paths.items():
        require(path.is_file(), f"INPUT_MISSING:{file_id}")
    hashes = {file_id: sha256(path) for file_id, path in paths.items()}
    require(hashes["O02_PROTOCOL"] == O02_SHA256, "O02_HASH_MISMATCH")
    protocol = read_json(paths["O02_PROTOCOL"])
    selection = read_json(paths["O09_SELECTION"])
    eligible = read_json(paths["O09_ELIGIBLE_MODELS"])
    o09_manifest = read_json(paths["O09_ARTIFACT_MANIFEST"])
    require(selection["status"] == eligible["status"] == "REJECTED", "O09_NOT_REJECTED")
    require(eligible["eligible_models"] == [], "O09_ELIGIBLE_MODELS_NOT_EMPTY")
    require(eligible["selection_sha256"] == hashes["O09_SELECTION"], "O09_SELECTION_HASH_MISMATCH")
    o09_outputs = {record["path"]: record["sha256"] for record in o09_manifest["outputs"]}
    require(
        o09_outputs["selection/selection.json"] == hashes["O09_SELECTION"]
        and o09_outputs["selection/eligible_models.json"] == hashes["O09_ELIGIBLE_MODELS"],
        "O09_MANIFEST_HASH_MISMATCH",
    )
    o11_protocol = read_json(paths["O11_SEARCH_PROTOCOL"])
    o11_manifest = read_json(paths["O11_ARTIFACT_MANIFEST"])
    require(o11_protocol["status"] == o11_manifest["status"] == "REJECTED", "O11_NOT_REJECTED")
    require(
        not o11_protocol["search_executed"] and not o11_manifest["search_executed"],
        "O11_SEARCH_EXECUTED",
    )
    require(o11_protocol["eligible_models"] == [], "O11_ELIGIBLE_MODELS_NOT_EMPTY")
    require(o11_protocol["new_grid_prediction_count"] == 0, "O11_NEW_PREDICTIONS_PRESENT")
    require(
        o11_protocol["o09_selection_sha256"] == hashes["O09_SELECTION"],
        "O11_O09_SELECTION_HASH_MISMATCH",
    )
    o11_outputs = {record["file_id"]: record["sha256"] for record in o11_manifest["outputs"]}
    require(
        o11_outputs["search_protocol.json"] == hashes["O11_SEARCH_PROTOCOL"]
        and o11_outputs["grid_predictions.csv"] == hashes["O11_GRID_PREDICTIONS"],
        "O11_MANIFEST_OUTPUT_HASH_MISMATCH",
    )
    o01 = read_json(paths["O01_INPUT_MANIFEST"])
    o01_manifest = read_json(paths["O01_ARTIFACT_MANIFEST"])
    require(o01["status"] == o01_manifest["status"] == "PASS", "O01_NOT_PASS")
    require(
        protocol["inputs"]["audit/input_manifest.json"] == hashes["O01_INPUT_MANIFEST"],
        "O02_O01_HASH_MISMATCH",
    )
    o01_outputs = {record["path"]: record["sha256"] for record in o01_manifest["outputs"]}
    require(
        o01_outputs["audit/input_manifest.json"] == hashes["O01_INPUT_MANIFEST"],
        "O01_MANIFEST_HASH_MISMATCH",
    )
    o01_records = {record["file_id"]: record for record in o01["inputs"]}
    for file_id in (
        "P12_CANDIDATES",
        "P12_OUTPUTS_MANIFEST",
        "P3_STRUCTURES",
        "P3_DATA_OUTPUTS_MANIFEST",
        "P3_DATA_AUDIT",
        "P3_DELIVERY_MANIFEST",
    ):
        require(o01_records[file_id]["sha256"] == hashes[file_id], f"O01_HASH_MISMATCH:{file_id}")
    p12_manifest = read_json(paths["P12_OUTPUTS_MANIFEST"])
    require(p12_manifest["candidates.csv"] == hashes["P12_CANDIDATES"], "P12_HASH_MISMATCH")
    p3_manifest = read_json(paths["P3_DATA_OUTPUTS_MANIFEST"])
    require(p3_manifest["structures.csv"] == hashes["P3_STRUCTURES"], "P3_STRUCTURES_HASH_MISMATCH")
    require(p3_manifest["data_audit.json"] == hashes["P3_DATA_AUDIT"], "P3_AUDIT_HASH_MISMATCH")
    delivery = read_json(paths["P3_DELIVERY_MANIFEST"])
    require(
        delivery["files"]["data/structures.csv"]["sha256"] == hashes["P3_STRUCTURES"]
        and delivery["files"]["data/data_audit.json"]["sha256"] == hashes["P3_DATA_AUDIT"],
        "P3_DELIVERY_HASH_MISMATCH",
    )
    grid = pd.read_csv(paths["O11_GRID_PREDICTIONS"], dtype=str, keep_default_na=False)
    p12 = pd.read_csv(paths["P12_CANDIDATES"], dtype=str, keep_default_na=False)
    measured = pd.read_csv(paths["P3_STRUCTURES"], dtype=str, keep_default_na=False)
    require(len(grid) == len(p12) == 20 and len(measured) == 576, "INPUT_ROW_COUNT_MISMATCH")
    require(grid[list(p12.columns)].equals(p12), "O11_P12_VALUES_CHANGED")
    require(grid.candidate_id.is_unique, "DUPLICATE_CANDIDATE_ID")
    require(measured.structure_id.is_unique, "DUPLICATE_MEASURED_STRUCTURE_ID")
    require(
        grid.control_type.value_counts().to_dict()
        == {"nonnegative_control": 17, "negative_control": 3},
        "O11_COMPOSITION_MISMATCH",
    )
    require(false_mask(grid.new_prediction).all(), "O11_NEW_PREDICTION_FLAG_CHANGED")
    validate_geometry(grid)
    validate_geometry(measured)
    data_audit = read_json(paths["P3_DATA_AUDIT"])
    require(data_audit["n_structures"] == 576, "P3_MEASURED_COUNT_MISMATCH")
    deduplication = protocol["search"]["measured_deduplication"]
    require(
        not deduplication["approximate_neighbor_exclusion"],
        "APPROXIMATE_NEIGHBOR_EXCLUSION_CHANGED",
    )
    return {
        "paths": paths,
        "hashes": hashes,
        "protocol": protocol,
        "grid": grid,
        "p12": p12,
        "measured": measured,
        "deduplication": deduplication,
    }


def measured_overlaps(
    candidates: pd.DataFrame, measured: pd.DataFrame, deduplication: dict
) -> list[dict]:
    eg_tolerance = float(deduplication["eg_fl_atol_um"])
    area_tolerance = float(deduplication["area_atol_um2"])
    require(deduplication["pf_must_match"] is True, "PF_MATCH_RULE_CHANGED")
    measured_eg = pd.to_numeric(measured.eg_um).to_numpy(dtype=float)
    measured_fl = pd.to_numeric(measured.fl_um).to_numpy(dtype=float)
    measured_area = pd.to_numeric(measured.area_um2).to_numpy(dtype=float)
    measured_pf = pd.to_numeric(measured.has_pf).to_numpy(dtype=int)
    results = []
    for row in candidates.itertuples(index=False):
        eg = float(row.eg_um)
        fl = float(row.fl_um)
        area = float(row.area_um2)
        pf = int(row.has_pf)
        mask = (
            (np.abs(measured_eg - eg) <= eg_tolerance)
            & (np.abs(measured_fl - fl) <= eg_tolerance)
            & (np.abs(measured_area - area) <= area_tolerance)
            & (measured_pf == pf)
        )
        ids = measured.loc[mask, "structure_id"].tolist()
        results.append(
            {
                "candidate_id": row.candidate_id,
                "measured_exact_match": bool(ids),
                "measured_match_count": len(ids),
                "measured_match_structure_ids": ";".join(ids),
            }
        )
    return results


def build_shortlist(bundle: dict) -> pd.DataFrame:
    grid = bundle["grid"]
    require(grid.candidate_id.is_unique, "DUPLICATE_CANDIDATE_ID")
    validate_geometry(grid)
    overlaps = measured_overlaps(grid, bundle["measured"], bundle["deduplication"])
    negative = grid.control_type.eq("negative_control")
    nonnegative_overlap = [
        row["candidate_id"]
        for row, is_negative in zip(overlaps, negative, strict=True)
        if not is_negative and row["measured_exact_match"]
    ]
    require(not nonnegative_overlap, "NONNEGATIVE_CONTROL_MEASURED_OVERLAP")
    result = grid.copy()
    result["included"] = ~negative
    result["included_as_failure_control"] = ~negative
    result["recommendation_eligible"] = False
    result["reason"] = "no_eligible_models"
    result.loc[negative, "reason"] = result.loc[negative, "risk_flags"]
    result["exclusion_reason"] = ""
    result.loc[negative, "exclusion_reason"] = result.loc[negative, "risk_flags"]
    result["geometry_valid"] = True
    result["geometry_invalid_reason"] = ""
    for field in ("measured_exact_match", "measured_match_count", "measured_match_structure_ids"):
        result[field] = [row[field] for row in overlaps]
    result["new_candidate"] = False
    result["a_or_b_eligible"] = False
    return result


def validate_shortlist(path: Path, bundle: dict) -> dict:
    require(path.is_file(), "SHORTLIST_MISSING")
    shortlist = pd.read_csv(path, dtype=str, keep_default_na=False)
    source = bundle["grid"]
    require(len(shortlist) == 20, "SHORTLIST_ROW_COUNT_MISMATCH")
    require(
        list(shortlist.columns) == list(source.columns) + APPENDED_COLUMNS,
        "SHORTLIST_SCHEMA_MISMATCH",
    )
    require(shortlist[list(source.columns)].equals(source), "O11_VALUES_OR_ORDER_CHANGED")
    require(shortlist.candidate_id.is_unique, "DUPLICATE_CANDIDATE_ID")
    validate_geometry(shortlist)
    negative = shortlist.control_type.eq("negative_control")
    require(int(negative.sum()) == 3, "NEGATIVE_CONTROL_COUNT_MISMATCH")
    require(true_mask(shortlist.loc[~negative, "included"]).all(), "FAILURE_CONTROL_NOT_INCLUDED")
    require(
        true_mask(shortlist.loc[~negative, "included_as_failure_control"]).all(),
        "FAILURE_CONTROL_FLAG_MISMATCH",
    )
    require(false_mask(shortlist.loc[negative, "included"]).all(), "NEGATIVE_CONTROL_INCLUDED")
    require(
        false_mask(shortlist.loc[negative, "included_as_failure_control"]).all(),
        "NEGATIVE_FAILURE_FLAG_MISMATCH",
    )
    require(false_mask(shortlist.recommendation_eligible).all(), "RECOMMENDATION_ELIGIBLE_PRESENT")
    require(
        shortlist.loc[~negative, "reason"].eq("no_eligible_models").all(), "FAILURE_REASON_MISMATCH"
    )
    require(
        shortlist.loc[negative, "reason"].equals(shortlist.loc[negative, "risk_flags"]),
        "NEGATIVE_ORIGINAL_REASON_CHANGED",
    )
    require(
        shortlist.loc[negative, "exclusion_reason"].equals(shortlist.loc[negative, "risk_flags"]),
        "NEGATIVE_EXCLUSION_REASON_CHANGED",
    )
    require(shortlist.loc[negative, "risk_flags"].ne("").all(), "NEGATIVE_EXCLUSION_REASON_MISSING")
    require(true_mask(shortlist.geometry_valid).all(), "ILLEGAL_GEOMETRY_ROW")
    require(false_mask(shortlist.new_candidate).all(), "NEW_CANDIDATE_PRESENT")
    require(false_mask(shortlist.a_or_b_eligible).all(), "A_OR_B_ELIGIBILITY_PRESENT")
    overlaps = measured_overlaps(shortlist, bundle["measured"], bundle["deduplication"])
    for index, overlap in enumerate(overlaps):
        row = shortlist.iloc[index]
        require(
            row.measured_match_structure_ids == overlap["measured_match_structure_ids"]
            and int(row.measured_match_count) == overlap["measured_match_count"],
            "MEASURED_OVERLAP_RECORD_MISMATCH",
        )
        require(
            (row.measured_exact_match == "True") == overlap["measured_exact_match"],
            "MEASURED_MATCH_FLAG_MISMATCH",
        )
    nonnegative_matches = [
        row["candidate_id"]
        for row, is_negative in zip(overlaps, negative, strict=True)
        if not is_negative and row["measured_exact_match"]
    ]
    require(not nonnegative_matches, "NONNEGATIVE_CONTROL_MEASURED_OVERLAP")
    negative_matches = [
        row
        for row, is_negative in zip(overlaps, negative, strict=True)
        if is_negative and row["measured_exact_match"]
    ]
    return {
        "status": "PASS",
        "row_count": 20,
        "failure_control_count": 17,
        "negative_control_excluded_count": 3,
        "new_candidate_count": 0,
        "measured_structure_count": len(bundle["measured"]),
        "nonnegative_measured_match_row_count": 0,
        "negative_measured_match_row_count": len(negative_matches),
        "negative_measured_matches": negative_matches,
        "negative_matches_preserved_with_exclusion": True,
        "geometry_invalid_row_count": 0,
        "geometry_invalid_candidate_ids": [],
        "branch_mismatch_count": 0,
        "duplicate_candidate_id_count": 0,
        "original_o11_values_and_order_preserved": True,
        "new_argmax_count": 0,
        "deployment_prediction_count": 0,
        "a_or_b_grade_generated": False,
    }


def audit_no_model_or_extremum_execution(source_path: Path) -> dict:
    tree = ast.parse(source_path.read_text())
    forbidden = {"fit", "predict", "predict_frame", "fit_predict", "argmax", "idxmax"}
    calls = []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in forbidden:
                calls.append(node.func.attr)
        if isinstance(node, ast.Import):
            imports.extend(
                alias.name
                for alias in node.names
                if alias.name.split(".")[0] in {"app", "sklearn", "torch", "joblib"}
            )
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in {"app", "sklearn", "torch", "joblib"}:
                imports.append(node.module)
    require(not calls, "MODEL_OR_EXTREMUM_CALL_DETECTED")
    require(not imports, "MODEL_LIBRARY_IMPORT_DETECTED")
    return {
        "status": "PASS",
        "source_sha256": sha256(source_path),
        "fit_call_count": 0,
        "deployment_prediction_call_count": 0,
        "argmax_call_count": 0,
        "model_library_imports": [],
        "new_geometry_point_count": 0,
    }


def generate(run: Path, work: Path) -> dict:
    bundle = validate_inputs(run)
    source_audit = audit_no_model_or_extremum_execution(Path(__file__).resolve())
    shortlist = build_shortlist(bundle)
    output = work / "outputs"
    output.mkdir(parents=True, exist_ok=False)
    shortlist.to_csv(output / "shortlist.csv", index=False)
    validation = validate_shortlist(output / "shortlist.csv", bundle)
    validation["source_audit"] = source_audit
    write_json(work / "t13_validation.json", validation)
    return validation


def environment() -> dict:
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
        "dependency_install_or_update": False,
        "random_processes": [],
    }


def command_record(command: str, log: Path, exit_code: int) -> dict:
    require(log.is_file(), f"COMMAND_LOG_MISSING:{log}")
    return {
        "command": command,
        "cwd": str(BACKEND),
        "exit_code": exit_code,
        "log": str(log),
        "log_sha256": sha256(log),
    }


def finalize(run: Path, work: Path, ruff_exit: int, pytest_exit: int, generate_exit: int) -> dict:
    require(ruff_exit == pytest_exit == generate_exit == 0, "T13_PREREQUISITE_COMMAND_FAILED")
    bundle = validate_inputs(run)
    protected_before = dict(bundle["hashes"])
    validation = validate_shortlist(work / "outputs/shortlist.csv", bundle)
    formal = run / "search/shortlist.csv"
    require(not formal.exists(), "FORMAL_SHORTLIST_ALREADY_EXISTS")
    shutil.copyfile(work / "outputs/shortlist.csv", formal)
    require(sha256(formal) == sha256(work / "outputs/shortlist.csv"), "PROMOTION_HASH_MISMATCH")
    formal_validation = validate_shortlist(formal, bundle)
    bundle_after = validate_inputs(run)
    protected_after = dict(bundle_after["hashes"])
    require(protected_after == protected_before, "PROTECTED_INPUT_CHANGED")
    logs = work / "logs"
    commands = [
        command_record(
            "uv run --no-sync ruff check --no-cache "
            "scripts/run_optimization_p13_t13.py tests/ml/test_optimization_p13_t13.py",
            logs / "t13_ruff.log",
            ruff_exit,
        ),
        command_record(
            "uv run --no-sync pytest -q -p no:cacheprovider "
            f"--basetemp {work}/pytest_tmp tests/ml/test_optimization_p13_t13.py",
            logs / "t13_pytest.log",
            pytest_exit,
        ),
        command_record(
            "uv run --no-sync python scripts/run_optimization_p13_t13.py "
            f"--run {run} --work {work} --stage generate",
            logs / "t13_generate.log",
            generate_exit,
        ),
    ]
    finalize_log = logs / "t13_finalize.log"
    finalize_log.write_text(
        "T13 temporary output validated and promoted; formal output re-read: PASS\n"
    )
    finalize_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t13.py "
        f"--run {run} --work {work} --stage finalize --ruff-exit {ruff_exit} "
        f"--pytest-exit {pytest_exit} --generate-exit {generate_exit}"
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
        "schema": "aln_optimization_p13_t13_artifact_manifest_v1",
        "task_id": "T13",
        "output_id": "O12",
        "attempt_id": "attempt_001",
        "run_id": "r01",
        "status": "REJECTED",
        "scientific_status": "REJECTED",
        "execution_status": "PASS",
        "software_validation": "PASS",
        "reason": "no_eligible_models",
        "created_at": datetime.now(UTC).isoformat(),
        "command": finalize_command,
        "commands": commands,
        "environment": environment(),
        "inputs": [file_record(file_id, path) for file_id, path in bundle["paths"].items()],
        "outputs": [file_record("shortlist.csv", formal)],
        "source_sha256": {
            "scripts/run_optimization_p13_t13.py": sha256(
                BACKEND / "scripts/run_optimization_p13_t13.py"
            ),
            "tests/ml/test_optimization_p13_t13.py": sha256(
                BACKEND / "tests/ml/test_optimization_p13_t13.py"
            ),
        },
        "failure_control_count": 17,
        "negative_control_excluded_count": 3,
        "new_candidate_count": 0,
        "search_executed": False,
        "new_geometry_point_count": 0,
        "new_argmax_count": 0,
        "deployment_prediction_count": 0,
        "a_or_b_grade_generated": False,
        "measured_deduplication": bundle["deduplication"],
        "o09_selection_sha256_before": protected_before["O09_SELECTION"],
        "o09_selection_sha256_after": protected_after["O09_SELECTION"],
        "o09_selection_hash_unchanged": protected_before["O09_SELECTION"]
        == protected_after["O09_SELECTION"],
        "o11_grid_sha256_before": protected_before["O11_GRID_PREDICTIONS"],
        "o11_grid_sha256_after": protected_after["O11_GRID_PREDICTIONS"],
        "protected_before": protected_before,
        "protected_after": protected_after,
        "temporary_validation": validation,
        "formal_validation": formal_validation,
        "formal_output_matches_temporary": True,
        "t14_started": False,
        "exit_code": 0,
    }
    manifest_path = run / "search/t13_artifact_manifest.json"
    require(not manifest_path.exists(), "T13_MANIFEST_ALREADY_EXISTS")
    write_json(manifest_path, manifest)
    return {
        "status": "REJECTED",
        "execution_status": "PASS",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "failure_control_count": 17,
        "negative_control_excluded_count": 3,
        "nonnegative_measured_match_row_count": 0,
        "negative_measured_match_row_count": validation["negative_measured_match_row_count"],
        "geometry_invalid_row_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--stage", choices=("generate", "finalize"), required=True)
    parser.add_argument("--ruff-exit", type=int)
    parser.add_argument("--pytest-exit", type=int)
    parser.add_argument("--generate-exit", type=int)
    args = parser.parse_args()
    if args.stage == "generate":
        result = generate(args.run.resolve(), args.work.resolve())
    else:
        require(
            args.ruff_exit is not None
            and args.pytest_exit is not None
            and args.generate_exit is not None,
            "FINALIZE_EXIT_CODES_REQUIRED",
        )
        result = finalize(
            args.run.resolve(),
            args.work.resolve(),
            args.ruff_exit,
            args.pytest_exit,
            args.generate_exit,
        )
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
