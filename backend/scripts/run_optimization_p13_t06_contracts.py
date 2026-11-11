from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import runpy
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.ml.optimization_p13.contracts import (  # noqa: E402
    O02_SHA256,
    O05_CAPACITY_SHA256,
    O05_SPLITS_SHA256,
    build_schema_document,
    instantiate_p6c_control,
    load_protocol,
    p6c_control_spec,
    sha256_file,
    validate_schema_document,
)

SOURCE_RELATIVE = [
    "app/ml/optimization_p13/contracts.py",
    "app/ml/optimization_p13/evaluation.py",
    "scripts/run_optimization_p13_t06_contracts.py",
    "scripts/verify_optimization_p13_t04.py",
    "tests/ml/test_optimization_p13_contracts.py",
    "tests/ml/test_optimization_p13_evaluation.py",
    "tests/ml/test_optimization_p13_t04.py",
]
COMMAND_NAMES = ("prepare", "t06_pytest", "t04_pytest", "t04_verifier", "ruff")


def record(path: Path, file_id: str, usage: str, relative_to: Path | None = None) -> dict:
    result = {
        "file_id": file_id,
        "absolute_path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "usage": usage,
    }
    if relative_to is not None:
        result["path"] = str(path.relative_to(relative_to))
    return result


def load_context(run: Path) -> dict:
    protocol_path = run / "contracts/protocol.json"
    protocol = load_protocol(protocol_path)
    preflight_path = run / "control/preflight.json"
    preflight = json.loads(preflight_path.read_text())
    if sha256_file(preflight_path) != protocol["inputs"]["control/preflight.json"]:
        raise ValueError("O00 hash mismatch")
    o01_path = run / "audit/input_manifest.json"
    o01 = json.loads(o01_path.read_text())
    if sha256_file(o01_path) != protocol["inputs"]["audit/input_manifest.json"]:
        raise ValueError("O01 hash mismatch")
    o01_artifact_path = run / "audit/artifact_manifest.json"
    conflicts_path = run / "audit/version_conflicts.csv"
    if sha256_file(conflicts_path) != protocol["inputs"]["audit/version_conflicts.csv"]:
        raise ValueError("O01 version-conflict hash mismatch")
    o05_manifest_path = run / "evaluation/t04_artifact_manifest.json"
    expected_o05_manifest = protocol["inputs"]["evaluation/t04_artifact_manifest.json"]
    if sha256_file(o05_manifest_path) != expected_o05_manifest:
        raise ValueError("O05 manifest hash mismatch")
    split_path = run / "evaluation/splits.csv"
    capacity_path = run / "evaluation/capacity.csv"
    if sha256_file(split_path) != O05_SPLITS_SHA256:
        raise ValueError("O05 splits hash mismatch")
    if sha256_file(capacity_path) != O05_CAPACITY_SHA256:
        raise ValueError("O05 capacity hash mismatch")
    registry = {entry["file_id"]: entry for entry in o01["inputs"]}
    for file_id in ("P3_STRUCTURES", "SOURCE_OPTIMIZATION_DATA", "SOURCE_OPTIMIZATION_ABLATION"):
        entry = registry[file_id]
        if sha256_file(Path(entry["absolute_path"])) != entry["sha256"]:
            raise ValueError(f"Registered source changed: {file_id}")
    source_data = registry["SOURCE_OPTIMIZATION_DATA"]
    formula = protocol["data_contract"]["k2_formula"]
    if source_data["sha256"] != formula["sha256"]:
        raise ValueError("O02/P3 formula source mismatch")
    paths = {
        "O02_PROTOCOL": protocol_path,
        "O02_REVIEW": Path(preflight["paths"]["DOC"]) / "2026-10-27-surrogate-p13-protocol.md",
        "O00_PREFLIGHT": preflight_path,
        "O01_INPUT_MANIFEST": o01_path,
        "O01_ARTIFACT_MANIFEST": o01_artifact_path,
        "O01_VERSION_CONFLICTS": conflicts_path,
        "O05_SPLITS": split_path,
        "O05_CAPACITY": capacity_path,
        "O05_MANIFEST": o05_manifest_path,
        "P3_STRUCTURES": Path(registry["P3_STRUCTURES"]["absolute_path"]),
        "SOURCE_OPTIMIZATION_DATA": Path(source_data["absolute_path"]),
        "SOURCE_OPTIMIZATION_ABLATION": Path(
            registry["SOURCE_OPTIMIZATION_ABLATION"]["absolute_path"]
        ),
        "SOURCE_OPTIMIZATION_BOUNDARY_PILOT": Path(
            registry["SOURCE_OPTIMIZATION_BOUNDARY_PILOT"]["absolute_path"]
        ),
    }
    split_ids = {
        "structure_holdout": "P3_SPLIT_STRUCTURE_HOLDOUT",
        "area_edges": "P3_SPLIT_AREA_EDGES",
        "eg_upper": "P3_SPLIT_EG_UPPER",
        "fl_upper": "P3_SPLIT_FL_UPPER",
    }
    fold_ids = {
        "structure_holdout": "P6C_STRUCTURE_HOLDOUT_INNER_FOLDS",
        "area_edges": "P6C_AREA_EDGES_INNER_FOLDS",
        "eg_upper": "P6C_EG_UPPER_INNER_FOLDS",
        "fl_upper": "P6C_FL_UPPER_INNER_FOLDS",
    }
    for scope, file_id in split_ids.items():
        paths[f"P3_SPLIT_{scope.upper()}"] = Path(registry[file_id]["absolute_path"])
    for scope, file_id in fold_ids.items():
        paths[f"P6C_FOLD_{scope.upper()}"] = Path(registry[file_id]["absolute_path"])
    records = [
        record(path, file_id, "t06_frozen_input")
        for file_id, path in paths.items()
    ]
    return {
        "protocol": protocol,
        "preflight": preflight,
        "o01": o01,
        "registry": registry,
        "paths": paths,
        "records": records,
        "split_ids": split_ids,
        "fold_ids": fold_ids,
    }


