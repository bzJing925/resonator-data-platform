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
O02_SHA256 = "bc0f16a4791d1968cac10afafd5fd14a32abfa7bd7d0329b2591ff825d88f05c"
CANONICAL_EMPTY_INDICES_PAYLOAD = '{"indices":[]}'
CANONICAL_EMPTY_INDICES_SHA256 = hashlib.sha256(
    CANONICAL_EMPTY_INDICES_PAYLOAD.encode("utf-8")
).hexdigest()
NULL_EVIDENCE_FIELDS = (
    "conservative_value",
    "error_margin",
    "extrapolation_distance",
    "testable_max_distance",
    "neighborhood_result",
    "a_or_b_grade",
)
SCORE_COLUMNS = [
    "evidence_status",
    "conservative_value",
    "conservative_value_null_reason",
    "error_margin",
    "error_margin_null_reason",
    "extrapolation_distance",
    "extrapolation_distance_null_reason",
    "testable_max_distance",
    "testable_max_distance_null_reason",
    "neighborhood_result",
    "neighborhood_status",
    "neighborhood_null_reason",
    "a_or_b_grade",
    "a_or_b_grade_null_reason",
]
FORMAL_OUTPUTS = ("bootstrap_indices.json", "candidate_scores.csv", "robustness_report.json")


class T14Error(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise T14Error(reason)


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
        "O05_SPLITS": run / "evaluation/splits.csv",
        "O05_CAPACITY": run / "evaluation/capacity.csv",
        "O05_ARTIFACT_MANIFEST": run / "evaluation/t04_artifact_manifest.json",
        "O12_SHORTLIST": run / "search/shortlist.csv",
        "O12_ARTIFACT_MANIFEST": run / "search/t13_artifact_manifest.json",
    }


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
    require(not eligible["new_recommendation_search_allowed"], "O09_SEARCH_ALLOWED")
    require(eligible["selection_sha256"] == hashes["O09_SELECTION"], "O09_SELECTION_HASH_MISMATCH")
    o09_outputs = {record["path"]: record["sha256"] for record in o09_manifest["outputs"]}
    require(
        o09_outputs["selection/selection.json"] == hashes["O09_SELECTION"]
        and o09_outputs["selection/eligible_models.json"] == hashes["O09_ELIGIBLE_MODELS"],
        "O09_MANIFEST_HASH_MISMATCH",
    )
    o05_manifest = read_json(paths["O05_ARTIFACT_MANIFEST"])
    require(o05_manifest["status"] == "PASS", "O05_NOT_PASS")
    o05_outputs = {record["path"]: record["sha256"] for record in o05_manifest["outputs"]}
    require(
        o05_outputs["evaluation/splits.csv"] == hashes["O05_SPLITS"]
        and o05_outputs["evaluation/capacity.csv"] == hashes["O05_CAPACITY"],
        "O05_MANIFEST_HASH_MISMATCH",
    )
    require(
        protocol["inputs"]["evaluation/splits.csv"] == hashes["O05_SPLITS"]
        and protocol["inputs"]["evaluation/capacity.csv"] == hashes["O05_CAPACITY"]
        and protocol["inputs"]["evaluation/t04_artifact_manifest.json"]
        == hashes["O05_ARTIFACT_MANIFEST"],
        "O02_O05_HASH_MISMATCH",
    )
    o12_manifest = read_json(paths["O12_ARTIFACT_MANIFEST"])
    require(o12_manifest["status"] == "REJECTED", "O12_NOT_REJECTED")
    require(o12_manifest["new_candidate_count"] == 0, "O12_NEW_CANDIDATE_PRESENT")
    require(
        o12_manifest["failure_control_count"] == 17
        and o12_manifest["negative_control_excluded_count"] == 3,
        "O12_COMPOSITION_MISMATCH",
    )
    require(
        o12_manifest["outputs"][0]["sha256"] == hashes["O12_SHORTLIST"],
        "O12_MANIFEST_HASH_MISMATCH",
    )
    shortlist = pd.read_csv(paths["O12_SHORTLIST"], dtype=str, keep_default_na=False)
    splits = pd.read_csv(paths["O05_SPLITS"])
    capacity = pd.read_csv(paths["O05_CAPACITY"])
    require(len(shortlist) == 20 and shortlist.candidate_id.is_unique, "O12_ROW_ID_MISMATCH")
    require(
        shortlist.control_type.value_counts().to_dict()
        == {"nonnegative_control": 17, "negative_control": 3},
        "O12_CONTROL_COMPOSITION_MISMATCH",
    )
    require(false_mask(shortlist.recommendation_eligible).all(), "O12_RECOMMENDATION_PRESENT")
    require(false_mask(shortlist.a_or_b_eligible).all(), "O12_A_OR_B_PRESENT")
    require(false_mask(shortlist.new_candidate).all(), "O12_NEW_CANDIDATE_FLAG_PRESENT")
    return {
        "paths": paths,
        "hashes": hashes,
        "protocol": protocol,
        "selection": selection,
        "eligible": eligible,
        "shortlist": shortlist,
        "splits": splits,
        "capacity": capacity,
    }


