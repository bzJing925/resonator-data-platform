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

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
P12 = BACKEND / "surrogate_data/optimization_p12_20261026"
O02_SHA256 = "bc0f16a4791d1968cac10afafd5fd14a32abfa7bd7d0329b2591ff825d88f05c"
PROVENANCE_COLUMNS = [
    "row_kind",
    "control_type",
    "source_file_id",
    "source_sha256",
    "source_row_number",
    "new_prediction",
    "prediction_recomputed",
    "values_preserved_from_source",
]
FORMAL_OUTPUTS = ("search_protocol.json", "grid_predictions.csv")


class T12Error(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise T12Error(reason)


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


def input_paths(run: Path) -> dict[str, Path]:
    return {
        "O02_PROTOCOL": run / "contracts/protocol.json",
        "O09_SELECTION": run / "selection/selection.json",
        "O09_ELIGIBLE_MODELS": run / "selection/eligible_models.json",
        "O09_ARTIFACT_MANIFEST": run / "selection/artifact_manifest.json",
        "O01_INPUT_MANIFEST": run / "audit/input_manifest.json",
        "O01_ARTIFACT_MANIFEST": run / "audit/artifact_manifest.json",
        "P12_CANDIDATES": P12 / "candidates.csv",
        "P12_OUTPUTS_MANIFEST": P12 / "outputs_manifest.json",
        "P12_SUMMARY": P12 / "summary.json",
        "P12_VERIFICATION": P12 / "verification.json",
    }


def false_mask(series: pd.Series) -> pd.Series:
    return series.map(lambda value: value is False or str(value).lower() == "false")


def true_mask(series: pd.Series) -> pd.Series:
    return series.map(lambda value: value is True or str(value).lower() == "true")


def validate_o09(selection: dict, eligible: dict, manifest: dict, hashes: dict[str, str]) -> None:
    require(selection["status"] == "REJECTED", "O09_SELECTION_NOT_REJECTED")
    require(eligible["status"] == "REJECTED", "O09_ELIGIBLE_MODELS_NOT_REJECTED")
    require(eligible["eligible_models"] == [], "O09_ELIGIBLE_MODELS_NOT_EMPTY")
    require(selection["selected_configurations"] == [], "O09_SELECTED_CONFIGURATIONS_NOT_EMPTY")
    require(not eligible["new_recommendation_search_allowed"], "O09_SEARCH_ALLOWED")
    require(not selection["new_recommendation_search_allowed"], "O09_SELECTION_SEARCH_ALLOWED")
    require(eligible["selection_sha256"] == hashes["O09_SELECTION"], "O09_SELECTION_HASH_MISMATCH")
    outputs = {record["path"]: record["sha256"] for record in manifest["outputs"]}
    require(
        outputs["selection/selection.json"] == hashes["O09_SELECTION"],
        "O09_MANIFEST_SELECTION_HASH_MISMATCH",
    )
    require(
        outputs["selection/eligible_models.json"] == hashes["O09_ELIGIBLE_MODELS"],
        "O09_MANIFEST_ELIGIBLE_HASH_MISMATCH",
    )


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
    require(selection["protocol_sha256"] == hashes["O02_PROTOCOL"], "O09_O02_HASH_MISMATCH")
    validate_o09(selection, eligible, o09_manifest, hashes)
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
    for file_id in ("P12_CANDIDATES", "P12_OUTPUTS_MANIFEST", "P12_SUMMARY", "P12_VERIFICATION"):
        require(
            o01_records[file_id]["sha256"] == hashes[file_id],
            f"O01_SOURCE_HASH_MISMATCH:{file_id}",
        )
    p12_manifest = read_json(paths["P12_OUTPUTS_MANIFEST"])
    require(
        p12_manifest["candidates.csv"] == hashes["P12_CANDIDATES"],
        "P12_CANDIDATES_MANIFEST_HASH_MISMATCH",
    )
    summary = read_json(paths["P12_SUMMARY"])
    verification = read_json(paths["P12_VERIFICATION"])
    require(
        summary["n_candidates"] == 20 and summary["n_negative_controls"] == 3,
        "P12_SUMMARY_MISMATCH",
    )
    require(verification["all_passed"] is True, "P12_VERIFICATION_NOT_PASS")
    candidates = pd.read_csv(paths["P12_CANDIDATES"], dtype=str, keep_default_na=False)
    require(len(candidates) == 20, "P12_CANDIDATE_ROW_COUNT_MISMATCH")
    require(candidates.candidate_id.is_unique, "P12_CANDIDATE_ID_NOT_UNIQUE")
    require(
        candidates.origin.eq("negative_control").sum() == 3, "P12_NEGATIVE_CONTROL_COUNT_MISMATCH"
    )
    require(
        candidates.group.value_counts().to_dict() == {"P11": 10, "P12": 7, "C": 3},
        "P12_COMPOSITION_MISMATCH",
    )
    return {
        "paths": paths,
        "hashes": hashes,
        "protocol": protocol,
        "selection": selection,
        "eligible": eligible,
        "candidates": candidates,
    }


def build_failure_controls(candidates: pd.DataFrame, source_sha256: str) -> pd.DataFrame:
    require(len(candidates) == 20, "FAILURE_CONTROL_ROW_COUNT_MISMATCH")
    negative = candidates.origin.eq("negative_control")
    require(int(negative.sum()) == 3, "FAILURE_CONTROL_NEGATIVE_COUNT_MISMATCH")
    result = candidates.copy()
    result["row_kind"] = "p12_failure_control"
    result["control_type"] = negative.map({True: "negative_control", False: "nonnegative_control"})
    result["source_file_id"] = "P12_CANDIDATES"
    result["source_sha256"] = source_sha256
    result["source_row_number"] = range(1, 21)
    result["new_prediction"] = False
    result["prediction_recomputed"] = False
    result["values_preserved_from_source"] = True
    return result


def audit_no_model_execution(source_path: Path) -> dict:
    tree = ast.parse(source_path.read_text())
    forbidden_calls = []
    model_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"fit", "predict", "predict_frame", "fit_predict"}:
                forbidden_calls.append(node.func.attr)
        if isinstance(node, ast.Import):
            model_imports.extend(
                alias.name
                for alias in node.names
                if alias.name.split(".")[0] in {"app", "sklearn", "torch", "joblib"}
            )
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in {"app", "sklearn", "torch", "joblib"}:
                model_imports.append(node.module)
    require(not forbidden_calls, "MODEL_FIT_OR_PREDICT_CALL_DETECTED")
    require(not model_imports, "MODEL_LIBRARY_IMPORT_DETECTED")
    return {
        "status": "PASS",
        "source_sha256": sha256(source_path),
        "fit_call_count": 0,
        "prediction_call_count": 0,
        "model_library_imports": [],
        "outer_results_read_for_tuning": False,
    }