def source_hashes() -> dict:
    return {relative: sha256_file(BACKEND / relative) for relative in SOURCE_RELATIVE}


def audit_o05(context: dict) -> dict:
    paths = context["paths"]
    splits = pd.read_csv(paths["O05_SPLITS"], keep_default_na=False)
    capacity = pd.read_csv(paths["O05_CAPACITY"], keep_default_na=False)
    if len(splits) != 30528 or len(capacity) != 112:
        raise ValueError("O05 row counts changed")
    valid = capacity[capacity.valid.map(lambda value: value is True or str(value) == "True")]
    if len(valid) != 53 or set(splits.unit_id) != set(valid.unit_id):
        raise ValueError("O05 valid unit coverage changed")
    if not splits.fold.eq(splits.unit_id).all():
        raise ValueError("O05 fold/unit mismatch")
    leakage_count = 0
    for _, rows in splits.groupby("unit_id"):
        fit_groups = set(rows.loc[rows.role.eq("fit"), "design_group_id"])
        for role in ("evaluation", "embargo", "outer_calibration", "outer_test", "outer_embargo"):
            leakage_count += bool(
                fit_groups & set(rows.loc[rows.role.eq(role), "design_group_id"])
            )
    if leakage_count:
        raise ValueError("O05 design-group leakage")
    baseline_group = context["protocol"]["baseline"]["design_group_id"]
    baseline = splits[splits.design_group_id.eq(baseline_group)]
    if not baseline.role.eq("baseline_only").all():
        raise ValueError("Baseline escaped baseline_only")
    if len(baseline) != 106:
        raise ValueError("Baseline row count changed")
    original_roles = {}
    for scope, file_id in context["split_ids"].items():
        path = Path(context["registry"][file_id]["absolute_path"])
        original = pd.read_csv(path)
        for row in original.itertuples(index=False):
            original_roles[(scope, row.structure_id)] = row.role
    for row in splits.itertuples(index=False):
        if original_roles[(row.scope, row.structure_id)] != row.original_role:
            raise ValueError("Original scope role mismatch")
        expected_split_hash = context["registry"][context["split_ids"][row.scope]]["sha256"]
        if row.source_split_sha256 != expected_split_hash:
            raise ValueError("Original split source hash mismatch")
        if row.source_structure_sha256 != context["registry"]["P3_STRUCTURES"]["sha256"]:
            raise ValueError("Original structure source hash mismatch")
    p6c_spec = p6c_control_spec(context["protocol"])
    p6c_model = instantiate_p6c_control(p6c_spec)
    if p6c_model.recipe != p6c_spec.recipe or p6c_model.target != p6c_spec.target:
        raise ValueError("P6C control instantiation mismatch")
    return {
        "split_rows": len(splits),
        "capacity_rows": len(capacity),
        "valid_units": len(valid),
        "invalid_units": len(capacity) - len(valid),
        "fit_evaluation_or_excluded_group_leak_count": leakage_count,
        "baseline_rows": len(baseline),
        "baseline_role": "baseline_only",
        "original_scope_roles_verified": True,
        "o05_source_hashes_verified": True,
        "p6c_recipe": p6c_spec.recipe,
        "p6c_control_instantiated_without_fit": True,
        "real_fit_executed": False,
        "search_executed": False,
    }


def command_specs(run: Path, attempt: str) -> dict:
    work = run / "verification/t06_work" / attempt
    return {
        "prepare": (
            "uv run --no-sync python scripts/run_optimization_p13_t06_contracts.py "
            f"--run {run} --attempt {attempt} --stage prepare"
        ),
        "t06_pytest": (
            "uv run --no-sync pytest -q -p no:cacheprovider "
            f"--basetemp {work}/pytest_t06 "
            "tests/ml/test_optimization_p13_contracts.py "
            "tests/ml/test_optimization_p13_evaluation.py"
        ),
        "t04_pytest": (
            "uv run --no-sync pytest -q -p no:cacheprovider "
            f"--basetemp {work}/pytest_t04 tests/ml/test_optimization_p13_t04.py"
        ),
        "t04_verifier": (
            "uv run --no-sync python scripts/verify_optimization_p13_t04.py "
            f"--run {run} --attempt t06_regression"
        ),
        "ruff": (
            "uv run --no-sync ruff check --no-cache "
            "app/ml/optimization_p13/contracts.py "
            "app/ml/optimization_p13/evaluation.py "
            "scripts/run_optimization_p13_t06_contracts.py "
            "scripts/verify_optimization_p13_t04.py "
            "tests/ml/test_optimization_p13_contracts.py "
            "tests/ml/test_optimization_p13_evaluation.py "
            "tests/ml/test_optimization_p13_t04.py"
        ),
    }


