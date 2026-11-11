from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.ml.optimization_p13.evaluation import (  # noqa: E402
    CAPACITY_COLUMNS,
    GEOMETRY,
    HARD_MIN_GROUPS,
    HARD_MIN_UNITS,
    IDENTITY,
    MIN_FIT_GROUPS,
    MIN_FIT_LEVELS,
    SCOPES,
    SPLIT_COLUMNS,
    aggregate_hard_capacity,
    build_scope_splits,
    pooled_descriptive_capacity,
    validate_outputs,
    write_csv_outputs,
)

SCOPE_INPUTS = {
    "structure_holdout": {
        "split": "P3_SPLIT_STRUCTURE_HOLDOUT",
        "fold": "P6C_STRUCTURE_HOLDOUT_INNER_FOLDS",
        "p6d_capacity": "P6D_STRUCTURE_HOLDOUT_CAPACITY",
        "p6d_manifest": "P6D_STRUCTURE_HOLDOUT_STRESS_MANIFEST",
    },
    "area_edges": {
        "split": "P3_SPLIT_AREA_EDGES",
        "fold": "P6C_AREA_EDGES_INNER_FOLDS",
        "p6d_capacity": "P6D_AREA_EDGES_CAPACITY",
        "p6d_manifest": "P6D_AREA_EDGES_STRESS_MANIFEST",
    },
    "eg_upper": {
        "split": "P3_SPLIT_EG_UPPER",
        "fold": "P6C_EG_UPPER_INNER_FOLDS",
        "p6d_capacity": "P6D_EG_UPPER_CAPACITY",
        "p6d_manifest": "P6D_EG_UPPER_STRESS_MANIFEST",
    },
    "fl_upper": {
        "split": "P3_SPLIT_FL_UPPER",
        "fold": "P6C_FL_UPPER_INNER_FOLDS",
        "p6d_capacity": "P6D_FL_UPPER_CAPACITY",
        "p6d_manifest": "P6D_FL_UPPER_STRESS_MANIFEST",
    },
}
REVIEW_ONLY_IDS = {
    "DOC_2026_10_15_SURROGATE_P3_RESULTS",
    "DOC_2026_10_22_SURROGATE_P6C_RESULTS",
    "DOC_2026_10_22_SURROGATE_P6D_RESULTS",
    "P6C_SELECTION",
    "P6D_SELECTION",
}
INPUT_IDS = [
    "O00_PREFLIGHT",
    "O00_ARTIFACT_MANIFEST",
    "DOC_2026_10_27_SURROGATE_P13_AGENT_EXECUTION_ORDER",
    "DOC_2026_10_27_SURROGATE_NO_FAB_K2_PLUS_ONE_PLAN",
    "DOC_2026_10_15_SURROGATE_P3_PROTOCOL",
    "DOC_2026_10_15_SURROGATE_P3_RESULTS",
    "DOC_2026_10_22_SURROGATE_P6C_PROTOCOL",
    "DOC_2026_10_22_SURROGATE_P6C_RESULTS",
    "DOC_2026_10_22_SURROGATE_P6D_PROTOCOL",
    "DOC_2026_10_22_SURROGATE_P6D_RESULTS",
    "P3_DELIVERY_MANIFEST",
    "P3_DATA_OUTPUTS_MANIFEST",
    "P3_EVALUATION_OUTPUTS_MANIFEST",
    "P3_STRUCTURES",
    "P3_DATA_AUDIT",
    "P3_INPUT_PROVENANCE",
    "P3_MODEL_METADATA",
    "P3_EVALUATION_PROTOCOL",
    *(value["split"] for value in SCOPE_INPUTS.values()),
    "P6C_DELIVERY_MANIFEST",
    "P6C_EXPERIMENT_OUTPUTS_MANIFEST",
    "P6C_FROZEN_INPUTS",
    "P6C_SELECTION",
    "P6C_FIT_RECEIPTS",
    *(value["fold"] for value in SCOPE_INPUTS.values()),
    "P6D_DELIVERY_MANIFEST",
    "P6D_EXPERIMENT_OUTPUTS_MANIFEST",
    "P6D_FROZEN_INPUTS",
    "P6D_SELECTION",
    *(value["p6d_capacity"] for value in SCOPE_INPUTS.values()),
    *(value["p6d_manifest"] for value in SCOPE_INPUTS.values()),
    "SOURCE_OPTIMIZATION_BASELINES",
    "SOURCE_OPTIMIZATION_BOUNDARY_PILOT",
    "SOURCE_OPTIMIZATION_BOUNDARY_SELECTION",
    "SOURCE_RUN_OPTIMIZATION_P6C",
]
SOURCE_PATHS = [
    BACKEND / "app/ml/optimization_p13/evaluation.py",
    BACKEND / "scripts/run_optimization_p13_t04.py",
    BACKEND / "scripts/verify_optimization_p13_t04.py",
    BACKEND / "tests/ml/test_optimization_p13_t04.py",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path, file_id: str, usage: str) -> dict:
    return {
        "file_id": file_id,
        "absolute_path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "usage": usage,
    }


