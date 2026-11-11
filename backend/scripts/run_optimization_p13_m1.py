import argparse
import hashlib
import importlib
import json
import math
import os
import platform
import subprocess
import sys
import traceback
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

SCRIPT_PATH = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT_PATH.parents[1]))
contracts = importlib.import_module("app.ml.optimization_p13.contracts")
evaluation = importlib.import_module("app.ml.optimization_p13.evaluation")
m1_module = importlib.import_module("app.ml.optimization_p13.m1")
ablation = importlib.import_module("app.ml.optimization_ablation")

O02_HASH = "bc0f16a4791d1968cac10afafd5fd14a32abfa7bd7d0329b2591ff825d88f05c"
O05_HASHES = {
    "splits": "fd000bd582f01286d6d862d1dddffa16199146e7acf07802ab2d8a27050a71a9",
    "capacity": "77cd9e9b664fa2e0e39a6ebd32c50579ee407adb13cd938d2450e6951896d60f",
}
P3_HASH = "426d844b4854c95593e3e9b621637d12f42eb4422f6ad75b8e6e67a86d837000"
P3_SPLITS = {
    "structure_holdout": (
        "P3_SPLIT_STRUCTURE_HOLDOUT",
        "ad2f8531152bc76ceff14971dcd5d2e4cbcaa4b73a04b438897b8dbc49777e17",
        "split_manifest.csv",
    ),
    "area_edges": (
        "P3_SPLIT_AREA_EDGES",
        "ab36dd5d8081df07b3d48631bceed36676289f020c42789c84017a2d9cedeb94",
        "split_area_edges.csv",
    ),
    "eg_upper": (
        "P3_SPLIT_EG_UPPER",
        "77890e1f215bb322d3bc9d2bd1ec2eaae8a9e32c196257dcd3066b2c11b64872",
        "split_eg_upper.csv",
    ),
    "fl_upper": (
        "P3_SPLIT_FL_UPPER",
        "b05e8ea1efdb1b0a8071e345d9edce79b080609473b0f66319b6f1b0c8bac3c2",
        "split_fl_upper.csv",
    ),
}
TEST_PATH = SCRIPT_PATH.parents[1] / "tests/ml/test_optimization_p13_m1.py"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_new_json(path, value):
    with Path(path).open("xb") as handle:
        handle.write(json_bytes(value))