def prepare(run: Path, attempt: str) -> dict:
    work = run / "verification/t06_work" / attempt
    work.mkdir(parents=True, exist_ok=True)
    formal_schema = run / "contracts/schema.json"
    for path in (
        formal_schema,
        run / "verification/contract_report.json",
        run / "verification/t06_artifact_manifest.json",
        work / "schema.json",
        work / "contract_report.json",
        work / "prepared.json",
    ):
        if path.exists():
            raise FileExistsError(path)
    context = load_context(run)
    schema = build_schema_document(context["protocol"])
    validate_schema_document(schema, context["protocol"])
    checks = audit_o05(context)
    temporary_schema = work / "schema.json"
    temporary_schema.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    loaded_schema = json.loads(temporary_schema.read_text())
    validate_schema_document(loaded_schema, context["protocol"])
    preliminary = {
        "schema": "aln_optimization_p13_contract_report_v1",
        "task_id": "T06",
        "attempt_id": attempt,
        "status": "PREPARED",
        "o02_sha256": O02_SHA256,
        "schema_sha256": sha256_file(temporary_schema),
        "g2_self_checks": checks,
        "change_request": None,
        "real_fit_executed": False,
        "search_executed": False,
    }
    (work / "contract_report.json").write_text(
        json.dumps(preliminary, indent=2, ensure_ascii=False) + "\n"
    )
    prepared = {
        "schema": "aln_optimization_p13_t06_prepared_v1",
        "task_id": "T06",
        "attempt_id": attempt,
        "status": "PREPARED",
        "created_at": datetime.now(UTC).isoformat(),
        "input_sha256": {row["file_id"]: row["sha256"] for row in context["records"]},
        "source_sha256": source_hashes(),
        "schema_sha256": sha256_file(temporary_schema),
        "preliminary_report_sha256": sha256_file(work / "contract_report.json"),
        "o05_sha256": {
            "evaluation/splits.csv": O05_SPLITS_SHA256,
            "evaluation/capacity.csv": O05_CAPACITY_SHA256,
        },
        "g2_self_checks": checks,
    }
    (work / "prepared.json").write_text(json.dumps(prepared, indent=2) + "\n")
    promote = work / ".schema.promote"
    promote.write_bytes(temporary_schema.read_bytes())
    os.replace(promote, formal_schema)
    return {
        "status": "PREPARED",
        "schema": str(formal_schema),
        "schema_sha256": prepared["schema_sha256"],
        "g2_self_checks": checks,
    }


def environment() -> dict:
    packages = {
        name: importlib.metadata.version(name)
        for name in ("numpy", "pandas", "scikit-learn", "pytest", "ruff")
    }
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "uv": subprocess.run(
            ["uv", "--version"], check=True, capture_output=True, text=True
        ).stdout.strip(),
        "cpu_count": os.cpu_count(),
        "numeric_threads": {
            name: os.environ.get(name, "1")
            for name in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
        "python_dont_write_bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE", "1"),
    }


def passed_count(log_path: Path) -> int:
    matches = re.findall(r"(\d+) passed", log_path.read_text())
    if not matches:
        raise ValueError(f"No pytest pass count in {log_path}")
    return int(matches[-1])