def build_search_protocol(bundle: dict, source_audit: dict) -> dict:
    protocol = bundle["protocol"]
    hashes = bundle["hashes"]
    candidates = bundle["candidates"]
    return {
        "schema": "aln_optimization_p13_t12_search_protocol_v1",
        "task_id": "T12",
        "output_id": "O11",
        "attempt_id": "attempt_001",
        "run_id": "r01",
        "status": "REJECTED",
        "scientific_status": "REJECTED",
        "artifact_generation_status": "PASS",
        "reason": "no_eligible_models",
        "eligible_models": [],
        "search_executed": False,
        "deployment_fit_count": 0,
        "new_grid_prediction_count": 0,
        "eg_extension_count_executed": 0,
        "new_recommendation_grid_created": False,
        "extension_search_executed": False,
        "outer_results_read_for_tuning": False,
        "o02_protocol_sha256": hashes["O02_PROTOCOL"],
        "o09_selection_sha256": hashes["O09_SELECTION"],
        "o09_eligible_models_sha256": hashes["O09_ELIGIBLE_MODELS"],
        "o09_artifact_manifest_sha256": hashes["O09_ARTIFACT_MANIFEST"],
        "o09_selection_hash_unchanged": True,
        "o01_input_manifest_sha256": hashes["O01_INPUT_MANIFEST"],
        "o01_artifact_manifest_sha256": hashes["O01_ARTIFACT_MANIFEST"],
        "o02_no_eligible_model_rule": protocol["search"]["no_eligible_model_rule"],
        "unexecuted_o02_domain_reference": {
            "reference_only": True,
            "base": protocol["search"]["base"],
            "extension": protocol["search"]["extension"],
            "base_grid_executed": False,
            "extension_executed": False,
        },
        "failure_controls": {
            "source_file_id": "P12_CANDIDATES",
            "source_sha256": hashes["P12_CANDIDATES"],
            "source_outputs_manifest_sha256": hashes["P12_OUTPUTS_MANIFEST"],
            "row_count": 20,
            "nonnegative_control_count": 17,
            "negative_control_count": 3,
            "p12_peak_candidate_count": int(candidates.group.eq("P12").sum()),
            "p11_retained_control_count": int(candidates.group.eq("P11").sum()),
            "original_row_order_preserved": True,
            "original_values_preserved": True,
            "new_prediction": False,
            "prediction_recomputed": False,
        },
        "activity_evidence": {
            "deployment_fit_executed": False,
            "new_grid_prediction_executed": False,
            "new_grid_point_count": 0,
            "new_candidate_count": 0,
            "candidate_ranking_performed": False,
            "source_audit": source_audit,
        },
        "t13_started": False,
    }


