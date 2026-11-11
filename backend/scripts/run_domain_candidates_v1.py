"""Run the frozen D02 same-split comparison without fitting any models."""

from __future__ import annotations

import argparse
import copy
import csv
import gc
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_device_median_models_v1 import (
    EVALUATION_PREDICTION_FIELDS,
    LABEL_COLUMNS,
    file_record,
    file_sha256,
    load_populations,
    prepare_output_directory,
    stable_id,
    write_csv,
    write_json,
)

P3 = "p3_extra_trees_scalar"
P6C = "p6c_trend_gp_log_area_scalar"
CONSTANT = "training_target_median_constant"
CANONICAL_METHODS = (P3, P6C, CONSTANT)
NEAR_ZERO_ABS_ACTUAL = 1e-12
MAX_EXISTING_ACTUAL_ULP = 1

MODEL_COMPARISON_FIELDS = [
    "population",
    "scope",
    "target",
    "comparison_id",
    "method_a",
    "method_b",
    "unit",
    "n_native_a",
    "n_native_b",
    "n_common",
    "n_lost_a",
    "n_lost_b",
    "lost_structure_ids_a_json",
    "lost_structure_ids_b_json",
    "native_MAE_a",
    "native_RMSE_a",
    "native_bias_a",
    "native_abs_error_P90_a",
    "native_max_abs_error_a",
    "native_mean_absolute_relative_error_a",
    "native_mean_relative_bias_a",
    "native_n_relative_a",
    "native_MAE_b",
    "native_RMSE_b",
    "native_bias_b",
    "native_abs_error_P90_b",
    "native_max_abs_error_b",
    "native_mean_absolute_relative_error_b",
    "native_mean_relative_bias_b",
    "native_n_relative_b",
    "common_MAE_a",
    "common_RMSE_a",
    "common_bias_a",
    "common_abs_error_P90_a",
    "common_max_abs_error_a",
    "common_mean_absolute_relative_error_a",
    "common_mean_relative_bias_a",
    "common_n_relative_a",
    "common_MAE_b",
    "common_RMSE_b",
    "common_bias_b",
    "common_abs_error_P90_b",
    "common_max_abs_error_b",
    "common_mean_absolute_relative_error_b",
    "common_mean_relative_bias_b",
    "common_n_relative_b",
    "better_a_count",
    "better_b_count",
    "tie_count",
    "better_a_fraction",
    "better_b_fraction",
    "tie_fraction",
]
PAIRED_PREDICTION_FIELDS = [
    "population",
    "scope",
    "target",
    "comparison_id",
    "method_a",
    "method_b",
    "structure_key",
    "p3_structure_id",
    "p3_design_group_id",
    "p3_branch_id",
    "unit",
    "label_sha256",
    "training_id_a",
    "training_id_b",
    "model_bundle_sha256_a",
    "model_bundle_sha256_b",
    "sample_status",
    "native_a",
    "native_b",
    "in_common",
    "actual_a",
    "actual_b",
    "prediction_a",
    "prediction_b",
    "signed_error_a",
    "signed_error_b",
    "absolute_error_a",
    "absolute_error_b",
    "squared_error_a",
    "squared_error_b",
    "absolute_error_difference_a_minus_b",
    "signed_relative_error_a",
    "signed_relative_error_b",
    "absolute_relative_error_a",
    "absolute_relative_error_b",
    "relative_status_a",
    "relative_status_b",
    "better_method",
]
P3_PREDICTION_FIELDS = [
    *EVALUATION_PREDICTION_FIELDS,
    "training_id",
    "label_sha256",
]
METRIC_NAMES = (
    "MAE",
    "RMSE",
    "bias",
    "abs_error_P90",
    "max_abs_error",
    "mean_absolute_relative_error",
    "mean_relative_bias",
    "n_relative",
)


def _tree_sha256(root: Path) -> tuple[str, int, int]:
    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    digest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total_bytes += size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha256(path).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), len(files), total_bytes


def _read_csv_round_trip(path: Path, **kwargs: Any) -> pd.DataFrame:
    return pd.read_csv(path, float_precision="round_trip", **kwargs)