def load_context(run: Path) -> dict:
    o01_path = run / "audit/input_manifest.json"
    artifact_path = run / "audit/artifact_manifest.json"
    conflicts_path = run / "audit/version_conflicts.csv"
    o01 = json.loads(o01_path.read_text())
    artifact = json.loads(artifact_path.read_text())
    artifact_outputs = {entry["path"]: entry for entry in artifact["outputs"]}
    assert sha256(o01_path) == artifact_outputs["audit/input_manifest.json"]["sha256"]
    assert sha256(conflicts_path) == artifact_outputs["audit/version_conflicts.csv"]["sha256"]
    assert o01["status"] == "PASS" and artifact["status"] == "PASS"
    allowed = set(o01["downstream_handoff"]["T04"]["file_ids"])
    registry = {entry["file_id"]: entry for entry in o01["inputs"]}
    for file_id in INPUT_IDS:
        if file_id not in registry:
            raise ValueError(f"Missing O01 input {file_id}")
        if file_id not in allowed and not file_id.startswith("O00_"):
            raise ValueError(f"Input is not registered for T04: {file_id}")
    records = []
    for file_id in INPUT_IDS:
        registered = registry[file_id]
        path = Path(registered["absolute_path"])
        actual = sha256(path)
        if actual != registered["sha256"] or path.stat().st_size != registered["bytes"]:
            raise ValueError(f"Frozen input changed: {file_id}")
        usage = (
            "review_only_no_fold_or_model_selection"
            if file_id in REVIEW_ONLY_IDS
            else "frozen_t04_input"
        )
        records.append(
            {
                "file_id": file_id,
                "absolute_path": str(path),
                "bytes": registered["bytes"],
                "sha256": actual,
                "usage": usage,
            }
        )
    records.extend(
        [
            file_record(o01_path, "O01_INPUT_MANIFEST", "input_whitelist_and_baseline_rule"),
            file_record(artifact_path, "O01_ARTIFACT_MANIFEST", "upstream_integrity"),
            file_record(conflicts_path, "O01_VERSION_CONFLICTS", "baseline_role_conflict_rule"),
        ]
    )
    return {
        "o01": o01,
        "registry": registry,
        "records": records,
        "o01_record": records[-3],
        "artifact_record": records[-2],
        "conflicts_record": records[-1],
    }