def validate_generated_outputs(output: Path, bundle: dict) -> dict:
    for name in FORMAL_OUTPUTS:
        require((output / name).is_file(), f"OUTPUT_MISSING:{name}")
    protocol = read_json(output / "search_protocol.json")
    grid = pd.read_csv(output / "grid_predictions.csv", dtype=str, keep_default_na=False)
    source = bundle["candidates"]
    require(protocol["status"] == protocol["scientific_status"] == "REJECTED", "T12_NOT_REJECTED")
    require(protocol["reason"] == "no_eligible_models", "T12_REJECTION_REASON_MISMATCH")
    require(protocol["eligible_models"] == [], "T12_ELIGIBLE_MODELS_NOT_EMPTY")
    for field in (
        "search_executed",
        "new_recommendation_grid_created",
        "extension_search_executed",
    ):
        require(protocol[field] is False, f"T12_FORBIDDEN_ACTIVITY:{field}")
    for field in (
        "deployment_fit_count",
        "new_grid_prediction_count",
        "eg_extension_count_executed",
    ):
        require(protocol[field] == 0, f"T12_NONZERO_ACTIVITY_COUNT:{field}")
    require(protocol["o09_selection_hash_unchanged"] is True, "O09_HASH_NOT_CONFIRMED")
    require(
        protocol["o09_selection_sha256"] == bundle["hashes"]["O09_SELECTION"],
        "O09_SELECTION_HASH_CHANGED",
    )
    reference = protocol["unexecuted_o02_domain_reference"]
    require(reference["reference_only"] is True, "O02_DOMAIN_NOT_REFERENCE_ONLY")
    require(reference["base"] == bundle["protocol"]["search"]["base"], "O02_BASE_REFERENCE_CHANGED")
    require(
        reference["extension"] == bundle["protocol"]["search"]["extension"],
        "O02_EXTENSION_REFERENCE_CHANGED",
    )
    require(
        not reference["base_grid_executed"] and not reference["extension_executed"],
        "DOMAIN_EXECUTED",
    )
    require(len(grid) == 20, "GRID_ROW_COUNT_MISMATCH")
    require(list(grid.columns) == list(source.columns) + PROVENANCE_COLUMNS, "GRID_SCHEMA_MISMATCH")
    require(grid[list(source.columns)].equals(source), "ORIGINAL_VALUES_NOT_PRESERVED")
    require(
        grid.candidate_id.tolist() == source.candidate_id.tolist(), "ORIGINAL_ROW_ORDER_CHANGED"
    )
    require(grid.row_kind.eq("p12_failure_control").all(), "ROW_KIND_MISMATCH")
    require(
        grid.control_type.value_counts().to_dict()
        == {"nonnegative_control": 17, "negative_control": 3},
        "CONTROL_COMPOSITION_MISMATCH",
    )
    require(grid.source_file_id.eq("P12_CANDIDATES").all(), "SOURCE_FILE_ID_MISMATCH")
    require(grid.source_sha256.eq(bundle["hashes"]["P12_CANDIDATES"]).all(), "SOURCE_HASH_MISMATCH")
    require(
        grid.source_row_number.tolist() == [str(index) for index in range(1, 21)],
        "SOURCE_ROW_NUMBER_MISMATCH",
    )
    require(false_mask(grid.new_prediction).all(), "NEW_PREDICTION_FLAG_NOT_FALSE")
    require(false_mask(grid.prediction_recomputed).all(), "PREDICTION_RECOMPUTED")
    require(true_mask(grid.values_preserved_from_source).all(), "VALUE_PRESERVATION_FLAG_MISMATCH")
    return {
        "status": "PASS",
        "row_count": 20,
        "nonnegative_control_count": 17,
        "negative_control_count": 3,
        "original_column_count": len(source.columns),
        "output_column_count": len(grid.columns),
        "original_values_preserved_exactly_as_text": True,
        "deployment_fit_count": 0,
        "new_grid_prediction_count": 0,
        "eg_extension_count_executed": 0,
        "search_executed": False,
        "o09_selection_hash_unchanged": True,
    }