def _boolean(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    mapped = str(value).strip().lower()
    if mapped not in {"true", "false", "1", "0"}:
        raise RuntimeError(f"Invalid boolean value: {value!r}")
    return mapped in {"true", "1"}


def _load_canonical_populations(
    protocol: dict[str, Any], data_dir: Path
) -> dict[str, dict[str, Any]]:
    loaded = load_populations(protocol, data_dir)
    for item in loaded.values():
        path = Path(item["source"]["path"])
        frame = _read_csv_round_trip(path, usecols=LABEL_COLUMNS)
        frame["low_support"] = frame["low_support"].map(_boolean)
        item["frame"] = frame.sort_values(["parameter", "structure_key"]).reset_index(drop=True)
    return loaded


def canonical_actual(stored: Any, canonical: Any) -> tuple[float, int]:
    """Replace a serialized actual with its hashed-label float, allowing one ULP."""
    stored_float = float(stored)
    canonical_float = float(canonical)
    if not np.isfinite(stored_float) or not np.isfinite(canonical_float):
        raise RuntimeError("Existing scored actual and canonical actual must both be finite")
    if stored_float == canonical_float:
        return canonical_float, 0
    if np.nextafter(stored_float, canonical_float) == canonical_float:
        return canonical_float, 1
    raise RuntimeError(
        f"Existing actual/canonical actual discrepancy exceeds {MAX_EXISTING_ACTUAL_ULP} ULP: "
        f"{stored_float!r} != {canonical_float!r}"
    )


def _error_components(actual: float, prediction: float) -> dict[str, Any]:
    signed_error = float(prediction) - float(actual)
    absolute_error = abs(signed_error)
    relative_eligible = abs(float(actual)) > NEAR_ZERO_ABS_ACTUAL
    return {
        "signed_error": signed_error,
        "absolute_error": absolute_error,
        "squared_error": signed_error**2,
        "signed_relative_error": (signed_error / abs(float(actual)) if relative_eligible else None),
        "absolute_relative_error": (
            absolute_error / abs(float(actual)) if relative_eligible else None
        ),
        "relative_status": "OK" if relative_eligible else "NEAR_ZERO_ACTUAL",
    }


def error_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute native-unit and relative metrics without dropping near-zero rows."""
    if not rows:
        return {"n": 0, **{name: 0 if name == "n_relative" else None for name in METRIC_NAMES}}
    actual = np.asarray([float(row["actual"]) for row in rows], dtype=float)
    prediction = np.asarray([float(row["prediction"]) for row in rows], dtype=float)
    if not np.isfinite(actual).all() or not np.isfinite(prediction).all():
        raise RuntimeError("Metrics require finite actuals and predictions")
    signed = prediction - actual
    absolute = np.abs(signed)
    relative = np.abs(actual) > NEAR_ZERO_ABS_ACTUAL
    signed_relative = signed[relative] / np.abs(actual[relative])
    absolute_relative = absolute[relative] / np.abs(actual[relative])
    return {
        "n": len(rows),
        "MAE": float(absolute.mean()),
        "RMSE": float(np.sqrt(np.mean(signed**2))),
        "bias": float(signed.mean()),
        "abs_error_P90": float(np.quantile(absolute, 0.90, method="linear")),
        "max_abs_error": float(absolute.max()),
        "mean_absolute_relative_error": (
            float(absolute_relative.mean()) if relative.any() else None
        ),
        "mean_relative_bias": float(signed_relative.mean()) if relative.any() else None,
        "n_relative": int(relative.sum()),
    }


def _lineage_value(
    side: str,
    field: str,
    explicit: dict[str, Any] | None,
    reference: dict[str, Any] | None,
) -> Any:
    if explicit is not None and field in explicit:
        return explicit[field]
    if reference is not None:
        if side == "a" and field in {"training_id", "model_bundle_sha256"}:
            return reference.get(field)
        if side == "b" and field in {"training_id", "model_bundle_sha256"}:
            return reference.get(field)
    return None


def build_pair_comparison(
    *,
    population: str,
    scope: str,
    target: str,
    comparison_id: str,
    method_a: str,
    method_b: str,
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    lineage_a: dict[str, Any] | None = None,
    lineage_b: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build one frozen pair row and deterministic UNION-grain paired rows."""
    by_a = {row["structure_key"]: row for row in rows_a}
    by_b = {row["structure_key"]: row for row in rows_b}
    if len(by_a) != len(rows_a) or len(by_b) != len(rows_b):
        raise RuntimeError("Duplicate native structure key in method cell")
    common_ids = sorted(by_a.keys() & by_b.keys())
    native_a = error_metrics(rows_a)
    native_b = error_metrics(rows_b)
    common_a = error_metrics([by_a[key] for key in common_ids])
    common_b = error_metrics([by_b[key] for key in common_ids])
    paired_rows: list[dict[str, Any]] = []
    better_a = better_b = ties = 0
    for structure_key in sorted(by_a.keys() | by_b.keys()):
        row_a = by_a.get(structure_key)
        row_b = by_b.get(structure_key)
        reference = row_a or row_b
        assert reference is not None
        if row_a is not None and row_b is not None:
            for field in (
                "structure_key",
                "p3_structure_id",
                "p3_design_group_id",
                "p3_branch_id",
                "unit",
                "label_sha256",
            ):
                if row_a.get(field) != row_b.get(field):
                    raise RuntimeError(f"Paired identity mismatch: {field}/{structure_key}")
            if float(row_a["actual"]) != float(row_b["actual"]):
                raise RuntimeError(f"Paired canonical actual mismatch: {structure_key}")
            status = "COMMON"
            in_common = True
        else:
            status = "ONLY_A" if row_a is not None else "ONLY_B"
            in_common = False
        output = {
            "population": population,
            "scope": scope,
            "target": target,
            "comparison_id": comparison_id,
            "method_a": method_a,
            "method_b": method_b,
            "structure_key": structure_key,
            "p3_structure_id": reference["p3_structure_id"],
            "p3_design_group_id": reference["p3_design_group_id"],
            "p3_branch_id": reference["p3_branch_id"],
            "unit": reference["unit"],
            "label_sha256": reference["label_sha256"],
            "training_id_a": _lineage_value("a", "training_id", lineage_a, row_a or row_b),
            "training_id_b": _lineage_value("b", "training_id", lineage_b, row_b),
            "model_bundle_sha256_a": _lineage_value(
                "a", "model_bundle_sha256", lineage_a, row_a or row_b
            ),
            "model_bundle_sha256_b": _lineage_value(
                "b", "model_bundle_sha256", lineage_b, row_b or row_a
            ),
            "sample_status": status,
            "native_a": row_a is not None,
            "native_b": row_b is not None,
            "in_common": in_common,
            "better_method": "N/A",
        }
        for side, row in (("a", row_a), ("b", row_b)):
            if row is None:
                output.update(
                    {
                        f"actual_{side}": None,
                        f"prediction_{side}": None,
                        f"signed_error_{side}": None,
                        f"absolute_error_{side}": None,
                        f"squared_error_{side}": None,
                        f"signed_relative_error_{side}": None,
                        f"absolute_relative_error_{side}": None,
                        f"relative_status_{side}": None,
                    }
                )
                continue
            output[f"actual_{side}"] = float(row["actual"])
            output[f"prediction_{side}"] = float(row["prediction"])
            components = _error_components(float(row["actual"]), float(row["prediction"]))
            for field, value in components.items():
                output[f"{field}_{side}"] = value
        if in_common:
            difference = output["absolute_error_a"] - output["absolute_error_b"]
            output["absolute_error_difference_a_minus_b"] = difference
            if difference < 0:
                better_a += 1
                output["better_method"] = method_a
            elif difference > 0:
                better_b += 1
                output["better_method"] = method_b
            else:
                ties += 1
                output["better_method"] = "TIE"
        else:
            output["absolute_error_difference_a_minus_b"] = None
        paired_rows.append(output)

    comparison = {
        "population": population,
        "scope": scope,
        "target": target,
        "comparison_id": comparison_id,
        "method_a": method_a,
        "method_b": method_b,
        "unit": (rows_a or rows_b)[0]["unit"],
        "n_native_a": len(rows_a),
        "n_native_b": len(rows_b),
        "n_common": len(common_ids),
        "n_lost_a": len(by_a.keys() - by_b.keys()),
        "n_lost_b": len(by_b.keys() - by_a.keys()),
        "lost_structure_ids_a_json": json.dumps(
            sorted(by_a.keys() - by_b.keys()), separators=(",", ":")
        ),
        "lost_structure_ids_b_json": json.dumps(
            sorted(by_b.keys() - by_a.keys()), separators=(",", ":")
        ),
        "better_a_count": better_a,
        "better_b_count": better_b,
        "tie_count": ties,
        "better_a_fraction": better_a / len(common_ids) if common_ids else None,
        "better_b_fraction": better_b / len(common_ids) if common_ids else None,
        "tie_fraction": ties / len(common_ids) if common_ids else None,
    }
    for sample, side, metrics in (
        ("native", "a", native_a),
        ("native", "b", native_b),
        ("common", "a", common_a),
        ("common", "b", common_b),
    ):
        for name in METRIC_NAMES:
            comparison[f"{sample}_{name}_{side}"] = metrics[name]
    if set(comparison) != set(MODEL_COMPARISON_FIELDS):
        raise RuntimeError("Internal comparison schema mismatch")
    return comparison, paired_rows


def validate_fold_lineage(
    *,
    fit: dict[str, Any],
    expected_training_id: str,
    eligible_training_structure_ids: list[str],
    eligible_training_design_group_ids: list[str],
    outer_test_structure_ids: list[str],
    outer_test_design_group_ids: list[str],
) -> None:
    """Require exact outer-fold membership and reject train/test leakage."""
    if fit.get("training_id") != expected_training_id:
        raise RuntimeError("Final-fit training_id mismatch")
    if fit.get("outer_test_used") is not False:
        raise RuntimeError("Final fit reports outer-test use")
    train_ids = sorted(fit.get("training_structure_ids", []))
    train_groups = sorted(fit.get("training_design_group_ids", []))
    if train_ids != sorted(eligible_training_structure_ids):
        raise RuntimeError("Final-fit eligible training structure membership mismatch")
    if train_groups != sorted(eligible_training_design_group_ids):
        raise RuntimeError("Final-fit eligible training design-group membership mismatch")
    if set(train_ids) & set(outer_test_structure_ids):
        raise RuntimeError("Final-fit training/outer-test structure overlap")
    if set(train_groups) & set(outer_test_design_group_ids):
        raise RuntimeError("Final-fit training/outer-test design-group overlap")


def _source(contract: dict[str, Any], identifier: str) -> Path:
    matches = [item for item in contract["source_artifacts"] if item["id"] == identifier]
    if len(matches) != 1:
        raise RuntimeError(f"Missing or ambiguous source artifact: {identifier}")
    return Path(matches[0]["path"])


def _validate_source_hashes(contract: dict[str, Any]) -> None:
    for item in contract["source_artifacts"]:
        if item.get("kind") == "directory":
            continue
        path = Path(item["path"])
        if not path.is_file() or file_sha256(path) != item["sha256"]:
            raise RuntimeError(f"Frozen source hash mismatch: {item['id']}")


def _validate_d01_seal(contract_path: Path, availability_path: Path) -> dict[str, Any]:
    d_run = contract_path.parent
    review_path = d_run / "review" / "D01" / "independent_review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("acceptance_status") != "PASS" or review.get("next_task") != "D02":
        raise RuntimeError("D01 independent review is not PASS/READY for D02")
    expected = {item["path"]: item["sha256"] for item in review["reviewed_inputs"]}
    for path in (contract_path, availability_path):
        if expected.get(str(path)) != file_sha256(path):
            raise RuntimeError(f"D01 reviewed input hash mismatch: {path}")
    return review


def _native(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["eligible_main"] and row["scored"]]


def _label_index(frame: pd.DataFrame, target: str) -> dict[str, pd.Series]:
    target_rows = frame.loc[frame["parameter"].eq(target)]
    return {row.structure_key: row for row in target_rows.itertuples(index=False)}


def _label_value(label: Any, field: str) -> Any:
    return getattr(label, field)


def _check_prediction_identity(
    row: pd.Series,
    label: Any,
    *,
    cell: dict[str, Any],
    model: str,
) -> None:
    expected = {
        "population": cell["population"],
        "scope": cell["scope"],
        "target": cell["target"],
        "model": model,
        "structure_key": label.structure_key,
        "p3_structure_id": label.p3_structure_id,
        "p3_design_group_id": label.p3_design_group_id,
        "p3_branch_id": label.p3_branch_id,
        "unit": cell["unit"],
        "model_bundle_sha256": cell["model_bundle_sha256"],
    }
    for field, value in expected.items():
        if row[field] != value:
            raise RuntimeError(f"Existing prediction identity mismatch: {field}/{model}")
    for field in ("eg_um", "fl_um", "area_um2", "has_pf", "n_valid"):
        if float(row[field]) != float(_label_value(label, field)):
            raise RuntimeError(f"Existing prediction feature/support mismatch: {field}/{model}")
    if _boolean(row["low_support"]) != _boolean(label.low_support):
        raise RuntimeError(f"Existing prediction low_support mismatch: {model}")


def _existing_records(
    prediction_rows: pd.DataFrame,
    *,
    cell: dict[str, Any],
    model: str,
    labels: dict[str, Any],
    ulp_counts: Counter[int],
) -> list[dict[str, Any]]:
    if len(prediction_rows) != len(labels) or set(prediction_rows["structure_key"]) != set(labels):
        raise RuntimeError(
            f"Existing outer-test coverage mismatch: "
            f"{cell['population']}/{cell['scope']}/{cell['target']}/{model}"
        )
    records = []
    for _, row in prediction_rows.sort_values("structure_key").iterrows():
        label = labels[row["structure_key"]]
        _check_prediction_identity(row, label, cell=cell, model=model)
        actual, ulp = canonical_actual(row["actual"], label.median)
        ulp_counts[ulp] += 1
        prediction = float(row["prediction"])
        supported = bool(np.isfinite(prediction) and prediction > 0)
        valid_actual = bool(np.isfinite(actual) and actual > 0)
        eligible_main = bool(
            valid_actual and int(label.n_valid) >= 5 and not _boolean(label.low_support)
        )
        scored = bool(valid_actual and supported)
        if _boolean(row["eligible_main"]) != eligible_main or _boolean(row["scored"]) != scored:
            raise RuntimeError(f"Existing prediction validity/scoring mismatch: {model}")
        records.append(
            {
                "structure_key": label.structure_key,
                "p3_structure_id": label.p3_structure_id,
                "p3_design_group_id": label.p3_design_group_id,
                "p3_branch_id": label.p3_branch_id,
                "unit": cell["unit"],
                "label_sha256": cell["label_sha256"],
                "training_id": cell["training_ids"][model],
                "model_bundle_sha256": cell["model_bundle_sha256"],
                "actual": actual,
                "prediction": prediction if supported else None,
                "eligible_main": eligible_main,
                "scored": scored,
            }
        )
    return records


def _p3_output_records(
    test: pd.DataFrame,
    predicted: np.ndarray,
    *,
    cell: dict[str, Any],
) -> list[dict[str, Any]]:
    if len(predicted) != len(test):
        raise RuntimeError("P3 outer prediction length mismatch")
    records = []
    for row, value in zip(test.itertuples(index=False), predicted, strict=True):
        actual = float(row.median)
        prediction = float(value)
        valid_actual = bool(np.isfinite(actual) and actual > 0)
        supported = bool(np.isfinite(prediction) and prediction > 0)
        eligible_main = bool(valid_actual and row.n_valid >= 5 and not row.low_support)
        scored = bool(valid_actual and supported)
        error = prediction - actual if scored else None
        records.append(
            {
                "population": cell["population"],
                "scope": cell["scope"],
                "target": cell["target"],
                "model": P3,
                "model_role": "d02_saved_outer_fold",
                "prediction_id": stable_id(
                    "outer",
                    [
                        cell["population"],
                        cell["scope"],
                        cell["target"],
                        "d02_p3",
                        row.structure_key,
                    ],
                ),
                "structure_key": row.structure_key,
                "p3_structure_id": row.p3_structure_id,
                "p3_design_group_id": row.p3_design_group_id,
                "p3_branch_id": row.p3_branch_id,
                "unit": cell["unit"],
                "actual": actual,
                "prediction": prediction if supported else None,
                "signed_error": error,
                "absolute_error": abs(error) if error is not None else None,
                "squared_error": error**2 if error is not None else None,
                "scored": scored,
                "eligible_main": eligible_main,
                "support_layer": (
                    "main"
                    if eligible_main
                    else ("low_support" if valid_actual else "missing_or_invalid")
                ),
                "status": "SCORED" if scored else "NOT_SCORED",
                "rejection_reason": (
                    None
                    if scored
                    else (
                        "missing_or_invalid_target"
                        if not valid_actual
                        else "unsupported_branch_or_invalid_prediction"
                    )
                ),
                "n_valid": int(row.n_valid),
                "low_support": bool(row.low_support),
                "eg_um": float(row.eg_um),
                "fl_um": float(row.fl_um),
                "area_um2": float(row.area_um2),
                "has_pf": int(row.has_pf),
                "model_bundle_sha256": cell["model_bundle_sha256"],
                "training_id": cell["training_ids"][P3],
                "label_sha256": cell["label_sha256"],
            }
        )
    return records


def _p3_records(
    model: Any,
    test: pd.DataFrame,
    *,
    cell: dict[str, Any],
) -> list[dict[str, Any]]:
    return _p3_output_records(test, model.predict(test), cell=cell)


def run_comparison(contract_path: Path, *, reuse_p3: Path | None = None) -> dict[str, Any]:
    started = time.monotonic()
    contract_path = contract_path.resolve()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if (
        contract.get("task_id") != "D01"
        or contract.get("schema") != "aln_domain_candidates_d01_comparison_contract_v1"
    ):
        raise RuntimeError("Unexpected comparison contract")
    d_run = Path(contract["d_run"]).resolve()
    if contract_path != d_run / "comparison_contract.json":
        raise RuntimeError("Contract path does not match frozen D run")
    output_dir = d_run / "comparison"
    availability_path = Path(contract["availability_path"]).resolve()
    review = _validate_d01_seal(contract_path, availability_path)
    _validate_source_hashes(contract)
    frozen_columns = contract["d02_contract_frozen_before_results"]
    if frozen_columns["model_comparison_columns_frozen"] != MODEL_COMPARISON_FIELDS:
        raise RuntimeError("Model-comparison schema differs from frozen D01 contract")
    if frozen_columns["paired_predictions_columns_frozen"] != PAIRED_PREDICTION_FIELDS:
        raise RuntimeError("Paired-prediction schema differs from frozen D01 contract")
    if frozen_columns["relative_near_zero_rule"]["threshold_abs_actual"] != NEAR_ZERO_ABS_ACTUAL:
        raise RuntimeError("Near-zero threshold differs from frozen D01 contract")
    pairs = frozen_columns["required_canonical_pairs"]
    if len(pairs) != 3 or any(pair["method_a"] == pair["method_b"] for pair in pairs):
        raise RuntimeError("Frozen canonical pairs are invalid")
    if any("b_inner_selected" in (pair["method_a"], pair["method_b"]) for pair in pairs):
        raise RuntimeError("SAME_METHOD alias must not become a performance pair")

    output_paths = {
        "p3_outer_predictions": output_dir / "p3_outer_predictions.csv",
        "model_comparison": output_dir / "model_comparison.csv",
        "paired_predictions": output_dir / "paired_predictions.csv",
        "execution_summary": output_dir / "execution_summary.json",
    }
    for path in output_paths.values():
        if reuse_p3 is None and path.exists():
            raise FileExistsError(f"Refusing to overwrite D02 output: {path}")
    if reuse_p3 is not None:
        reuse_p3 = reuse_p3.resolve()
        if reuse_p3 != output_paths["p3_outer_predictions"] or not all(
            path.is_file() for path in output_paths.values()
        ):
            raise RuntimeError(
                "Canonicalization-only recompute requires the complete existing D02 output set"
            )

    source_run = Path(contract["source_run"])
    protected_before = {}
    for name in ("data", "models", "evaluation"):
        digest, files, size = _tree_sha256(source_run / name)
        protected_before[name] = {"tree_sha256": digest, "files": files, "bytes": size}
        expected = review["protection"]["protected_trees"][f"b_{name}"]
        if (
            digest != expected["tree_sha256"]
            or files != expected["files"]
            or size != expected["bytes"]
        ):
            raise RuntimeError(f"Protected B tree changed before D02: {name}")

    with availability_path.open(newline="", encoding="utf-8") as handle:
        availability = list(csv.DictReader(handle))
    if len(availability) != 160:
        raise RuntimeError("Availability row count changed")
    missing = contract["availability_audit"]["unique_missing_prediction_cell_list"]
    missing_keys = {
        (row["population"], row["scope"], row["target"], row["model"]) for row in missing
    }
    if len(missing) != 40 or len(missing_keys) != 40 or {row["model"] for row in missing} != {P3}:
        raise RuntimeError("D02 inference set is not exactly the 40 missing P3 cells")
    if any(row["refit_needed"] for row in missing):
        raise RuntimeError("D02 saved-model inference must not refit")

    protocol = json.loads(_source(contract, "b_protocol").read_text(encoding="utf-8"))
    splits = json.loads(_source(contract, "b_splits").read_text(encoding="utf-8"))
    final_fits = json.loads(_source(contract, "b_final_fit_index").read_text(encoding="utf-8"))[
        "fits"
    ]
    fit_map = {
        (fit["population"], fit["scope"], fit["target"], fit["model"]): fit for fit in final_fits
    }
    if len(fit_map) != 120:
        raise RuntimeError("Final-fit index does not contain 120 unique method cells")
    scope_map = {scope["scope_id"]: scope for scope in splits["scopes"]}
    loaded = _load_canonical_populations(protocol, source_run / "data")
    prediction_path = _source(contract, "b_evaluation_predictions")
    predictions = _read_csv_round_trip(prediction_path)
    reused_p3_predictions = (
        _read_csv_round_trip(reuse_p3) if reuse_p3 is not None else None
    )
    p3_configuration = next(
        method["configuration"] for method in contract["methods"] if method["method"] == P3
    )

    all_p3_rows: list[dict[str, Any]] = []
    comparison_rows: list[dict[str, Any]] = []
    paired_rows: list[dict[str, Any]] = []
    ulp_counts: Counter[int] = Counter()
    reused_p3_ulp_counts: Counter[int] = Counter()
    lineage_checks = 0
    existing_rows_reused = 0
    cells = contract["comparison_cells"]
    if len(cells) != 40:
        raise RuntimeError("Frozen comparison-cell count changed")
    for cell_index, cell in enumerate(cells, start=1):
        population, scope_id, target = (cell[key] for key in ("population", "scope", "target"))
        cell_key = (population, scope_id, target)
        if (population, scope_id, target, P3) not in missing_keys:
            raise RuntimeError(f"Unexpected P3 inference cell: {cell_key}")
        if cell["selected_model"] != P6C or cell["p3_outer_prediction_count"] != 0:
            raise RuntimeError(f"Frozen selected/P3 availability mismatch: {cell_key}")
        scope = scope_map[scope_id]
        outer_train = scope["outer"]["train"]
        outer_test = scope["outer"]["test"]
        test_ids = sorted(outer_test["structure_ids"])
        test_groups = sorted(outer_test["design_group_ids"])
        if (
            cell["split_outer_test_structure_ids_sha256"]
            != outer_test["id_hashes"]["structure_ids"]
        ):
            raise RuntimeError(f"Outer-test split hash mismatch: {cell_key}")
        frame = loaded[population]["frame"]
        target_frame = frame.loc[frame["parameter"].eq(target)].copy()
        test = target_frame.loc[target_frame["structure_key"].isin(test_ids)].copy()
        test = test.sort_values("structure_key").reset_index(drop=True)
        if test["structure_key"].tolist() != test_ids or len(test) != cell["test_structure_count"]:
            raise RuntimeError(f"Canonical outer-test label coverage mismatch: {cell_key}")
        if sorted(test["p3_design_group_id"].unique().tolist()) != test_groups:
            raise RuntimeError(f"Outer-test design-group mismatch: {cell_key}")
        train = target_frame.loc[
            target_frame["structure_key"].isin(outer_train["structure_ids"])
        ].copy()
        eligible_train = train.loc[
            train["median"].map(lambda value: np.isfinite(value) and value > 0)
            & train["n_valid"].ge(5)
            & ~train["low_support"]
        ]
        eligible_train_ids = sorted(eligible_train["structure_key"].tolist())
        eligible_train_groups = sorted(eligible_train["p3_design_group_id"].unique().tolist())
        if len(eligible_train_ids) != cell["train_eligible_structure_count"]:
            raise RuntimeError(f"Eligible outer-training count mismatch: {cell_key}")

        bundle_path = Path(cell["model_bundle_path"])
        if file_sha256(bundle_path) != cell["model_bundle_sha256"]:
            raise RuntimeError(f"Model-bundle hash mismatch: {cell_key}")
        bundle = joblib.load(bundle_path)
        metadata = bundle.get("metadata", {})
        sidecar = json.loads(bundle_path.with_name("metadata.json").read_text(encoding="utf-8"))
        for field, expected in (
            ("population", population),
            ("scope", scope_id),
            ("target", target),
            ("selected_model", P6C),
            ("training_structure_ids", eligible_train_ids),
            ("training_design_group_ids", eligible_train_groups),
            ("final_training_ids", cell["training_ids"]),
            ("outer_test_used_for_selection", False),
            ("old_weights_loaded", False),
        ):
            if metadata.get(field) != expected or sidecar.get(field) != expected:
                raise RuntimeError(f"Bundle lineage metadata mismatch: {field}/{cell_key}")
        if metadata["label_source"]["sha256"] != cell["label_sha256"]:
            raise RuntimeError(f"Bundle label hash mismatch: {cell_key}")
        models = bundle.get("models", {})
        if set(models) != set(CANONICAL_METHODS):
            raise RuntimeError(f"Bundle method set mismatch: {cell_key}")
        for model_name in CANONICAL_METHODS:
            key = (*cell_key, model_name)
            fit = fit_map.get(key)
            if fit is None or fit["label_sha256"] != cell["label_sha256"]:
                raise RuntimeError(f"Missing/mismatched final-fit record: {key}")
            expected_training_id = stable_id(
                "train",
                [population, scope_id, target, model_name, "final", eligible_train_ids],
            )
            if expected_training_id != cell["training_ids"][model_name]:
                raise RuntimeError(f"Recomputed training_id mismatch: {key}")
            validate_fold_lineage(
                fit=fit,
                expected_training_id=expected_training_id,
                eligible_training_structure_ids=eligible_train_ids,
                eligible_training_design_group_ids=eligible_train_groups,
                outer_test_structure_ids=test_ids,
                outer_test_design_group_ids=test_groups,
            )
            lineage_checks += 1
        p3_model = models[P3]
        if (
            p3_model.model_name != P3
            or p3_model.target != target
            or p3_model.config != p3_configuration
        ):
            raise RuntimeError(f"Frozen P3 identity/configuration mismatch: {cell_key}")
        if metadata["configurations"][P3] != p3_configuration:
            raise RuntimeError(f"Bundle P3 configuration mismatch: {cell_key}")
        for model_name in (P6C, CONSTANT):
            if models[model_name].model_name != model_name or models[model_name].target != target:
                raise RuntimeError(f"Existing method identity mismatch: {model_name}/{cell_key}")

        labels = _label_index(frame, target)
        test_labels = {key: labels[key] for key in test_ids}
        native_by_method = {}
        for model_name, role in ((P6C, "selected"), (CONSTANT, "constant")):
            selected = predictions.loc[
                predictions["population"].eq(population)
                & predictions["scope"].eq(scope_id)
                & predictions["target"].eq(target)
                & predictions["model"].eq(model_name)
                & predictions["model_role"].eq(role)
            ]
            existing = _existing_records(
                selected,
                cell=cell,
                model=model_name,
                labels=test_labels,
                ulp_counts=ulp_counts,
            )
            existing_rows_reused += len(existing)
            native_by_method[model_name] = _native(existing)
            expected_count = cell["existing_main_scored_prediction_counts"][model_name]
            if len(native_by_method[model_name]) != expected_count:
                raise RuntimeError(
                    f"Existing native prediction count mismatch: {model_name}/{cell_key}"
                )

        test_for_prediction = test.copy()
        test_for_prediction[target] = test_for_prediction["median"]
        if reused_p3_predictions is None:
            generated = _p3_records(p3_model, test_for_prediction, cell=cell)
        else:
            selected_p3 = reused_p3_predictions.loc[
                reused_p3_predictions["population"].eq(population)
                & reused_p3_predictions["scope"].eq(scope_id)
                & reused_p3_predictions["target"].eq(target)
                & reused_p3_predictions["model"].eq(P3)
                & reused_p3_predictions["model_role"].eq("d02_saved_outer_fold")
            ].sort_values("structure_key")
            if (
                len(selected_p3) != len(test_labels)
                or set(selected_p3["structure_key"]) != set(test_labels)
            ):
                raise RuntimeError(f"Reused P3 outer-test coverage mismatch: {cell_key}")
            for _, row in selected_p3.iterrows():
                label = test_labels[row["structure_key"]]
                _check_prediction_identity(row, label, cell=cell, model=P3)
                if (
                    row["training_id"] != cell["training_ids"][P3]
                    or row["label_sha256"] != cell["label_sha256"]
                ):
                    raise RuntimeError(f"Reused P3 lineage mismatch: {cell_key}")
                _, ulp = canonical_actual(row["actual"], label.median)
                reused_p3_ulp_counts[ulp] += 1
            generated = _p3_output_records(
                test_for_prediction,
                selected_p3["prediction"].to_numpy(float),
                cell=cell,
            )
        all_p3_rows.extend(generated)
        native_by_method[P3] = _native(generated)
        if len(generated) != cell["test_structure_count"]:
            raise RuntimeError(f"P3 outer prediction count mismatch: {cell_key}")

        for pair in pairs:
            method_a, method_b = pair["method_a"], pair["method_b"]
            lineage = {
                name: {
                    "training_id": cell["training_ids"][name],
                    "model_bundle_sha256": cell["model_bundle_sha256"],
                }
                for name in (method_a, method_b)
            }
            comparison, paired = build_pair_comparison(
                population=population,
                scope=scope_id,
                target=target,
                comparison_id=pair["comparison_id"],
                method_a=method_a,
                method_b=method_b,
                rows_a=native_by_method[method_a],
                rows_b=native_by_method[method_b],
                lineage_a=lineage[method_a],
                lineage_b=lineage[method_b],
            )
            comparison_rows.append(comparison)
            paired_rows.extend(paired)
        print(
            f"D02_CELL {cell_index}/40 {population}/{scope_id}/{target} "
            f"p3_rows={len(generated)} p3_native={len(native_by_method[P3])}",
            flush=True,
        )

    if len(all_p3_rows) == 0 or len(comparison_rows) != 120:
        raise RuntimeError("D02 output cardinality mismatch")
    if sum(ulp_counts.values()) != existing_rows_reused or max(ulp_counts, default=0) > 1:
        raise RuntimeError("Existing actual canonicalization audit failed")
    protected_after = {}
    for name in ("data", "models", "evaluation"):
        digest, files, size = _tree_sha256(source_run / name)
        protected_after[name] = {"tree_sha256": digest, "files": files, "bytes": size}
        if protected_after[name] != protected_before[name]:
            raise RuntimeError(f"Protected B tree changed during D02: {name}")

    write_csv(output_paths["p3_outer_predictions"], all_p3_rows, P3_PREDICTION_FIELDS)
    write_csv(output_paths["model_comparison"], comparison_rows, MODEL_COMPARISON_FIELDS)
    write_csv(output_paths["paired_predictions"], paired_rows, PAIRED_PREDICTION_FIELDS)
    for name, fieldnames, expected_rows in (
        ("p3_outer_predictions", P3_PREDICTION_FIELDS, len(all_p3_rows)),
        ("model_comparison", MODEL_COMPARISON_FIELDS, 120),
        ("paired_predictions", PAIRED_PREDICTION_FIELDS, len(paired_rows)),
    ):
        with output_paths[name].open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            readback = list(reader)
        if reader.fieldnames != fieldnames or len(readback) != expected_rows:
            raise RuntimeError(f"D02 output readback mismatch: {name}")

    sample_statuses = Counter(row["sample_status"] for row in paired_rows)
    relative_statuses = Counter(
        row[f"relative_status_{side}"]
        for row in paired_rows
        for side in ("a", "b")
        if row[f"relative_status_{side}"] is not None
    )
    summary = {
        "schema": "aln_domain_candidates_d02_execution_summary_v1",
        "schema_version": "1.0.0",
        "task_id": "D02",
        "attempt_id": contract["attempt_id"],
        "scientific_status": "RETROSPECTIVE_ONLY",
        "trusted": False,
        "source_script": file_record("source:run_domain_candidates_v1", Path(__file__).resolve()),
        "source_contract": file_record("d01_comparison_contract", contract_path),
        "source_availability": file_record("d01_availability", availability_path),
        "protected_b_trees_unchanged": True,
        "protected_b_trees_before": protected_before,
        "protected_b_trees_after": protected_after,
        "counts": {
            "comparison_cells": len(cells),
            "new_p3_prediction_cells": len(
                {(row["population"], row["scope"], row["target"]) for row in all_p3_rows}
            ),
            "new_p3_prediction_rows": len(all_p3_rows),
            "new_p3_native_prediction_rows": sum(
                row["eligible_main"] and row["scored"] for row in all_p3_rows
            ),
            "existing_prediction_rows_reused": existing_rows_reused,
            "comparison_rows": len(comparison_rows),
            "paired_prediction_rows": len(paired_rows),
            "common_rows": sample_statuses["COMMON"],
            "only_a_rows": sample_statuses["ONLY_A"],
            "only_b_rows": sample_statuses["ONLY_B"],
            "native_loss_a": sum(int(row["n_lost_a"]) for row in comparison_rows),
            "native_loss_b": sum(int(row["n_lost_b"]) for row in comparison_rows),
            "near_zero_relative_na_by_side": relative_statuses["NEAR_ZERO_ACTUAL"],
            "final_fit_lineage_checks": lineage_checks,
            "train_test_structure_overlap": 0,
            "train_test_design_group_overlap": 0,
            "missing_prediction_cells_before": 40,
            "missing_prediction_cells_after": 0,
            "missing_model_cells": 0,
            "not_comparable_cells": 0,
            "refit_performed_cells": 0,
            "fit_calls": 0,
            "p3_predict_calls_this_run": 0 if reuse_p3 is not None else len(cells),
            "p6c_predict_calls_this_run": 0,
            "constant_predict_calls_this_run": 0,
        },
        "existing_actual_ulp_counts": {str(key): ulp_counts[key] for key in sorted(ulp_counts)},
        "reused_p3_actual_ulp_counts": {
            str(key): reused_p3_ulp_counts[key] for key in sorted(reused_p3_ulp_counts)
        },
        "execution_mode": (
            "CANONICALIZATION_RECOMPUTE_NO_INFERENCE"
            if reuse_p3 is not None
            else "SAVED_OUTER_FOLD_MODEL_INFERENCE"
        ),
        "method_identity": {
            "b_inner_selected_vs_p6c": "SAME_METHOD",
            "alias_performance_pair_emitted": False,
            "independent_double_model_evidence": False,
        },
        "excluded_work": {
            "fitting_or_refit": False,
            "other_new_predictions": False,
            "D03_policy_or_winner_selection": False,
            "B_or_P13_modification": False,
            "external_or_prospective_validation": False,
        },
        "outputs": [
            file_record(name, path)
            for name, path in output_paths.items()
            if name != "execution_summary"
        ],
        "elapsed_seconds": time.monotonic() - started,
    }
    write_json(output_paths["execution_summary"], summary)
    print("D02_SUMMARY " + json.dumps(summary["counts"], sort_keys=True), flush=True)
    return summary


D05_TARGETS = ("k2eff_pct", "qp", "qs", "bodeq_fitted")
D05_AXES = ("eg_um", "fl_um", "area_um2")
D05_CATEGORIES = (
    "MEASURED_CONTROL",
    "UNMEASURED_INTERPOLATION",
    "UNMEASURED_SPARSE_SUPPORT",
    "UNMEASURED_EXTRAPOLATION",
    "UNSUPPORTED",
)
D05_MODEL = "p6c_trend_gp_log_area_scalar"
D05_PERFORMANCE_EXCLUSION = "D03_ZERO_ALLOWED_RANGES"


def _d05_canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _d05_decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"Invalid decimal geometry value: {value!r}")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal geometry value: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"Nonfinite geometry value: {value!r}")
    return number