def compute_capacity_audit(splits: pd.DataFrame, capacity: pd.DataFrame, protocol: dict) -> dict:
    rule = protocol["evaluation"]["hard_capacity"]
    minimum_groups = int(rule["minimum_independent_design_groups"])
    minimum_units = int(rule["minimum_valid_boundary_units"])
    evaluation = splits[splits.role.eq("evaluation") & splits.family.ne("ordinary")]
    grouped = (
        evaluation.groupby(["scope", "family", "branch_id"])
        .agg(
            valid_unit_count=("unit_id", "nunique"),
            independent_design_group_count=("design_group_id", "nunique"),
        )
        .reset_index()
    )
    insufficient = []
    for row in grouped.itertuples(index=False):
        reasons = []
        if row.independent_design_group_count < minimum_groups:
            reasons.append("FEWER_THAN_20_INDEPENDENT_DESIGN_GROUPS")
        if row.valid_unit_count < minimum_units:
            reasons.append("FEWER_THAN_2_VALID_BOUNDARY_UNITS")
        if reasons:
            insufficient.append(
                {
                    "scope": row.scope,
                    "family": row.family,
                    "branch_id": row.branch_id,
                    "valid_unit_count": int(row.valid_unit_count),
                    "independent_design_group_count": int(row.independent_design_group_count),
                    "reasons": reasons,
                }
            )
    insufficient.sort(key=lambda row: (row["scope"], row["family"], row["branch_id"]))
    require(len(insufficient) == rule["known_insufficient_keys"], "CAPACITY_KEY_COUNT_MISMATCH")
    require(
        int(capacity.counts_toward_hard_capacity.sum()) == 37, "O05_CAPACITY_UNIT_COUNT_CHANGED"
    )
    return {
        "key": rule["key"],
        "deduplication": rule["deduplication"],
        "minimum_independent_design_groups": minimum_groups,
        "minimum_valid_boundary_units": minimum_units,
        "evaluated_key_count": len(grouped),
        "insufficient_key_count": len(insufficient),
        "insufficient_keys": insufficient,
        "pooled_count_substitute_used": False,
    }


def build_bootstrap_indices(eligible: dict, protocol: dict) -> dict:
    require(eligible["eligible_models"] == [], "BOOTSTRAP_REQUIRES_NO_ELIGIBLE_MODELS")
    bootstrap = protocol["robustness"]["bootstrap"]
    return {
        "schema": "aln_optimization_p13_t14_bootstrap_indices_v1",
        "task_id": "T14",
        "status": "REJECTED",
        "scientific_status": "REJECTED",
        "reason": "no_eligible_models",
        "generation_executed": False,
        "replicate_count": 0,
        "planned_replicates": bootstrap["replicates"],
        "planned_seed": bootstrap["seed"],
        "seed_consumed": False,
        "indices": [],
        "canonical_empty_indices_payload": CANONICAL_EMPTY_INDICES_PAYLOAD,
        "canonical_empty_indices_payload_sha256": CANONICAL_EMPTY_INDICES_SHA256,
        "canonicalization": "UTF-8 compact JSON with no trailing newline",
        "rng_called": False,
        "random_draw_count": 0,
    }