def output_receipt(run, path):
    path = Path(path)
    return {
        "path": str(path.relative_to(run)),
        "kind": "file",
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def environment():
    packages = {
        name: version(name)
        for name in ("numpy", "scipy", "pandas", "scikit-learn", "pytest", "ruff")
    }
    uv = subprocess.run(
        ["uv", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    git = subprocess.run(
        ["/opt/homebrew/bin/git", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "uv": uv,
        "git": git,
        "cpu_count": os.cpu_count(),
        "numeric_threads": {
            key: os.environ.get(key, "1")
            for key in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
        "python_dont_write_bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE", "1"),
        "worker_threads_per_process": 1,
    }


def input_receipts(run, t06_manifest):
    be = run.parents[1]
    schema = read_json(run / "contracts/schema.json")
    report = read_json(run / "verification/contract_report.json")
    specs = [
        ("O02_PROTOCOL", run / "contracts/protocol.json", O02_HASH),
        ("O05_SPLITS", run / "evaluation/splits.csv", O05_HASHES["splits"]),
        ("O05_CAPACITY", run / "evaluation/capacity.csv", O05_HASHES["capacity"]),
        ("O05_MANIFEST", run / "evaluation/t04_artifact_manifest.json", None),
        ("O06_SCHEMA", run / "contracts/schema.json", report["schema_sha256"]),
        ("O06_CONTRACT_REPORT", run / "verification/contract_report.json", None),
        ("O06_MANIFEST", run / "verification/t06_artifact_manifest.json", None),
        (
            "M1_PROTOCOL_PROPOSAL",
            run / "m1/protocol_proposal.json",
            "6a1934ad6cf5c9d2fac01aa3330850ce9a1158f1949e3a563f8140e3211e07fa",
        ),
        (
            "O04_SIMULATION_GATE",
            run / "audit/simulation_gate.json",
            "7ff7b5b9c2b5102e406c3205620761b19dc21fcf527a264abc075ee157ecba9f",
        ),
        (
            "P3_STRUCTURES",
            be / "surrogate_data/optimization_p3_20261015/data/structures.csv",
            P3_HASH,
        ),
        (
            "SOURCE_OPTIMIZATION_ABLATION",
            be / "app/ml/optimization_ablation.py",
            "e1b1535f391afca00e934a2dbe9209a7ba9b94824bc95c09ddb6d45b83700e00",
        ),
        (
            "SOURCE_OPTIMIZATION_BOUNDARY_PILOT",
            be / "app/ml/optimization_boundary_pilot.py",
            "8f9194ec6b86e29a252daac8a3159732fa600c78e31a1438689ef8161bc7d30c",
        ),
        (
            "SOURCE_OPTIMIZATION_DATA",
            be / "app/ml/optimization_data.py",
            "c9d61cb4d63381cc6b5e849fb255a991c6d6f28e67fa29dcaf44e90c5b00c8b7",
        ),
    ]
    for _scope, (file_id, expected, filename) in P3_SPLITS.items():
        specs.append(
            (
                file_id,
                be / f"surrogate_data/optimization_p3_20261015/evaluation/{filename}",
                expected,
            )
        )
    for relative in (
        "app/ml/optimization_p13/contracts.py",
        "app/ml/optimization_p13/evaluation.py",
    ):
        specs.append(
            (
                f"SOURCE_{relative.upper()}",
                be / relative,
                t06_manifest["source_sha256"][relative],
            )
        )
    receipts = []
    for file_id, path, expected in specs:
        actual = sha256(path)
        if expected is not None and actual != expected:
            raise ValueError(f"Input hash mismatch: {file_id}: {actual}")
        receipts.append(
            {
                "file_id": file_id,
                "absolute_path": str(path),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "bytes": path.stat().st_size,
                "matches": expected is None or actual == expected,
            }
        )
    if not schema["o02_sha256"] == report["o02_sha256"] == O02_HASH:
        raise ValueError("O06/O02 hash identity changed")
    if not report["g2"]["t07_t08_start_allowed"]:
        raise ValueError("G2 does not allow T07")
    return receipts


def load_labels(run):
    path = run.parents[1] / "surrogate_data/optimization_p3_20261015/data/structures.csv"
    labels = pd.read_csv(path).set_index("structure_id")
    if len(labels) != 576 or sha256(path) != P3_HASH:
        raise ValueError("P3 structure labels changed")
    return labels


def unit_frames(splits, capacity, labels, baseline_group):
    frames = []
    for unit in capacity[capacity.valid].itertuples(index=False):
        rows = splits[splits.unit_id.eq(unit.unit_id)]
        fit = rows[rows.role.eq("fit")].copy()
        scored = rows[rows.role.eq("evaluation")].copy()
        if fit.empty or scored.empty:
            raise ValueError(f"Incomplete fit/evaluation rows for {unit.unit_id}")
        if baseline_group in set(fit.design_group_id) | set(scored.design_group_id):
            raise ValueError(f"Baseline group leaked into {unit.unit_id}")
        for name, frame in (("fit", fit), ("evaluation", scored)):
            if frame.structure_id.duplicated().any():
                raise ValueError(f"Duplicate structure in {name} for {unit.unit_id}")
            missing = set(frame.structure_id) - set(labels.index)
            if missing:
                raise ValueError(f"Missing P3 labels for {unit.unit_id}: {sorted(missing)}")
            frame["k2eff_pct"] = frame.structure_id.map(labels.k2eff_pct)
            values = frame.k2eff_pct.to_numpy(float)
            if not np.isfinite(values).all() or (values <= 0).any() or (values >= 100).any():
                raise ValueError(f"Invalid P3 labels for {unit.unit_id}")
        expected_fit = unit.all_branch_fit_structure_count
        expected_groups = unit.all_branch_fit_design_group_count
        expected_eval = unit.evaluation_structure_count
        expected_eval_groups = unit.independent_evaluation_design_group_count
        if (
            len(fit) != expected_fit
            or fit.design_group_id.nunique() != expected_groups
            or len(scored) != expected_eval
            or scored.design_group_id.nunique() != expected_eval_groups
        ):
            raise ValueError(f"Capacity count mismatch for {unit.unit_id}")
        for row in scored.itertuples(index=False):
            split_id, split_hash, _ = P3_SPLITS[row.scope]
            expected = {
                "source_file_id": split_id,
                "source_sha256": split_hash,
                "source_split_file_id": split_id,
                "source_split_sha256": split_hash,
                "source_structure_file_id": "P3_STRUCTURES",
                "source_structure_sha256": P3_HASH,
            }
            for field, expected_value in expected.items():
                if getattr(row, field) != expected_value:
                    raise ValueError(f"Source mismatch for {unit.unit_id}: {field}")
        sort_keys = ["branch_id", "design_group_id", "structure_id"]
        fit = fit.sort_values(sort_keys, kind="stable").reset_index(drop=True)
        scored = scored.sort_values(sort_keys, kind="stable").reset_index(drop=True)
        frames.append((unit, fit, scored))
    if len(frames) != 53 or sum(len(item[2]) for item in frames) != 2882:
        raise ValueError("O05 valid unit or evaluation row count changed")
    return frames


def make_prediction_record(row, *, model_family, model_id, config_id, prediction, valid, reason):
    prediction_value = None if prediction is None else float(prediction)
    return {
        "schema_version": "optimization_p13.contract.v1",
        "interface_version": "optimization_p13.geometry_io.v1",
        "record_kind": "control" if model_family == "P6C" else "evaluation",
        "model_family": model_family,
        "structure_id": row.structure_id,
        "candidate_id": None,
        "design_group_id": row.design_group_id,
        "scope": row.scope,
        "fold": row.unit_id,
        "unit_id": row.unit_id,
        "family": row.family,
        "role": "evaluation",
        "branch_id": row.branch_id,
        "model_id": model_id,
        "config_id": config_id,
        "target": "k2eff_pct",
        "unit": "percent_number",
        "prediction": prediction_value,
        "actual": float(row.k2eff_pct),
        "valid_prediction": bool(valid),
        "invalid_reason": reason,
        "null_reason": None,
        "support_label": "O05_GROUP_ISOLATED_INNER_EVALUATION",
        "source_mode": "deembedded",
        "label_source_file_id": "P3_STRUCTURES",
        "source_file_id": row.source_file_id,
        "source_sha256": row.source_sha256,
        "source_split_file_id": row.source_split_file_id,
        "source_split_sha256": row.source_split_sha256,
        "source_structure_file_id": row.source_structure_file_id,
        "source_structure_sha256": row.source_structure_sha256,
        "protocol_sha256": O02_HASH,
        "measured": True,
        "trusted_for_unmeasured_optimization": False,
        "eg_um": float(row.eg_um),
        "fl_um": float(row.fl_um),
        "area_um2": float(row.area_um2),
        "has_pf": int(row.has_pf),
    }


def invalid_reason(model, frame, mean, prediction, index):
    branch = frame.iloc[index].branch_id
    if branch not in model.branches:
        return "unknown_or_empty_branch"
    if not math.isfinite(mean[index]) or not math.isfinite(prediction[index]):
        return "nonfinite_model_output"
    if not 0 < prediction[index] < 100:
        return "non_physical_k2_prediction"
    return None


def fit_m1(frames, configurations):
    all_records = []
    receipts = []
    for config in configurations:
        config_records = []
        config_receipts = []
        for unit, fit, scored in frames:
            model = m1_module.M1SplineRidgeModel(config).fit(fit)
            mean = model.predict_log(scored)
            with np.errstate(over="ignore", invalid="ignore"):
                prediction = np.exp(mean)
            for index, row in enumerate(scored.itertuples(index=False)):
                reason = invalid_reason(model, scored, mean, prediction, index)
                valid = reason is None
                record = make_prediction_record(
                    row,
                    model_family="M1",
                    model_id=model.model_id,
                    config_id=model.config_id,
                    prediction=None if not valid else prediction[index],
                    valid=valid,
                    reason=reason,
                )
                config_records.append(record)
            receipt = model.fit_receipt()
            receipt.update(
                {
                    "unit_id": unit.unit_id,
                    "fit_structure_count": len(fit),
                    "fit_design_group_count": fit.design_group_id.nunique(),
                    "evaluation_structure_count": len(scored),
                    "evaluation_design_group_count": scored.design_group_id.nunique(),
                }
            )
            config_receipts.append(receipt)
        if len(config_records) != 2882:
            raise ValueError(f"Incomplete M1 predictions for {config['config_id']}")
        all_records.extend(config_records)
        receipts.append(
            {
                "config_id": config["config_id"],
                "grid_index": config["grid_index"],
                "row_count": len(config_records),
                "unit_receipts": config_receipts,
            }
        )
    return all_records, receipts


def fit_control(frames):
    records = []
    receipts = []
    for unit, fit, scored in frames:
        model = ablation.AblationModel("trend_gp_log_area", "k2eff_pct").fit(fit)
        mean, _ = model.predict_log(scored)
        with np.errstate(over="ignore", invalid="ignore"):
            prediction = np.exp(mean)
        for index, row in enumerate(scored.itertuples(index=False)):
            if not math.isfinite(mean[index]) or not math.isfinite(prediction[index]):
                reason = "nonfinite_model_output"
            elif not 0 < prediction[index] < 100:
                reason = "non_physical_k2_prediction"
            else:
                reason = None
            valid = reason is None
            records.append(
                make_prediction_record(
                    row,
                    model_family="P6C",
                    model_id="p6c_trend_gp_log_area",
                    config_id="trend_gp_log_area",
                    prediction=None if not valid else prediction[index],
                    valid=valid,
                    reason=reason,
                )
            )
        receipts.append(
            {
                "unit_id": unit.unit_id,
                "fit_structure_count": len(fit),
                "fit_design_group_count": fit.design_group_id.nunique(),
                "evaluation_structure_count": len(scored),
                "evaluation_design_group_count": scored.design_group_id.nunique(),
                "branch_count": fit.branch_id.nunique(),
            }
        )
    if len(records) != 2882:
        raise ValueError("Incomplete P6C control predictions")
    return records, receipts


def clean_record(row):
    cleaned = {}
    for key, value in row.items():
        if key in {"valid_prediction", "measured", "trusted_for_unmeasured_optimization"}:
            cleaned[key] = bool(value)
        elif value is None or (not isinstance(value, str) and pd.isna(value)):
            cleaned[key] = None
        elif key in {"eg_um", "fl_um", "area_um2", "actual", "prediction"}:
            cleaned[key] = float(value)
        elif key == "has_pf":
            cleaned[key] = int(value)
        else:
            cleaned[key] = value
    return cleaned


def validate_outputs(inner, control, columns, required_rows, expected_count=2882):
    inner_frame = pd.DataFrame(inner, columns=columns)
    control_frame = pd.DataFrame(control, columns=columns)
    for frame in (inner_frame, control_frame):
        if list(frame.columns) != columns:
            raise ValueError("Prediction column contract mismatch")
        for row in frame.to_dict(orient="records"):
            contracts.validate_prediction_record(clean_record(row))
    evaluation.assert_complete_evaluation_rows(control_frame, required_rows)
    for config_id in sorted({row["config_id"] for row in inner}):
        candidate = pd.DataFrame(
            [row for row in inner if row["config_id"] == config_id], columns=columns
        )
        if len(candidate) != expected_count:
            raise ValueError(f"Incomplete rows for {config_id}")
        evaluation.assert_complete_evaluation_rows(candidate, required_rows)
        evaluation.assert_same_evaluation_rows(candidate, control_frame)
    return inner_frame, control_frame


def count_by_config(frame):
    counts = {}
    for config_id, group in frame.groupby("config_id", sort=True):
        invalid = int((~group.valid_prediction).sum())
        counts[config_id] = {
            "row_count": len(group),
            "valid_prediction_count": len(group) - invalid,
            "invalid_prediction_count": invalid,
            "invalid_reasons": dict(Counter(group.loc[~group.valid_prediction, "invalid_reason"])),
        }
    return counts


def output_receipts(run, paths):
    return [output_receipt(run, path) for path in paths]


def produce(run, attempt, ruff_exit, pytest_exit):
    run = run.resolve()
    be = run.parents[1]
    if ruff_exit != 0 or pytest_exit != 0:
        raise ValueError(f"Required prechecks failed: Ruff={ruff_exit}, pytest={pytest_exit}")
    inner_path = run / "m1/inner_predictions.csv"
    control_path = run / "m1/control_predictions.csv"
    config_path = run / "m1/model_config.json"
    manifest_path = run / "m1/artifact_manifest.json"
    for path in (inner_path, control_path, config_path, manifest_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}")
    work = run / "m1/work" / attempt
    work.mkdir(parents=True, exist_ok=True)
    for name in (
        "inner_predictions.csv",
        "control_predictions.csv",
        "model_config.json",
        "validation.json",
        "artifact_manifest.json",
    ):
        if (work / name).exists():
            raise FileExistsError(f"Refusing to overwrite temporary output {work / name}")
    log_dir = run / "m1/logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{attempt}.log"
    log_messages = []
    t06_manifest = read_json(run / "verification/t06_artifact_manifest.json")
    receipts = input_receipts(run, t06_manifest)
    protocol = contracts.load_protocol(run / "contracts/protocol.json")
    schema = read_json(run / "contracts/schema.json")
    if read_json(run / "audit/simulation_gate.json")["gate"] != "DISABLED":
        raise ValueError("Simulation gate must remain disabled")
    if protocol["models"]["M1"]["configuration_count"] != 12:
        raise ValueError("M1 frozen configuration count changed")
    configurations = sorted(
        protocol["models"]["M1"]["configurations"],
        key=lambda row: row["grid_index"],
    )
    labels = load_labels(run)
    splits = pd.read_csv(run / "evaluation/splits.csv")
    capacity = pd.read_csv(run / "evaluation/capacity.csv")
    if sha256(run / "evaluation/splits.csv") != O05_HASHES["splits"]:
        raise ValueError("O05 splits changed")
    if sha256(run / "evaluation/capacity.csv") != O05_HASHES["capacity"]:
        raise ValueError("O05 capacity changed")
    frames = unit_frames(splits, capacity, labels, protocol["baseline"]["design_group_id"])
    source_hashes = {
        str(SCRIPT_PATH): sha256(SCRIPT_PATH),
        str(m1_module.__file__): sha256(m1_module.__file__),
        str(TEST_PATH): sha256(TEST_PATH),
    }
    with threadpool_limits(limits=1):
        log_messages.append("M1 inner fit start: 12 configurations, 53 units each")
        inner, fit_receipts = fit_m1(frames, configurations)
        log_messages.append("M1 inner fit complete; P6C control fit start")
        control, control_receipts = fit_control(frames)
        first_unit, first_fit, first_scored = frames[0]
        first_config = configurations[0]
        check_model = m1_module.M1SplineRidgeModel(first_config).fit(first_fit)
        check_mean = check_model.predict_log(first_scored)
        expected = np.array(
            [
                row["prediction"]
                for row in inner
                if row["config_id"] == first_config["config_id"]
                and row["unit_id"] == first_unit.unit_id
            ]
        )
        m1_difference = float(np.max(np.abs(np.exp(check_mean) - expected)))
        np.testing.assert_allclose(m1_difference, 0.0, atol=1e-12, rtol=1e-12)
        check_control = ablation.AblationModel("trend_gp_log_area", "k2eff_pct").fit(first_fit)
        control_mean, _ = check_control.predict_log(first_scored)
        control_expected = np.array(
            [row["prediction"] for row in control if row["unit_id"] == first_unit.unit_id]
        )
        control_difference = float(np.max(np.abs(np.exp(control_mean) - control_expected)))
        np.testing.assert_allclose(control_difference, 0.0, atol=1e-12, rtol=1e-12)
    log_messages.append("P6C control fit complete; deterministic refits match")
    columns = schema["artifact_contracts"]["O07"]["required_fields"]
    required_rows = evaluation.frozen_o05_evaluation_rows(run / "evaluation/splits.csv")
    inner_frame, control_frame = validate_outputs(inner, control, columns, required_rows)
    config_counts = count_by_config(inner_frame)
    control_invalid = int((~control_frame.valid_prediction).sum())
    if len(inner_frame) != 12 * 2882 or len(control_frame) != 2882:
        raise ValueError("Final output row accounting failed")
    if set(inner_frame.config_id) != {row["config_id"] for row in configurations}:
        raise ValueError("A frozen configuration is missing")
    inner_temp = work / "inner_predictions.csv"
    control_temp = work / "control_predictions.csv"
    inner_frame.to_csv(inner_temp, index=False, lineterminator="\n")
    control_frame.to_csv(control_temp, index=False, lineterminator="\n")
    for path, expected_rows in ((inner_temp, 12 * 2882), (control_temp, 2882)):
        reloaded = pd.read_csv(path)
        if len(reloaded) != expected_rows or list(reloaded.columns) != columns:
            raise ValueError(f"CSV roundtrip mismatch: {path}")
        for row in reloaded.to_dict(orient="records"):
            contracts.validate_prediction_record(clean_record(row))
    validation = {
        "status": "PASS",
        "inner_row_count": len(inner_frame),
        "control_row_count": len(control_frame),
        "configuration_count": len(config_counts),
        "rows_per_configuration": {key: value["row_count"] for key, value in config_counts.items()},
        "all_o06_records_valid": True,
        "complete_o05_rows": True,
        "same_actual_and_source_rows_between_m1_and_p6c": True,
        "deterministic_refit": {
            "unit_id": first_unit.unit_id,
            "m1_config_id": first_config["config_id"],
            "m1_max_abs_difference": m1_difference,
            "p6c_max_abs_difference": control_difference,
            "atol": 1e-12,
            "rtol": 1e-12,
        },
        "control_invalid_prediction_count": control_invalid,
        "protected_hashes_unchanged": (
            sha256(run / "contracts/protocol.json") == O02_HASH
            and sha256(run / "evaluation/splits.csv") == O05_HASHES["splits"]
            and sha256(run / "evaluation/capacity.csv") == O05_HASHES["capacity"]
        ),
    }
    if not validation["protected_hashes_unchanged"]:
        raise ValueError("Protected file changed during fitting")
    current_source_hashes = {
        str(SCRIPT_PATH): sha256(SCRIPT_PATH),
        str(m1_module.__file__): sha256(m1_module.__file__),
        str(TEST_PATH): sha256(TEST_PATH),
    }
    if current_source_hashes != source_hashes:
        raise RuntimeError("M1 source changed during fitting")
    write_new_json(work / "validation.json", validation)
    config_document = {
        "schema": "aln_optimization_p13_m1_model_config_v1",
        "task_id": "T07",
        "attempt_id": attempt,
        "run_id": protocol["run_id"],
        "status": "PASS",
        "status_meaning": (
            "Complete O05 inner fitting and contract validation only; not model qualification"
        ),
        "model_eligibility": "NOT_EVALUATED",
        "selection_performed": False,
        "deployment_fit_performed": False,
        "outer_retrospective_performed": False,
        "candidate_search_performed": False,
        "soft_constraint_ablation": "DISABLED",
        "o02_sha256": O02_HASH,
        "o05_sha256": O05_HASHES,
        "o06_schema_sha256": sha256(run / "contracts/schema.json"),
        "o06_contract_report_sha256": sha256(run / "verification/contract_report.json"),
        "o06_manifest_sha256": sha256(run / "verification/t06_artifact_manifest.json"),
        "input_receipts": receipts,
        "source_sha256": source_hashes,
        "configurations": configurations,
        "configuration_results": config_counts,
        "fit_receipts": fit_receipts,
        "control_receipts": control_receipts,
        "control": {
            "model_family": "P6C",
            "recipe": "trend_gp_log_area",
            "row_count": len(control_frame),
            "invalid_prediction_count": control_invalid,
            "same_o05_rows_required": True,
            "optimizer": None,
            "old_outer_prediction_substitute_allowed": False,
            "recipe_reselection_allowed": False,
        },
        "budget": {
            "unit_fit_calls_per_configuration": 53,
            "branch_subfits_per_configuration": 318,
            "fit_row_assignments_per_configuration": 17692,
            "evaluation_rows_per_configuration": 2882,
            "control_unit_fit_calls": 53,
            "control_branch_subfits": 318,
            "determinism_refit_unit_calls_per_side": 1,
            "deployment_fit_calls": 0,
            "max_stored_fold_models": 1,
        },
        "execution": {
            "numeric_threads": 1,
            "worker_threads_per_process": 1,
            "row_order": [
                "config_grid_index",
                "capacity_unit_order",
                "branch_id",
                "design_group_id",
                "structure_id",
            ],
            "random_processes": [],
            "private_random_indices": False,
            "ruff_exit_code": ruff_exit,
            "pytest_exit_code": pytest_exit,
        },
        "validation": validation,
        "log": log_messages,
        "created_at": datetime.now(UTC).isoformat(),
    }
    config_temp = work / "model_config.json"
    write_new_json(config_temp, config_document)
    reloaded_config = read_json(config_temp)
    if (
        len(reloaded_config["configurations"]) != 12
        or reloaded_config["model_eligibility"] != "NOT_EVALUATED"
    ):
        raise ValueError("Model config contract mismatch")
    for source, target in (
        (inner_temp, inner_path),
        (control_temp, control_path),
        (config_temp, config_path),
    ):
        os.replace(source, target)
    with log_path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(log_messages) + "\n")
    main_command = (
        f"uv run --no-sync python {SCRIPT_PATH} --run {run} --attempt {attempt} "
        f"--ruff-exit {ruff_exit} --pytest-exit {pytest_exit}"
    )
    ruff_command = (
        "uv run --no-sync ruff check --no-cache "
        f"{m1_module.__file__} {SCRIPT_PATH} {TEST_PATH}"
    )
    manifest = {
        "schema": "aln_optimization_p13_artifact_manifest_v1",
        "task_id": "T07",
        "attempt_id": attempt,
        "run_id": protocol["run_id"],
        "status": "PASS",
        "status_meaning": "O05 inner fit complete; no model eligibility decision",
        "model_eligibility": "NOT_EVALUATED",
        "inputs": receipts,
        "outputs": output_receipts(
            run,
            [inner_path, control_path, config_path, work / "validation.json", log_path],
        ),
        "source_sha256": source_hashes,
        "command": main_command,
        "commands": [
            {"command": main_command, "cwd": str(be), "exit_code": 0},
            {
                "command": ruff_command,
                "cwd": str(be),
                "exit_code": ruff_exit,
                "log": str(log_dir / f"ruff_{attempt}.log"),
            },
            {
                "command": (
                    "uv run --no-sync pytest -q -p no:cacheprovider "
                    f"--basetemp {work / 'pytest_tmp'} {TEST_PATH}"
                ),
                "cwd": str(be),
                "exit_code": pytest_exit,
                "log": str(log_dir / f"pytest_{attempt}.log"),
            },
        ],
        "exit_code": 0,
        "environment": environment(),
        "validation": validation,
        "random_processes": [],
        "created_at": datetime.now(UTC).isoformat(),
        "notes": [
            (
                "No deployment fit, outer retrospective, candidate search, or model "
                "selection was performed"
            ),
            "All 12 configurations and P6C control use identical O05 rows and labels",
            (
                "Temporary CSV/JSON outputs were validated before formal outputs and "
                "manifest generation"
            ),
        ],
    }
    manifest_temp = work / "artifact_manifest.json"
    write_new_json(manifest_temp, manifest)
    reloaded_manifest = read_json(manifest_temp)
    for row in reloaded_manifest["outputs"]:
        if sha256(run / row["path"]) != row["sha256"]:
            raise RuntimeError(f"Manifest output hash mismatch: {row['path']}")
    os.replace(manifest_temp, manifest_path)
    return {
        "task_id": "T07",
        "status": "PASS",
        "configuration_results": config_counts,
        "control_row_count": len(control_frame),
        "control_invalid_prediction_count": control_invalid,
        "inner_predictions": str(inner_path),
        "control_predictions": str(control_path),
        "model_config": str(config_path),
        "artifact_manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "run_log": str(log_path),
    }


def write_failure(run, attempt, error):
    run = run.resolve()
    directory = run / "failures/T07" / attempt
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "failure.json"
    if path.exists():
        path = directory / f"failure_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.json"
    payload = {
        "schema": "aln_optimization_p13_failure_v1",
        "task_id": "T07",
        "attempt_id": attempt,
        "status": "FAILED",
        "error_type": type(error).__name__,
        "error": str(error),
        "traceback": traceback.format_exc(),
        "command": " ".join(sys.argv),
        "environment": environment(),
        "created_at": datetime.now(UTC).isoformat(),
        "model_training_completed": False,
        "deployment_fit_performed": False,
        "candidate_search_performed": False,
    }
    write_new_json(path, payload)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--ruff-exit", type=int, required=True)
    parser.add_argument("--pytest-exit", type=int, required=True)
    args = parser.parse_args()
    try:
        summary = produce(args.run, args.attempt, args.ruff_exit, args.pytest_exit)
    except Exception as error:
        path = write_failure(args.run, args.attempt, error)
        traceback.print_exc()
        raise SystemExit(f"T07 FAILED; failure evidence: {path}") from error
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