def _d05_decimal_token(value: Any) -> str:
    number = _d05_decimal(value)
    if number == 0:
        return "0"
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _d05_full_identity(point: dict[str, Any]) -> dict[str, Any]:
    material = point["material"]
    geometry = material["geometry"]
    return {
        "process_scope": point["process_scope"],
        "business_scope": point["business_scope"],
        "topology_value": material["topology"]["value"],
        "cohort": material["cohort"],
        "ag_status": material["ag"]["status"],
        "normalized_ag_um": material["ag"]["normalized_ag_um"],
        "has_pf": geometry["has_pf"],
        "p3_branch_id": point["p3_branch_id"],
        "eg_um_normalized_decimal": geometry["eg_um"],
        "fl_um_normalized_decimal": geometry["fl_um"],
        "area_um2_normalized_decimal": geometry["area_um2"],
        "mapping_semantics": material["mapping_semantics"],
        "structure_key_material": material,
    }


def refresh_point_identity(point: dict[str, Any]) -> dict[str, Any]:
    """Recompute B01 identity and the deterministic D05 full key from material."""
    full_key = _d05_full_identity(point)
    full_json = _d05_canonical_json(full_key)
    material_json = _d05_canonical_json(point["material"])
    point["full_structure_key_json"] = full_json
    point["full_structure_key_sha256"] = hashlib.sha256(full_json.encode("utf-8")).hexdigest()
    point["structure_key"] = "struct_" + hashlib.sha256(
        material_json.encode("utf-8")
    ).hexdigest()
    point["point_id"] = stable_id("d05", [full_json])
    return point


def make_research_point(
    material: dict[str, Any],
    *,
    p3_branch_id: str,
    business_scope: str,
    process_scope: str = "deembedded_primary",
) -> dict[str, Any]:
    point = {
        "material": copy.deepcopy(material),
        "process_scope": process_scope,
        "business_scope": business_scope,
        "p3_branch_id": p3_branch_id,
        "p3_structure_id": None,
        "p3_design_group_id": None,
        "generation_rule": "EXPLICIT_RESEARCH_POINT",
        "source_structure_keys": [],
    }
    return refresh_point_identity(point)


def quantize_geometry(
    point: dict[str, Any], quantization_steps: dict[str, Any]
) -> dict[str, Any]:
    """Apply only explicitly supplied research steps, then recompute identity."""
    unknown = set(quantization_steps) - set(D05_AXES)
    if unknown:
        raise ValueError(f"Unknown quantization axes: {sorted(unknown)}")
    quantized = copy.deepcopy(point)
    for axis, raw_step in quantization_steps.items():
        step = _d05_decimal(raw_step)
        if step <= 0:
            raise ValueError("Quantization steps must be positive")
        value = _d05_decimal(quantized["material"]["geometry"][axis])
        units = (value / step).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
        quantized["material"]["geometry"][axis] = _d05_decimal_token(units * step)
    quantized["generation_rule"] = "EXPLICIT_QUANTIZATION_THEN_RECLASSIFY"
    quantized.pop("generation_axis", None)
    quantized.pop("generation_bracket", None)
    quantized.pop("support", None)
    return refresh_point_identity(quantized)


def _d05_geometry(point: dict[str, Any]) -> tuple[dict[str, Decimal], bool]:
    material = point["material"]
    geometry = material["geometry"]
    values = {axis: _d05_decimal(geometry[axis]) for axis in D05_AXES}
    has_pf = geometry["has_pf"]
    if not isinstance(has_pf, bool):
        raise ValueError("has_pf must be boolean in the full structure material")
    return values, has_pf


def _d05_fixed_geometry_key(
    axis: str, geometry: dict[str, Decimal], has_pf: bool
) -> str:
    value: dict[str, Any] = {"has_pf": has_pf}
    for name in D05_AXES:
        if name != axis:
            value[name] = _d05_decimal_token(geometry[name])
    return _d05_canonical_json(value)


def _d05_port(point: dict[str, Any]) -> str:
    port = point.get("port")
    if port not in {"S11", "S22"}:
        raise ValueError(f"Unknown B01 source port: {port!r}")
    return port


def _d05_slice_key(point: dict[str, Any], axis: str) -> tuple[str, str, str, str]:
    """Key a one-axis slice by B01 source/non-geometry identity plus fixed geometry."""
    material = point["material"]
    geometry, has_pf = _d05_geometry(point)
    source_identity = {
        "process_scope": point["process_scope"],
        "business_scope": point["business_scope"],
        "structure_schema": material["schema"],
        "topology": material["topology"],
        "cohort": material["cohort"],
        "ag": material["ag"],
        "mapping_semantics": material["mapping_semantics"],
        "port": _d05_port(point),
    }
    return (
        point["p3_branch_id"],
        axis,
        _d05_canonical_json(source_identity),
        _d05_fixed_geometry_key(axis, geometry, has_pf),
    )


def _d05_domain_fixed_key(domain: dict[str, Any]) -> str:
    fixed = domain["fixed_other_geometry"]
    geometry = {
        axis: _d05_decimal(fixed[axis]) for axis in D05_AXES if axis in fixed
    }
    return _d05_fixed_geometry_key(
        domain["axis"], geometry, _boolean(fixed["has_pf"])
    )


def _d05_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, float) and np.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "null"}:
        return None
    return json.loads(text)