def build_candidate_scores(shortlist: pd.DataFrame) -> pd.DataFrame:
    require(len(shortlist) == 20, "CANDIDATE_SCORE_ROW_COUNT_MISMATCH")
    result = shortlist.copy()
    result["evidence_status"] = "unavailable"
    result["conservative_value"] = ""
    result["conservative_value_null_reason"] = (
        "no_eligible_models;P3_AGGREGATION_ESTIMAND_MISMATCH;"
        "E1_COORDINATE_OBSERVATIONS_MISSING;HARD_CAPACITY_INSUFFICIENT_13_KEYS"
    )
    result["error_margin"] = ""
    result["error_margin_null_reason"] = "no_eligible_models;HARD_CAPACITY_INSUFFICIENT_13_KEYS"
    result["extrapolation_distance"] = ""
    result["extrapolation_distance_null_reason"] = "no_eligible_models"
    result["testable_max_distance"] = ""
    result["testable_max_distance_null_reason"] = "no_eligible_models"
    result["neighborhood_result"] = ""
    result["neighborhood_status"] = "unavailable"
    result["neighborhood_null_reason"] = "no_eligible_models"
    result["a_or_b_grade"] = ""
    result["a_or_b_grade_null_reason"] = "no_eligible_models"
    return result


def build_robustness_report(
    protocol: dict,
    eligible: dict,
    capacity_audit: dict,
    source_audit: dict,
    hashes: dict[str, str],
) -> dict:
    robustness = protocol["robustness"]
    baseline = robustness["baseline_resampling"]
    require(
        not baseline["coordinate_records_available_under_o01"],
        "E1_COORDINATES_UNEXPECTEDLY_PRESENT",
    )
    require(not baseline["coordinate_indices_generated"], "E1_COORDINATE_INDICES_PRESENT")
    require(not baseline["same_round_candidate_baseline_delta_available"], "BASELINE_DELTA_PRESENT")
    require(
        not robustness["conservative_value"]["available_under_o02"], "CONSERVATIVE_VALUE_PRESENT"
    )
    require(not protocol["models"]["M2"]["aggregation_estimand_match"], "M2_AGGREGATION_OVERRIDE")
    return {
        "schema": "aln_optimization_p13_t14_robustness_report_v1",
        "task_id": "T14",
        "output_id": "O13",
        "gate": "G5",
        "run_id": "r01",
        "status": "REJECTED",
        "scientific_status": "REJECTED",
        "execution_status": "PASS",
        "status_meaning": "Failure path and null reasons complete; not a performance pass",
        "performance_pass": False,
        "reason": "no_eligible_models",
        "eligible_models": eligible["eligible_models"],
        "search_executed": False,
        "refit_count": 0,
        "bootstrap_prediction_count": 0,
        "neighborhood_prediction_count": 0,
        "random_draw_count": 0,
        "failure_control_count": 17,
        "negative_control_excluded_count": 3,
        "new_candidate_count": 0,
        "unavailable": {
            "conservative_value": {
                "value": None,
                "reasons": [
                    "no_eligible_models",
                    "P3_AGGREGATION_ESTIMAND_MISMATCH",
                    "E1_COORDINATE_OBSERVATIONS_MISSING",
                    "HARD_CAPACITY_INSUFFICIENT_13_KEYS",
                ],
            },
            "error_margin": {
                "value": None,
                "reasons": ["no_eligible_models", "HARD_CAPACITY_INSUFFICIENT_13_KEYS"],
            },
            "extrapolation_distance": {"value": None, "reasons": ["no_eligible_models"]},
            "testable_max_distance": {"value": None, "reasons": ["no_eligible_models"]},
            "neighborhood": {
                "value": None,
                "status": "unavailable",
                "reasons": ["no_eligible_models"],
                "nine_point_prediction_executed": False,
            },
            "a_or_b_grade": {
                "value": None,
                "eligible": False,
                "reasons": ["no_eligible_models"],
            },
        },
        "m2_aggregation": {
            "aggregation_estimand_match": False,
            "reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
            "eligible_model_family": False,
        },
        "baseline_resampling": {
            "coordinate_records_available_under_o01": False,
            "coordinate_indices_generated": False,
            "same_round_candidate_baseline_delta_available": False,
            "reason": "E1_COORDINATE_OBSERVATIONS_MISSING",
            "source_null_reason": baseline["null_reason"],
        },
        "capacity_audit": capacity_audit,
        "activity_evidence": source_audit,
        "o09_selection_sha256": hashes["O09_SELECTION"],
        "o12_shortlist_sha256": hashes["O12_SHORTLIST"],
        "o09_unchanged": True,
        "o12_unchanged": True,
        "t15_started": False,
    }