def finalize(run: Path, attempt: str) -> dict:
    work = run / "verification/t06_work" / attempt
    context = load_context(run)
    prepared = json.loads((work / "prepared.json").read_text())
    if prepared["input_sha256"] != {
        row["file_id"]: row["sha256"] for row in context["records"]
    }:
        raise ValueError("Input drift before T06 finalize")
    if prepared["source_sha256"] != source_hashes():
        raise ValueError("Source drift before T06 finalize")
    schema_path = run / "contracts/schema.json"
    schema = json.loads(schema_path.read_text())
    validate_schema_document(schema, context["protocol"])
    if sha256_file(schema_path) != prepared["schema_sha256"]:
        raise ValueError("Formal schema differs from validated temporary schema")
    specs = command_specs(run, attempt)
    commands = []
    for name in COMMAND_NAMES:
        log = work / "logs" / f"{name}.log"
        code = work / "logs" / f"{name}.exit_code"
        if not log.exists() or not code.exists():
            raise FileNotFoundError(f"Missing evidence for {name}")
        exit_code = int(code.read_text().strip())
        commands.append(
            {
                "name": name,
                "command": specs[name],
                "cwd": str(BACKEND),
                "exit_code": exit_code,
                "log": str(log),
                "exit_code_evidence": str(code),
            }
        )
    if any(command["exit_code"] != 0 for command in commands):
        raise ValueError("T06 cannot finalize with a failed command")
    t04_report_path = run / "evaluation/t04_work/t06_regression/independence_report.json"
    t04_report = json.loads(t04_report_path.read_text())
    if t04_report["status"] != "PASS":
        raise ValueError("T04 independent regression failed")
    for name, expected in prepared["o05_sha256"].items():
        if t04_report["output_sha256"][name] != expected or sha256_file(run / name) != expected:
            raise ValueError("O05 scientific bytes changed")
    t06_count = passed_count(work / "logs/t06_pytest.log")
    t04_count = passed_count(work / "logs/t04_pytest.log")
    contract_report = {
        "schema": "aln_optimization_p13_contract_report_v1",
        "task_id": "T06",
        "attempt_id": attempt,
        "status": "PASS",
        "created_at": datetime.now(UTC).isoformat(),
        "o02_sha256": O02_SHA256,
        "schema_sha256": prepared["schema_sha256"],
        "g2": {
            "self_check": "PASS",
            "a2_a3_crosscheck": "PENDING",
            "t07_t08_start_allowed": False,
        },
        "g2_self_checks": prepared["g2_self_checks"],
        "schema_contract_checks": {
            "versioned_interface": True,
            "unknown_fields_rejected": True,
            "null_reason_required": True,
            "measured_values_evaluation_only": True,
            "inference_geometry_only": True,
            "raw_simulation_injection_rejected": True,
            "k2_percent_number_and_pp": True,
            "m2_aggregation_estimand_match": False,
            "m2_qualified_model_set_eligible": False,
            "p6c_same_rows_and_fixed_recipe": True,
            "thresholds_and_tolerances_match_o02": True,
        },
        "test_results": {
            "t06_pytest": {"passed": t06_count, "failed": 0},
            "t04_regression_pytest": {"passed": t04_count, "failed": 0},
            "t04_independent_verifier": {
                "status": t04_report["status"],
                "report": str(t04_report_path),
                "report_sha256": sha256_file(t04_report_path),
                "checks": t04_report["checks"],
            },
            "ruff": {"status": "PASS"},
        },
        "commands": commands,
        "o05_protection": {
            "before_sha256": prepared["o05_sha256"],
            "after_sha256": prepared["o05_sha256"],
            "bytes_unchanged": True,
        },
        "scientific_thresholds_changed": False,
        "change_request": None,
        "real_fit_executed": False,
        "search_executed": False,
        "a2_a3_review_points": [
            (
                "A2 must implement M1 through the versioned geometry-only interface "
                "and preserve null reasons."
            ),
            (
                "A3 must keep frequency supervision separate from inference and keep "
                "M2 aggregation_estimand_match=false and qualified=false."
            ),
            "A2/A3 must use identical O05 evaluation rows for candidate and P6C predictions.",
            (
                "Zero P6C boundary control yields evidence_insufficient, "
                "never a passing 10% improvement."
            ),
            "Exact nonzero threshold contact is numerical_critical, not a pass.",
            "T07/T08 remain blocked until A2/A3 crosscheck O06.",
        ],
    }
    report_path = work / "contract_report.json"
    report_path.write_text(json.dumps(contract_report, indent=2, ensure_ascii=False) + "\n")
    loaded_report = json.loads(report_path.read_text())
    if loaded_report["status"] != "PASS" or loaded_report["g2"]["t07_t08_start_allowed"]:
        raise ValueError("Invalid contract report gate")
    formal_report = run / "verification/contract_report.json"
    promote = work / ".contract_report.promote"
    promote.write_bytes(report_path.read_bytes())
    os.replace(promote, formal_report)
    outputs = [
        record(schema_path, "O06_SCHEMA", "formal_o06", run),
        record(formal_report, "O06_CONTRACT_REPORT", "formal_o06", run),
    ]
    evidence_paths = [work / "prepared.json", t04_report_path]
    for name in COMMAND_NAMES:
        evidence_paths.extend(
            [work / "logs" / f"{name}.log", work / "logs" / f"{name}.exit_code"]
        )
    evidence = [
        record(path, f"T06_EVIDENCE_{index:02d}", "t06_work_evidence", run)
        for index, path in enumerate(evidence_paths, start=1)
    ]
    finalize_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t06_contracts.py "
        f"--run {run} --attempt {attempt} --stage finalize"
    )
    manifest = {
        "schema": "aln_optimization_p13_artifact_manifest_v1",
        "task_id": "T06",
        "attempt_id": attempt,
        "run_id": "r01",
        "status": "PASS",
        "created_at": datetime.now(UTC).isoformat(),
        "inputs": context["records"],
        "outputs": outputs,
        "evidence": evidence,
        "source_sha256": source_hashes(),
        "command": finalize_command,
        "commands": commands,
        "exit_code": 0,
        "environment": environment(),
        "o02_sha256": O02_SHA256,
        "o05_sha256": prepared["o05_sha256"],
        "validation": {
            "temporary_schema_validated_before_promotion": True,
            "temporary_report_validated_before_promotion": True,
            "formal_outputs_match_temporary": True,
            "input_hash_mismatch_count": 0,
            "source_drift_count": 0,
            "scientific_csv_hash_mismatch_count": 0,
            "all_recorded_commands_zero_exit": True,
            "real_fit_executed": False,
            "search_executed": False,
            "scientific_thresholds_changed": False,
        },
        "g2": contract_report["g2"],
        "change_request": None,
        "random_processes": [],
        "notes": [
            "O06 is a self-checked contract artifact. A2/A3 crosscheck is required before T07/T08.",
            "O02 and O05 scientific files remain byte-identical.",
            (
                "No real model fit, configuration selection, retrospective evaluation, "
                "or search was run."
            ),
        ],
    }
    temporary_manifest = work / "t06_artifact_manifest.json"
    temporary_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    loaded_manifest = json.loads(temporary_manifest.read_text())
    if loaded_manifest["task_id"] != "T06" or loaded_manifest["status"] != "PASS":
        raise ValueError("Invalid T06 manifest")
    for output in loaded_manifest["outputs"]:
        if sha256_file(run / output["path"]) != output["sha256"]:
            raise ValueError("Manifest output hash mismatch")
    os.replace(temporary_manifest, run / "verification/t06_artifact_manifest.json")
    return {
        "status": "PASS",
        "schema": str(schema_path),
        "contract_report": str(formal_report),
        "manifest": str(run / "verification/t06_artifact_manifest.json"),
        "t06_tests_passed": t06_count,
        "t04_tests_passed": t04_count,
        "t04_independent_status": t04_report["status"],
        "t07_t08_start_allowed": False,
    }