def read_csv(context: dict, file_id: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(context["registry"][file_id]["absolute_path"], **kwargs)


def read_json(context: dict, file_id: str) -> dict:
    return json.loads(Path(context["registry"][file_id]["absolute_path"]).read_text())


def source_map(context: dict, scope: str) -> dict[str, str]:
    ids = SCOPE_INPUTS[scope]
    split = context["registry"][ids["split"]]
    fold = context["registry"][ids["fold"]]
    structure = context["registry"]["P3_STRUCTURES"]
    return {
        "split_file_id": ids["split"],
        "split_sha256": split["sha256"],
        "fold_file_id": ids["fold"],
        "fold_sha256": fold["sha256"],
        "structure_file_id": "P3_STRUCTURES",
        "structure_sha256": structure["sha256"],
    }


def validate_p6d_reference(
    context: dict,
    scope: str,
    original_fit_ids: set[str],
) -> dict:
    ids = SCOPE_INPUTS[scope]
    capacity = read_csv(context, ids["p6d_capacity"])
    manifest = read_csv(context, ids["p6d_manifest"])
    leak_count = 0
    for scenario, rows in manifest.groupby("scenario"):
        if set(rows.structure_id) != original_fit_ids or rows.structure_id.duplicated().any():
            raise ValueError(f"P6D fit catalog mismatch: {scope}:{scenario}")
        train_groups = set(rows.loc[rows.role.eq("train"), "design_group_id"])
        for role in ("evaluation", "embargo"):
            leak_count += bool(
                train_groups & set(rows.loc[rows.role.eq(role), "design_group_id"])
            )
    if leak_count:
        raise ValueError(f"P6D reference leakage: {scope}")
    return {
        "scope": scope,
        "capacity_rows": len(capacity),
        "scenario_count": int(manifest.scenario.nunique()),
        "fit_evaluation_or_embargo_leak_count": leak_count,
        "reference_usage": "geometry_and_embargo_audit_only",
    }


def prepare(run: Path, attempt: str) -> dict:
    evaluation_dir = run / "evaluation"
    work = evaluation_dir / "t04_work" / attempt
    work.mkdir(parents=True, exist_ok=True)
    for path in (
        evaluation_dir / "splits.csv",
        evaluation_dir / "capacity.csv",
        evaluation_dir / "t04_artifact_manifest.json",
        work / "splits.csv",
        work / "capacity.csv",
        work / "prepared.json",
    ):
        if path.exists():
            raise FileExistsError(path)
    context = load_context(run)
    o01 = context["o01"]
    catalog = read_csv(context, "P3_STRUCTURES", usecols=[*IDENTITY, *GEOMETRY])
    data_audit = read_json(context, "P3_DATA_AUDIT")
    assert len(catalog) == data_audit["n_structures"] == 576
    assert catalog.design_group_id.nunique() == data_audit["n_design_groups"] == 288
    positive_eg_levels = sorted(
        float(value) for value in catalog.loc[catalog.eg_um.gt(0), "eg_um"].unique()
    )
    if positive_eg_levels != [0.75, 1.0, 1.5, 2.0]:
        raise ValueError(f"Unexpected positive EG levels: {positive_eg_levels}")
    baseline_group = o01["baseline"]["design_group_id"]
    baseline_structure = o01["baseline"]["structure_id"]
    baseline_row = catalog[catalog.structure_id.eq(baseline_structure)]
    if len(baseline_row) != 1 or baseline_row.iloc[0].design_group_id != baseline_group:
        raise ValueError("O01 baseline identity mismatch")
    original_splits, ordinary_folds, catalogs, sources = {}, {}, {}, {}
    split_parts, capacity_parts = [], []
    fit_summary, p6d_summary = [], []
    baseline_roles = {}
    expected_baseline_roles = {
        entry["scope"]: entry["role"] for entry in o01["baseline"]["scope_roles"]
    }
    p6c_receipts = read_json(context, "P6C_FIT_RECEIPTS")
    model_metadata = read_json(context, "P3_MODEL_METADATA")
    assert model_metadata["training_roles"] == ["train", "validation"]
    for scope in SCOPES:
        ids = SCOPE_INPUTS[scope]
        split = read_csv(context, ids["split"])
        folds = read_csv(context, ids["fold"])
        original_splits[scope] = split
        ordinary_folds[scope] = folds
        catalogs[scope] = catalog
        sources[scope] = source_map(context, scope)
        fit_roles = SCOPES[scope]
        fit_ids = set(split.loc[split.role.isin(fit_roles), "structure_id"])
        if set(p6c_receipts[scope]["fit"]) != fit_ids:
            raise ValueError(f"P6C fit receipt mismatch: {scope}")
        original_baseline_role = split.loc[split.structure_id.eq(baseline_structure), "role"]
        if len(original_baseline_role) != 1:
            raise ValueError(f"Baseline missing from scope {scope}")
        baseline_roles[scope] = original_baseline_role.iloc[0]
        if baseline_roles[scope] != expected_baseline_roles[scope]:
            raise ValueError(f"Baseline original role changed: {scope}")
        scope_splits, scope_capacity = build_scope_splits(
            scope=scope,
            catalog=catalog,
            split=split,
            ordinary_folds=folds,
            baseline_group=baseline_group,
            source=sources[scope],
        )
        split_parts.append(scope_splits)
        capacity_parts.append(scope_capacity)
        fit_summary.append(
            {
                "scope": scope,
                "original_fit_structure_count": len(fit_ids),
                "original_fit_design_group_count": split.loc[
                    split.role.isin(fit_roles), "design_group_id"
                ].nunique(),
                "p13_eligible_design_group_count": split.loc[
                    split.role.isin(fit_roles) & split.design_group_id.ne(baseline_group),
                    "design_group_id",
                ].nunique(),
                "positive_eg_levels_by_branch": {
                    branch: sorted(
                        float(value)
                        for value in split.loc[
                            split.role.isin(fit_roles)
                            & split.design_group_id.ne(baseline_group)
                            & split.branch_id.eq(branch)
                            & split.eg_um.gt(0),
                            "eg_um",
                        ].unique()
                    )
                    for branch in ("pf0_eg1_fl0", "pf0_eg1_fl1")
                },
            }
        )
        p6d_summary.append(validate_p6d_reference(context, scope, fit_ids))
    splits = pd.concat(split_parts, ignore_index=True)
    capacity = pd.concat(capacity_parts, ignore_index=True)
    validation = validate_outputs(
        splits=splits,
        capacity=capacity,
        catalogs=catalogs,
        original_splits=original_splits,
        ordinary_folds=ordinary_folds,
        baseline_group=baseline_group,
        sources=sources,
    )
    temp_split, temp_capacity = write_csv_outputs(splits, capacity, work)
    roundtrip_splits = pd.read_csv(temp_split, keep_default_na=False)
    roundtrip_capacity = pd.read_csv(temp_capacity, keep_default_na=False)
    if list(roundtrip_splits.columns) != SPLIT_COLUMNS:
        raise ValueError("Temporary splits roundtrip schema mismatch")
    if list(roundtrip_capacity.columns) != CAPACITY_COLUMNS:
        raise ValueError("Temporary capacity roundtrip schema mismatch")
    if len(roundtrip_splits) != len(splits) or len(roundtrip_capacity) != len(capacity):
        raise ValueError("Temporary CSV roundtrip row count mismatch")
    validation.update(
        {
            "temporary_csv_roundtrip": "PASS",
            "positive_eg_catalog_levels": positive_eg_levels,
            "original_fit_summary": fit_summary,
            "p6d_reference": p6d_summary,
            "baseline_structure_id": baseline_structure,
            "baseline_design_group_id": baseline_group,
            "baseline_original_scope_roles": baseline_roles,
            "baseline_rule": "final_difference_only_no_new_fit_no_inner_evaluation",
            "input_hash_mismatch_count": 0,
            "label_error_data_used_for_fold_construction": False,
            "model_training_performed": False,
        }
    )
    (work / "validation.json").write_text(
        json.dumps(validation, indent=2, ensure_ascii=False) + "\n"
    )
    for name in ("splits.csv", "capacity.csv"):
        temporary = work / f".{name}.promote"
        temporary.write_bytes((work / name).read_bytes())
        os.replace(temporary, evaluation_dir / name)
    source_hashes = {
        str(path.relative_to(BACKEND)): sha256(path) for path in SOURCE_PATHS
    }
    prepared = {
        "schema": "aln_optimization_p13_t04_prepared_v1",
        "task_id": "T04",
        "attempt_id": attempt,
        "status": "PREPARED",
        "temporary_outputs_validated_before_promotion": True,
        "outputs": {
            f"evaluation/{name}": {
                "sha256": sha256(evaluation_dir / name),
                "bytes": (evaluation_dir / name).stat().st_size,
                "temporary_sha256": sha256(work / name),
            }
            for name in ("splits.csv", "capacity.csv")
        },
        "source_sha256": source_hashes,
        "input_sha256": {record["file_id"]: record["sha256"] for record in context["records"]},
        "validation_sha256": sha256(work / "validation.json"),
        "created_at": datetime.now(UTC).isoformat(),
    }
    if any(
        output["sha256"] != output["temporary_sha256"]
        for output in prepared["outputs"].values()
    ):
        raise ValueError("Promoted output differs from validated temporary output")
    (work / "prepared.json").write_text(
        json.dumps(prepared, indent=2, ensure_ascii=False) + "\n"
    )
    return {
        "status": "PREPARED",
        "split_rows": len(splits),
        "capacity_rows": len(capacity),
        "valid_units": validation["valid_unit_count"],
        "valid_boundary_units": validation["valid_boundary_unit_count"],
        "hard_capacity_met": (
            f"{validation['hard_capacity_met_count']}/"
            f"{validation['hard_capacity_total_count']}"
        ),
        "pooled_descriptive_capacity": validation["pooled_descriptive_capacity"],
    }


def environment() -> dict:
    packages = {}
    for package in ("numpy", "pandas", "pytest", "ruff"):
        packages[package] = importlib.metadata.version(package)
    uv_version = subprocess.run(
        ["uv", "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "uv": uv_version,
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


def finalize(run: Path, attempt: str) -> dict:
    evaluation_dir = run / "evaluation"
    work = evaluation_dir / "t04_work" / attempt
    formal_manifest = evaluation_dir / "t04_artifact_manifest.json"
    if formal_manifest.exists():
        raise FileExistsError(formal_manifest)
    context = load_context(run)
    prepared = json.loads((work / "prepared.json").read_text())
    if prepared["input_sha256"] != {
        record["file_id"]: record["sha256"] for record in context["records"]
    }:
        raise ValueError("Input drift between prepare and finalize")
    source_hashes = {str(path.relative_to(BACKEND)): sha256(path) for path in SOURCE_PATHS}
    if source_hashes != prepared["source_sha256"]:
        raise ValueError("Source drift between prepare and finalize")
    for name, output in prepared["outputs"].items():
        path = run / name
        if sha256(path) != output["sha256"] or path.stat().st_size != output["bytes"]:
            raise ValueError(f"Formal output drift: {name}")
    prepare_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t04.py "
        f"--run {run} --attempt {attempt} --stage prepare"
    )
    pytest_command = (
        "uv run --no-sync pytest -q -p no:cacheprovider "
        f"--basetemp {work}/pytest_targeted tests/ml/test_optimization_p13_t04.py"
    )
    ruff_command = (
        "uv run --no-sync ruff check --no-cache "
        "app/ml/optimization_p13/evaluation.py "
        "scripts/run_optimization_p13_t04.py "
        "scripts/verify_optimization_p13_t04.py "
        "tests/ml/test_optimization_p13_t04.py"
    )
    independence_command = (
        "uv run --no-sync python scripts/verify_optimization_p13_t04.py "
        f"--run {run} --attempt {attempt}"
    )
    command_specs = {
        "prepare": prepare_command,
        "targeted_pytest": pytest_command,
        "ruff": ruff_command,
        "independence": independence_command,
    }
    commands = []
    for name, command in command_specs.items():
        code_path = work / "logs" / f"{name}.exit_code"
        log_path = work / "logs" / f"{name}.log"
        if not code_path.exists() or not log_path.exists():
            raise FileNotFoundError(f"Missing command evidence: {name}")
        exit_code = int(code_path.read_text().strip())
        commands.append(
            {
                "name": name,
                "command": command,
                "cwd": str(BACKEND),
                "exit_code": exit_code,
                "log": str(log_path),
                "exit_code_evidence": str(code_path),
            }
        )
    if any(command["exit_code"] != 0 for command in commands):
        raise ValueError("Cannot finalize with a failed command")
    independence_path = work / "independence_report.json"
    independence = json.loads(independence_path.read_text())
    if independence["status"] != "PASS":
        raise ValueError("Independent verification did not pass")
    for name in ("splits.csv", "capacity.csv"):
        key = f"evaluation/{name}"
        if independence["output_sha256"][key] != prepared["outputs"][key]["sha256"]:
            raise ValueError(f"Independent verification used stale output: {name}")
    validation = json.loads((work / "validation.json").read_text())
    splits = pd.read_csv(evaluation_dir / "splits.csv", keep_default_na=False)
    capacity = pd.read_csv(evaluation_dir / "capacity.csv", keep_default_na=False)
    hard_capacity = aggregate_hard_capacity(splits, capacity)
    if hard_capacity != validation["hard_capacity"]:
        raise ValueError("Hard capacity aggregate changed after CSV roundtrip")
    outputs = [
        file_record(evaluation_dir / name, f"O05_{name.upper().replace('.', '_')}", "formal_o05")
        | {"path": f"evaluation/{name}"}
        for name in ("splits.csv", "capacity.csv")
    ]
    evidence_paths = [
        work / "prepared.json",
        work / "validation.json",
        independence_path,
        *(work / "logs" / f"{name}.log" for name in command_specs),
        *(work / "logs" / f"{name}.exit_code" for name in command_specs),
    ]
    evidence = [
        file_record(path, f"T04_EVIDENCE_{index:02d}", "t04_work_evidence")
        | {"path": str(path.relative_to(run))}
        for index, path in enumerate(evidence_paths, start=1)
    ]
    unmet_joint = [
        record["capacity_key"]
        for record in hard_capacity
        if record["family"] == "joint_eg_upper_fl_lower" and not record["meets_hard_capacity"]
    ]
    if independence["hard_capacity"] != hard_capacity:
        raise ValueError("Independent hard-capacity aggregate mismatch")
    finalize_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t04.py "
        f"--run {run} --attempt {attempt} --stage finalize"
    )
    ordinary_fold_rule = (
        "Reuse frozen P6C four-fold design_group assignment; remove E1-1 group "
        "from all P13 inner fit and evaluation roles."
    )
    joint_rule = (
        "Within pf0_eg1_fl1, withhold the union of top-K EG and bottom-K FL "
        "groups for K=1,2; score only their intersection; all other withheld "
        "structures are embargo."
    )
    embargo_rule = (
        "Any design group selected for inner evaluation is absent from fit; "
        "same-group non-scored original-fit structures are embargo. Original P3 "
        "calibration/test/embargo remain outer-only. Joint marginal holdouts are "
        "embargo, not joint evaluation evidence."
    )
    hard_capacity_rule = (
        "Apply >=20 independent design groups AND >=2 valid boundary units "
        "separately at scope/family/branch. Never substitute pooled cross-scope counts."
    )
    baseline_rule = (
        "E1-1 observations are final-difference-only; baseline_only in every fold; "
        "no new refit and no inner evaluation; original P3 roles remain in original_role."
    )
    manifest = {
        "schema": "aln_optimization_p13_artifact_manifest_v1",
        "task_id": "T04",
        "attempt_id": attempt,
        "run_id": "r01",
        "status": "PASS",
        "created_at": datetime.now(UTC).isoformat(),
        "inputs": context["records"],
        "outputs": outputs,
        "evidence": evidence,
        "source_sha256": source_hashes,
        "command": finalize_command,
        "commands": commands,
        "exit_code": 0,
        "environment": environment(),
        "validation": {
            "temporary_outputs_validated_before_promotion": True,
            "formal_outputs_match_temporary": True,
            "input_hash_mismatch_count": 0,
            "source_drift_count": 0,
            "split_row_count": len(splits),
            "capacity_row_count": len(capacity),
            "valid_unit_count": int(capacity.valid.sum()),
            "invalid_unit_count": int((~capacity.valid).sum()),
            "ordinary_unit_count": int((capacity.valid & capacity.family.eq("ordinary")).sum()),
            "valid_boundary_unit_count": int(
                (capacity.valid & ~capacity.family.eq("ordinary")).sum()
            ),
            "fit_evaluation_leak_count": 0,
            "fit_outer_or_embargo_leak_count": 0,
            "baseline_fit_or_inner_evaluation_count": 0,
            "independent_check_status": independence["status"],
            "independent_check_counts": independence["checks"],
            "p6d_reference": validation["p6d_reference"],
            "positive_eg_catalog_levels": validation["positive_eg_catalog_levels"],
            "original_fit_summary": validation["original_fit_summary"],
            "all_recorded_commands_zero_exit": True,
            "model_training_performed": False,
            "public_evaluation_formula_implemented": False,
            "label_error_data_used_for_fold_construction": False,
        },
        "baseline": {
            "structure_id": validation["baseline_structure_id"],
            "design_group_id": validation["baseline_design_group_id"],
            "original_scope_roles": validation["baseline_original_scope_roles"],
            "usage": "final_difference_only",
            "new_refit_allowed": False,
            "inner_evaluation_allowed": False,
            "split_role": "baseline_only",
            "original_split_files_changed": False,
        },
        "capacity_contract": {
            "hard_capacity_key": ["scope", "family", "branch_id"],
            "minimum_independent_design_groups": HARD_MIN_GROUPS,
            "minimum_valid_boundary_units": HARD_MIN_UNITS,
            "deduplication": (
                "Union design_group_id once; repeated folds and scopes never add groups."
            ),
            "cross_scope_pooled_counts_are_descriptive_only": True,
            "per_scope_family_branch": hard_capacity,
            "pooled_descriptive_capacity": pooled_descriptive_capacity(splits, capacity),
            "joint_scope_level_warnings": unmet_joint,
        },
        "t05_freeze_fields": {
            "outer_fit_roles": {scope: list(roles) for scope, roles in SCOPES.items()},
            "ordinary_fold_rule": ordinary_fold_rule,
            "boundary_families": {
                "eg_upper": (
                    "PF=0 positive-EG branches; hold out top 1 and top 2 "
                    "available EG levels."
                ),
                "fl_lower": (
                    "PF=0 positive-FL branches; hold out bottom 1 and bottom 2 "
                    "available FL levels."
                ),
                "joint_eg_upper_fl_lower": joint_rule,
            },
            "embargo_rule": embargo_rule,
            "minimum_fit_levels_per_active_axis": MIN_FIT_LEVELS,
            "minimum_fit_design_groups": MIN_FIT_GROUPS,
            "invalid_reason_vocabulary": [
                "eg_upper_requires_a_supported_positive_pf0_branch",
                "fl_lower_requires_a_supported_positive_pf0_branch",
                "joint_eg_upper_fl_lower_requires_a_supported_positive_pf0_branch",
                "fewer_than_two_fit_levels__eg_um",
                "fewer_than_two_fit_levels__fl_um",
                "fewer_than_two_fit_design_groups",
                "no_evaluation_design_group",
            ],
            "hard_capacity_rule": hard_capacity_rule,
            "role_vocabulary": [
                "fit",
                "evaluation",
                "embargo",
                "baseline_only",
                "outer_calibration",
                "outer_test",
                "outer_embargo",
            ],
            "baseline_rule": baseline_rule,
            "fold_selection_data": (
                "Geometry, frozen roles and frozen P6C fold IDs only; "
                "no label errors or outer test results."
            ),
            "joint_capacity_warning": unmet_joint,
        },
        "random_processes": [],
        "notes": [
            (
                "PASS denotes successful O05 construction and verification, not model "
                "acceptance or universal capacity sufficiency."
            ),
            (
                "No model was trained; no public evaluation formula was implemented; "
                "no test result was used to choose folds."
            ),
            (
                "O01, all P3 splits, P6C folds and P6D artifacts remain read-only "
                "and hash-verified."
            ),
            (
                "Formal CSV files were written and validated in t04_work before "
                "promotion and manifest generation."
            ),
        ],
    }
    temporary_manifest = work / "t04_artifact_manifest.json"
    temporary_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    loaded = json.loads(temporary_manifest.read_text())
    assert loaded["task_id"] == "T04" and loaded["status"] == "PASS"
    for output in loaded["outputs"]:
        assert sha256(run / output["path"]) == output["sha256"]
    os.replace(temporary_manifest, formal_manifest)
    met_count = sum(record["meets_hard_capacity"] for record in hard_capacity)
    return {
        "status": "PASS",
        "manifest": str(formal_manifest),
        "split_rows": len(splits),
        "capacity_rows": len(capacity),
        "valid_units": int(capacity.valid.sum()),
        "valid_boundary_units": int((capacity.valid & ~capacity.family.eq("ordinary")).sum()),
        "hard_capacity_met": f"{met_count}/{len(hard_capacity)}",
        "joint_scope_level_warnings": unmet_joint,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--attempt", default="attempt_001")
    parser.add_argument("--stage", choices=("prepare", "finalize"), required=True)
    args = parser.parse_args()
    if args.stage == "prepare":
        result = prepare(args.run, args.attempt)
    else:
        result = finalize(args.run, args.attempt)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