def validate_outputs(output: Path, bundle: dict) -> dict:
    for name in FORMAL_OUTPUTS:
        require((output / name).is_file(), f"OUTPUT_MISSING:{name}")
    indices = read_json(output / "bootstrap_indices.json")
    report = read_json(output / "robustness_report.json")
    scores = pd.read_csv(output / "candidate_scores.csv", dtype=str, keep_default_na=False)
    source = bundle["shortlist"]
    require(indices["status"] == indices["scientific_status"] == "REJECTED", "INDICES_NOT_REJECTED")
    require(not indices["generation_executed"], "INDICES_GENERATED")
    require(indices["replicate_count"] == 0 and indices["indices"] == [], "NONEMPTY_INDICES")
    require(indices["planned_replicates"] == 1000, "PLANNED_REPLICATES_CHANGED")
    require(indices["planned_seed"] == 20261027, "PLANNED_SEED_CHANGED")
    require(not indices["seed_consumed"] and not indices["rng_called"], "SEED_OR_RNG_CONSUMED")
    require(indices["reason"] == "no_eligible_models", "INDICES_REASON_MISMATCH")
    canonical = indices["canonical_empty_indices_payload"]
    require(canonical == CANONICAL_EMPTY_INDICES_PAYLOAD, "CANONICAL_PAYLOAD_CHANGED")
    require(
        hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        == indices["canonical_empty_indices_payload_sha256"]
        == CANONICAL_EMPTY_INDICES_SHA256,
        "CANONICAL_PAYLOAD_HASH_MISMATCH",
    )
    require(len(scores) == 20 and scores.candidate_id.is_unique, "SCORE_ROW_ID_MISMATCH")
    require(list(scores.columns) == list(source.columns) + SCORE_COLUMNS, "SCORE_SCHEMA_MISMATCH")
    require(scores[list(source.columns)].equals(source), "O12_VALUES_OR_ORDER_CHANGED")
    require(scores.evidence_status.eq("unavailable").all(), "EVIDENCE_STATUS_MISMATCH")
    for field in NULL_EVIDENCE_FIELDS:
        require(scores[field].eq("").all(), f"ZERO_OR_VALUE_SUBSTITUTED_FOR_NULL:{field}")
        if field != "neighborhood_result":
            reason_field = f"{field}_null_reason"
            require(
                reason_field in scores and scores[reason_field].ne("").all(),
                f"NULL_REASON_MISSING:{field}",
            )
    require(scores.neighborhood_status.eq("unavailable").all(), "NEIGHBORHOOD_NOT_UNAVAILABLE")
    require(scores.neighborhood_null_reason.ne("").all(), "NULL_REASON_MISSING:neighborhood")
    negative = scores.control_type.eq("negative_control")
    require(int(negative.sum()) == 3, "NEGATIVE_CONTROL_COUNT_MISMATCH")
    require(
        true_mask(scores.loc[~negative, "included_as_failure_control"]).all(),
        "FAILURE_CONTROLS_CHANGED",
    )
    require(false_mask(scores.loc[negative, "included"]).all(), "NEGATIVE_CONTROL_INCLUDED")
    require(false_mask(scores.recommendation_eligible).all(), "RECOMMENDATION_PRESENT")
    require(false_mask(scores.a_or_b_eligible).all(), "A_OR_B_ELIGIBLE_PRESENT")
    require(report["status"] == report["scientific_status"] == "REJECTED", "REPORT_NOT_REJECTED")
    require(
        report["execution_status"] == "PASS" and not report["performance_pass"],
        "G5_STATUS_MISMATCH",
    )
    for field in (
        "refit_count",
        "bootstrap_prediction_count",
        "neighborhood_prediction_count",
        "random_draw_count",
    ):
        require(report[field] == 0, f"NONZERO_ACTIVITY_COUNT:{field}")
    for field in (
        "conservative_value",
        "error_margin",
        "extrapolation_distance",
        "testable_max_distance",
        "neighborhood",
        "a_or_b_grade",
    ):
        evidence = report["unavailable"][field]
        require(
            evidence["value"] is None and evidence["reasons"], f"REPORT_NULL_INCOMPLETE:{field}"
        )
    conservative_reasons = set(report["unavailable"]["conservative_value"]["reasons"])
    require(
        conservative_reasons
        == {
            "no_eligible_models",
            "P3_AGGREGATION_ESTIMAND_MISMATCH",
            "E1_COORDINATE_OBSERVATIONS_MISSING",
            "HARD_CAPACITY_INSUFFICIENT_13_KEYS",
        },
        "CONSERVATIVE_REASONS_INCOMPLETE",
    )
    require(
        report["capacity_audit"]["insufficient_key_count"] == 13, "REPORT_CAPACITY_COUNT_MISMATCH"
    )
    require(
        not report["baseline_resampling"]["coordinate_records_available_under_o01"],
        "E1_COORDINATES_PRESENT",
    )
    require(not report["m2_aggregation"]["aggregation_estimand_match"], "M2_AGGREGATION_OVERRIDE")
    require(not report["t15_started"], "T15_STARTED")
    return {
        "status": "PASS",
        "scientific_status": "REJECTED",
        "row_count": 20,
        "failure_control_count": 17,
        "negative_control_excluded_count": 3,
        "replicate_count": 0,
        "random_draw_count": 0,
        "refit_count": 0,
        "bootstrap_prediction_count": 0,
        "neighborhood_prediction_count": 0,
        "null_fields_empty_not_zero": list(NULL_EVIDENCE_FIELDS),
        "canonical_empty_indices_payload_sha256": CANONICAL_EMPTY_INDICES_SHA256,
        "capacity_insufficient_key_count": 13,
        "o09_unchanged": True,
        "o12_unchanged": True,
    }