def independent_recheck(run: Path, attempt: str) -> dict:
    work = run / "verification/t06_work" / attempt / "independent_recheck"
    work.mkdir(parents=True, exist_ok=False)
    report_path = run / "verification/contract_report.json"
    manifest_path = run / "verification/t06_artifact_manifest.json"
    (work / "contract_report_before.json").write_bytes(report_path.read_bytes())
    (work / "t06_manifest_before.json").write_bytes(manifest_path.read_bytes())
    if sha256_file(run / "evaluation/splits.csv") != O05_SPLITS_SHA256:
        raise ValueError("O05 splits changed before independent recheck")
    if sha256_file(run / "evaluation/capacity.csv") != O05_CAPACITY_SHA256:
        raise ValueError("O05 capacity changed before independent recheck")
    a2_formal_path = run / "m1/contract_crosscheck.json"
    a3_formal_path = run / "m2/contract_crosscheck.json"
    a2_formal_hash = sha256_file(a2_formal_path)
    a3_formal_hash = sha256_file(a3_formal_path)
    a2_log = work / "a2_synthetic.log"
    with a2_log.open("w") as stream, redirect_stdout(stream):
        a2_module = runpy.run_path(str(run / "m1/contract_crosscheck.py"))
        a2_checks = a2_module["compute_checks"](run)
    a2_failed = [row["check_id"] for row in a2_checks if row["status"] == "FAILED"]
    a2_report = {
        "status": "FAILED" if a2_failed else "PASS",
        "check_count": len(a2_checks),
        "failed_check_ids": a2_failed,
        "checks": a2_checks,
        "entrypoint": "m1/contract_crosscheck.py::compute_checks",
        "formal_report_modified": False,
    }
    a2_report_path = work / "a2_synthetic.json"
    a2_report_path.write_text(json.dumps(a2_report, indent=2, ensure_ascii=False) + "\n")
    if a2_failed:
        raise ValueError(f"A2 independent synthetic recheck failed: {a2_failed}")
    a3_log = work / "a3_synthetic.log"
    with a3_log.open("w") as stream, redirect_stdout(stream):
        a3_module = runpy.run_path(str(run / "m2/contract_crosscheck.py"))
        a3_module["generate"].__globals__["REPORT_PATH"] = work / "a3_contract_crosscheck.json"
        a3_module["generate"]()
    a3_report_path = work / "a3_contract_crosscheck.json"
    a3_report = json.loads(a3_report_path.read_text())
    if a3_report["failed_check_count"]:
        raise ValueError(f"A3 independent synthetic recheck failed: {a3_report['issues']}")
    if sha256_file(a2_formal_path) != a2_formal_hash:
        raise ValueError("A2 formal crosscheck report was modified")
    if sha256_file(a3_formal_path) != a3_formal_hash:
        raise ValueError("A3 formal crosscheck report was modified")
    recheck_report_path = run / "verification/t06_synthetic_recheck.json"
    recheck_report = {
        "schema": "aln_optimization_p13_t06_synthetic_recheck_v1",
        "task_id": "T06",
        "attempt_id": attempt,
        "status": "PASS",
        "created_at": datetime.now(UTC).isoformat(),
        "a2": {
            "status": a2_report["status"],
            "check_count": len(a2_checks),
            "failed_check_count": 0,
            "report": str(a2_report_path),
            "report_sha256": sha256_file(a2_report_path),
            "log": str(a2_log),
        },
        "a3": {
            "status": a3_report["status"],
            "check_count": a3_report["independent_check_count"],
            "failed_check_count": 0,
            "report": str(a3_report_path),
            "report_sha256": sha256_file(a3_report_path),
            "log": str(a3_log),
        },
        "a2_a3_formal_reports_modified": False,
        "a2_a3_crosscheck": "PENDING",
        "t07_t08_start_allowed": False,
        "model_training_performed": False,
    }
    temporary_recheck = work / "t06_synthetic_recheck.json"
    temporary_recheck.write_text(json.dumps(recheck_report, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary_recheck, recheck_report_path)
    contract_report = json.loads(report_path.read_text())
    contract_report["independent_synthetic_recheck"] = {
        "status": "PASS",
        "path": str(recheck_report_path),
        "sha256": sha256_file(recheck_report_path),
        "a2_a3_formal_reports_modified": False,
    }
    temporary_report = work / "contract_report.json"
    temporary_report.write_text(json.dumps(contract_report, indent=2, ensure_ascii=False) + "\n")
    if contract_report["g2"]["a2_a3_crosscheck"] != "PENDING":
        raise ValueError("Formal A2/A3 crosscheck must remain pending")
    os.replace(temporary_report, report_path)
    manifest = json.loads(manifest_path.read_text())
    for output in manifest["outputs"]:
        if output["file_id"] == "O06_CONTRACT_REPORT":
            output.update(
                {
                    "bytes": report_path.stat().st_size,
                    "sha256": sha256_file(report_path),
                }
            )
    recheck_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t06_contracts.py "
        f"--run {run} --attempt {attempt} --stage independent_recheck"
    )
    manifest["commands"].extend(
        [
            {
                "name": "a2_independent_synthetic_recheck",
                "command": recheck_command,
                "entrypoint": "m1/contract_crosscheck.py::compute_checks",
                "cwd": str(BACKEND),
                "exit_code": 0,
                "log": str(a2_log),
            },
            {
                "name": "a3_independent_synthetic_recheck",
                "command": recheck_command,
                "entrypoint": "m2/contract_crosscheck.py::generate with REPORT_PATH redirected",
                "cwd": str(BACKEND),
                "exit_code": 0,
                "log": str(a3_log),
            },
        ]
    )
    evidence_paths = [
        recheck_report_path,
        a2_report_path,
        a3_report_path,
        a2_log,
        a3_log,
        work / "contract_report_before.json",
        work / "t06_manifest_before.json",
    ]
    start = len(manifest["evidence"]) + 1
    manifest["evidence"].extend(
        [
            record(path, f"T06_RECHECK_EVIDENCE_{index:02d}", "t06_recheck_evidence", run)
            for index, path in enumerate(evidence_paths, start=start)
        ]
    )
    manifest["independent_synthetic_recheck"] = {
        "status": "PASS",
        "path": str(recheck_report_path),
        "sha256": sha256_file(recheck_report_path),
        "a2_a3_formal_reports_modified": False,
    }
    manifest["validation"].update(
        {
            "independent_synthetic_recheck_status": "PASS",
            "a2_a3_formal_reports_modified": False,
            "scientific_csv_hash_mismatch_count": 0,
        }
    )
    manifest["command"] = recheck_command
    manifest["updated_at"] = datetime.now(UTC).isoformat()
    temporary_manifest = work / "t06_artifact_manifest.json"
    temporary_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    loaded = json.loads(temporary_manifest.read_text())
    for entry in loaded["inputs"]:
        if sha256_file(Path(entry["absolute_path"])) != entry["sha256"]:
            raise ValueError(f"Input drift during independent recheck: {entry['file_id']}")
    for entry in loaded["outputs"] + loaded["evidence"]:
        if sha256_file(run / entry["path"]) != entry["sha256"]:
            raise ValueError(f"Evidence drift during independent recheck: {entry['path']}")
    for relative, expected in loaded["source_sha256"].items():
        if sha256_file(BACKEND / relative) != expected:
            raise ValueError(f"Source drift during independent recheck: {relative}")
    os.replace(temporary_manifest, manifest_path)
    return {
        "status": "PASS",
        "a2_checks": len(a2_checks),
        "a3_checks": a3_report["independent_check_count"],
        "recheck_report": str(recheck_report_path),
        "manifest": str(manifest_path),
        "a2_a3_crosscheck": "PENDING",
        "t07_t08_start_allowed": False,
    }


