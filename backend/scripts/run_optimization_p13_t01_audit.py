# ruff: noqa: E402, E501
"""Run the P13 T01 input/version audit without fitting or predicting."""

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
from pathlib import Path

BE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE))

from app.ml.optimization_p13.audit import (
    AuditError,
    HashMismatchError,
    InputRegistry,
    audit_candidates,
    manifest_expectation,
    read_decimal_baseline,
    read_json,
    sha256,
    validate_artifact,
    validate_conflicts,
    validate_input_document,
    verify_expected_hash,
)

attempt = "attempt_001"
SCOPES = ("structure_holdout", "area_edges", "eg_upper", "fl_upper")
SPLIT_FILES = {
    "structure_holdout": "split_manifest.csv",
    "area_edges": "split_area_edges.csv",
    "eg_upper": "split_eg_upper.csv",
    "fl_upper": "split_fl_upper.csv",
}
OWNED_SOURCES = (
    "backend/app/ml/optimization_p13/audit.py",
    "backend/scripts/run_optimization_p13_t01_audit.py",
    "backend/tests/ml/test_optimization_p13_audit.py",
)
SOURCE_FILES = (
    BE / "app/ml/optimization_p13/audit.py",
    BE / "scripts/run_optimization_p13_t01_audit.py",
    BE / "tests/ml/test_optimization_p13_audit.py",
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def git_status(root: Path) -> dict[str, str]:
    raw = subprocess.check_output(
        ["/opt/homebrew/bin/git", "-C", str(root), "status", "--porcelain=v1", "-uall", "-z"]
    )
    result = {}
    for item in raw.decode("utf-8", "surrogateescape").split("\0"):
        if not item:
            continue
        status, path = item[:2], item[3:]
        if status[0] in "RC":
            raise AuditError("Unexpected rename/copy status in protected worktree")
        result[path] = status
    return result


def validate_o00(run: Path) -> dict:
    preflight = read_json(run / "control/preflight.json")
    artifact = read_json(run / "control/artifact_manifest.json")
    if preflight["task_id"] != "T00" or preflight["g0"]["status"] != "PASS":
        raise AuditError("O00 G0 is not PASS")
    checked = 0
    for group, root in (("inputs", None), ("outputs", run)):
        for entry in artifact[group]:
            path = Path(entry["path"]) if root is None else root / entry["path"]
            if not path.exists():
                raise HashMismatchError(f"Missing O00 {group} entry: {path}")
            if entry["kind"] == "file":
                verify_expected_hash(path, entry["sha256"])
                if entry.get("bytes") is not None and path.stat().st_size != entry["bytes"]:
                    raise HashMismatchError(f"O00 byte count mismatch: {path}")
                checked += 1
    for entry in preflight["legacy_files"]:
        path = Path(entry["path"])
        verify_expected_hash(path, entry["sha256"])
        checked += 1
    protection = {}
    for name, repository in preflight["repositories"].items():
        root = Path(repository["root"])
        branch = subprocess.check_output(
            ["/opt/homebrew/bin/git", "-C", str(root), "branch", "--show-current"], text=True
        ).strip()
        head = subprocess.check_output(
            ["/opt/homebrew/bin/git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        if branch != repository["branch"] or head != repository["head"]:
            raise AuditError(f"Repository route changed for {name}")
        expected = {entry["path"]: entry["status"] for entry in repository["dirty_files"]}
        allowed = set(OWNED_SOURCES) if name == "ML" else set()
        current = git_status(root)
        if set(current) - set(expected) != allowed or set(expected) - set(current):
            raise AuditError(f"Unexpected dirty path drift in {name}")
        for path, status in expected.items():
            if current[path] != status:
                raise AuditError(f"Dirty status changed for {name}:{path}")
        for entry in repository["dirty_files"]:
            path = root / entry["path"]
            verify_expected_hash(path, entry["sha256"])
            if path.stat().st_size != entry["bytes"]:
                raise HashMismatchError(f"Protected byte count mismatch: {path}")
            checked += 1
        protection[name] = {
            "branch": branch,
            "head": head,
            "existing_dirty_preserved": len(expected),
            "new_owned_source_files": sorted(allowed),
        }
    return {
        "g0": "PASS",
        "file_hashes_checked": checked,
        "repositories": protection,
        "errors": [],
    }


def named_expectation(manifest_path: Path, section: str, key: str) -> dict[str, str]:
    value = read_json(manifest_path)[section][key]
    return {
        "manifest_path": str(manifest_path),
        "manifest_key": f"{section}:{key}",
        "expected_sha256": value,
    }


def o00_dirty_expectation(preflight_path: Path, repository: str, relative: str) -> dict[str, str]:
    preflight = read_json(preflight_path)
    entries = preflight["repositories"][repository]["dirty_files"]
    match = [entry for entry in entries if entry["path"] == relative]
    if len(match) != 1:
        raise AuditError(f"O00 does not protect {repository}:{relative}")
    return {
        "manifest_path": str(preflight_path),
        "manifest_key": f"repositories.{repository}.dirty_files:{relative}",
        "expected_sha256": match[0]["sha256"],
    }


def o00_legacy_expectation(preflight_path: Path, path: Path) -> dict[str, str]:
    preflight = read_json(preflight_path)
    match = [entry for entry in preflight["legacy_files"] if Path(entry["path"]) == path]
    if len(match) != 1:
        raise AuditError(f"O00 legacy list does not contain {path}")
    return {
        "manifest_path": str(preflight_path),
        "manifest_key": f"legacy_files:{path}",
        "expected_sha256": match[0]["sha256"],
    }


def o00_output_expectation(artifact_path: Path, relative: str) -> dict[str, str]:
    outputs = read_json(artifact_path)["outputs"]
    match = [entry for entry in outputs if entry["path"] == relative]
    if len(match) != 1:
        raise AuditError(f"O00 artifact outputs do not contain {relative}")
    return {
        "manifest_path": str(artifact_path),
        "manifest_key": f"outputs:{relative}",
        "expected_sha256": match[0]["sha256"],
    }


def execute(run: Path, attempt: str) -> int:
    preflight_path = run / "control/preflight.json"
    preflight = read_json(preflight_path)
    if run.resolve() != Path(preflight["paths"]["RUN"]).resolve():
        raise AuditError("RUN argument differs from frozen O00 path")
    if not attempt.startswith("attempt_") or Path(attempt).name != attempt:
        raise AuditError("Invalid attempt identifier")
    audit = run / "audit"
    formal = (
        audit / "input_manifest.json",
        audit / "version_conflicts.csv",
        audit / "artifact_manifest.json",
    )
    if any(path.exists() for path in formal):
        raise AuditError(f"Refusing to overwrite formal T01 outputs in {audit}")
    logs = audit / "logs"
    work = audit / "work" / attempt
    if work.exists():
        raise AuditError(f"Refusing to overwrite attempt evidence: {work}")
    logs.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        env[name] = "1"
    commands = []

    def run_checked(command: list[str], cwd: Path, log: Path) -> None:
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
        log.write_text(completed.stdout + completed.stderr)
        record = {
            "command": shlex.join(command),
            "cwd": str(cwd),
            "exit_code": completed.returncode,
            "log": str(log),
        }
        commands.append(record)
        if completed.returncode:
            write_json(
                work / "failure.json",
                {"task_id": "T01", "status": "FAILED", "failed_command": record},
            )
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
        ],
        BE,
        logs / f"targeted_pytest_{attempt}.log",
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
            "tests/ml/test_optimization_p13_audit.py",
        ],
        BE,
        logs / f"targeted_ruff_{attempt}.log",
    )

    o00 = validate_o00(run)
    main = Path(preflight["paths"]["MAIN"])
    ml = Path(preflight["paths"]["ML"])
    be = Path(preflight["paths"]["BE"])
    desktop = Path("/Users/jingbozuo/Desktop")
    p3 = be / "surrogate_data/optimization_p3_20261015"
    p4 = be / "surrogate_data/optimization_p4_20261016"
    p6a = be / "surrogate_data/optimization_p6a_20261022"
    p6b = be / "surrogate_data/optimization_p6b_20261022"
    p6c = be / "surrogate_data/optimization_p6c_20261022"
    p6d = be / "surrogate_data/optimization_p6d_20261022"
    p10 = be / "surrogate_data/optimization_p10_20261026"
    p12 = be / "surrogate_data/optimization_p12_20261026"
    registry = InputRegistry(
        {"MAIN": main, "ML": ml, "BE": be, "RUN": run, "DESKTOP": desktop}
    )

    def add(
        file_id: str,
        path: Path,
        category: str,
        consumers: list[str],
        expectations: list[dict[str, str]] | None = None,
        alias: str = "BE",
    ) -> None:
        registry.add(file_id, path, alias, category, consumers, expectations)

    def add_document(relative: str, consumers: list[str]) -> None:
        path = main / relative
        add(
            "DOC_" + Path(relative).stem.upper().replace("-", "_"),
            path,
            "document",
            consumers,
            [o00_dirty_expectation(preflight_path, "MAIN", relative)],
            alias="MAIN",
        )

    add(
        "O00_PREFLIGHT",
        preflight_path,
        "control",
        ["T01"],
        [o00_output_expectation(run / "control/artifact_manifest.json", "control/preflight.json")],
        alias="RUN",
    )
    add(
        "O00_ARTIFACT_MANIFEST",
        run / "control/artifact_manifest.json",
        "control",
        ["T01"],
        alias="RUN",
    )
    for relative, tasks in (
        ("docs/superpowers/plans/2026-10-27-surrogate-p13-agent-execution-order.md", ["T02", "T03", "T04"]),
        ("docs/superpowers/plans/2026-10-27-surrogate-no-fab-k2-plus-one-plan.md", ["T02", "T03", "T04"]),
        ("docs/superpowers/plans/2026-10-15-surrogate-p3-protocol.md", ["T02", "T04"]),
        ("docs/superpowers/plans/2026-10-15-surrogate-p3-results.md", ["T02", "T04"]),
        ("docs/superpowers/plans/2026-10-22-surrogate-p6a-protocol.md", ["T03"]),
        ("docs/superpowers/plans/2026-10-22-surrogate-p6a-results.md", ["T03"]),
        ("docs/superpowers/plans/2026-10-22-surrogate-p6b-protocol.md", ["T03"]),
        ("docs/superpowers/plans/2026-10-22-surrogate-p6b-results.md", ["T03"]),
        ("docs/superpowers/plans/2026-10-22-surrogate-p6c-protocol.md", ["T02", "T04"]),
        ("docs/superpowers/plans/2026-10-22-surrogate-p6c-results.md", ["T02", "T04"]),
        ("docs/superpowers/plans/2026-10-22-surrogate-p6d-protocol.md", ["T04"]),
        ("docs/superpowers/plans/2026-10-22-surrogate-p6d-results.md", ["T04"]),
        ("docs/superpowers/plans/2026-10-26-surrogate-p12-protocol.md", ["T02"]),
        ("docs/superpowers/plans/2026-10-26-surrogate-p12-results.md", ["T02"]),
        ("docs/handoffs/2026-10-27-surrogate-p6g-p12-handoff.md", ["T02"]),
    ):
        add_document(relative, tasks)

    p3_delivery = p3 / "receipts/delivery_manifest.json"
    p3_data_outputs = p3 / "data/outputs_manifest.json"
    p3_eval_outputs = p3 / "evaluation/outputs_manifest.json"
    add("P3_DELIVERY_MANIFEST", p3_delivery, "manifest", ["T02", "T04"])
    for file_id, path, key in (
        ("P3_DATA_OUTPUTS_MANIFEST", p3_data_outputs, "data/outputs_manifest.json"),
        ("P3_EVALUATION_OUTPUTS_MANIFEST", p3_eval_outputs, "evaluation/outputs_manifest.json"),
    ):
        add(file_id, path, "manifest", ["T02", "T04"], [manifest_expectation(p3_delivery, key)])
    for file_id, relative, tasks in (
        ("P3_STRUCTURES", "data/structures.csv", ["T02", "T04"]),
        ("P3_DATA_AUDIT", "data/data_audit.json", ["T02", "T04"]),
        ("P3_INPUT_PROVENANCE", "data/input_manifest.json", ["T02", "T04"]),
    ):
        add(
            file_id,
            p3 / relative,
            "measured_data",
            tasks,
            [manifest_expectation(p3_data_outputs, Path(relative).name), manifest_expectation(p3_delivery, relative)],
        )
    for scope, filename in SPLIT_FILES.items():
        relative = f"evaluation/{filename}"
        add(
            f"P3_SPLIT_{scope.upper()}",
            p3 / relative,
            "split",
            ["T04"] + (["T02"] if scope == "structure_holdout" else []),
            [manifest_expectation(p3_eval_outputs, filename), manifest_expectation(p3_delivery, relative)],
        )
    for file_id, relative in (
        ("P3_MODEL_BUNDLE", "evaluation/scalar_baselines.joblib"),
        ("P3_MODEL_METADATA", "evaluation/model_metadata.json"),
        ("P3_EVALUATION_PROTOCOL", "evaluation/frozen_protocol.json"),
    ):
        add(
            file_id,
            p3 / relative,
            "fitted_artifact" if file_id == "P3_MODEL_BUNDLE" else "model_contract",
            ["T02", "T04"],
            [manifest_expectation(p3_eval_outputs, Path(relative).name), manifest_expectation(p3_delivery, relative)],
        )

    def add_experiment_family(prefix: str, root: Path, tasks: list[str]) -> tuple[Path, Path]:
        delivery = root / "delivery_manifest.json"
        outputs = root / "experiment/outputs_manifest.json"
        add(f"{prefix}_DELIVERY_MANIFEST", delivery, "manifest", tasks)
        add(
            f"{prefix}_EXPERIMENT_OUTPUTS_MANIFEST",
            outputs,
            "manifest",
            tasks,
            [manifest_expectation(delivery, "experiment/outputs_manifest.json")],
        )
        return delivery, outputs

    p6c_delivery, p6c_outputs = add_experiment_family("P6C", p6c, ["T02", "T04"])
    p6c_files = [
        "frozen_inputs.json",
        "protocol.md",
        "selection.json",
        "fit_receipts.json",
        "calibration.json",
        "models.joblib",
        "predictions.csv",
        "metrics.csv",
        "comparison_to_p3.csv",
    ]
    p6c_files += [f"{scope}_{kind}.csv" for scope in SCOPES for kind in ("inner_folds", "inner_predictions")]
    for filename in p6c_files:
        relative = f"experiment/{filename}"
        category = "fitted_artifact" if filename == "models.joblib" else "p6c_experiment"
        add(
            f"P6C_{Path(filename).stem.upper()}",
            p6c / relative,
            category,
            ["T02", "T04"],
            [manifest_expectation(p6c_outputs, filename), manifest_expectation(p6c_delivery, relative)],
        )

    p6d_delivery, p6d_outputs = add_experiment_family("P6D", p6d, ["T04"])
    p6d_files = ["frozen_inputs.json", "protocol.md", "selection.json", "outer_predictions.csv", "outer_metrics.csv"]
    p6d_files += [
        f"{scope}_{kind}.csv"
        for scope in SCOPES
        for kind in ("capacity", "stress_manifest", "stress_predictions", "stress_cells", "balanced_cells")
    ]
    for filename in p6d_files:
        relative = f"experiment/{filename}"
        add(
            f"P6D_{Path(filename).stem.upper()}",
            p6d / relative,
            "boundary_evidence",
            ["T04"],
            [manifest_expectation(p6d_outputs, filename), manifest_expectation(p6d_delivery, relative)],
        )

    p12_outputs = p12 / "outputs_manifest.json"
    add("P12_OUTPUTS_MANIFEST", p12_outputs, "manifest", ["T02"])
    p12_files = [
        "candidates.csv",
        "eg_extension_behavior.csv",
        "falsification_plan.md",
        "frozen_inputs.json",
        "grid_predictions.csv",
        "measured_frontier.csv",
        "negative_controls.csv",
        "peak_searches.csv",
        "protocol.md",
        "rankings.csv",
        "summary.json",
    ]
    for filename in p12_files:
        expectations = [manifest_expectation(p12_outputs, filename)]
        if filename == "candidates.csv":
            expectations.append(o00_legacy_expectation(preflight_path, p12 / filename))
        add(
            f"P12_{Path(filename).stem.upper()}",
            p12 / filename,
            "p12_search",
            ["T02"] + (["T03"] if filename in {"candidates.csv", "peak_searches.csv", "protocol.md"} else []),
            expectations,
        )
    add("P12_VERIFICATION", p12 / "verification.json", "verification", ["T02"])
    build_manifest = p12 / "viewer_build/build_manifest.json"
    browser_data = p12 / "viewer_build/browser_data.json"
    viewer_html = p12 / "viewer_build/AlN结构寻优.html"
    desktop_html = desktop / "AlN结构寻优-P12-外推候选-20261026.html"
    add("P12_VIEWER_BUILD_MANIFEST", build_manifest, "viewer", ["T01", "T02"])
    for file_id, path in (("P12_BROWSER_DATA", browser_data), ("P12_VIEWER_HTML", viewer_html)):
        expectations = [named_expectation(build_manifest, "output_sha256", path.name)]
        if file_id == "P12_VIEWER_HTML":
            expectations.append(o00_legacy_expectation(preflight_path, path))
        add(
            file_id,
            path,
            "viewer",
            ["T02"],
            expectations,
        )
    add(
        "P12_DESKTOP_HTML",
        desktop_html,
        "viewer",
        ["T02"],
        [o00_legacy_expectation(preflight_path, desktop_html)],
        alias="DESKTOP",
    )

    p4_delivery = p4 / "receipts/delivery_manifest.json"
    add("P4_VIEWER_DELIVERY_MANIFEST", p4_delivery, "viewer_input_manifest", ["T01"])
    for relative in (
        "measured/candidates.csv",
        "measured/summary.json",
        "exploratory/candidates.csv",
        "exploratory/summary.json",
        "audit/joint_four_target_coverage.csv",
    ):
        add(
            f"P4_VIEWER_{Path(relative).parent.name.upper()}_{Path(relative).stem.upper()}",
            p4 / relative,
            "viewer_input",
            ["T01"],
            [manifest_expectation(p4_delivery, relative)],
        )
    p10_outputs = p10 / "outputs_manifest.json"
    add("P10_VIEWER_OUTPUTS_MANIFEST", p10_outputs, "viewer_input_manifest", ["T01"])
    for relative in ("experiment1_intervals/small_area_pf_qp.csv", "selection_e1.json"):
        add(
            f"P10_VIEWER_{Path(relative).stem.upper()}",
            p10 / relative,
            "viewer_input",
            ["T01"],
            [manifest_expectation(p10_outputs, relative)],
        )
    add("P10_VIEWER_VERIFICATION", p10 / "verification.json", "viewer_input", ["T01"])

    p6a_delivery = p6a / "delivery_manifest.json"
    p6a_outputs = p6a / "simulation_audit_v2/outputs_manifest.json"
    add("P6A_DELIVERY_MANIFEST", p6a_delivery, "manifest", ["T03"])
    add(
        "P6A_SIMULATION_OUTPUTS_MANIFEST",
        p6a_outputs,
        "manifest",
        ["T03"],
        [manifest_expectation(p6a_delivery, "simulation_audit_v2/outputs_manifest.json")],
    )
    for filename in (
        "frozen_inputs.json",
        "summary.json",
        "simulation_extraction.csv",
        "quarantine.csv",
        "rejected_series.csv",
        "trend_series.csv",
        "trend_pairs.csv",
        "trend_directions.csv",
    ):
        relative = f"simulation_audit_v2/{filename}"
        add(
            f"P6A_SIM_{Path(filename).stem.upper()}",
            p6a / relative,
            "simulation_metadata",
            ["T03"],
            [manifest_expectation(p6a_outputs, filename), manifest_expectation(p6a_delivery, relative)],
        )
    p6b_delivery = p6b / "delivery_manifest.json"
    p6b_outputs = p6b / "diagnostics_v2/outputs_manifest.json"
    add("P6B_DELIVERY_MANIFEST", p6b_delivery, "manifest", ["T03"])
    add(
        "P6B_DIAGNOSTICS_V2_OUTPUTS_MANIFEST",
        p6b_outputs,
        "manifest",
        ["T03"],
        [manifest_expectation(p6b_delivery, "diagnostics_v2/outputs_manifest.json")],
    )
    for filename in (
        "frozen_inputs.json",
        "summary.json",
        "eg_series_diagnostics.csv",
        "eg_pair_diagnostics.csv",
        "simulation_peak_probes.csv",
        "protocol.md",
    ):
        relative = f"diagnostics_v2/{filename}"
        add(
            f"P6B_V2_{Path(filename).stem.upper()}",
            p6b / relative,
            "simulation_metadata",
            ["T03"],
            [manifest_expectation(p6b_outputs, filename), manifest_expectation(p6b_delivery, relative)],
        )
    add(
        "P6B_DIAGNOSTIC_V1_CORRECTION",
        p6b / "diagnostic_v1_correction.json",
        "version_evidence",
        ["T03"],
        [manifest_expectation(p6b_delivery, "diagnostic_v1_correction.json")],
    )

    source_roles = {
        "app/ml/optimization_data.py": (["T02", "T04"], "P6C"),
        "app/ml/optimization_baselines.py": (["T02", "T04"], "P6C"),
        "app/ml/optimization_boundary_pilot.py": (["T02", "T04"], "P6C"),
        "app/ml/optimization_ablation.py": (["T02", "T04"], "P6C"),
        "app/ml/optimization_boundary_selection.py": (["T04"], "P6D"),
        "scripts/run_optimization_p6c.py": (["T02", "T04"], "P6C"),
        "app/ml/optimization_p11.py": (["T02"], "P12"),
        "app/ml/optimization_p12.py": (["T02"], "P12"),
        "scripts/run_optimization_p12.py": (["T02"], "P12"),
    }
    for relative, (tasks, family) in source_roles.items():
        expectations = [o00_dirty_expectation(preflight_path, "ML", f"backend/{relative}")]
        if family == "P6C" and (p6c / "source_snapshot" / relative).is_file():
            expectations.append(named_expectation(p6c_delivery, "sources", str(be / relative)))
        if family == "P12":
            expectations.append(named_expectation(p12 / "frozen_inputs.json", "sources", str(be / relative)))
        add(
            "SOURCE_" + Path(relative).stem.upper(),
            be / relative,
            "source",
            tasks,
            expectations,
        )
    viewer_sources = {
        "backend/scripts/build_optimization_p5.py": be / "scripts/build_optimization_p5.py",
        "backend/app/ml/optimization_p12_view.py": be / "app/ml/optimization_p12_view.py",
        "backend/app/ml/optimization_d2_view.py": be / "app/ml/optimization_d2_view.py",
        "backend/app/ml/optimization_p11_view.py": be / "app/ml/optimization_p11_view.py",
        "frontend/optimization/app.ts": ml / "frontend/optimization/app.ts",
        "frontend/optimization/index.html": ml / "frontend/optimization/index.html",
        "frontend/optimization/style.css": ml / "frontend/optimization/style.css",
    }
    for relative, path in viewer_sources.items():
        repository = "ML"
        expectations = [o00_dirty_expectation(preflight_path, repository, relative)]
        if str(path) in read_json(build_manifest)["source_sha256"]:
            expectations.append(named_expectation(build_manifest, "source_sha256", str(path)))
        add(
            "SOURCE_VIEWER_" + path.stem.upper().replace("-", "_"),
            path,
            "viewer_source",
            ["T01"],
            expectations,
            alias="ML" if path.is_relative_to(ml) and not path.is_relative_to(be) else "BE",
        )

    split_paths = {scope: p3 / "evaluation" / filename for scope, filename in SPLIT_FILES.items()}
    baseline = read_decimal_baseline(p3 / "data/structures.csv", split_paths)
    baseline["source_file_id"] = "P3_STRUCTURES"
    baseline["split_file_ids"] = {scope: f"P3_SPLIT_{scope.upper()}" for scope in SCOPES}
    baseline["usage_constraint"] = (
        "E1-1 is train in structure_holdout and test in eg_upper/area_edges. "
        "T05 must explicitly freeze the P13 rule that baseline observations are used only for "
        "final difference evaluation and are not newly refit; frozen P3 split files remain unchanged."
    )
    candidates = audit_candidates(
        p12 / "candidates.csv", browser_data, viewer_html, p12 / "negative_controls.csv"
    )
    summary = read_json(p12 / "summary.json")
    if summary["n_candidates"] != candidates["total_rows"]:
        raise AuditError("P12 summary n_candidates differs from candidates.csv")
    if summary["n_negative_controls"] != candidates["negative_control_rows"]:
        raise AuditError("P12 summary negative-control count differs from candidates.csv")
    with (p12 / "grid_predictions.csv").open(newline="") as handle:
        grid_rows = sum(1 for _ in handle) - 1
    if grid_rows != summary["n_grid_points"] or grid_rows != 9801:
        raise AuditError("P12 grid row count differs from frozen 9801-point definition")

    from scripts.build_optimization_p5 import make_package

    regenerated_package = make_package(p4, p10, None, p12)
    if regenerated_package != read_json(browser_data):
        raise AuditError("Current builder inputs/source do not reproduce viewer browser_data.json")
    if sha256(viewer_html) != sha256(desktop_html):
        raise HashMismatchError("Desktop P12 HTML differs from viewer_build HTML")
    build = read_json(build_manifest)
    if build["p12_root"] != str(p12) or build["p12_files_verified"] != len(p12_files):
        raise AuditError("P12 viewer build root or verified-file count is inconsistent")
    if build["p11_root"] is not None or build["p11_candidates_embedded"]:
        raise AuditError("Current P12 viewer unexpectedly includes the superseded P11 block")
    if build["p4_root"] != str(p4) or build["p10_root"] != str(p10):
        raise AuditError("Current viewer P4/P10 build roots differ from build_manifest")
    direct_sources = set(build["source_sha256"])
    transitive_sources = [*viewer_sources.values(), be / "app/ml/optimization_data.py"]
    transitive_missing = sorted(
        str(path) for path in transitive_sources if str(path) not in direct_sources
    )

    results_text = (main / "docs/superpowers/plans/2026-10-26-surrogate-p12-results.md").read_text()
    import re

    documented_new_ids = re.findall(r"^\| (P12\d{2}) \|", results_text, flags=re.MULTILINE)
    actual_new_ids = [
        row["candidate_id"].split("_", 1)[0]
        for row in candidates["non_negative_candidates"]
        if row["group"] == "P12"
    ]
    omitted_from_results = sorted(set(actual_new_ids) - set(documented_new_ids))
    if omitted_from_results != ["P1206"]:
        raise AuditError(f"Unexplained P12 results-table ID difference: {omitted_from_results}")

    import joblib

    models = joblib.load(p6c / "experiment/models.joblib")
    expected_model_keys = {
        (scope, recipe, target)
        for scope in SCOPES
        for recipe in ("gp_raw", "gp_log_area", "trend_gp_raw", "trend_gp_log_area")
        for target in ("k2eff_pct", "qs", "qp", "bodeq_fitted")
    }
    if set(models) != expected_model_keys:
        raise AuditError("P6C saved model keys differ from the 4×4×4 frozen recipe grid")
    fit_receipts = read_json(p6c / "experiment/fit_receipts.json")
    fit_summary = {
        scope: {role: len(ids) for role, ids in roles.items()}
        for scope, roles in fit_receipts.items()
    }

    conflict_rows = [
        {
            "conflict_id": "VC01_BASELINE_FULL_PRECISION",
            "topic": "E1-1 baseline and threshold precision",
            "left_source": "DOC_2026_10_27_SURROGATE_NO_FAB_K2_PLUS_ONE_PLAN",
            "left_value": "14.6744702842 / 15.6744702842",
            "right_source": "P3_STRUCTURES",
            "right_value": f"{baseline['baseline_k2eff_pct']} / {baseline['threshold_k2eff_pct']}",
            "affected_ids": "E1-1;s_f7db3a637f7a4b11",
            "explanation": "The plan is rounded; it explicitly requires reading full precision from the original CSV.",
            "disposition": "Use Decimal CSV literals and baseline+1 percentage point; never use display rounding for decisions.",
            "status": "PASS",
        },
        {
            "conflict_id": "VC02_P12_NEW_CANDIDATE_COUNT",
            "topic": "P12 results table lists six new candidates but current CSV has seven",
            "left_source": "DOC_2026_10_26_SURROGATE_P12_RESULTS",
            "left_value": "6; IDs P1200-P1205",
            "right_source": "P12_CANDIDATES",
            "right_value": "7; IDs P1200-P1206",
            "affected_ids": "P1206_joint_p6c_EG3.0_FL1.875_A5500_PF0",
            "explanation": "P1206 is the P6C joint Pareto representative recorded in peak_searches.csv; the results table omits it.",
            "disposition": "Treat current candidates.csv as authoritative for the existing artifact; retain the documentation discrepancy.",
            "status": "PASS",
        },
        {
            "conflict_id": "VC03_16_17_20_COUNT_CALIBERS",
            "topic": "16/17 display counts and 20 total rows are different calibers",
            "left_source": "DOC_2026_10_27_SURROGATE_P6G_P12_HANDOFF;DOC_2026_10_26_SURROGATE_P12_RESULTS",
            "left_value": "16 displayable",
            "right_source": "P12_CANDIDATES;P12_BROWSER_DATA;P12_VIEWER_HTML;P12_DESKTOP_HTML",
            "right_value": "17 non-negative; 17 displayable; 20 total; 3 negative controls",
            "affected_ids": "P1206_joint_p6c_EG3.0_FL1.875_A5500_PF0;C17;C18;C19",
            "explanation": "Current HTML contains all 17 P11/P12 rows, including P1206; three C rows remain excluded. The 16-count documents omit P1206.",
            "disposition": "Use 17/17/20/3 for the frozen current artifacts; do not propagate the stale 16-count into P13.",
            "status": "PASS",
        },
        {
            "conflict_id": "VC04_VIEWER_BASELINE_ROUNDING",
            "topic": "P12 viewer baseline fields are rounded",
            "left_source": "SOURCE_VIEWER_OPTIMIZATION_P12_VIEW",
            "left_value": "14.674470 in p12_candidates.baselines; 14.6745 in detail text",
            "right_source": "P3_STRUCTURES",
            "right_value": baseline["baseline_k2eff_pct"],
            "affected_ids": "E1-1;all P12 candidate delta displays",
            "explanation": "The old page does not compute acceptance from its baselines block; candidate deltas were precomputed by P12 and are displayed at limited precision.",
            "disposition": "Keep the old page unchanged. P13 threshold and acceptance must use O01 full precision and O14 values, not viewer literals.",
            "status": "PASS",
        },
        {
            "conflict_id": "VC05_VIEWER_SOURCE_MANIFEST_COVERAGE",
            "topic": "P12 build_manifest lists direct sources but omits imported Python viewer modules",
            "left_source": "P12_VIEWER_BUILD_MANIFEST",
            "left_value": "4 direct source hashes",
            "right_source": "SOURCE_VIEWER_OPTIMIZATION_P12_VIEW;SOURCE_VIEWER_OPTIMIZATION_D2_VIEW;SOURCE_VIEWER_OPTIMIZATION_P11_VIEW;SOURCE_OPTIMIZATION_DATA",
            "right_value": "current transitive source hashes frozen separately in O01",
            "affected_ids": ";".join(transitive_missing),
            "explanation": "Current make_package reproduces browser_data exactly, HTML embeds that same package, and direct source/output hashes match; the omission is manifest coverage, not observed content drift.",
            "disposition": "T01 supplements the missing hashes. Future A5 builds should record all imported viewer modules; old HTML remains untouched.",
            "status": "PASS",
        },
        {
            "conflict_id": "VC06_P6B_DIAGNOSTICS_VERSION",
            "topic": "P6B initial diagnostics versus corrected diagnostics_v2",
            "left_source": "P6B_DIAGNOSTIC_V1_CORRECTION",
            "left_value": "diagnostics used a global 2 MHz step",
            "right_source": "P6B_V2_EG_PAIR_DIAGNOSTICS;P6B_V2_SIMULATION_PEAK_PROBES",
            "right_value": "diagnostics_v2 uses local fs/fp neighbor spacings",
            "affected_ids": "P6B EG trend and peak-switch evidence",
            "explanation": "The initial resolution envelopes were overly conservative; the correction document preserves v1 and names v2 as replacement.",
            "disposition": "T03 may consume only diagnostics_v2 and the correction record; do not use the superseded diagnostics directory.",
            "status": "PASS",
        },
        {
            "conflict_id": "VC07_BASELINE_ROLE_USAGE",
            "topic": "E1-1 frozen split roles require an explicit P13 usage rule",
            "left_source": "P3_SPLIT_STRUCTURE_HOLDOUT;P3_SPLIT_EG_UPPER;P3_SPLIT_AREA_EDGES",
            "left_value": "structure_holdout=train; eg_upper=test; area_edges=test; fl_upper=train",
            "right_source": "DOC_2026_10_27_SURROGATE_P13_AGENT_EXECUTION_ORDER",
            "right_value": "baseline observations only for final difference evaluation; no refit",
            "affected_ids": "E1-1;s_f7db3a637f7a4b11;d_71c5a74d95544080",
            "explanation": "This is a usage constraint, not a file hash mismatch. The original split remains frozen and roles are now explicit.",
            "disposition": "T04 must carry the roles forward and T05 must freeze the no-new-refit baseline rule before any model work; T01 changes no split.",
            "status": "PASS",
        },
    ]

    inputs = registry.records
    handoff = {}
    for task in ("T02", "T03", "T04"):
        file_ids = [record["file_id"] for record in inputs if task in record["consumers"]]
        registry.require_ids(file_ids)
        notes = {
            "T02": [
                "Use frozen P3/P6C artifacts and P12 grid/candidates; this O01 performs no peak audit.",
                "Current candidate calibers are 17 non-negative, 17 displayed, 20 total, 3 negative controls.",
            ],
            "T03": [
                "Use only registered P6A simulation_audit_v2 and P6B diagnostics_v2 metadata; raw COMSOL sweeps were not reread.",
                "Simulation is qualitative evidence only: no measured labels, pseudo-labels, raw/de mixing, or Qp prior.",
            ],
            "T04": [
                "Preserve P3 design_group_id and all four frozen split manifests; use P6C folds and P6D capacity/embargo evidence.",
                baseline["usage_constraint"],
            ],
        }[task]
        handoff[task] = {"file_ids": file_ids, "notes": notes}

    document = {
        "schema": "aln_optimization_p13_input_manifest_v1",
        "task_id": "T01",
        "attempt_id": attempt,
        "run_id": preflight["run_id"],
        "status": "PASS",
        "created_at": now(),
        "paths": {name: str(path) for name, path in registry.aliases.items()},
        "o00_validation": o00,
        "inputs": inputs,
        "baseline": baseline,
        "source_isolation": {
            "authoritative_measured_labels": "P3 structures.csv, deembedded Sheet1 structure typical values",
            "raw": "paired diagnostic only; never mixed into deembedded training",
            "de": "only measured label source for the P13 model route",
            "simulation": "P6A/P6B metadata for qualitative/conditional review only; never measured labels or pseudo-labels",
            "qp_simulation_prior": "disabled because COMSOL lacks electrical loss",
            "new_structure_inference_inputs": ["eg_um", "fl_um", "area_um2", "has_pf", "branch_id derived from geometry"],
            "frequency_rule": "fs_ghz/fp_ghz may supervise M2 training only; never inputs for unmeasured-structure inference",
            "ag_rule": "unrecorded; omitted and never guessed",
            "trusted_for_unmeasured_optimization": False,
        },
        "p6c_fit_audit": {
            "model_count": len(models),
            "key_shape": "4 scopes × 4 recipes × 4 targets",
            "fit_receipt_counts": fit_summary,
            "p12_model_key_present": ("structure_holdout", "trend_gp_log_area", "k2eff_pct") in models,
        },
        "p12_candidate_audit": candidates,
        "p12_grid_rows": grid_rows,
        "p12_results_documented_new_ids": documented_new_ids,
        "p12_results_omitted_current_ids": omitted_from_results,
        "viewer_build_audit": {
            "builder": "scripts/build_optimization_p5.py",
            "builder_arguments": {
                "p4_root": str(p4),
                "p10_root": str(p10),
                "p11_root": None,
                "p12_root": str(p12),
            },
            "declared_p4_files_verified": build["p4_files_verified"],
            "declared_p10_files_verified": build["p10_files_verified"],
            "declared_p12_files_verified": build["p12_files_verified"],
            "current_make_package_reproduces_browser_data": True,
            "html_embedded_package_matches_browser_data": True,
            "desktop_html_sha256": sha256(desktop_html),
            "viewer_html_sha256": sha256(viewer_html),
            "desktop_matches_viewer_build": True,
            "direct_source_hashes_match": True,
            "transitive_sources_absent_from_old_build_manifest": transitive_missing,
            "old_html_modified": False,
        },
        "downstream_handoff": handoff,
        "missing_inputs": [],
        "unexplained_differences": [],
        "blocked_reasons": [],
        "raw_comsol_sweeps_reread": False,
        "model_training_performed": False,
    }
    temp_input = work / "input_manifest.json"
    temp_conflicts = work / "version_conflicts.csv"
    write_json(temp_input, document)
    fields = [
        "conflict_id",
        "topic",
        "left_source",
        "left_value",
        "right_source",
        "right_value",
        "affected_ids",
        "explanation",
        "disposition",
        "status",
    ]
    with temp_conflicts.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(conflict_rows)
    input_validation = validate_input_document(temp_input)
    conflict_validation = validate_conflicts(temp_conflicts)
    validation = {
        "input_manifest": input_validation,
        "version_conflicts": conflict_validation,
        "o00": o00,
        "candidate_counts": {key: candidates[key] for key in ("total_rows", "non_negative_control_rows", "displayable_rows", "negative_control_rows")},
        "viewer_package_reproduced": True,
        "model_training_performed": False,
        "raw_comsol_sweeps_reread": False,
    }
    summary_path = logs / f"audit_validation_{attempt}.json"
    write_json(summary_path, validation)

    main_command = (
        "uv run --no-sync python scripts/run_optimization_p13_t01_audit.py --run "
        + str(run)
        + " --attempt "
        + attempt
    )
    packages = {}
    for name in ("numpy", "scipy", "pandas", "scikit-learn", "joblib", "pytest", "ruff"):
        packages[name] = importlib.metadata.version(name)
    environment = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "uv": subprocess.check_output(["uv", "--version"], text=True).strip(),
        "git": subprocess.check_output(["/opt/homebrew/bin/git", "--version"], text=True).strip(),
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "npm": subprocess.check_output(["npm", "--version"], text=True).strip(),
        "cpu_count": os.cpu_count(),
        "numeric_threads": {name: env[name] for name in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"
        )},
        "python_dont_write_bytecode": "1",
    }
    prior_attempts = []
    for failure_path in sorted((audit / "work").glob("attempt_*/failure.json")):
        failure = read_json(failure_path)
        if failure.get("status") != "FAILED":
            continue
        failed_command = failure["failed_command"]
        commands.append(failed_command)
        evidence = []
        for evidence_path in (failure_path, Path(failed_command["log"])):
            evidence.append({
                "path": str(evidence_path),
                "sha256": sha256(evidence_path),
                "bytes": evidence_path.stat().st_size,
            })
        prior_attempts.append({
            "attempt_id": failure_path.parent.name,
            "status": "FAILED",
            "evidence": evidence,
        })
    commands.insert(0, {"command": main_command, "cwd": str(be), "exit_code": 0})
    outputs = []
    for path, relative in (
        (temp_input, "audit/input_manifest.json"),
        (temp_conflicts, "audit/version_conflicts.csv"),
        (summary_path, f"audit/logs/audit_validation_{attempt}.json"),
        (logs / f"targeted_pytest_{attempt}.log", f"audit/logs/targeted_pytest_{attempt}.log"),
        (logs / f"targeted_ruff_{attempt}.log", f"audit/logs/targeted_ruff_{attempt}.log"),
    ):
        outputs.append({"path": relative, "kind": "file", "sha256": sha256(path), "bytes": path.stat().st_size})
    artifact = {
        "schema": "aln_optimization_p13_artifact_manifest_v1",
        "task_id": "T01",
        "attempt_id": attempt,
        "run_id": preflight["run_id"],
        "status": "PASS",
        "inputs": {record["file_id"]: record["sha256"] for record in inputs},
        "outputs": outputs,
        "source_sha256": {str(path): sha256(path) for path in SOURCE_FILES},
        "command": main_command,
        "commands": commands,
        "attempts": [
            *prior_attempts,
            {"attempt_id": attempt, "status": "PASS", "evidence": "audit/artifact_manifest.json"},
        ],
        "exit_code": 0,
        "environment": environment,
        "validation": validation,
        "random_processes": [],
        "created_at": now(),
        "notes": [
            "No T02 peak audit, model training, prediction, deployment, dependency change, or COMSOL sweep reread was performed.",
            "Only three T01-owned source/test files were added; all O00 dirty files remain byte-identical.",
            "Formal outputs were written as temporary files and validated before this manifest was generated.",
        ],
    }
    temp_artifact = work / "artifact_manifest.json"
    write_json(temp_artifact, artifact)
    if read_json(temp_artifact)["outputs"] != outputs:
        raise AuditError("Temporary artifact manifest could not be read back intact")
    if any(path.exists() for path in formal):
        raise AuditError("Refusing to overwrite a formal T01 output")
    temp_input.rename(audit / "input_manifest.json")
    temp_conflicts.rename(audit / "version_conflicts.csv")
    temp_artifact.rename(audit / "artifact_manifest.json")
    print(json.dumps(validate_artifact(run), ensure_ascii=False))
    print(json.dumps({"status": "PASS", "O01": str(audit / "input_manifest.json")}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=BE / "surrogate_data/optimization_p13_20261027_r01")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--attempt", default=attempt)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(validate_artifact(args.run), ensure_ascii=False))
        return 0
    try:
        return execute(args.run.resolve(), args.attempt)
    except (AuditError, HashMismatchError) as error:
        print(f"T01 BLOCKED/FAILED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