def audit_no_random_fit_or_prediction(source_path: Path) -> dict:
    tree = ast.parse(source_path.read_text())
    forbidden_attributes = {
        "fit",
        "predict",
        "predict_frame",
        "fit_predict",
        "argmax",
        "idxmax",
        "default_rng",
        "RandomState",
        "seed",
        "rand",
        "randn",
        "randint",
        "random",
        "random_sample",
        "choice",
        "permutation",
        "shuffle",
        "resample",
    }
    forbidden_names = {"default_rng", "RandomState", "seed", "resample"}
    calls = []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_attributes:
                calls.append(node.func.attr)
            if isinstance(node.func, ast.Name) and node.func.id in forbidden_names:
                calls.append(node.func.id)
        if isinstance(node, ast.Import):
            imports.extend(
                alias.name
                for alias in node.names
                if alias.name == "random"
                or alias.name.startswith("numpy.random")
                or alias.name.split(".")[0] in {"app", "sklearn", "torch", "joblib"}
            )
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "random" or node.module.startswith("numpy.random"):
                imports.append(node.module)
            elif node.module.split(".")[0] in {"app", "sklearn", "torch", "joblib"}:
                imports.append(node.module)
    require(not calls, "RANDOM_FIT_OR_PREDICTION_CALL_DETECTED")
    require(not imports, "RANDOM_OR_MODEL_IMPORT_DETECTED")
    return {
        "status": "PASS",
        "source_sha256": sha256(source_path),
        "rng_call_count": 0,
        "random_draw_count": 0,
        "refit_call_count": 0,
        "bootstrap_prediction_call_count": 0,
        "neighborhood_prediction_call_count": 0,
        "private_resampling_executed": False,
        "new_candidate_point_count": 0,
        "error_margin_fabricated": False,
    }