def _verify_crosscheck_manifest(
    run: Path,
    role: str,
    expected_report_hash: str,
    expected_checks: int,
) -> dict:
    report_path = run / role / "contract_crosscheck.json"
    manifest_path = run / role / "contract_crosscheck_artifact_manifest.json"
    if sha256_file(report_path) != expected_report_hash:
        raise ValueError(f"{role} report hash mismatch")
    report = json.loads(report_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    if manifest["status"] != "PASS" or manifest["exit_code"] != 0:
        raise ValueError(f"{role} manifest is not PASS")
    if role == "m1":
        if report["a2_crosscheck"] != "PASS" or len(report["checks"]) != expected_checks:
            raise ValueError("A2 crosscheck is not complete PASS")
        if report["failed_checks"] or report["code_issue_count"] != 0:
            raise ValueError("A2 crosscheck still has failures")
        if any(not row["matches"] for row in report["a4_source_receipts"]):
            raise ValueError("A2 A4 source receipt mismatch")
    else:
        if report["status"] != "PASS" or report["independent_check_count"] != expected_checks:
            raise ValueError("A3 crosscheck is not complete PASS")
        if report["failed_check_count"] != 0 or report["issues"]:
            raise ValueError("A3 crosscheck still has failures")
    for command in manifest["commands"]:
        if command["exit_code"] != 0:
            raise ValueError(f"{role} manifest has a failed command")
    for entry in manifest["inputs"]:
        if "matches" in entry and entry["matches"] is not True:
            raise ValueError(f"{role} input receipt mismatch: {entry.get('file_id')}")
        path = Path(entry["absolute_path"])
        expected = entry.get("sha256", entry.get("expected_sha256"))
        if expected is not None and sha256_file(path) != expected:
            raise ValueError(f"{role} input hash mismatch: {path}")
    for entry in manifest["outputs"]:
        path = Path(entry.get("absolute_path") or run / entry["path"])
        if sha256_file(path) != entry["sha256"]:
            raise ValueError(f"{role} output hash mismatch: {path}")
    for source, expected in manifest["source_sha256"].items():
        path = Path(source)
        if not path.is_absolute():
            path = BACKEND / path
        if sha256_file(path) != expected:
            raise ValueError(f"{role} source hash mismatch: {path}")
    return {
        "role": role,
        "status": "PASS",
        "check_count": expected_checks,
        "report": str(report_path),
        "report_sha256": expected_report_hash,
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_inputs_outputs_sources_verified": True,
    }


def g2_signoff(run: Path, attempt: str) -> dict:
    work = run / "verification/t06_work" / attempt
    work.mkdir(parents=True, exist_ok=False)
    report_path = run / "verification/contract_report.json"
    manifest_path = run / "verification/t06_artifact_manifest.json"
    schema_path = run / "contracts/schema.json"
    (work / "contract_report_before.json").write_bytes(report_path.read_bytes())
    (work / "t06_manifest_before.json").write_bytes(manifest_path.read_bytes())
    (work / "schema_before.json").write_bytes(schema_path.read_bytes())
    expected_schema = "767b853871cf29e528cacf414cfd9596ef89c283c5be50c7da1a30a860c257d1"
    if sha256_file(schema_path) != expected_schema:
        raise ValueError("Schema changed before G2 signoff")
    if sha256_file(run / "contracts/protocol.json") != O02_SHA256:
        raise ValueError("O02 changed before G2 signoff")
    if sha256_file(run / "evaluation/splits.csv") != O05_SPLITS_SHA256:
        raise ValueError("O05 splits changed before G2 signoff")
    if sha256_file(run / "evaluation/capacity.csv") != O05_CAPACITY_SHA256:
        raise ValueError("O05 capacity changed before G2 signoff")
    before_report = json.loads(report_path.read_text())
    if before_report["test_results"]["t06_pytest"] != {"passed": 48, "failed": 0}:
        raise ValueError("T06 test evidence is not 48/0")
    if before_report["test_results"]["t04_regression_pytest"] != {"passed": 10, "failed": 0}:
        raise ValueError("T04 test evidence is not 10/0")
    if before_report["independent_synthetic_recheck"]["status"] != "PASS":
        raise ValueError("Synthetic recheck evidence is not PASS")
    a2 = _verify_crosscheck_manifest(
        run,
        "m1",
        "c1967974cf3c68a5a47f75ddca038783d448bcdc3e7f056b4cc1cd0b326e39e9",
        31,
    )
    a3 = _verify_crosscheck_manifest(
        run,
        "m2",
        "7692bf1a7e017bdf16ff52f9e90fef5b38f079246dfe40b2f961f16d2d4ac37f",
        42,
    )
    now = datetime.now(UTC).isoformat()
    signoff_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t06_contracts.py "
        f"--run {run} --attempt {attempt} --stage g2_signoff"
    )
    command_log = work / "logs/g2_signoff.log"
    command_log.parent.mkdir(parents=True, exist_ok=True)
    contract_report = dict(before_report)
    contract_report.update(
        {
            "attempt_id": attempt,
            "created_at": now,
            "g2": {
                "self_check": "PASS",
                "a2_a3_crosscheck": "PASS",
                "t07_t08_start_allowed": True,
            },
            "g2_signoff": {
                "a2": a2,
                "a3": a3,
                "schema_unchanged": True,
                "schema_sha256": sha256_file(schema_path),
                "t07_t08_started_in_this_signoff": False,
            },
            "real_fit_executed": False,
            "search_executed": False,
        }
    )
    contract_report["a2_a3_review_points"] = [
        *before_report["a2_a3_review_points"][:-1],
        "G2 is signed; T07/T08 are allowed by the gate but were not started in this signoff.",
    ]
    contract_report["commands"].append(
        {
            "name": "g2_signoff",
            "command": signoff_command,
            "cwd": str(BACKEND),
            "exit_code": 0,
            "log": str(command_log),
        }
    )
    temporary_report = work / "contract_report.json"
    temporary_report.write_text(json.dumps(contract_report, indent=2, ensure_ascii=False) + "\n")
    loaded_report = json.loads(temporary_report.read_text())
    if loaded_report["g2"] != {
        "self_check": "PASS",
        "a2_a3_crosscheck": "PASS",
        "t07_t08_start_allowed": True,
    }:
        raise ValueError("Invalid final G2 state")
    if loaded_report["real_fit_executed"] or loaded_report["search_executed"]:
        raise ValueError("G2 signoff must not execute fit or search")
    os.replace(temporary_report, report_path)
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        {
            "attempt_id": attempt,
            "created_at": now,
            "updated_at": now,
            "status": "PASS",
            "g2": contract_report["g2"],
            "command": signoff_command,
            "exit_code": 0,
            "environment": environment(),
        }
    )
    manifest["inputs"].extend(
        [
            record(Path(a2["report"]), "A2_G2_CROSSCHECK", "g2_signoff_input", run),
            record(Path(a2["manifest"]), "A2_G2_MANIFEST", "g2_signoff_input", run),
            record(Path(a3["report"]), "A3_G2_CROSSCHECK", "g2_signoff_input", run),
            record(Path(a3["manifest"]), "A3_G2_MANIFEST", "g2_signoff_input", run),
        ]
    )
    for output in manifest["outputs"]:
        if output["file_id"] == "O06_CONTRACT_REPORT":
            output.update(
                {
                    "bytes": report_path.stat().st_size,
                    "sha256": sha256_file(report_path),
                }
            )
    manifest["evidence"].extend(
        [
            record(
                work / "contract_report_before.json",
                "T06_G2_EVIDENCE_20",
                "pre_signoff_snapshot",
                run,
            ),
            record(
                work / "t06_manifest_before.json",
                "T06_G2_EVIDENCE_21",
                "pre_signoff_snapshot",
                run,
            ),
            record(
                work / "schema_before.json",
                "T06_G2_EVIDENCE_22",
                "schema_unchanged_snapshot",
                run,
            ),
        ]
    )
    previous_source = manifest["source_sha256"]["scripts/run_optimization_p13_t06_contracts.py"]
    current_source = source_hashes()["scripts/run_optimization_p13_t06_contracts.py"]
    manifest["source_sha256"] = source_hashes()
    manifest.setdefault("source_receipt_updates", []).append(
        {
            "path": "scripts/run_optimization_p13_t06_contracts.py",
            "previous_sha256": previous_source,
            "current_sha256": current_source,
            "reason": (
                "Add only the G2 signoff stage; schema and scientific protocol are unchanged."
            ),
        }
    )
    manifest["commands"].append(
        {
            "name": "g2_signoff",
            "command": signoff_command,
            "cwd": str(BACKEND),
            "exit_code": 0,
            "log": str(command_log),
        }
    )
    manifest["g2_signoff"] = {
        "a2": a2,
        "a3": a3,
        "schema_unchanged": True,
        "t07_t08_started_in_this_signoff": False,
    }
    manifest["validation"].update(
        {
            "temporary_report_validated_before_promotion": True,
            "temporary_manifest_validated_before_promotion": True,
            "schema_unchanged": True,
            "a2_a3_crosscheck_manifests_verified": True,
            "input_hash_mismatch_count": 0,
            "source_drift_count": 0,
            "scientific_csv_hash_mismatch_count": 0,
            "real_fit_executed": False,
            "search_executed": False,
        }
    )
    temporary_manifest = work / "t06_artifact_manifest.json"
    temporary_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    loaded = json.loads(temporary_manifest.read_text())
    for entry in loaded["inputs"]:
        if sha256_file(Path(entry["absolute_path"])) != entry["sha256"]:
            raise ValueError(f"Input drift during G2 signoff: {entry['file_id']}")
    for entry in loaded["outputs"] + loaded["evidence"]:
        if sha256_file(run / entry["path"]) != entry["sha256"]:
            raise ValueError(f"Evidence drift during G2 signoff: {entry['path']}")
    for relative, expected in loaded["source_sha256"].items():
        if sha256_file(BACKEND / relative) != expected:
            raise ValueError(f"Source drift during G2 signoff: {relative}")
    if loaded["g2"]["t07_t08_start_allowed"] is not True:
        raise ValueError("Final G2 gate was not opened")
    os.replace(temporary_manifest, manifest_path)
    return {
        "status": "PASS",
        "attempt_id": attempt,
        "schema": str(schema_path),
        "schema_sha256": sha256_file(schema_path),
        "contract_report": str(report_path),
        "contract_report_sha256": sha256_file(report_path),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "g2": loaded["g2"],
        "t07_t08_started_in_this_signoff": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--attempt", default="attempt_004")
    parser.add_argument(
        "--stage",
        choices=("prepare", "finalize", "independent_recheck", "g2_signoff"),
        required=True,
    )
    args = parser.parse_args()
    if args.stage == "prepare":
        result = prepare(args.run, args.attempt)
    elif args.stage == "finalize":
        result = finalize(args.run, args.attempt)
    elif args.stage == "independent_recheck":
        result = independent_recheck(args.run, args.attempt)
    else:
        result = g2_signoff(args.run, args.attempt)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