def generate(run: Path, work: Path) -> dict:
    bundle = validate_inputs(run)
    source_audit = audit_no_model_execution(Path(__file__).resolve())
    protocol = build_search_protocol(bundle, source_audit)
    grid = build_failure_controls(bundle["candidates"], bundle["hashes"]["P12_CANDIDATES"])
    output = work / "outputs"
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "search_protocol.json", protocol)
    grid.to_csv(output / "grid_predictions.csv", index=False)
    validation = validate_generated_outputs(output, bundle)
    write_json(work / "t12_validation.json", validation)
    return validation


def environment() -> dict:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": {
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
    require(ruff_exit == pytest_exit == generate_exit == 0, "T12_PREREQUISITE_COMMAND_FAILED")
    bundle = validate_inputs(run)
    protected_before = dict(bundle["hashes"])
    validation = validate_generated_outputs(work / "outputs", bundle)
    search = run / "search"
    for name in FORMAL_OUTPUTS:
        require(not (search / name).exists(), f"FORMAL_OUTPUT_ALREADY_EXISTS:{name}")
        shutil.copyfile(work / "outputs" / name, search / name)
        require(sha256(search / name) == sha256(work / "outputs" / name), "PROMOTION_HASH_MISMATCH")
    formal_validation = validate_generated_outputs(search, bundle)
    bundle_after = validate_inputs(run)
    protected_after = dict(bundle_after["hashes"])
    require(protected_after == protected_before, "PROTECTED_INPUT_CHANGED")
    o09_unchanged = protected_after["O09_SELECTION"] == protected_before["O09_SELECTION"]
    require(o09_unchanged, "O09_SELECTION_CHANGED")
    logs = work / "logs"
    commands = [
        command_record(
            "uv run --no-sync ruff check --no-cache "
            "scripts/run_optimization_p13_t12.py tests/ml/test_optimization_p13_t12.py",
            logs / "t12_ruff.log",
            ruff_exit,
        ),
        command_record(
            "uv run --no-sync pytest -q -p no:cacheprovider "
            f"--basetemp {work}/pytest_tmp tests/ml/test_optimization_p13_t12.py",
            logs / "t12_pytest.log",
            pytest_exit,
        ),
        command_record(
            "uv run --no-sync python scripts/run_optimization_p13_t12.py "
            f"--run {run} --work {work} --stage generate",
            logs / "t12_generate.log",
            generate_exit,
        ),
    ]
    finalize_log = logs / "t12_finalize.log"
    finalize_log.write_text(
        "T12 temporary outputs validated and promoted; formal outputs re-read: PASS\n"
    )
    finalize_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t12.py "
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
        "schema": "aln_optimization_p13_t12_artifact_manifest_v1",
        "task_id": "T12",
        "output_id": "O11",
        "attempt_id": "attempt_001",
        "run_id": "r01",
        "status": "REJECTED",
        "scientific_status": "REJECTED",
        "execution_status": "PASS",
        "reason": "no_eligible_models",
        "created_at": datetime.now(UTC).isoformat(),
        "command": finalize_command,
        "commands": commands,
        "environment": environment(),
        "inputs": [file_record(file_id, path) for file_id, path in bundle["paths"].items()],
        "outputs": [file_record(name, search / name) for name in FORMAL_OUTPUTS],
        "source_sha256": {
            "scripts/run_optimization_p13_t12.py": sha256(
                BACKEND / "scripts/run_optimization_p13_t12.py"
            ),
            "tests/ml/test_optimization_p13_t12.py": sha256(
                BACKEND / "tests/ml/test_optimization_p13_t12.py"
            ),
        },
        "search_executed": False,
        "deployment_fit_count": 0,
        "new_grid_prediction_count": 0,
        "eg_extension_count_executed": 0,
        "outer_results_read_for_tuning": False,
        "eligible_models": [],
        "o09_selection_sha256_before": protected_before["O09_SELECTION"],
        "o09_selection_sha256_after": protected_after["O09_SELECTION"],
        "o09_selection_hash_unchanged": o09_unchanged,
        "protected_before": protected_before,
        "protected_after": protected_after,
        "temporary_validation": validation,
        "formal_validation": formal_validation,
        "formal_outputs_match_temporary": True,
        "t13_started": False,
        "exit_code": 0,
    }
    manifest_path = search / "t12_artifact_manifest.json"
    require(not manifest_path.exists(), "T12_MANIFEST_ALREADY_EXISTS")
    write_json(manifest_path, manifest)
    return {
        "status": "REJECTED",
        "execution_status": "PASS",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "row_count": 20,
        "nonnegative_control_count": 17,
        "negative_control_count": 3,
        "deployment_fit_count": 0,
        "new_grid_prediction_count": 0,
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