def build_support_catalog(
    measured_points: list[dict[str, Any]], domain_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Index exact measured identities, numerical one-axis slices, and D03 ranges."""
    measured_by_key: dict[str, dict[str, Any]] = {}
    slices: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = {}
    branch_levels: dict[str, dict[str, set[Decimal]]] = {}
    for original in measured_points:
        point = refresh_point_identity(original)
        key = point["full_structure_key_json"]
        if key in measured_by_key:
            raise RuntimeError("Duplicate measured full structure key")
        measured_by_key[key] = point
        geometry, has_pf = _d05_geometry(point)
        branch = point["p3_branch_id"]
        levels = branch_levels.setdefault(branch, {axis: set() for axis in D05_AXES})
        for axis in D05_AXES:
            levels[axis].add(geometry[axis])
            key_1d = _d05_slice_key(point, axis)
            level_map = slices.setdefault(key_1d, {})
            token = _d05_decimal_token(geometry[axis])
            existing = level_map.get(token)
            if existing is not None and (
                existing["full_structure_key_json"] != point["full_structure_key_json"]
            ):
                raise RuntimeError(f"Non-unique measured level in exact slice: {key_1d}/{token}")
            level_map[token] = point

    extrapolation: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    interpolation: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for domain in domain_rows:
        if domain.get("population") != "primary":
            continue
        axis = domain["axis"]
        branch = domain["p3_branch_id"]
        fixed_key = _d05_domain_fixed_key(domain)
        if domain["direction"] in {"LOWER_EXTRAPOLATION", "UPPER_EXTRAPOLATION"}:
            key = (
                axis,
                domain["direction"],
                branch,
                fixed_key,
                _d05_decimal_token(domain["held_out_level"]),
            )
            extrapolation.setdefault(key, []).append(domain)
        elif domain["direction"] == "INTERPOLATION":
            bracket = _d05_json_value(domain.get("interpolation_bracket"))
            if bracket is not None:
                if len(bracket) != 2:
                    raise RuntimeError("Invalid D03 interpolation bracket")
                bracket_key = _d05_canonical_json(
                    [_d05_decimal_token(value) for value in bracket]
                )
                key = (axis, branch, fixed_key, bracket_key)
                interpolation.setdefault(key, []).append(domain)
    return {
        "measured_by_key": measured_by_key,
        "slices": slices,
        "branch_levels": {
            branch: {axis: sorted(values) for axis, values in axes.items()}
            for branch, axes in branch_levels.items()
        },
        "extrapolation": extrapolation,
        "interpolation": interpolation,
    }


def _d05_support(category: str) -> dict[str, Any]:
    return {
        "category": category,
        "axis": None,
        "direction": None,
        "physical_distance": None,
        "delta": None,
        "relative_distance_delta": None,
        "interpolation_bracket": None,
        "domain_id": None,
        "d03_policy_status": None,
        "d03_diagnostic_authorized": False,
        "historical_error_margin_k2_pp": None,
        "historical_error_margin_qp": None,
        "margin_status": "NO_CORRESPONDING_D03_RANGE",
        "prediction_allowed": category != "UNSUPPORTED",
        "diagnostic_candidate": category.startswith("UNMEASURED_"),
        "performance_candidate": False,
        "performance_exclusion_reason": D05_PERFORMANCE_EXCLUSION,
        "rejection_reasons": [],
        "exclusion_reasons": [],
    }


def _d05_unsupported(*reasons: str) -> dict[str, Any]:
    support = _d05_support("UNSUPPORTED")
    support["rejection_reasons"] = sorted(set(reasons))
    support["exclusion_reasons"] = list(support["rejection_reasons"])
    return support


def _d05_apply_domain(
    support: dict[str, Any], matches: list[dict[str, Any]], *, required: bool
) -> dict[str, Any] | None:
    if not matches:
        return support if not required else _d05_unsupported("D03_SLICE_VALIDATION_MISSING")
    if len(matches) != 1:
        return _d05_unsupported("AMBIGUOUS_D03_RANGE") if required else None
    domain = matches[0]
    support["domain_id"] = domain["domain_id"]
    support["d03_policy_status"] = domain["policy_status"]
    support["historical_error_margin_k2_pp"] = domain.get("historical_error_margin_k2_pp")
    support["historical_error_margin_qp"] = domain.get("historical_error_margin_qp")
    support["margin_status"] = (
        "CORRESPONDING_D03_RANGE"
        if support["historical_error_margin_k2_pp"] is not None
        and support["historical_error_margin_qp"] is not None
        else "CORRESPONDING_RANGE_MARGIN_MISSING"
    )
    return domain


def classify_point(
    point: dict[str, Any], catalog: dict[str, Any], protocol: dict[str, Any]
) -> dict[str, Any]:
    """Classify one geometry without consulting or producing performance values."""
    try:
        geometry, has_pf = _d05_geometry(point)
    except (KeyError, TypeError, ValueError):
        return _d05_unsupported("ILLEGAL_OR_NONFINITE_GEOMETRY")
    if (
        geometry["eg_um"] < 0
        or geometry["fl_um"] < 0
        or geometry["area_um2"] <= 0
    ):
        return _d05_unsupported("ILLEGAL_OR_NONFINITE_GEOMETRY")
    material = point["material"]
    topology = material.get("topology", {})
    ag = material.get("ag", {})
    if (
        topology.get("status") != "standard_mapping"
        or topology.get("value") != "EG_FL_PORT_AREA_PF"
        or material.get("cohort") != "standard_eg_fl_unrecorded_ag"
        or ag.get("status") != "unrecorded"
        or ag.get("normalized_ag_um") is not None
    ):
        return _d05_unsupported("UNKNOWN_TOPOLOGY_COHORT_OR_AG")
    mapping = material.get("mapping_semantics", {})
    if (
        mapping.get("has_pf") != has_pf
        or mapping.get("port_area_rule") != "first_area=S11;second_area=S22"
        or not mapping.get("normalized_area_s11")
        or not mapping.get("normalized_area_s22")
    ):
        return _d05_unsupported("UNKNOWN_PORT_AREA_MAPPING")

    branch = point["p3_branch_id"]
    try:
        _d05_port(point)
    except ValueError:
        return _d05_unsupported("UNKNOWN_PORT_IDENTITY")
    expected_branch = (
        f"pf{int(has_pf)}_eg{int(geometry['eg_um'] > 0)}_fl{int(geometry['fl_um'] > 0)}"
    )
    allowed_branches = set(protocol["geometry_constraints"]["allowed_research_branches"])
    if branch not in allowed_branches or branch not in catalog["branch_levels"]:
        return _d05_unsupported("UNKNOWN_BRANCH")
    if branch != expected_branch:
        return _d05_unsupported("BRANCH_GEOMETRY_MISMATCH")
    point = refresh_point_identity(point)
    measured = catalog["measured_by_key"].get(point["full_structure_key_json"])
    if measured is not None:
        support = _d05_support("MEASURED_CONTROL")
        support["diagnostic_candidate"] = False
        support["exclusion_reasons"] = ["MEASURED_CONTROL_CONTROL_ONLY"]
        support["measured_structure_key"] = measured["structure_key"]
        return support

    branch_range = catalog["branch_levels"][branch]
    outside = [
        axis
        for axis in D05_AXES
        if geometry[axis] < branch_range[axis][0] or geometry[axis] > branch_range[axis][-1]
    ]
    if len(outside) > 1:
        return _d05_unsupported("MULTIAXIS_OUT_OF_BOUNDS")
    if len(outside) == 1:
        axis = outside[0]
        slice_key = _d05_slice_key(point, axis)
        level_map = catalog["slices"].get(slice_key)
        if not level_map:
            return _d05_unsupported("FIXED_OTHER_GEOMETRY_NOT_MEASURED_SLICE")
        levels = sorted(_d05_decimal(token) for token in level_map)
        minimum_levels = int(
            protocol["extrapolation_rule"]["minimum_distinct_measured_levels_on_slice"]
        )
        if len(levels) < minimum_levels:
            return _d05_unsupported("INSUFFICIENT_MEASURED_LEVELS")
        direction = (
            "LOWER_EXTRAPOLATION" if geometry[axis] < levels[0] else "UPPER_EXTRAPOLATION"
        )
        if direction == "LOWER_EXTRAPOLATION":
            boundary, next_level = levels[0], levels[1]
            distance = boundary - geometry[axis]
        else:
            boundary, next_level = levels[-1], levels[-2]
            distance = geometry[axis] - boundary
        delta = abs(boundary - next_level)
        if delta <= 0 or distance <= 0:
            return _d05_unsupported("INVALID_EXTRAPOLATION_DISTANCE")
        relative = distance / delta
        increments = {
            _d05_decimal_token(value)
            for value in protocol["extrapolation_rule"]["increments_delta"]
        }
        if _d05_decimal_token(relative) not in increments:
            return _d05_unsupported("INVALID_EXTRAPOLATION_INCREMENT")
        domain_key = (
            axis,
            direction,
            branch,
            _d05_fixed_geometry_key(axis, geometry, has_pf),
            _d05_decimal_token(boundary),
        )
        matches = catalog["extrapolation"].get(domain_key, [])
        if not matches:
            return _d05_unsupported("D03_SLICE_VALIDATION_MISSING")
        if len(matches) != 1:
            return _d05_unsupported("AMBIGUOUS_D03_RANGE")
        domain = matches[0]
        if (
            domain.get("distance_support_status") != "SUPPORTED"
            or domain.get("policy_status") != "DIAGNOSTIC_ONLY"
            or domain.get("search_policy") is not None
            or domain.get("physical_extrapolation_distance") is None
            or domain.get("nearest_two_training_level_spacing") is None
        ):
            return _d05_unsupported("D03_DISTANCE_VALIDATION_MISSING")
        historical_distance = _d05_decimal(domain["physical_extrapolation_distance"])
        historical_delta = _d05_decimal(domain["nearest_two_training_level_spacing"])
        if historical_distance <= 0 or historical_delta <= 0:
            return _d05_unsupported("D03_DISTANCE_VALIDATION_MISSING")
        if distance > historical_distance or relative > historical_distance / historical_delta:
            return _d05_unsupported("EXCESSIVE_DISTANCE")
        support = _d05_support("UNMEASURED_EXTRAPOLATION")
        support.update(
            axis=axis,
            direction=direction,
            physical_distance=float(distance),
            delta=float(delta),
            relative_distance_delta=float(relative),
            d03_diagnostic_authorized=True,
        )
        _d05_apply_domain(support, matches, required=True)
        return support

    interpolation_matches = []
    for axis in D05_AXES:
        slice_key = _d05_slice_key(point, axis)
        level_map = catalog["slices"].get(slice_key)
        if not level_map:
            continue
        levels = sorted(_d05_decimal(token) for token in level_map)
        for lower, upper in zip(levels, levels[1:], strict=False):
            if (lower + upper) / 2 == geometry[axis]:
                interpolation_matches.append((axis, lower, upper))
                break
    if len(interpolation_matches) > 1:
        return _d05_unsupported("MULTIAXIS_INTERPOLATION")
    if interpolation_matches:
        axis, lower, upper = interpolation_matches[0]
        support = _d05_support("UNMEASURED_INTERPOLATION")
        support.update(
            axis=axis,
            direction="INTERPOLATION",
            physical_distance=float((upper - lower) / 2),
            delta=float(upper - lower),
            relative_distance_delta=0.5,
            interpolation_bracket=[float(lower), float(upper)],
        )
        bracket_key = _d05_canonical_json(
            [_d05_decimal_token(lower), _d05_decimal_token(upper)]
        )
        domain_key = (
            axis,
            branch,
            _d05_fixed_geometry_key(axis, geometry, has_pf),
            bracket_key,
        )
        matches = catalog["interpolation"].get(domain_key, [])
        domain = _d05_apply_domain(support, matches, required=False)
        if domain is None and matches:
            support["domain_id"] = None
            support["historical_error_margin_k2_pp"] = None
            support["historical_error_margin_qp"] = None
            support["margin_status"] = "AMBIGUOUS_D03_RANGE_MARGIN_NULL"
        return support
    support = _d05_support("UNMEASURED_SPARSE_SUPPORT")
    generation_axis = point.get("generation_axis")
    generation_bracket = point.get("generation_bracket")
    if generation_axis in D05_AXES and generation_bracket is not None:
        lower, upper = (_d05_decimal(value) for value in generation_bracket)
        level_map = catalog["slices"].get(
            _d05_slice_key(point, generation_axis), {}
        )
        if (
            lower < upper
            and (lower + upper) / 2 == geometry[generation_axis]
            and _d05_decimal_token(lower) in level_map
            and _d05_decimal_token(upper) in level_map
        ):
            support.update(
                axis=generation_axis,
                direction="INTERPOLATION",
                physical_distance=float((upper - lower) / 2),
                delta=float(upper - lower),
                relative_distance_delta=0.5,
                interpolation_bracket=[float(lower), float(upper)],
            )
            bracket_key = _d05_canonical_json(
                [_d05_decimal_token(lower), _d05_decimal_token(upper)]
            )
            domain_key = (
                generation_axis,
                branch,
                _d05_fixed_geometry_key(generation_axis, geometry, has_pf),
                bracket_key,
            )
            matches = catalog["interpolation"].get(domain_key, [])
            domain = _d05_apply_domain(support, matches, required=False)
            if domain is None and matches:
                support["domain_id"] = None
                support["historical_error_margin_k2_pp"] = None
                support["historical_error_margin_qp"] = None
                support["margin_status"] = "AMBIGUOUS_D03_RANGE_MARGIN_NULL"
    return support


def _d05_clone_at(
    template: dict[str, Any],
    axis: str,
    value: Decimal,
    rule: str,
    sources: list[str],
    bracket: tuple[Decimal, Decimal] | None = None,
) -> dict[str, Any]:
    point = copy.deepcopy(template)
    point["material"]["geometry"][axis] = _d05_decimal_token(value)
    point["p3_structure_id"] = None
    point["p3_design_group_id"] = None
    point["generation_rule"] = rule
    point["generation_axis"] = axis
    point["generation_bracket"] = (
        [float(bracket[0]), float(bracket[1])] if bracket is not None else None
    )
    point["source_structure_keys"] = sorted(sources)
    point.pop("support", None)
    return refresh_point_identity(point)


def generate_geometry(
    measured_points: list[dict[str, Any]],
    catalog: dict[str, Any],
    protocol: dict[str, Any],
    quantization_steps: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Freeze and classify all geometry before any model or performance evaluation."""
    generated = [copy.deepcopy(point) for point in measured_points]
    for point in generated:
        point["generation_rule"] = "MEASURED_CATALOG"
        refresh_point_identity(point)
    skipped_single_level_slices = 0
    increments = [
        _d05_decimal(value) for value in protocol["extrapolation_rule"]["increments_delta"]
    ]
    for slice_key, level_map in sorted(catalog["slices"].items()):
        axis = slice_key[1]
        levels = sorted((_d05_decimal(token) for token in level_map), key=float)
        for lower, upper in combinations(levels, 2):
            lower_point = level_map[_d05_decimal_token(lower)]
            upper_point = level_map[_d05_decimal_token(upper)]
            generated.append(
                _d05_clone_at(
                    lower_point,
                    axis,
                    (lower + upper) / 2,
                    "SINGLE_AXIS_PAIR_MIDPOINT",
                    [lower_point["structure_key"], upper_point["structure_key"]],
                    (lower, upper),
                )
            )
        if len(levels) < 2:
            skipped_single_level_slices += 1
            continue
        for direction in ("LOWER_EXTRAPOLATION", "UPPER_EXTRAPOLATION"):
            if direction == "LOWER_EXTRAPOLATION":
                boundary, neighbor = levels[0], levels[1]
                sign = Decimal(-1)
            else:
                boundary, neighbor = levels[-1], levels[-2]
                sign = Decimal(1)
            delta = abs(boundary - neighbor)
            template = level_map[_d05_decimal_token(boundary)]
            for increment in increments:
                generated.append(
                    _d05_clone_at(
                        template,
                        axis,
                        boundary + sign * delta * increment,
                        f"{direction}_{_d05_decimal_token(increment)}_DELTA",
                        [template["structure_key"]],
                    )
                )
    if quantization_steps is not None:
        generated = [quantize_geometry(point, quantization_steps) for point in generated]

    by_full_key: dict[str, list[dict[str, Any]]] = {}
    for point in generated:
        by_full_key.setdefault(point["full_structure_key_json"], []).append(point)
    points = []
    for full_key in sorted(by_full_key):
        duplicates = by_full_key[full_key]
        if len({_d05_port(item) for item in duplicates}) != 1:
            raise RuntimeError("Full structure key collision across B01 source ports")
        point = min(
            duplicates,
            key=lambda item: (
                item["generation_rule"] != "MEASURED_CATALOG",
                item["generation_rule"],
                _d05_canonical_json(item["source_structure_keys"]),
            ),
        )
        point = copy.deepcopy(point)
        point["support"] = classify_point(point, catalog, protocol)
        points.append(point)
    counts = Counter(point["support"]["category"] for point in points)
    maximum = int(protocol["geometry_budget_and_order"]["maximum_points"])
    stopped = len(points) > maximum
    report = {
        "schema": "aln_domain_candidates_d05_geometry_scale_v1",
        "status": (
            "STOPPED_GEOMETRY_BUDGET_EXCEEDED" if stopped else "READY_FOR_DIAGNOSTIC_INFERENCE"
        ),
        "generated_pre_deduplication": len(generated),
        "generated_post_deduplication": len(points),
        "duplicates_removed": len(generated) - len(points),
        "maximum_points": maximum,
        "prediction_allowed": not stopped,
        "geometry_frozen_before_inference": True,
        "counts": {category: counts[category] for category in D05_CATEGORIES},
        "skipped_single_level_slices": skipped_single_level_slices,
        "quantization_applied": quantization_steps is not None,
        "manufacturing_steps_inferred": False,
    }
    return points, report


class DResearchInferenceAdapter:
    """Isolated D-only diagnostic entry around verified frozen P6C model objects."""

    def __init__(self, research_model: Any):
        self._research_model = research_model
        self._models = research_model.models
        self.target_units = research_model.target_units

    def predict_points(self, points: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
        if not points:
            return {}
        for point in points:
            support = point["support"]
            if not support["prediction_allowed"] or support["category"] == "UNSUPPORTED":
                raise RuntimeError("Unsupported geometry cannot enter D research inference")
            if support["performance_candidate"]:
                raise RuntimeError("D05 diagnostic adapter cannot produce performance candidates")
            if (
                support["category"] == "UNMEASURED_EXTRAPOLATION"
                and not support["d03_diagnostic_authorized"]
            ):
                raise RuntimeError("Extrapolation lacks exact D03 diagnostic authorization")
        rows = []
        for point in points:
            geometry, has_pf = _d05_geometry(point)
            rows.append(
                {
                    "structure_key": point["structure_key"],
                    "p3_branch_id": point["p3_branch_id"],
                    "eg_um": float(geometry["eg_um"]),
                    "fl_um": float(geometry["fl_um"]),
                    "area_um2": float(geometry["area_um2"]),
                    "has_pf": int(has_pf),
                }
            )
        frame = pd.DataFrame(rows)
        output: dict[tuple[str, str], float] = {}
        for target in sorted(self._models):
            model = self._models[target]
            if getattr(model, "model_name", None) != D05_MODEL:
                raise RuntimeError(f"D05 requires frozen P6C, got {target}")
            predicted = np.asarray(model.predict(frame), dtype=float)
            if (
                predicted.shape != (len(points),)
                or not np.isfinite(predicted).all()
                or (predicted <= 0).any()
            ):
                raise RuntimeError(f"Invalid frozen P6C diagnostic output: {target}")
            for point, value in zip(points, predicted, strict=True):
                key = (point["point_id"], target)
                if key in output:
                    raise RuntimeError("Duplicate model/target/point prediction")
                output[key] = float(value)
        return output


GEOMETRY_FIELDS = [
    "point_id",
    "full_structure_key_sha256",
    "full_structure_key_json",
    "structure_key",
    "structure_key_material_json",
    "process_scope",
    "business_scope",
    "p3_structure_id",
    "p3_design_group_id",
    "p3_branch_id",
    "port",
    "eg_um",
    "fl_um",
    "area_um2",
    "has_pf",
    "mapping_semantics_json",
    "generation_rule",
    "source_structure_keys_json",
    "category",
    "axis",
    "direction",
    "physical_distance",
    "delta",
    "relative_distance_delta",
    "domain_id",
    "rejection_reasons_json",
]
SUPPORT_FIELDS = [
    *GEOMETRY_FIELDS,
    "interpolation_bracket_json",
    "d03_policy_status",
    "d03_diagnostic_authorized",
    "historical_error_margin_k2_pp",
    "historical_error_margin_qp",
    "margin_status",
    "prediction_allowed",
    "diagnostic_candidate",
    "diagnostic_status",
    "scientific_status",
    "method_status",
    "trusted",
    "performance_candidate",
    "performance_exclusion_reason",
    "exclusion_reasons_json",
    "manufacture_state",
    "manufacturing_release",
    "manufacturing_step",
    "manufacturing_tolerance",
    "model_bundle_sha256",
    "data_sha256",
    "protocol_sha256",
    "policy_sha256",
]
PREDICTION_FIELDS = [
    "point_id",
    "structure_key",
    "full_structure_key_sha256",
    "category",
    "target",
    "model",
    "model_role",
    "model_call_status",
    "prediction",
    "unit",
    "baseline_measured_median",
    "baseline_delta",
    "k2_improvement_pp",
    "qp_ratio",
    "historical_error_margin",
    "stress_gain_k2",
    "stress_qp_ratio",
    "axis",
    "physical_distance",
    "relative_distance_delta",
    "domain_id",
    "diagnostic_status",
    "scientific_status",
    "method_status",
    "trusted",
    "performance_candidate",
    "performance_exclusion_reason",
    "rejection_reasons_json",
    "manufacture_state",
    "model_bundle_sha256",
    "data_sha256",
    "protocol_sha256",
    "policy_sha256",
]
REJECTION_FIELDS = [
    *GEOMETRY_FIELDS,
    "diagnostic_status",
    "scientific_status",
    "method_status",
    "trusted",
    "performance_candidate",
    "performance_exclusion_reason",
    "manufacture_state",
    "manufacturing_release",
    "model_bundle_sha256",
    "data_sha256",
    "protocol_sha256",
    "policy_sha256",
]


def _d05_base_row(point: dict[str, Any], hashes: dict[str, str]) -> dict[str, Any]:
    material = point["material"]
    geometry, has_pf = _d05_geometry(point)
    support = point["support"]
    return {
        "point_id": point["point_id"],
        "full_structure_key_sha256": point["full_structure_key_sha256"],
        "full_structure_key_json": point["full_structure_key_json"],
        "structure_key": point["structure_key"],
        "structure_key_material_json": _d05_canonical_json(material),
        "process_scope": point["process_scope"],
        "business_scope": point["business_scope"],
        "p3_structure_id": point.get("p3_structure_id"),
        "p3_design_group_id": point.get("p3_design_group_id"),
        "p3_branch_id": point["p3_branch_id"],
        "port": _d05_port(point),
        "eg_um": float(geometry["eg_um"]),
        "fl_um": float(geometry["fl_um"]),
        "area_um2": float(geometry["area_um2"]),
        "has_pf": has_pf,
        "mapping_semantics_json": _d05_canonical_json(material["mapping_semantics"]),
        "generation_rule": point["generation_rule"],
        "source_structure_keys_json": _d05_canonical_json(point["source_structure_keys"]),
        **{key: support.get(key) for key in (
            "category", "axis", "direction", "physical_distance", "delta",
            "relative_distance_delta", "domain_id", "d03_policy_status",
            "d03_diagnostic_authorized", "historical_error_margin_k2_pp",
            "historical_error_margin_qp", "margin_status", "prediction_allowed",
            "diagnostic_candidate", "performance_candidate", "performance_exclusion_reason",
        )},
        "interpolation_bracket_json": (
            _d05_canonical_json(support["interpolation_bracket"])
            if support["interpolation_bracket"] is not None
            else None
        ),
        "rejection_reasons_json": _d05_canonical_json(support["rejection_reasons"]),
        "exclusion_reasons_json": _d05_canonical_json(support["exclusion_reasons"]),
        "diagnostic_status": "DIAGNOSTIC_ONLY",
        "scientific_status": "UNVERIFIED_CANDIDATE",
        "method_status": "single_method",
        "trusted": False,
        "manufacture_state": "VIRTUAL_ONLY",
        "manufacturing_release": False,
        "manufacturing_step": None,
        "manufacturing_tolerance": None,
        **hashes,
    }


def _d05_geometry_bounds(points: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for axis in D05_AXES:
        values = [float(_d05_geometry(point)[0][axis]) for point in points]
        result[axis] = {
            "generated_min": min(values) if values else None,
            "generated_max": max(values) if values else None,
        }
        for category in D05_CATEGORIES:
            selected = [
                float(_d05_geometry(point)[0][axis])
                for point in points
                if point["support"]["category"] == category
            ]
            result[axis][category] = {
                "min": min(selected) if selected else None,
                "max": max(selected) if selected else None,
            }
    return result


def _d05_validate_reviewed_inputs(protocol_path: Path, policy_path: Path) -> dict[str, Any]:
    d_run = protocol_path.parent
    review_path = d_run / "review" / "D04" / "independent_review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("acceptance_status") != "PASS" or review.get("next_task") != "D05":
        raise RuntimeError("D04 independent review has not released D05")
    for item in review["reviewed_artifacts"]:
        path = Path(item["path"])
        if not path.is_file() or file_sha256(path) != item["sha256"]:
            raise RuntimeError(f"D04 reviewed input hash mismatch: {path}")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if (
        protocol.get("schema") != "aln_domain_candidates_d04_search_protocol_v1"
        or protocol.get("protocol_status") != "FROZEN_BEFORE_D05"
        or protocol.get("trusted") is not False
    ):
        raise RuntimeError("Unexpected or unfrozen D04 search protocol")
    for item in protocol["inputs"]:
        path = Path(item["path"])
        if not path.is_file() or file_sha256(path) != item["sha256"]:
            raise RuntimeError(f"D04 registered input hash mismatch: {item['id']}")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("schema") != "aln_domain_candidates_d03_domain_policy_v1":
        raise RuntimeError("Unexpected D03 policy schema")
    if any(
        policy.get(name)
        for name in (
            "allowed_range_ids",
            "allowed_axes",
            "allowed_branches",
            "allowed_search_classes",
            "allowed_physical_distances_by_axis",
        )
    ) or any(row.get("search_policy") is not None for row in policy["range_policies"]):
        raise RuntimeError("D05 frozen run expects the empty D03 performance allowlist")
    permission = protocol["permissions"]
    if (
        permission["performance_search"]["enabled"] is not False
        or permission["diagnostic_search"]["enabled"] is not True
        or permission["diagnostic_search"]["status"] != "VIRTUAL_ONLY"
    ):
        raise RuntimeError("D04 performance/diagnostic permissions changed")
    fabrication_path = Path(permission["diagnostic_search"]["fabrication_inputs"])
    fabrication = json.loads(fabrication_path.read_text(encoding="utf-8"))
    if (
        fabrication.get("status") != "VIRTUAL_ONLY"
        or fabrication.get("manufacturing_release") is not False
        or fabrication.get("candidate_manufacturing_list_generated") is not False
    ):
        raise RuntimeError("Manufacturing remains VIRTUAL_ONLY")
    return {
        "protocol": protocol,
        "policy": policy,
        "fabrication": fabrication,
        "review": review,
        "review_path": review_path,
    }


def _d05_domain_rows(policy_path: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    d_run = policy_path.parent.parent
    report_path = policy_path.parent / "domain_report.csv"
    receipt_path = d_run / "receipts" / "D03.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    report_record = next(
        item for item in receipt["outputs"] if item["id"] == "domain_report.csv"
    )
    if (
        Path(report_record["path"]) != report_path
        or file_sha256(report_path) != report_record["sha256"]
    ):
        raise RuntimeError("D03 domain report hash mismatch")
    report = _read_csv_round_trip(report_path)
    report = report.loc[report["population"].eq("primary")]
    report_by_id = {row.domain_id: row for row in report.itertuples(index=False)}
    rows = []
    for policy_row in policy["range_policies"]:
        if policy_row["population"] != "primary":
            continue
        domain_id = policy_row["domain_id"]
        if domain_id not in report_by_id:
            raise RuntimeError(f"Missing primary D03 report row: {domain_id}")
        report_row = report_by_id[domain_id]
        fixed_json = _d05_canonical_json(policy_row["fixed_other_geometry"])
        report_fixed = _d05_canonical_json(
            _d05_json_value(report_row.fixed_other_geometry_json)
        )
        if fixed_json != report_fixed:
            raise RuntimeError(f"D03 fixed-geometry mismatch: {domain_id}")
        row = dict(policy_row)
        row.update(
            historical_error_margin_k2_pp=(
                float(report_row.historical_error_margin_k2_pp)
                if pd.notna(report_row.historical_error_margin_k2_pp)
                else None
            ),
            historical_error_margin_qp=(
                float(report_row.historical_error_margin_qp)
                if pd.notna(report_row.historical_error_margin_qp)
                else None
            ),
            nearest_two_training_level_spacing=(
                float(report_row.nearest_two_training_level_spacing)
                if pd.notna(report_row.nearest_two_training_level_spacing)
                else None
            ),
            interpolation_bracket=_d05_json_value(report_row.interpolation_bracket_json),
        )
        rows.append(row)
    if len(rows) != len(report_by_id):
        raise RuntimeError("D03 primary policy/report cardinality mismatch")
    return rows


def _d05_load_measured_points(
    source_run: Path, protocol: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    b_protocol_path = source_run / "protocol.json"
    b_protocol = json.loads(b_protocol_path.read_text(encoding="utf-8"))
    loaded = _load_canonical_populations(b_protocol, source_run / "data")
    frame = loaded["primary"]["frame"]
    metadata = frame.drop_duplicates("structure_key").set_index("structure_key", drop=False)
    if len(metadata) != 576:
        raise RuntimeError("D05 requires exactly 576 primary measured structures")
    identity_path = Path(
        next(item["path"] for item in protocol["inputs"] if item["id"] == "b01_identity")
    )
    with identity_path.open(encoding="utf-8") as handle:
        identity = json.load(handle)
    business_scope = protocol["performance_baseline"]["comparable_scope"]["business_scope"]
    measured: dict[str, dict[str, Any]] = {}
    for record in identity["records"]:
        key = record["structure_key"]
        if (
            key not in metadata.index
            or record["mode"] != "deembedded"
            or record["processing_scope"] != "deembedded_primary"
            or not record["is_canonical_measurement"]
        ):
            continue
        association = record["structure_association"]
        if association["association_status"] != "geometry_eligible":
            raise RuntimeError(f"Measured identity is not geometry eligible: {key}")
        point = make_research_point(
            association["structure_key_material"],
            p3_branch_id=association["p3_branch_id"],
            process_scope=record["processing_scope"],
            business_scope=business_scope,
        )
        point["port"] = record["port"]
        _d05_port(point)
        point["p3_structure_id"] = association["p3_structure_id"]
        point["p3_design_group_id"] = association["p3_design_group_id"]
        if point["structure_key"] != key:
            raise RuntimeError(f"B01 structure identity recomputation mismatch: {key}")
        existing = measured.get(key)
        if existing is not None:
            if existing["material"] != point["material"] or existing["port"] != point["port"]:
                raise RuntimeError(f"B01 full identity/source mismatch: {key}")
            continue
        label = metadata.loc[key]
        geometry, has_pf = _d05_geometry(point)
        checks = (
            point["p3_branch_id"] == label.p3_branch_id,
            point["p3_structure_id"] == label.p3_structure_id,
            point["p3_design_group_id"] == label.p3_design_group_id,
            float(geometry["eg_um"]) == float(label.eg_um),
            float(geometry["fl_um"]) == float(label.fl_um),
            float(geometry["area_um2"]) == float(label.area_um2),
            has_pf == bool(label.has_pf),
            point["material"]["ag"]["status"] == label.ag_status,
            point["material"]["cohort"] == label.cohort,
            point["material"]["topology"]["status"] == label.topology_status,
        )
        if not all(checks):
            raise RuntimeError(f"B01/B02 measured metadata mismatch: {key}")
        measured[key] = point
    del identity
    gc.collect()
    if set(measured) != set(metadata.index):
        raise RuntimeError("B01/B02 measured full-key coverage mismatch")
    return sorted(measured.values(), key=lambda point: point["full_structure_key_json"]), loaded[
        "primary"
    ]["source"]


def _d05_verified_adapter(
    source_run: Path, measured_point: dict[str, Any]
) -> tuple[DResearchInferenceAdapter, dict[str, Any]]:
    root = source_run / "research_bundle"
    bundle_path = root / "research_bundle.joblib"
    receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
    readback = json.loads((root / "readback.json").read_text(encoding="utf-8"))
    metadata_json = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    bundle_hash = file_sha256(bundle_path)
    if (
        receipt.get("research_bundle_sha256") != bundle_hash
        or readback.get("research_bundle_sha256") != bundle_hash
        or readback.get("status") != "PASS"
        or readback.get("unsupported_range_rejection_passed") is not True
        or readback.get("unsupported_branch_rejection_passed") is not True
    ):
        raise RuntimeError("Frozen research bundle verification failed")
    bundle = joblib.load(bundle_path)
    if bundle.get("metadata") != metadata_json or metadata_json.get("trusted") is not False:
        raise RuntimeError("Research bundle metadata/readback mismatch")
    research_model = bundle["research_model"]
    if set(research_model.models) != set(D05_TARGETS):
        raise RuntimeError("Research bundle does not contain exactly the four D05 targets")
    for target, model in research_model.models.items():
        if model.model_name != D05_MODEL or model.target != target:
            raise RuntimeError(f"Frozen P6C target/model identity mismatch: {target}")
        if metadata_json["selected_models"][target] != D05_MODEL:
            raise RuntimeError(f"Frozen P6C selection mismatch: {target}")

    # Re-run the registered readback and prove that the old range rejection is intact.
    for record in readback["records"]:
        fixed = dict(record["fixed_input"])
        fixed.update(
            structure_key=record["structure_key"],
            p3_branch_id=record["p3_branch_id"],
        )
        frame = pd.DataFrame([fixed])
        target = record["target"]
        actual = research_model.predict_records(target, frame)[0]
        expected = record["after"]
        if actual["status"] != "ok" or not np.isclose(
            actual["prediction"], expected["prediction"], rtol=1e-12, atol=1e-12
        ):
            raise RuntimeError(f"Frozen model readback failed: {target}")
    geometry, has_pf = _d05_geometry(measured_point)
    outside = {
        "structure_key": measured_point["structure_key"],
        "p3_branch_id": measured_point["p3_branch_id"],
        "eg_um": float(geometry["eg_um"]),
        "fl_um": float(geometry["fl_um"]),
        "area_um2": float(geometry["area_um2"]) + 1.0,
        "has_pf": int(has_pf),
    }
    branch_range = research_model.training["k2eff_pct"]["branches"][
        measured_point["p3_branch_id"]
    ]
    outside["area_um2"] = float(branch_range["area_um2"]["max"]) + 1.0
    old_rejection = research_model.predict_records("k2eff_pct", pd.DataFrame([outside]))[0]
    if (
        old_rejection["status"] != "unsupported"
        or old_rejection["reason"] != "outside_training_range"
    ):
        raise RuntimeError("Original outside_training_range rejection changed")
    return DResearchInferenceAdapter(research_model), {
        "model_bundle_sha256": bundle_hash,
        "registered_readback_predictions": len(readback["records"]),
        "original_outside_training_range_rejection_unchanged": True,
    }


def run_search(
    source_run: Path, policy_path: Path, protocol_path: Path, out: Path
) -> dict[str, Any]:
    started = time.monotonic()
    source_run = source_run.resolve()
    policy_path = policy_path.resolve()
    protocol_path = protocol_path.resolve()
    out = out.resolve()
    validated = _d05_validate_reviewed_inputs(protocol_path, policy_path)
    protocol = validated["protocol"]
    policy = validated["policy"]
    domain_rows = _d05_domain_rows(policy_path, policy)
    measured, label_source = _d05_load_measured_points(source_run, protocol)
    catalog = build_support_catalog(measured, domain_rows)
    points, scale = generate_geometry(measured, catalog, protocol)
    hashes = {
        "model_bundle_sha256": None,
        "data_sha256": label_source["sha256"],
        "protocol_sha256": file_sha256(protocol_path),
        "policy_sha256": file_sha256(policy_path),
    }
    prepare_output_directory(out)
    geometry_rows = [_d05_base_row(point, hashes) for point in points]
    scale["search_bounds"] = _d05_geometry_bounds(points)
    scale["geometry_sha256"] = hashlib.sha256(
        _d05_canonical_json([point["full_structure_key_json"] for point in points]).encode(
            "utf-8"
        )
    ).hexdigest()
    write_csv(out / "geometry.csv", geometry_rows, GEOMETRY_FIELDS)
    write_json(out / "scale_report.json", scale)
    print("D05_GEOMETRY " + json.dumps(scale["counts"], sort_keys=True), flush=True)
    if not scale["prediction_allowed"]:
        raise RuntimeError(
            "Geometry budget exceeded: "
            f"{scale['generated_post_deduplication']}>{scale['maximum_points']}"
        )

    adapter, model_verification = _d05_verified_adapter(source_run, measured[0])
    hashes["model_bundle_sha256"] = model_verification["model_bundle_sha256"]
    supported = [point for point in points if point["support"]["prediction_allowed"]]
    predicted = adapter.predict_points(supported)
    baseline_labels = protocol["performance_baseline"]["labels"]
    baseline_qp = float(
        protocol["goals"]["performance_candidate"]["baseline_measured_median_qp"]
    )
    prediction_rows = []
    for point in points:
        support = point["support"]
        for target in D05_TARGETS:
            key = (point["point_id"], target)
            value = predicted.get(key)
            baseline = float(baseline_labels[target]["median"])
            margin = (
                support["historical_error_margin_k2_pp"]
                if target == "k2eff_pct"
                else support["historical_error_margin_qp"]
                if target == "qp"
                else None
            )
            prediction_rows.append(
                {
                    "point_id": point["point_id"],
                    "structure_key": point["structure_key"],
                    "full_structure_key_sha256": point["full_structure_key_sha256"],
                    "category": support["category"],
                    "target": target,
                    "model": D05_MODEL,
                    "model_role": "d05_isolated_diagnostic_frozen_p6c",
                    "model_call_status": (
                        "CALLED_ONCE_PER_POINT_TARGET"
                        if key in predicted
                        else "NOT_CALLED_UNSUPPORTED"
                    ),
                    "prediction": value,
                    "unit": (
                        baseline_labels[target].get("unit")
                        or adapter.target_units.get(target)
                    ),
                    "baseline_measured_median": baseline,
                    "baseline_delta": value - baseline if value is not None else None,
                    "k2_improvement_pp": (
                        value - baseline
                        if value is not None and target == "k2eff_pct"
                        else None
                    ),
                    "qp_ratio": (
                        value / baseline_qp
                        if value is not None and target == "qp"
                        else None
                    ),
                    "historical_error_margin": margin,
                    "stress_gain_k2": (
                        value - baseline - float(margin)
                        if value is not None and target == "k2eff_pct" and margin is not None
                        else None
                    ),
                    "stress_qp_ratio": (
                        (value - float(margin)) / baseline_qp
                        if value is not None and target == "qp" and margin is not None
                        else None
                    ),
                    "axis": support["axis"],
                    "physical_distance": support["physical_distance"],
                    "relative_distance_delta": support["relative_distance_delta"],
                    "domain_id": support["domain_id"],
                    "diagnostic_status": "DIAGNOSTIC_ONLY",
                    "scientific_status": "UNVERIFIED_CANDIDATE",
                    "method_status": "single_method",
                    "trusted": False,
                    "performance_candidate": False,
                    "performance_exclusion_reason": D05_PERFORMANCE_EXCLUSION,
                    "rejection_reasons_json": _d05_canonical_json(support["rejection_reasons"]),
                    "manufacture_state": "VIRTUAL_ONLY",
                    **hashes,
                }
            )
    support_rows = [_d05_base_row(point, hashes) for point in points]
    rejection_rows = [
        _d05_base_row(point, hashes)
        for point in points
        if point["support"]["category"] == "UNSUPPORTED"
    ]
    write_csv(out / "predictions.csv", prediction_rows, PREDICTION_FIELDS)
    write_csv(out / "support.csv", support_rows, SUPPORT_FIELDS)
    write_csv(out / "rejections.csv", rejection_rows, REJECTION_FIELDS)

    category_counts = Counter(point["support"]["category"] for point in points)
    rejection_counts = Counter(
        reason
        for point in points
        for reason in point["support"]["rejection_reasons"]
    )
    outputs = {
        name: file_record(name, out / name)
        for name in (
            "geometry.csv",
            "scale_report.json",
            "predictions.csv",
            "support.csv",
            "rejections.csv",
        )
    }
    summary = {
        "schema": "aln_domain_candidates_d05_execution_summary_v1",
        "schema_version": "1.0.0",
        "task_id": "D05",
        "attempt_id": protocol["attempt_id"],
        "execution_status": "PENDING_INDEPENDENT_A4",
        "scientific_status": "UNVERIFIED_CANDIDATE",
        "diagnostic_status": "DIAGNOSTIC_ONLY",
        "method_status": "single_method",
        "trusted": False,
        "next_task": None,
        "created_at": datetime.now(UTC).isoformat(),
        "counts": {
            "geometry": len(points),
            **{category: category_counts[category] for category in D05_CATEGORIES},
            "prediction_rows": len(prediction_rows),
            "nonnull_target_predictions": len(predicted),
            "model_predict_batch_calls": len(D05_TARGETS),
            "support_rows": len(support_rows),
            "rejection_rows": len(rejection_rows),
            "performance_candidates": 0,
            "manufacturing_list_entries": 0,
            "fit_or_refit_calls": 0,
        },
        "rejection_reason_counts": dict(sorted(rejection_counts.items())),
        "search_bounds": scale["search_bounds"],
        "geometry_sha256": scale["geometry_sha256"],
        "model_verification": model_verification,
        "inputs": [
            file_record("search_protocol", protocol_path),
            file_record("domain_policy", policy_path),
            file_record("domain_report", policy_path.parent / "domain_report.csv"),
            file_record("primary_labels", Path(label_source["path"])),
            file_record("d04_independent_review", validated["review_path"]),
        ],
        "outputs": outputs,
        "elapsed_seconds": time.monotonic() - started,
        "numeric_thread_environment": {
            name: os.environ.get(name)
            for name in (
                "OPENBLAS_NUM_THREADS",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
        "excluded_work": {
            "performance_candidate_pass": False,
            "extra_trees_trend_confirmation": False,
            "coordinate_clamping": False,
            "catch_and_continue_inference": False,
            "old_research_package_modification": False,
            "manufacturing_quantization_or_list": False,
            "fit_or_refit": False,
            "grid_refinement_or_seed_chase": False,
            "agents_spawned": False,
        },
    }
    write_json(out / "execution_summary.json", summary)
    print("D05_SUMMARY " + json.dumps(summary["counts"], sort_keys=True), flush=True)
    return summary


D06_REVIEW_FIELDS = [
    *SUPPORT_FIELDS,
    "k2_prediction",
    "qp_prediction",
    "qs_prediction",
    "bodeq_prediction",
    "k2_improvement_pp",
    "qp_ratio",
    "stress_gain_k2",
    "stress_qp_ratio",
    "model_disagreement_k2_pp",
    "model_disagreement_qp",
    "model_disagreement_status",
    "performance_exclusion_reasons_d06_json",
    "diagnostic_selected",
    "diagnostic_rank",
    "diagnostic_selection_basis",
]


def performance_exclusion_reasons(
    support: dict[str, Any], predictions: dict[str, dict[str, Any]], goals: dict[str, Any]
) -> list[str]:
    """Apply every frozen performance gate; a diagnostic prediction can never bypass one."""
    reasons = []
    if support.get("d03_policy_status") != "ALLOWED_SEARCH":
        reasons.append("D03_RANGE_NOT_ALLOWED_FOR_SEARCH")
    if not str(support.get("category", "")).startswith("UNMEASURED_"):
        reasons.append("MEASURED_OR_UNSUPPORTED")
    source_rejections = set(json.loads(support.get("rejection_reasons_json") or "[]"))
    source_rejections.update(json.loads(support.get("exclusion_reasons_json") or "[]"))
    if source_rejections:
        reasons.append("SOURCE_REJECTION_PRESENT")
    k2 = predictions["k2eff_pct"]
    qp = predictions["qp"]
    k2_value = _d06_optional_number(k2.get("prediction"))
    qp_value = _d06_optional_number(qp.get("prediction"))
    if k2_value is None:
        reasons.append("MISSING_K2_PREDICTION")
    elif not 0 < k2_value < 100:
        reasons.append("K2_OUTSIDE_PHYSICAL_RANGE")
    if qp_value is None:
        reasons.append("MISSING_QP_PREDICTION")
    elif qp_value <= 0:
        reasons.append("QP_NOT_POSITIVE")
    if _d06_optional_number(k2.get("historical_error_margin")) is None:
        reasons.append("MISSING_K2_MARGIN")
    if _d06_optional_number(qp.get("historical_error_margin")) is None:
        reasons.append("MISSING_QP_MARGIN")
    gates = (
        (
            "k2_improvement_pp",
            goals["k2_improvement_min_percentage_points"],
            "K2_IMPROVEMENT_BELOW_MINIMUM",
        ),
        (
            "stress_gain_k2",
            goals["stress_gain_k2_min_percentage_points"],
            "STRESS_K2_BELOW_MINIMUM",
        ),
        ("qp_ratio", goals["qp_ratio_min"], "QP_RATIO_BELOW_MINIMUM"),
        ("stress_qp_ratio", goals["stress_qp_ratio_min"], "STRESS_QP_RATIO_BELOW_MINIMUM"),
    )
    gate_rows = (
        (k2, *gates[0]),
        (k2, *gates[1]),
        (qp, *gates[2]),
        (qp, *gates[3]),
    )
    for row, field, minimum, reason in gate_rows:
        value = _d06_optional_number(row.get(field))
        if value is None or value < float(minimum):
            reasons.append(reason)
    return reasons


def _d06_optional_number(value: Any) -> float | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    number = float(value)
    return number if np.isfinite(number) else None


def select_diagnostic_candidates(review_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select at most two boundary diagnostics without treating them as recommendations."""
    eligible = [
        row
        for row in review_rows
        if str(row["category"]).startswith("UNMEASURED_")
        and _d06_optional_number(row["k2_prediction"]) is not None
        and _d06_optional_number(row["qp_prediction"]) is not None
    ]

    def order(row: dict[str, Any]) -> tuple[Any, ...]:
        delta = _d06_optional_number(row["relative_distance_delta"])
        return (
            0 if row["category"] == "UNMEASURED_EXTRAPOLATION" else 1,
            abs(delta - 0.5) if delta is not None else float("inf"),
            -float(row["k2_improvement_pp"]),
            row["full_structure_key_json"],
        )

    ordered = sorted(eligible, key=order)
    selected = ordered[:1]
    if ordered:
        first_axis = selected[0]["axis"]
        second = next((row for row in ordered[1:] if row["axis"] != first_axis), None)
        if second is None and len(ordered) > 1:
            second = ordered[1]
        if second is not None:
            selected.append(second)
    for rank, row in enumerate(selected, 1):
        row["diagnostic_selected"] = True
        row["diagnostic_rank"] = rank
        row["diagnostic_selection_basis"] = (
            "NEAREST_FROZEN_DIRECTION_BOUNDARY_THEN_K2_DIFFERENCE;"
            "NO_VALIDATED_DIRECTION;MODEL_DISAGREEMENT_UNAVAILABLE_SINGLE_METHOD"
        )
    return selected


def run_shortlist(run: Path) -> dict[str, Any]:
    run = run.resolve()
    review_path = run / "review" / "D05_attempt_002" / "receipt.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("acceptance_status") != "PASS" or review.get("next_task") != "D06":
        raise RuntimeError("D05 independent A4 has not released D06")
    protocol_path = run / "search_protocol.json"
    policy_path = run / "domain" / "domain_policy.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    goals = protocol["goals"]["performance_candidate"]
    support = _read_csv_round_trip(run / "candidates" / "support.csv")
    predictions = _read_csv_round_trip(run / "candidates" / "predictions.csv")
    if len(support) != 2160 or len(predictions) != 4 * len(support):
        raise RuntimeError("D05 point/prediction cardinality changed")
    grouped = {point_id: rows for point_id, rows in predictions.groupby("point_id", sort=False)}
    review_rows = []
    performance = []
    for source in support.to_dict(orient="records"):
        target_rows = grouped[source["point_id"]]
        by_target = {row["target"]: row for row in target_rows.to_dict(orient="records")}
        if set(by_target) != set(D05_TARGETS):
            raise RuntimeError(f"D05 target identity mismatch: {source['point_id']}")
        reasons = performance_exclusion_reasons(source, by_target, goals)
        row = {
            **source,
            "k2_prediction": by_target["k2eff_pct"]["prediction"],
            "qp_prediction": by_target["qp"]["prediction"],
            "qs_prediction": by_target["qs"]["prediction"],
            "bodeq_prediction": by_target["bodeq_fitted"]["prediction"],
            "k2_improvement_pp": by_target["k2eff_pct"]["k2_improvement_pp"],
            "qp_ratio": by_target["qp"]["qp_ratio"],
            "stress_gain_k2": by_target["k2eff_pct"]["stress_gain_k2"],
            "stress_qp_ratio": by_target["qp"]["stress_qp_ratio"],
            "model_disagreement_k2_pp": None,
            "model_disagreement_qp": None,
            "model_disagreement_status": "UNAVAILABLE_SINGLE_METHOD_CANDIDATE_INFERENCE",
            "performance_candidate": not reasons,
            "performance_exclusion_reason": "ADMITTED" if not reasons else "D06_GATES_FAILED",
            "performance_exclusion_reasons_d06_json": _d05_canonical_json(reasons),
            "diagnostic_selected": False,
            "diagnostic_rank": None,
            "diagnostic_selection_basis": None,
        }
        review_rows.append(row)
        if not reasons:
            performance.append(row)
    performance.sort(
        key=lambda row: (
            -float(row["stress_gain_k2"]),
            -float(row["stress_qp_ratio"]),
            float(row["physical_distance"]),
            row["full_structure_key_json"],
        )
    )
    diagnostics = select_diagnostic_candidates(review_rows)
    selected_ids = {row["point_id"] for row in diagnostics}
    selected_by_id = {row["point_id"]: row for row in diagnostics}
    for row in review_rows:
        if row["point_id"] in selected_ids:
            row.update({key: selected_by_id[row["point_id"]][key] for key in (
                "diagnostic_selected", "diagnostic_rank", "diagnostic_selection_basis"
            )})
    shortlist = [
        {**row, "candidate_role": "PERFORMANCE", "candidate_rank": rank}
        for rank, row in enumerate(performance[:3], 1)
    ] + [
        {**row, "candidate_role": "DIAGNOSTIC_ONLY", "candidate_rank": row["diagnostic_rank"]}
        for row in diagnostics
    ]
    write_csv(run / "candidate_review.csv", review_rows, D06_REVIEW_FIELDS)
    write_csv(
        run / "shortlist.csv",
        shortlist,
        [*D06_REVIEW_FIELDS, "candidate_role", "candidate_rank"],
    )
    k2_rows = predictions.loc[predictions["target"].eq("k2eff_pct")]
    finite_improvements = k2_rows["k2_improvement_pp"].dropna()
    no_candidate = {
        "schema": "aln_domain_candidates_d06_no_candidate_v1",
        "task_id": "D06",
        "status": (
            "NO_PERFORMANCE_CANDIDATE" if not performance else "PERFORMANCE_CANDIDATES_UNVERIFIED"
        ),
        "performance_candidate_count": len(performance),
        "performance_shortlist_count": min(3, len(performance)),
        "diagnostic_candidate_count": len(diagnostics),
        "reasons": [
            "D03_HAS_ZERO_ALLOWED_SEARCH_RANGES",
            "NO_POINT_MEETS_ALL_FROZEN_PERFORMANCE_GATES",
        ] if not performance else [],
        "maximum_point_predicted_k2_improvement_pp": float(finite_improvements.max()),
        "performance_gate_failure_counts": dict(sorted(Counter(
            reason for row in review_rows
            for reason in json.loads(row["performance_exclusion_reasons_d06_json"])
        ).items())),
        "diagnostics_are_not_performance_recommendations": True,
        "manufacturing_release": False,
        "trusted": False,
        "inputs": [
            file_record("search_protocol", protocol_path),
            file_record("domain_policy", policy_path),
        ],
        "diagnostic_point_ids": [row["point_id"] for row in diagnostics],
    }
    write_json(run / "no_candidate.json", no_candidate)
    receipt = {
        "schema": "aln_domain_candidates_d06_receipt_v1",
        "task_id": "D06",
        "execution_status": "PENDING_INDEPENDENT_A4",
        "scientific_status": "NO_PERFORMANCE_CANDIDATE",
        "diagnostic_status": "DIAGNOSTIC_ONLY",
        "method_status": "single_method",
        "trusted": False,
        "next_task": None,
        "counts": {
            "reviewed_points": len(review_rows),
            "prediction_rows": len(predictions),
            "performance_candidates": len(performance),
            "performance_shortlist": min(3, len(performance)),
            "diagnostic_candidates": len(diagnostics),
            "diagnostic_axes": sorted({row["axis"] for row in diagnostics}),
            "new_geometry_or_inference_calls": 0,
        },
        "selection": {
            "performance_order": (
                "stress_gain_k2 desc, stress_qp_ratio desc, "
                "physical_distance asc, full_structure_key_json asc"
            ),
            "diagnostic_order": (
                "extrapolation first, abs(relative_delta-0.5) asc, "
                "k2 improvement desc, full key asc; second axis differs when possible"
            ),
            "model_disagreement": "UNAVAILABLE_SINGLE_METHOD_CANDIDATE_INFERENCE",
            "validated_directions_available": False,
        },
        "inputs": [
            file_record("d05_independent_review", review_path),
            file_record("search_protocol", protocol_path),
            file_record("domain_policy", policy_path),
            file_record("d05_support", run / "candidates" / "support.csv"),
            file_record("d05_predictions", run / "candidates" / "predictions.csv"),
        ],
        "outputs": [file_record(name, run / name) for name in (
            "candidate_review.csv", "shortlist.csv", "no_candidate.json"
        )],
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(run / "receipts" / "D06.json", receipt)
    print("D06 " + json.dumps(receipt["counts"], sort_keys=True), flush=True)
    return receipt



def run_protocol(run: Path, d06_review_path: Path) -> dict[str, Any]:
    """Write the prospective protocol draft without starting measurement."""
    run = run.resolve()
    review = json.loads(d06_review_path.read_text(encoding="utf-8"))
    if review.get("acceptance_status") != "PASS" or review.get("next_task") != "D07":
        raise RuntimeError("D06 independent A4 has not released D07")
    no_candidate = json.loads((run / "no_candidate.json").read_text(encoding="utf-8"))
    if no_candidate["status"] != "NO_PERFORMANCE_CANDIDATE":
        raise RuntimeError("D07 requires the frozen no-performance-candidate result")
    shortlist = _read_csv_round_trip(run / "shortlist.csv")
    diagnostics = shortlist.loc[shortlist["candidate_role"].eq("DIAGNOSTIC_ONLY")]
    if len(diagnostics) > 2 or not diagnostics["d03_policy_status"].eq("DIAGNOSTIC_ONLY").all():
        raise RuntimeError("D07 diagnostic candidates do not match D06 restrictions")
    search = json.loads((run / "search_protocol.json").read_text(encoding="utf-8"))
    baseline = search["performance_baseline"]
    old_c_path = (
        run.parent.parent
        / "optimization_device_median_v1"
        / "attempt_001"
        / "independent_measurement_protocol.json"
    )
    old_c = json.loads(old_c_path.read_text(encoding="utf-8"))
    prediction_path = run / "candidates" / "predictions.csv"
    prediction_rows = _read_csv_round_trip(prediction_path)
    diagnostic_groups = []
    for _, candidate in diagnostics.iterrows():
        rows = prediction_rows.loc[prediction_rows["point_id"].eq(candidate["point_id"])]
        if set(rows["target"]) != set(D05_TARGETS) or len(rows) != 4:
            raise RuntimeError("Diagnostic candidate does not have four frozen predictions")
        diagnostic_groups.append({
            "point_id": candidate["point_id"],
            "structure_key": candidate["structure_key"],
            "full_structure_key": json.loads(candidate["full_structure_key_json"]),
            "geometry": {
                "eg_um": float(candidate["eg_um"]),
                "fl_um": float(candidate["fl_um"]),
                "area_um2": float(candidate["area_um2"]),
                "has_pf": bool(int(candidate["has_pf"])),
            },
            "axis": candidate["axis"],
            "direction": candidate["direction"],
            "delta": float(candidate["delta"]),
            "physical_distance": float(candidate["physical_distance"]),
            "relative_distance_delta": float(candidate["relative_distance_delta"]),
            "domain_policy_status": candidate["d03_policy_status"],
            "scientific_status": "UNVERIFIED_CANDIDATE",
            "model_method": "single_method",
            "manufacturing_status": "VIRTUAL_ONLY",
            "frozen_predictions": {
                row.target: None if pd.isna(row.prediction) else float(row.prediction)
                for row in rows.itertuples()
            },
            "model_bundle_sha256": candidate["model_bundle_sha256"],
            "data_sha256": candidate["data_sha256"],
            "policy_sha256": candidate["policy_sha256"],
        })
    old_predictions = {}
    for row in old_c["frozen_predictions"]:
        old_predictions.setdefault(row["structure_key"], {})[row["target"]] = row["prediction"]
    repeatability = [
        {
            "source_group": group["role"],
            "mapping_mark": "B1-6" if group["role"] == "TEST" else "A1-6",
            "structure_key": group["structure_key"],
            "geometry": {
                key: group[key] for key in ("eg_um", "fl_um", "area_um2", "has_pf")
            },
            "p3_branch_id": group["p3_branch_id"],
            "frozen_predictions": old_predictions[group["structure_key"]],
            "role": "REPEATABILITY_ONLY",
        }
        for group in old_c["frozen_structures"]
    ]
    groups = [
        {
            "group_id": "performance",
            "role": "PERFORMANCE_VALIDATION",
            "status": "EMPTY_NO_PERFORMANCE_CANDIDATE",
            "structures": [],
            "reasons": no_candidate["reasons"],
            "may_claim_predicted_plus_one_validation": False,
        },
        {
            "group_id": "extrapolation",
            "role": "EXTRAPOLATION_DIAGNOSTIC",
            "status": "PLANNED_DIAGNOSTIC_NOT_RUN" if diagnostic_groups else "EMPTY",
            "structures": diagnostic_groups,
            "performance_success_presumed": False,
        },
        {
            "group_id": "interpolation",
            "role": "UNMEASURED_INTERPOLATION_DIAGNOSTIC",
            "status": "EMPTY_NOT_SELECTED_THIS_ROUND",
            "structures": [],
            "may_be_used_as_extrapolation_evidence": False,
        },
        {
            "group_id": "repeatability",
            "role": "KNOWN_STRUCTURE_REMEASUREMENT",
            "status": "PLANNED_NOT_RUN",
            "structures": repeatability,
            "historical_test_b1_6_and_control_a1_6_are_new_geometry": False,
            "retrospective_k2_difference_pp": old_c["same_batch_baseline"][
                "retrospective_test_minus_baseline_k2eff_pct_percentage_points"
            ],
            "difference_is_plus_one_candidate_evidence": False,
        },
        {
            "group_id": "same_batch_baseline",
            "role": "FROZEN_SAME_BATCH_BASELINE",
            "status": "PLANNED_NOT_RUN",
            "structures": [{
                "mapping_mark": baseline["identity_and_mapping"]["mapping_mark"],
                "structure_key": baseline["structure_key"],
                "geometry": baseline["geometry"],
                "identity_and_mapping": baseline["identity_and_mapping"],
                "historical_labels_for_planning_only": baseline["labels"],
                "historical_member_device_count": baseline["member_device_count"],
                "historical_values_may_replace_new_same_batch_measurements": False,
            }],
        },
    ]
    missing_inputs = [
        "NEW_PHYSICAL_DEVICES_AND_INDEPENDENT_MEASUREMENTS",
        "FABRICATION_PROCESS_LAYOUT_AND_LEGAL_GEOMETRY",
        "WAFER_POSITION_CLUSTER_AND_BATCH_SAMPLING_FRAME",
        "NEW_BATCH_REPEATABILITY_AND_DEVICE_VARIANCE_FOR_SAMPLE_SIZE",
        "CLUSTER_AWARE_MEDIAN_DIFFERENCE_AND_RATIO_INTERVAL_METHOD",
        "INSTRUMENT_POWER_IFBW_FREQUENCY_STEP_AND_TEMPERATURE",
        "CALIBRATION_AND_OPEN_SHORT_DEEMBEDDING_RECORDS",
        "ANONYMIZED_DEVICE_ID_AND_MEASUREMENT_ORDER",
    ]
    protocol = {
        "schema": "aln_domain_candidates_measurement_protocol_v2",
        "schema_version": "2.0.0",
        "task_id": "D07",
        "attempt_id": "attempt_001",
        "protocol_status": "DRAFT_MISSING_INPUTS",
        "execution_status": "NOT_RUN",
        "scientific_status": "NOT_RUN",
        "trusted": False,
        "manufacturing_status": "VIRTUAL_ONLY",
        "d09_status": "NOT_RUN",
        "d10_status": "NOT_RUN",
        "groups": groups,
        "sample_design": {
            "status": "SAMPLE_SIZE_UNRESOLVED",
            "independent_sampling_unit": "UNRESOLVED_DEVICE_POSITION_CLUSTER_WAFER_OR_BATCH",
            "confidence_level_one_sided": 0.95,
            "power_target": 0.80,
            "candidate_count_performance": 0,
            "fixed_sample_size": None,
            "old_five_devices_three_repeats_is_sufficiency_guarantee": False,
            "pilot_allowed": True,
            "shortfall_rule": "NOT_EVALUABLE; never backfill with historical devices or repeats",
        },
        "aggregation": {
            "repeat_rule": (
                "Freeze repeat combination before unblinding; repeats do not increase device n"
            ),
            "device_rule": "Per device and parameter median of valid repeats",
            "structure_rule": "Equal-weight median over all independent devices per parameter",
            "missing_rule": "Missing one parameter does not delete other valid parameters",
            "best_device_selection_allowed": False,
        },
        "required_measurement_records": {
            "status": "NOT_RECORDED",
            "required": [
                "geometry_layout_process_version_wafer_position_and_port_ids",
                "measurement_order_instrument_serial_firmware_operator_and_timestamp",
                "power_ifbw_temperature_frequency_start_stop_step_and_point_count",
                "calibration_kit_standard_ids_method_timestamp_and_raw_calibration_files",
                "open_short_structure_ids_recipe_version_configuration_and_files",
                "raw_and_deembedded_touchstone_files_and_sha256",
                "repeat_to_physical_device_mapping_extraction_version_and_exclusion_ledger",
            ],
            "raw_deembedded_separate": True,
            "frequency_step_frozen_before_measurement": True,
            "deembedding_without_calibration_evidence": "NOT_EVALUABLE",
        },
        "blinding_and_freeze": {
            "anonymous_device_ids_required_where_possible": True,
            "raw_files_hashed_before_unblinding": True,
            "predictions_frozen_before_measurement": True,
            "post_unblinding_model_baseline_exclusion_or_sample_changes_allowed": False,
            "changes_require_new_protocol_version": True,
        },
        "preregistered_acceptance": {
            "accuracy": (
                "Per structure/target/axis/distance compare all-device median to frozen "
                "prediction against D03 budget; no averaging away failures"
            ),
            "k2_benefit": (
                "New-structure median minus same-batch baseline median; "
                "one-sided lower bound must be >=1.0 percentage point"
            ),
            "qp_constraint": (
                "New/baseline median ratio with corresponding one-sided interval; threshold 1.0"
            ),
            "multiple_comparisons": (
                "Freeze primary comparisons and joint success before unblinding; "
                "Bonferroni if multiple performance candidates"
            ),
            "interval_method": "UNRESOLVED_CLUSTER_AWARE_MEDIAN_DIFFERENCE_AND_RATIO_METHOD",
            "historical_stress_margin_is_statistical_interval": False,
            "performance_rule_invoked_without_candidates": False,
            "insufficient_sample_or_assumptions": "NOT_EVALUABLE",
        },
        "missing_inputs": missing_inputs,
        "source_hashes": {
            "d06_review": file_sha256(d06_review_path),
            "d06_no_candidate": file_sha256(run / "no_candidate.json"),
            "d06_shortlist": file_sha256(run / "shortlist.csv"),
            "d05_predictions": file_sha256(prediction_path),
            "d04_search_protocol": file_sha256(run / "search_protocol.json"),
            "old_c_protocol_unchanged": file_sha256(old_c_path),
        },
    }
    measurement = run / "measurement"
    measurement.mkdir(parents=True, exist_ok=True)
    protocol_path = measurement / "measurement_protocol_v2.json"
    write_json(protocol_path, protocol)
    lines = [
        "# 独立实测协议 v2（草案，未执行）",
        "",
        "- 状态：DRAFT_MISSING_INPUTS；D09/D10：NOT_RUN；不得制造或宣称性能提升。",
        "- 性能组为空：没有 D06 性能候选，不要求验证已预测的 +1 个百分点。",
        "- 两个外推点仅用于诊断；B1-6/A1-6 仅用于重复性复测。",
        "- 同批基准必须重新测量 E1-1，历史值不能替代。",
        "- 默认单侧 95% 置信下界、80% 功效目标；样本量与簇感知区间方法尚未解决。",
        "",
        "## 实验组",
        "",
    ]
    for group in groups:
        lines.append(
            f"- {group['group_id']}: {group['status']}，结构数 {len(group['structures'])}。"
        )
    lines.extend(["", "## 最小缺项", ""])
    lines.extend(f"- {item}" for item in missing_inputs)
    plan_path = measurement / "sample_plan.md"
    plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    receipt = {
        "schema": "aln_domain_candidates_d07_receipt_v1",
        "task_id": "D07",
        "execution_status": "PENDING_INDEPENDENT_A4",
        "protocol_status": "DRAFT_MISSING_INPUTS",
        "scientific_status": "NOT_RUN",
        "trusted": False,
        "next_task": None,
        "verification_status": "PASS",
        "checks": [
            {"check_id": "d06_independent_review", "status": "PASS", "details": {
                "path": str(d06_review_path), "sha256": file_sha256(d06_review_path),
            }},
            {"check_id": "groups_separate_questions", "status": "PASS", "details": {
                group["group_id"]: {
                    "status": group["status"], "structures": len(group["structures"])
                }
                for group in groups
            }},
            {
                "check_id": "no_false_performance_or_repeatability_claim",
                "status": "PASS",
                "details": {
                "performance_candidates": 0,
                "repeatability_old_prediction_difference_pp": old_c["same_batch_baseline"][
                    "retrospective_test_minus_baseline_k2eff_pct_percentage_points"
                ],
            }},
            {"check_id": "sample_size_and_interval_unresolved", "status": "PASS", "details": {
                "sample_design": protocol["sample_design"]["status"],
                "protocol_status": protocol["protocol_status"],
            }},
            {"check_id": "no_measurement_manufacturing_or_d09_d10", "status": "PASS", "details": {
                "execution_status": "NOT_RUN", "manufacturing_status": "VIRTUAL_ONLY",
            }},
        ],
        "inputs": [
            file_record("d06_review", d06_review_path),
            file_record("no_candidate", run / "no_candidate.json"),
            file_record("shortlist", run / "shortlist.csv"),
            file_record("d05_predictions", prediction_path),
            file_record("search_protocol", run / "search_protocol.json"),
            file_record("old_c_protocol", old_c_path),
        ],
        "outputs": [
            file_record("measurement_protocol_v2", protocol_path),
            file_record("sample_plan", plan_path),
        ],
        "commands": [{
            "command_id": "d07_protocol",
            "command": (
                "uv run --no-sync python scripts/run_domain_candidates_v1.py protocol "
                f"--run {run} --d06-review {d06_review_path}"
            ),
            "exit_code": 0,
        }],
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(run / "receipts" / "D07.json", receipt)
    print("D07 " + json.dumps({
        "protocol_status": protocol["protocol_status"],
        "performance_candidates": 0,
        "diagnostic_candidates": len(diagnostic_groups),
        "d09": "NOT_RUN",
        "d10": "NOT_RUN",
    }, sort_keys=True), flush=True)
    return receipt


def run_finalize(run: Path) -> dict[str, Any]:
    """Seal digital D00-D07 outputs while leaving prospective work NOT_RUN."""
    run = run.resolve()
    review_paths = {
        "D00": run / "review" / "D00" / "receipt.json",
        "D01": run / "review" / "D01" / "receipt.json",
        "D02": run / "review" / "D02" / "receipt.json",
        "D03": run / "review" / "D03_attempt_002" / "receipt.json",
        "D04": run / "review" / "D04" / "receipt.json",
        "D05": run / "review" / "D05_attempt_002" / "receipt.json",
        "D06": run / "review" / "D06" / "receipt.json",
        "D07": run / "review" / "D07" / "receipt.json",
    }
    reviews = {}
    expected_next = {
        "D00": "D01", "D01": "D02", "D02": "D03", "D03": "D04",
        "D04": "D05", "D05": "D06", "D06": "D07", "D07": "D08",
    }
    for task, path in review_paths.items():
        review = json.loads(path.read_text(encoding="utf-8"))
        if review.get("acceptance_status") != "PASS":
            raise RuntimeError(f"{task} independent A4 has not passed")
        if review.get("next_task") != expected_next[task]:
            raise RuntimeError(f"{task} independent A4 has not released {expected_next[task]}")
        reviews[task] = review
    no_candidate = json.loads((run / "no_candidate.json").read_text(encoding="utf-8"))
    protocol_path = run / "measurement" / "measurement_protocol_v2.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if (
        no_candidate["performance_candidate_count"] != 0
        or protocol["protocol_status"] != "DRAFT_MISSING_INPUTS"
        or protocol["execution_status"] != "NOT_RUN"
    ):
        raise RuntimeError("Final negative-result/protocol states changed")
    domain_policy = json.loads((run / "domain" / "domain_policy.json").read_text(encoding="utf-8"))
    if domain_policy["allowed_range_ids"]:
        raise RuntimeError("D03 performance allowlist is not empty")
    artifact_paths = [
        run / "inputs.json",
        run / "comparison_contract.json",
        run / "comparison" / "paired_predictions.csv",
        run / "comparison" / "model_comparison.csv",
        run / "comparison" / "execution_summary.json",
        run / "domain" / "domain_report.csv",
        run / "domain" / "domain_policy.json",
        run / "search_protocol.json",
        run / "fabrication_inputs.json",
        run / "candidates" / "geometry.csv",
        run / "candidates" / "support.csv",
        run / "candidates" / "predictions.csv",
        run / "candidates" / "rejections.csv",
        run / "candidate_review.csv",
        run / "shortlist.csv",
        run / "no_candidate.json",
        protocol_path,
        run / "measurement" / "sample_plan.md",
    ]
    final_report = {
        "schema": "aln_domain_candidates_final_report_v1",
        "schema_version": "1.0.0",
        "attempt_id": "attempt_001",
        "execution_status": "PENDING_INDEPENDENT_A4",
        "digital_work_status": "COMPLETE",
        "performance_candidate_status": "NO_PERFORMANCE_CANDIDATE",
        "scientific_status": "REJECTED",
        "diagnostic_status": "DIAGNOSTIC_ONLY",
        "measurement_protocol_status": protocol["protocol_status"],
        "measurement_execution_status": "NOT_RUN",
        "d09_status": "NOT_RUN",
        "d10_status": "NOT_RUN",
        "trusted": False,
        "manufacturing_status": "VIRTUAL_ONLY",
        "external_validation_implication": "NONE",
        "method_status": "single_method",
        "results": {
            "paired_prediction_rows": 9120,
            "model_comparison_rows": 120,
            "domain_ranges": len(domain_policy["range_policies"]),
            "allowed_search_ranges": len(domain_policy["allowed_range_ids"]),
            "diagnostic_ranges": len(domain_policy["range_policies"]),
            "candidate_points": 2160,
            "prediction_rows": 8640,
            "performance_candidates": 0,
            "diagnostic_candidates": no_candidate["diagnostic_candidate_count"],
            "diagnostic_point_ids": no_candidate["diagnostic_point_ids"],
            "maximum_point_predicted_k2_improvement_pp": no_candidate[
                "maximum_point_predicted_k2_improvement_pp"
            ],
            "performance_gate_failure_counts": no_candidate["performance_gate_failure_counts"],
        },
        "interpretation": [
            "D00-D07 digital execution is separate from scientific performance success.",
            "No range passed D03 performance gates; all candidate points remain diagnostic only.",
            "B-inner-selected and P6C are the same method; no independent model confirmation.",
            "Historical P90 margins are not statistical prediction intervals.",
            "Four scalar predictions are not measured devices or self-consistent impedance curves.",
            "No new independent measurement supports a k2 improvement claim.",
            "Missing manufacturing and statistical inputs prevent READY_FOR_MEASUREMENT.",
        ],
        "independent_reviews": {
            task: {"path": str(path), "sha256": file_sha256(path)}
            for task, path in review_paths.items()
        },
        "artifacts": {
            "domain_report": str(run / "domain" / "domain_report.csv"),
            "domain_policy": str(run / "domain" / "domain_policy.json"),
            "all_candidate_review": str(run / "candidate_review.csv"),
            "shortlist": str(run / "shortlist.csv"),
            "no_candidate": str(run / "no_candidate.json"),
            "measurement_protocol": str(protocol_path),
            "sample_plan": str(run / "measurement" / "sample_plan.md"),
        },
        "missing_measurement_inputs": protocol["missing_inputs"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    report_path = run / "final_report.json"
    readme_path = run / "README.md"
    write_json(report_path, final_report)
    readme = f"""# 适用域与候选研究：数字阶段封存

- 数字工作：COMPLETE（最终独立 A4 前，执行回执仍为 PENDING_INDEPENDENT_A4）。
- 性能候选：NO_PERFORMANCE_CANDIDATE；科学候选结论：REJECTED。
- 模型可信状态：trusted=false；全部候选仅为 DIAGNOSTIC_ONLY。
- 实测协议：DRAFT_MISSING_INPUTS；D09/D10：NOT_RUN。

## 核心结果

D02 形成 9,120 行同折配对预测与 120 行模型比较。D03 的
{len(domain_policy["range_policies"])} 个范围全部为诊断性质，性能允许范围为 0。
D05 保留 2,160 个几何点和 8,640 条四目标预测；D06 没有点通过全部性能门槛，
最高逐点预测 k² 改善仅 {no_candidate["maximum_point_predicted_k2_improvement_pp"]:.6f}
个百分点，低于 1.0 个百分点。保留 {no_candidate["diagnostic_candidate_count"]}
个外推诊断点，但它们不是性能推荐，也不是制造清单。

## 实测边界

协议 v2 将性能、外推、插值、已知结构复测与同批基准分开。性能组为空；
B1-6/A1-6 仅作重复性对照；E1-1 必须在同批新器件中重新测量。样本量与
簇感知区间方法尚未解决，因此状态不是 READY_FOR_MEASUREMENT。

没有新增流片、COMSOL、制造、部署或实测；不得宣称新结构 k² 已提升 1 个百分点。
"""
    readme_path.write_text(readme, encoding="utf-8")
    manifest = {
        "schema": "aln_domain_candidates_final_manifest_v1",
        "schema_version": "1.0.0",
        "attempt_id": "attempt_001",
        "digital_work_status": "COMPLETE",
        "performance_candidate_status": "NO_PERFORMANCE_CANDIDATE",
        "measurement_protocol_status": protocol["protocol_status"],
        "d09_status": "NOT_RUN",
        "d10_status": "NOT_RUN",
        "trusted": False,
        "artifacts": [
            file_record(path.stem, path)
            for path in [*artifact_paths, readme_path, report_path]
        ],
        "independent_reviews": [
            file_record(f"{task}_review", path) for task, path in review_paths.items()
        ],
        "created_at": datetime.now(UTC).isoformat(),
    }
    manifest_path = run / "final_manifest.json"
    write_json(manifest_path, manifest)
    receipt = {
        "schema": "aln_domain_candidates_d08_receipt_v1",
        "task_id": "D08",
        "execution_status": "PENDING_INDEPENDENT_A4",
        "digital_work_status": "COMPLETE",
        "performance_candidate_status": "NO_PERFORMANCE_CANDIDATE",
        "measurement_protocol_status": protocol["protocol_status"],
        "scientific_status": "REJECTED",
        "trusted": False,
        "next_task": None,
        "verification_status": "PASS",
        "checks": [
            {"check_id": "all_prior_independent_reviews_pass", "status": "PASS", "details": {
                "tasks": list(review_paths), "review_count": len(review_paths),
            }},
            {"check_id": "three_states_not_conflated", "status": "PASS", "details": {
                "digital_work": "COMPLETE", "performance_candidates": 0,
                "measurement_protocol": protocol["protocol_status"],
            }},
            {"check_id": "all_maps_candidates_and_gaps_registered", "status": "PASS", "details": {
                "manifest_artifacts": len(manifest["artifacts"]),
                "missing_inputs": len(protocol["missing_inputs"]),
            }},
            {"check_id": "prospective_work_not_run", "status": "PASS", "details": {
                "D09": "NOT_RUN", "D10": "NOT_RUN", "trusted": False,
            }},
        ],
        "outputs": [
            file_record("README", readme_path),
            file_record("final_report", report_path),
            file_record("final_manifest", manifest_path),
        ],
        "commands": [{
            "command_id": "d08_finalize",
            "command": (
                "uv run --no-sync python scripts/run_domain_candidates_v1.py "
                f"finalize --run {run}"
            ),
            "exit_code": 0,
        }],
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(run / "receipts" / "D08.json", receipt)
    print("D08 " + json.dumps({
        "digital_work": "COMPLETE",
        "performance_candidates": 0,
        "protocol": protocol["protocol_status"],
        "D09": "NOT_RUN",
        "D10": "NOT_RUN",
    }, sort_keys=True), flush=True)
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--contract", type=Path, required=True)
    compare.add_argument(
        "--reuse-p3",
        type=Path,
        help="Recompute canonical comparisons from existing D02 P3 CSV without inference",
    )
    search = subparsers.add_parser("search")
    search.add_argument("--source-run", type=Path, required=True)
    search.add_argument("--policy", type=Path, required=True)
    search.add_argument("--protocol", type=Path, required=True)
    shortlist = subparsers.add_parser("shortlist")
    shortlist.add_argument("--run", type=Path, required=True)
    protocol = subparsers.add_parser("protocol")
    protocol.add_argument("--run", type=Path, required=True)
    protocol.add_argument("--d06-review", type=Path, required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--run", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "compare":
        run_comparison(args.contract, reuse_p3=args.reuse_p3)
        return 0
    if args.command == "search":
        run_search(args.source_run, args.policy, args.protocol, args.out)
        return 0
    if args.command == "shortlist":
        run_shortlist(args.run)
        return 0
    if args.command == "protocol":
        run_protocol(args.run, args.d06_review)
        return 0
    if args.command == "finalize":
        run_finalize(args.run)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