def generate(run: Path, work: Path) -> dict:
    bundle = validate_inputs(run)
    source_audit = audit_no_random_fit_or_prediction(Path(__file__).resolve())
    capacity_audit = compute_capacity_audit(
        bundle["splits"], bundle["capacity"], bundle["protocol"]
    )
    indices = build_bootstrap_indices(bundle["eligible"], bundle["protocol"])
    scores = build_candidate_scores(bundle["shortlist"])
    report = build_robustness_report(
        bundle["protocol"], bundle["eligible"], capacity_audit, source_audit, bundle["hashes"]
    )
    output = work / "outputs"
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "bootstrap_indices.json", indices)
    scores.to_csv(output / "candidate_scores.csv", index=False)
    write_json(output / "robustness_report.json", report)
    validation = validate_outputs(output, bundle)
    write_json(work / "t14_validation.json", validation)
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
    require(ruff_exit == pytest_exit == generate_exit == 0, "T14_PREREQUISITE_COMMAND_FAILED")
    bundle = validate_inputs(run)
    protected_before = dict(bundle["hashes"])
    validation = validate_outputs(work / "outputs", bundle)
    robustness = run / "robustness"
    for name in FORMAL_OUTPUTS:
        require(not (robustness / name).exists(), f"FORMAL_OUTPUT_ALREADY_EXISTS:{name}")
        shutil.copyfile(work / "outputs" / name, robustness / name)
        require(
            sha256(robustness / name) == sha256(work / "outputs" / name), "PROMOTION_HASH_MISMATCH"
        )
    formal_validation = validate_outputs(robustness, bundle)
    bundle_after = validate_inputs(run)
    protected_after = dict(bundle_after["hashes"])
    require(protected_after == protected_before, "PROTECTED_INPUT_CHANGED")
    logs = work / "logs"
    commands = [
        command_record(
            "uv run --no-sync ruff check --no-cache "
            "scripts/run_optimization_p13_t14.py tests/ml/test_optimization_p13_t14.py",
            logs / "t14_ruff.log",
            ruff_exit,
        ),
        command_record(
            "uv run --no-sync pytest -q -p no:cacheprovider "
            f"--basetemp {work}/pytest_tmp tests/ml/test_optimization_p13_t14.py",
            logs / "t14_pytest.log",
            pytest_exit,
        ),
        command_record(
            "uv run --no-sync python scripts/run_optimization_p13_t14.py "
            f"--run {run} --work {work} --stage generate",
            logs / "t14_generate.log",
            generate_exit,
        ),
    ]
    finalize_log = logs / "t14_finalize.log"
    finalize_log.write_text(
        "T14 temporary outputs validated and promoted; formal outputs re-read: PASS\n"
    )
    finalize_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t14.py "
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
        "schema": "aln_optimization_p13_t14_artifact_manifest_v1",
        "task_id": "T14",
        "output_id": "O13",
        "gate": "G5",
        "attempt_id": "attempt_001",
        "run_id": "r01",
        "status": "REJECTED",
        "scientific_status": "REJECTED",
        "execution_status": "PASS",
        "software_validation": "PASS",
        "performance_pass": False,
        "reason": "no_eligible_models",
        "created_at": datetime.now(UTC).isoformat(),
        "command": finalize_command,
        "commands": commands,
        "environment": environment(),
        "inputs": [file_record(file_id, path) for file_id, path in bundle["paths"].items()],
        "outputs": [file_record(name, robustness / name) for name in FORMAL_OUTPUTS],
        "source_sha256": {
            "scripts/run_optimization_p13_t14.py": sha256(
                BACKEND / "scripts/run_optimization_p13_t14.py"
            ),
            "tests/ml/test_optimization_p13_t14.py": sha256(
                BACKEND / "tests/ml/test_optimization_p13_t14.py"
            ),
        },
        "generation_executed": False,
        "replicate_count": 0,
        "planned_replicates": 1000,
        "planned_seed": 20261027,
        "seed_consumed": False,
        "indices": [],
        "canonical_empty_indices_payload": CANONICAL_EMPTY_INDICES_PAYLOAD,
        "canonical_empty_indices_payload_sha256": CANONICAL_EMPTY_INDICES_SHA256,
        "refit_count": 0,
        "bootstrap_prediction_count": 0,
        "neighborhood_prediction_count": 0,
        "random_draw_count": 0,
        "failure_control_count": 17,
        "negative_control_excluded_count": 3,
        "new_candidate_count": 0,
        "null_reasons": [
            "no_eligible_models",
            "P3_AGGREGATION_ESTIMAND_MISMATCH",
            "E1_COORDINATE_OBSERVATIONS_MISSING",
            "HARD_CAPACITY_INSUFFICIENT_13_KEYS",
        ],
        "o09_selection_sha256_before": protected_before["O09_SELECTION"],
        "o09_selection_sha256_after": protected_after["O09_SELECTION"],
        "o12_shortlist_sha256_before": protected_before["O12_SHORTLIST"],
        "o12_shortlist_sha256_after": protected_after["O12_SHORTLIST"],
        "protected_before": protected_before,
        "protected_after": protected_after,
        "temporary_validation": validation,
        "formal_validation": formal_validation,
        "formal_outputs_match_temporary": True,
        "random_processes_executed": [],
        "t15_started": False,
        "exit_code": 0,
    }
    manifest_path = robustness / "t14_artifact_manifest.json"
    require(not manifest_path.exists(), "T14_MANIFEST_ALREADY_EXISTS")
    write_json(manifest_path, manifest)
    return {
        "status": "REJECTED",
        "gate": "G5",
        "execution_status": "PASS",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "replicate_count": 0,
        "random_draw_count": 0,
        "neighborhood_prediction_count": 0,
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
