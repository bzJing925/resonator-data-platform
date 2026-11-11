"""Fit only the frozen B04 scalar models and select them on inner predictions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern, WhiteKernel
from sklearn.linear_model import Ridge
from threadpoolctl import threadpool_info

CANONICAL_MODULE = "scripts.run_device_median_models_v1"

POPULATIONS = ("primary", "sensitivity")
CANDIDATE_ORDER = (
    "p3_extra_trees_scalar",
    "p6c_trend_gp_log_area_scalar",
    "training_target_median_constant",
)
LABEL_COLUMNS = [
    "structure_key",
    "mode",
    "parameter",
    "p3_structure_id",
    "p3_branch_id",
    "p3_design_group_id",
    "eg_um",
    "fl_um",
    "area_um2",
    "has_pf",
    "ag_status",
    "cohort",
    "topology_status",
    "median",
    "n_valid",
    "low_support",
]
METADATA_COLUMNS = [
    column
    for column in LABEL_COLUMNS
    if column not in {"median", "n_valid", "low_support"}
]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(identifier: str, path: Path) -> dict[str, Any]:
    return {
        "id": identifier,
        "path": str(path.resolve()),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "sha256": file_sha256(path) if path.is_file() else None,
    }


def _boolean(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    mapped = series.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )
    if mapped.isna().any():
        raise ValueError("Invalid low_support boolean in structure targets")
    return mapped.astype(bool)


def _read_labels(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=LABEL_COLUMNS)
    if frame.empty:
        raise ValueError(f"Empty structure-target file: {path}")
    if frame.duplicated(["structure_key", "parameter"]).any():
        raise ValueError(f"Duplicate structure/parameter labels: {path}")
    if not frame["mode"].eq("deembedded").all():
        raise ValueError("Only deembedded structure targets are eligible")
    for column in ("eg_um", "fl_um", "area_um2", "has_pf", "median", "n_valid"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["low_support"] = _boolean(frame["low_support"])
    geometry = frame[["eg_um", "fl_um", "area_um2"]].to_numpy(float)
    if (
        not np.isfinite(geometry).all()
        or (geometry[:, :2] < 0).any()
        or (geometry[:, 2] <= 0).any()
    ):
        raise ValueError("Invalid training geometry")
    if not frame["has_pf"].isin([0, 1]).all():
        raise ValueError("has_pf must remain a discrete 0/1 branch attribute")
    return frame.sort_values(["parameter", "structure_key"]).reset_index(drop=True)


def load_populations(protocol: dict[str, Any], data_dir: Path) -> dict[str, dict[str, Any]]:
    """Load exactly the two registered B02 structure-target files and verify their hashes."""
    registered = {
        item["id"]: item
        for item in protocol["data_version"]["inputs"]
        if item.get("id") in {f"structure_targets_{name}" for name in POPULATIONS}
    }
    result = {}
    for population in POPULATIONS:
        identifier = f"structure_targets_{population}"
        if identifier not in registered:
            raise RuntimeError(f"Missing registered label input: {identifier}")
        path = (data_dir / f"{identifier}.csv").resolve()
        actual_hash = file_sha256(path)
        if actual_hash != registered[identifier]["sha256"]:
            raise RuntimeError(f"Label hash mismatch: {identifier}")
        result[population] = {
            "frame": _read_labels(path),
            "source": file_record(identifier, path),
        }

    primary = result["primary"]["frame"]
    sensitivity = result["sensitivity"]["frame"]
    left = primary[METADATA_COLUMNS].sort_values(["structure_key", "parameter"])
    right = sensitivity[METADATA_COLUMNS].sort_values(["structure_key", "parameter"])
    pd.testing.assert_frame_equal(left.reset_index(drop=True), right.reset_index(drop=True))
    comparison = primary[["structure_key", "parameter", "median", "n_valid", "low_support"]].merge(
        sensitivity[["structure_key", "parameter", "median", "n_valid", "low_support"]],
        on=["structure_key", "parameter"],
        suffixes=("_primary", "_sensitivity"),
        validate="one_to_one",
    )
    non_qp = comparison[~comparison["parameter"].eq("qp")]
    for column in ("median", "n_valid", "low_support"):
        if not non_qp[f"{column}_primary"].equals(non_qp[f"{column}_sensitivity"]):
            raise RuntimeError(f"Sensitivity population changed non-Qp {column}")
    return result


def select_model(raw_unit_mae: dict[str, float]) -> str:
    """Select by pooled raw-unit MAE, then the registered exact-tie priority."""
    if set(raw_unit_mae) != set(CANDIDATE_ORDER):
        raise ValueError("Selection requires exactly the three frozen candidates")
    values = {name: float(value) for name, value in raw_unit_mae.items()}
    if not all(np.isfinite(value) and value >= 0 for value in values.values()):
        raise ValueError("Every candidate needs a finite nonnegative inner MAE")
    return min(CANDIDATE_ORDER, key=lambda name: (values[name], CANDIDATE_ORDER.index(name)))


def gate_mae_comparison(
    selected_mae: float, constant_mae: float, normal_holdout: bool
) -> dict[str, Any]:
    """Apply the frozen selected-vs-constant MAE gate without dividing by zero."""
    selected = float(selected_mae)
    constant = float(constant_mae)
    if not np.isfinite(selected) or not np.isfinite(constant) or min(selected, constant) < 0:
        raise ValueError("Gate MAE values must be finite and nonnegative")
    zero_baseline = constant == 0
    reduction = None if zero_baseline else 100 * (constant - selected) / constant
    result = {
        "mae_reduction_pct": reduction,
        "mae_reduction_status": (
            "not_computed_zero_constant_baseline" if zero_baseline else "computed"
        ),
        "cell_gate_pass": True,
        "failure_reason": None,
    }
    if zero_baseline and selected != 0:
        result.update(
            cell_gate_pass=False,
            failure_reason="zero constant baseline requires selected MAE == 0",
        )
    elif selected > constant:
        result.update(
            cell_gate_pass=False,
            failure_reason="selected MAE exceeds constant-model MAE",
        )
    elif normal_holdout and not zero_baseline and reduction < 5.0:
        result.update(
            cell_gate_pass=False,
            failure_reason="normal-holdout MAE reduction is below 5 percent",
        )
    return result


def validate_scope_for_fit(scope: dict[str, Any]) -> str | None:
    """Return the NOT_EVALUABLE reason instead of silently weakening a frozen scope."""
    if scope.get("status") != "EVALUABLE":
        return str(scope.get("not_evaluable_reason") or "frozen scope is not evaluable")
    groups = int(scope["outer"]["train"]["counts"]["design_groups"])
    test_structures = int(scope["outer"]["test"]["counts"]["structures"])
    folds = int(scope["inner_group_kfold"]["actual_splits"])
    if groups < 3:
        return "fewer than 3 outer-training design groups"
    if test_structures == 0:
        return "empty outer test"
    if folds < 2 or folds > min(5, groups):
        return "invalid frozen GroupKFold count"
    if len(scope["inner_group_kfold"].get("folds", [])) != folds:
        return "missing frozen fold assignments"
    return None


class TrainingTargetMedianConstant:
    model_name = "training_target_median_constant"

    def __init__(self, target: str, value: float):
        self.target = target
        self.value = float(value)
        self.target_transform = "none_raw_unit_median"

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.value, dtype=float)

    def transform_records(self) -> list[dict[str, Any]]:
        return [{"branch_id": "all", "target_transform": self.target_transform}]


def _validate_training_frame(frame: pd.DataFrame, target: str) -> None:
    if frame.empty:
        raise ValueError("Empty target-specific training frame")
    y = frame[target].to_numpy(float)
    if not np.isfinite(y).all() or (y <= 0).any():
        raise ValueError("Frozen scalar models require finite positive training targets")
    x = frame[["eg_um", "fl_um", "area_um2"]].to_numpy(float)
    if not np.isfinite(x).all() or (x[:, :2] < 0).any() or (x[:, 2] <= 0).any():
        raise ValueError("Invalid model features")
    expected = [
        f"pf{int(row.has_pf)}_eg{int(row.eg_um > 0)}_fl{int(row.fl_um > 0)}"
        for row in frame.itertuples()
    ]
    if not frame["p3_branch_id"].eq(pd.Series(expected, index=frame.index)).all():
        raise ValueError("Frozen topology branch does not match discrete geometry")


def _scaled(features: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    low = features.min(axis=0)
    high = features.max(axis=0)
    scale = np.where(high > low, high - low, 1.0)
    return (features - low) / scale, low, high, scale


def _prediction_locations(frame: pd.DataFrame, branch: str) -> np.ndarray:
    return np.flatnonzero(frame["p3_branch_id"].eq(branch).to_numpy())


class ExtraTreesScalar:
    model_name = "p3_extra_trees_scalar"

    def __init__(self, target: str, config: dict[str, Any]):
        self.target = target
        self.config = dict(config)
        self.branches: dict[str, dict[str, Any]] = {}

    def fit(self, frame: pd.DataFrame) -> ExtraTreesScalar:
        _validate_training_frame(frame, self.target)
        self.branches = {}
        features = list(self.config["feature_order"])
        for branch, group in frame.groupby("p3_branch_id", sort=True):
            x = group[features].to_numpy(float)
            xn, low, high, scale = _scaled(x)
            model = ExtraTreesRegressor(
                n_estimators=int(self.config["n_estimators"]),
                min_samples_leaf=int(self.config["min_samples_leaf"]),
                max_features=float(self.config["max_features"]),
                random_state=int(self.config["random_state"]),
                n_jobs=int(self.config["n_jobs"]),
            ).fit(xn, np.log(group[self.target].to_numpy(float)))
            self.branches[branch] = {
                "model": model,
                "low": low,
                "high": high,
                "scale": scale,
                "feature_order": features,
                "scaling": "training_fold_min_max_zero_range_scale_1",
                "target_transform": "natural_log",
                "prediction_transform": "exp",
            }
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        result = np.full(len(frame), np.nan, dtype=float)
        for branch in frame["p3_branch_id"].unique():
            state = self.branches.get(branch)
            if state is None:
                continue
            where = _prediction_locations(frame, branch)
            x = frame.iloc[where][state["feature_order"]].to_numpy(float)
            xn = (x - state["low"]) / state["scale"]
            result[where] = np.exp(state["model"].predict(xn))
        return result

    def transform_records(self) -> list[dict[str, Any]]:
        return [
            {
                "branch_id": branch,
                "low": state["low"].tolist(),
                "high": state["high"].tolist(),
                "scale": state["scale"].tolist(),
                **{
                    key: state[key]
                    for key in (
                        "feature_order",
                        "scaling",
                        "target_transform",
                        "prediction_transform",
                    )
                },
            }
            for branch, state in sorted(self.branches.items())
        ]


class TrendGPLogAreaScalar:
    model_name = "p6c_trend_gp_log_area_scalar"

    def __init__(self, target: str, config: dict[str, Any]):
        self.target = target
        self.config = dict(config)
        self.branches: dict[str, dict[str, Any]] = {}

    def fit(self, frame: pd.DataFrame) -> TrendGPLogAreaScalar:
        _validate_training_frame(frame, self.target)
        self.branches = {}
        gp_config = self.config["gp"]
        for branch, group in frame.groupby("p3_branch_id", sort=True):
            x = np.column_stack(
                [
                    group["eg_um"].to_numpy(float),
                    group["fl_um"].to_numpy(float),
                    np.log(group["area_um2"].to_numpy(float)),
                ]
            )
            xn, low, high, scale = _scaled(x)
            log_y = np.log(group[self.target].to_numpy(float))
            trend = Ridge(alpha=float(self.config["trend"]["alpha"])).fit(xn, log_y)
            log_residual = log_y - trend.predict(xn)
            kernel = Matern(
                length_scale=float(gp_config["length_scale"]),
                length_scale_bounds=gp_config["length_scale_bounds"],
                nu=float(gp_config["nu"]),
            ) + WhiteKernel(
                noise_level=float(gp_config["noise_level"]),
                noise_level_bounds=gp_config["noise_level_bounds"],
            )
            gp = GaussianProcessRegressor(
                kernel=kernel,
                alpha=float(gp_config["alpha"]),
                optimizer=gp_config["optimizer"],
                normalize_y=bool(gp_config["normalize_y"]),
                random_state=gp_config["random_state"],
            ).fit(xn, log_residual)
            self.branches[branch] = {
                "trend": trend,
                "gp": gp,
                "low": low,
                "high": high,
                "scale": scale,
                "feature_order": list(self.config["feature_order_after_transform"]),
                "area_transform": "natural_log_before_trend_and_gp",
                "scaling": "training_fold_min_max_zero_range_scale_1",
                "target_transform": "natural_log_residual",
                "prediction_transform": "exp_gp_log_residual_plus_trend_log",
            }
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        result = np.full(len(frame), np.nan, dtype=float)
        for branch in frame["p3_branch_id"].unique():
            state = self.branches.get(branch)
            if state is None:
                continue
            where = _prediction_locations(frame, branch)
            selected = frame.iloc[where]
            x = np.column_stack(
                [
                    selected["eg_um"].to_numpy(float),
                    selected["fl_um"].to_numpy(float),
                    np.log(selected["area_um2"].to_numpy(float)),
                ]
            )
            xn = (x - state["low"]) / state["scale"]
            log_prediction = state["gp"].predict(xn) + state["trend"].predict(xn)
            result[where] = np.exp(log_prediction)
        return result

    def transform_records(self) -> list[dict[str, Any]]:
        return [
            {
                "branch_id": branch,
                "low": state["low"].tolist(),
                "high": state["high"].tolist(),
                "scale": state["scale"].tolist(),
                **{
                    key: state[key]
                    for key in (
                        "feature_order",
                        "area_transform",
                        "scaling",
                        "target_transform",
                        "prediction_transform",
                    )
                },
            }
            for branch, state in sorted(self.branches.items())
        ]


def fit_model(
    model_name: str, frame: pd.DataFrame, target: str, protocol: dict[str, Any]
) -> TrainingTargetMedianConstant | ExtraTreesScalar | TrendGPLogAreaScalar:
    """Fit one fixed configuration; no weights, search, or outer result is accepted."""
    _validate_training_frame(frame, target)
    if model_name == "training_target_median_constant":
        return TrainingTargetMedianConstant(target, float(frame[target].median()))
    if model_name == "p3_extra_trees_scalar":
        return ExtraTreesScalar(target, protocol["models"][model_name]).fit(frame)
    if model_name == "p6c_trend_gp_log_area_scalar":
        return TrendGPLogAreaScalar(target, protocol["models"][model_name]).fit(frame)
    raise ValueError(f"Unknown frozen model: {model_name}")


def stable_id(prefix: str, values: Any) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def prepare_output_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise FileExistsError(f"Refusing non-empty output directory: {path}")
        return
    path.mkdir(parents=True, exist_ok=False)


def _role_ids(role: dict[str, Any], field: str) -> list[str]:
    values = list(role[field])
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate frozen IDs in {field}")
    count_field = {"structure_ids": "structures", "design_group_ids": "design_groups"}[field]
    if len(values) != int(role["counts"][count_field]):
        raise ValueError(f"Frozen {field} count mismatch")
    return values


def validate_scope_ids(scope: dict[str, Any], frame: pd.DataFrame) -> None:
    outer = scope["outer"]
    train_ids = set(_role_ids(outer["train"], "structure_ids"))
    test_ids = set(_role_ids(outer["test"], "structure_ids"))
    embargo_role = outer.get("embargo")
    embargo_ids = set(_role_ids(embargo_role, "structure_ids")) if embargo_role else set()
    if train_ids & (test_ids | embargo_ids):
        raise ValueError("Outer structure leakage in frozen splits")
    known = set(frame["structure_key"])
    if not train_ids | test_ids | embargo_ids <= known:
        raise ValueError("Frozen split IDs are absent from the new label catalog")
    metadata = frame.drop_duplicates("structure_key").set_index("structure_key")
    train_groups = set(metadata.loc[sorted(train_ids), "p3_design_group_id"])
    held_groups = set(metadata.loc[sorted(test_ids | embargo_ids), "p3_design_group_id"])
    if train_groups & held_groups:
        raise ValueError("Outer design-group leakage in frozen splits")

    seen_validation = set()
    for fold in scope["inner_group_kfold"]["folds"]:
        fold_train = set(_role_ids(fold["train"], "structure_ids"))
        validation = set(_role_ids(fold["validation"], "structure_ids"))
        if fold_train & validation or not fold_train | validation <= train_ids:
            raise ValueError("Inner structure leakage or out-of-scope IDs")
        if seen_validation & validation:
            raise ValueError("Duplicate inner validation assignment")
        seen_validation |= validation
        inner_train_groups = set(metadata.loc[sorted(fold_train), "p3_design_group_id"])
        validation_groups = set(metadata.loc[sorted(validation), "p3_design_group_id"])
        if inner_train_groups & validation_groups:
            raise ValueError("Inner design-group leakage")
    if seen_validation != train_ids:
        raise ValueError("Inner folds do not partition every outer-training structure exactly once")


def eligible_target_frame(
    frame: pd.DataFrame, target: str, structure_ids: set[str], threshold: int
) -> pd.DataFrame:
    selected = frame.loc[
        frame["parameter"].eq(target) & frame["structure_key"].isin(structure_ids)
    ].copy()
    eligible = (
        np.isfinite(selected["median"].to_numpy(float))
        & selected["median"].gt(0)
        & selected["n_valid"].ge(threshold)
        & ~selected["low_support"]
    )
    selected = selected.loc[eligible].rename(columns={"median": target})
    return selected.sort_values("structure_key").reset_index(drop=True)


def fold_assignment_rows(
    population: str, scope: dict[str, Any], frame: pd.DataFrame
) -> list[dict[str, Any]]:
    metadata = frame.drop_duplicates("structure_key").set_index("structure_key")
    rows = []
    for fold in scope["inner_group_kfold"]["folds"]:
        for structure_id in sorted(_role_ids(fold["validation"], "structure_ids")):
            row = metadata.loc[structure_id]
            rows.append(
                {
                    "population": population,
                    "scope": scope["scope_id"],
                    "fold": int(fold["fold"]),
                    "structure_key": structure_id,
                    "p3_structure_id": row["p3_structure_id"],
                    "p3_design_group_id": row["p3_design_group_id"],
                    "p3_branch_id": row["p3_branch_id"],
                    "assignment": "validation; all other frozen folds are training-only",
                }
            )
    return rows


def _not_evaluable_cell(
    out: Path,
    population: str,
    scope: dict[str, Any],
    target: str,
    reason: str,
    label_source: dict[str, Any],
    source_hashes: dict[str, str],
) -> dict[str, Any]:
    record = {
        "population": population,
        "scope": scope["scope_id"],
        "target": target,
        "status": "NOT_EVALUABLE",
        "not_evaluable_reason": reason,
        "selected_model": None,
        "model_bundle_path": None,
        "model_bundle_sha256": None,
    }
    for model in CANDIDATE_ORDER:
        record[f"inner_mae__{model}"] = None
        record[f"inner_n__{model}"] = 0
    metadata = {
        "schema": "aln_device_median_b04_cell_v1",
        **record,
        "label_source": label_source,
        "source_hashes": source_hashes,
        "training_structure_ids": [],
        "outer_test_predictions_generated": False,
        "outer_test_used_for_selection": False,
    }
    cell_dir = out / population / scope["scope_id"] / target
    write_json(cell_dir / "metadata.json", metadata)
    return {"selection": record, "inner_predictions": [], "inner_fits": [], "final_fits": []}


def fit_cell(
    out: Path,
    population: str,
    scope: dict[str, Any],
    target: str,
    frame: pd.DataFrame,
    protocol: dict[str, Any],
    label_source: dict[str, Any],
    source_hashes: dict[str, str],
) -> dict[str, Any]:
    reason = validate_scope_for_fit(scope)
    if reason is None and len(
        eligible_target_frame(
            frame,
            target,
            set(scope["outer"]["train"]["structure_ids"]),
            int(protocol["validity_and_support"]["n_valid_threshold"]),
        )["p3_design_group_id"].unique()
    ) < 3:
        reason = "fewer than 3 target-eligible outer-training design groups"
    if reason is not None:
        return _not_evaluable_cell(
            out, population, scope, target, reason, label_source, source_hashes
        )

    validate_scope_ids(scope, frame)
    train_ids = sorted(_role_ids(scope["outer"]["train"], "structure_ids"))
    eligible = eligible_target_frame(
        frame,
        target,
        set(train_ids),
        int(protocol["validity_and_support"]["n_valid_threshold"]),
    )
    eligible_ids = sorted(eligible["structure_key"])
    inner_predictions = []
    inner_fits = []
    for fold in scope["inner_group_kfold"]["folds"]:
        fold_number = int(fold["fold"])
        fold_train_ids = set(_role_ids(fold["train"], "structure_ids"))
        validation_ids = set(_role_ids(fold["validation"], "structure_ids"))
        fold_train = eligible.loc[eligible["structure_key"].isin(fold_train_ids)].copy()
        validation = eligible.loc[eligible["structure_key"].isin(validation_ids)].copy()
        if fold_train.empty:
            raise RuntimeError("NOT_EVALUABLE: empty target-specific inner-training fold")
        for model_name in CANDIDATE_ORDER:
            model = fit_model(model_name, fold_train, target, protocol)
            predicted = model.predict(validation)
            actual = validation[target].to_numpy(float)
            if len(actual) and (
                not np.isfinite(predicted).all()
                or (predicted <= 0).any()
                or len(predicted) != len(actual)
            ):
                raise RuntimeError(
                    f"Invalid inner prediction: {population}/{scope['scope_id']}/{target}/"
                    f"{model_name}/fold_{fold_number}"
                )
            fit_train_ids = sorted(fold_train["structure_key"])
            fit_prediction_ids = sorted(validation["structure_key"])
            training_id = stable_id(
                "train",
                [population, scope["scope_id"], target, model_name, fold_number, fit_train_ids],
            )
            inner_fits.append(
                {
                    "population": population,
                    "scope": scope["scope_id"],
                    "target": target,
                    "model": model_name,
                    "fold": fold_number,
                    "training_id": training_id,
                    "training_structure_ids": fit_train_ids,
                    "training_design_group_ids": sorted(
                        fold_train["p3_design_group_id"].unique().tolist()
                    ),
                    "prediction_structure_ids": fit_prediction_ids,
                    "prediction_design_group_ids": sorted(
                        validation["p3_design_group_id"].unique().tolist()
                    ),
                    "feature_target_transforms": model.transform_records(),
                    "configuration": protocol["models"][model_name],
                    "label_sha256": label_source["sha256"],
                    "outer_test_used": False,
                }
            )
            for row, prediction in zip(validation.itertuples(), predicted, strict=True):
                actual_value = float(getattr(row, target))
                prediction_value = float(prediction)
                prediction_id = stable_id(
                    "pred",
                    [
                        population,
                        scope["scope_id"],
                        target,
                        model_name,
                        fold_number,
                        row.structure_key,
                    ],
                )
                inner_predictions.append(
                    {
                        "population": population,
                        "scope": scope["scope_id"],
                        "target": target,
                        "model": model_name,
                        "fold": fold_number,
                        "training_id": training_id,
                        "prediction_id": prediction_id,
                        "structure_key": row.structure_key,
                        "p3_structure_id": row.p3_structure_id,
                        "p3_design_group_id": row.p3_design_group_id,
                        "p3_branch_id": row.p3_branch_id,
                        "actual": actual_value,
                        "prediction": prediction_value,
                        "signed_error": prediction_value - actual_value,
                        "absolute_error": abs(prediction_value - actual_value),
                    }
                )

    raw_unit_mae = {}
    inner_counts = {}
    for model_name in CANDIDATE_ORDER:
        rows = [row for row in inner_predictions if row["model"] == model_name]
        if len(rows) != len(eligible_ids) or {row["structure_key"] for row in rows} != set(
            eligible_ids
        ):
            raise RuntimeError("Inner out-of-fold predictions are incomplete for a candidate")
        inner_counts[model_name] = len(rows)
        raw_unit_mae[model_name] = float(
            np.mean([row["absolute_error"] for row in rows], dtype=float)
        )
    selected_model = select_model(raw_unit_mae)

    final_models = {}
    final_fits = []
    final_transforms = {}
    for model_name in CANDIDATE_ORDER:
        model = fit_model(model_name, eligible, target, protocol)
        final_models[model_name] = model
        training_id = stable_id(
            "train", [population, scope["scope_id"], target, model_name, "final", eligible_ids]
        )
        final_fits.append(
            {
                "population": population,
                "scope": scope["scope_id"],
                "target": target,
                "model": model_name,
                "training_id": training_id,
                "training_structure_ids": eligible_ids,
                "training_design_group_ids": sorted(eligible["p3_design_group_id"].unique()),
                "label_sha256": label_source["sha256"],
                "outer_test_used": False,
            }
        )
        final_transforms[model_name] = model.transform_records()

    selection = {
        "population": population,
        "scope": scope["scope_id"],
        "target": target,
        "status": "SELECTED",
        "not_evaluable_reason": None,
        "selected_model": selected_model,
    }
    for model_name in CANDIDATE_ORDER:
        selection[f"inner_mae__{model_name}"] = raw_unit_mae[model_name]
        selection[f"inner_n__{model_name}"] = inner_counts[model_name]
    metadata = {
        "schema": "aln_device_median_b04_model_bundle_v1",
        "task_id": "B04",
        "attempt_id": protocol["attempt_id"],
        "population": population,
        "scope": scope["scope_id"],
        "target": target,
        "target_unit": protocol.get("units", {}).get(target),
        "selected_model": selected_model,
        "selection": selection,
        "selection_rule": "pooled eligible inner out-of-fold raw-target-unit MAE; frozen tie order",
        "label_source": label_source,
        "source_hashes": source_hashes,
        "training_structure_ids": eligible_ids,
        "training_design_group_ids": sorted(eligible["p3_design_group_id"].unique()),
        "final_training_ids": {row["model"]: row["training_id"] for row in final_fits},
        "feature_target_transforms": final_transforms,
        "configurations": protocol["models"],
        "outer_test_predictions_generated": False,
        "outer_test_used_for_selection": False,
        "old_weights_loaded": False,
        "prediction_intervals": {"generated": False, "lower": None, "upper": None},
    }
    bundle = {"metadata": metadata, "models": final_models}
    cell_dir = out / population / scope["scope_id"] / target
    cell_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = cell_dir / "model_bundle.joblib"
    joblib.dump(bundle, bundle_path, compress=3)
    bundle_record = file_record("model_bundle", bundle_path)
    selection["model_bundle_path"] = bundle_record["path"]
    selection["model_bundle_sha256"] = bundle_record["sha256"]
    metadata["model_bundle"] = bundle_record
    write_json(cell_dir / "metadata.json", metadata)
    return {
        "selection": selection,
        "inner_predictions": inner_predictions,
        "inner_fits": inner_fits,
        "final_fits": final_fits,
    }


INNER_PREDICTION_FIELDS = [
    "population",
    "scope",
    "target",
    "model",
    "fold",
    "training_id",
    "prediction_id",
    "structure_key",
    "p3_structure_id",
    "p3_design_group_id",
    "p3_branch_id",
    "actual",
    "prediction",
    "signed_error",
    "absolute_error",
]
FOLD_ASSIGNMENT_FIELDS = [
    "population",
    "scope",
    "fold",
    "structure_key",
    "p3_structure_id",
    "p3_design_group_id",
    "p3_branch_id",
    "assignment",
]
SELECTION_FIELDS = [
    "population",
    "scope",
    "target",
    "status",
    "not_evaluable_reason",
    "selected_model",
    *[f"inner_mae__{model}" for model in CANDIDATE_ORDER],
    *[f"inner_n__{model}" for model in CANDIDATE_ORDER],
    "model_bundle_path",
    "model_bundle_sha256",
]


def _validate_protocol(protocol: dict[str, Any], splits: dict[str, Any]) -> None:
    if protocol.get("schema") != "aln_device_median_b03_frozen_experiment_protocol_v1":
        raise ValueError("Unexpected frozen protocol schema")
    if splits.get("schema") != "aln_device_median_b03_frozen_splits_v1":
        raise ValueError("Unexpected frozen splits schema")
    if protocol["attempt_id"] != splits["attempt_id"]:
        raise ValueError("Protocol/splits attempt mismatch")
    if protocol["resource_policy"]["seed"] != splits["seed"]:
        raise ValueError("Protocol/splits seed mismatch")
    if protocol["model_selection"]["candidate_order_and_tie_priority"] != list(CANDIDATE_ORDER):
        raise ValueError("Frozen candidate/tie order changed")
    trees = protocol["models"]["p3_extra_trees_scalar"]
    if trees["random_state"] != splits["seed"] or int(trees["n_jobs"]) > 2:
        raise ValueError("Frozen ExtraTrees seed or resource policy changed")
    if protocol.get("split_protocol", {}).get("outer_scopes") is not None:
        expected = protocol["split_protocol"]["outer_scopes"]
        actual = [scope["scope_id"] for scope in splits["scopes"]]
        if actual != expected:
            raise ValueError("Frozen outer scope order changed")


def _resource_record(
    protocol: dict[str, Any],
    started_at: str,
    started_monotonic: float,
    status: str,
    inner_fit_count: int,
    final_fit_count: int,
) -> dict[str, Any]:
    thread_variables = (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
    )
    return {
        "schema": "aln_device_median_b04_resource_v1",
        "status": status,
        "started_at": started_at,
        "recorded_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": time.monotonic() - started_monotonic,
        "checkpoint_seconds": int(protocol["resource_policy"]["checkpoint_seconds"]),
        "same_configuration_continuation_allowed": True,
        "algorithm_change_or_search_allowed": False,
        "seed": int(protocol["resource_policy"]["seed"]),
        "numeric_thread_environment": {name: os.environ.get(name) for name in thread_variables},
        "threadpool_info": threadpool_info(),
        "extra_trees_n_jobs": int(protocol["models"]["p3_extra_trees_scalar"]["n_jobs"]),
        "inner_fits_completed": inner_fit_count,
        "final_outer_train_fits_completed": final_fit_count,
    }


def run_fit(args: argparse.Namespace) -> None:
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        if os.environ.get(name) != "1":
            raise RuntimeError(f"B04 requires {name}=1")
    args.out = args.out.resolve()
    prepare_output_directory(args.out)
    started_at = datetime.now(UTC).isoformat()
    started_monotonic = time.monotonic()
    protocol_path = args.protocol.resolve()
    splits_path = args.splits.resolve()
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    splits = json.loads(splits_path.read_text(encoding="utf-8"))
    _validate_protocol(protocol, splits)
    loaded = load_populations(protocol, args.data.resolve())

    input_paths = {"protocol": protocol_path, "splits": splits_path}
    input_paths.update(
        {population: loaded[population]["source"]["path"] for population in POPULATIONS}
    )
    optional = {
        "b02_manifest": args.data.resolve() / "manifest.json",
        "config_provenance": protocol_path.parent / "config_provenance.json",
    }
    input_paths.update({name: path for name, path in optional.items() if path.is_file()})
    before = {name: file_sha256(Path(path)) for name, path in input_paths.items()}
    source_hashes = dict(before)

    selections = []
    inner_predictions = []
    inner_fits = []
    final_fits = []
    fold_assignments = []
    completed_cells = []
    last_resource_checkpoint = started_monotonic
    for population in POPULATIONS:
        frame = loaded[population]["frame"]
        for scope in splits["scopes"]:
            if validate_scope_for_fit(scope) is None:
                validate_scope_ids(scope, frame)
                fold_assignments.extend(fold_assignment_rows(population, scope, frame))
            for target in protocol["core_targets"]:
                result = fit_cell(
                    args.out,
                    population,
                    scope,
                    target,
                    frame,
                    protocol,
                    loaded[population]["source"],
                    source_hashes,
                )
                selections.append(result["selection"])
                inner_predictions.extend(result["inner_predictions"])
                inner_fits.extend(result["inner_fits"])
                final_fits.extend(result["final_fits"])
                completed_cells.append(f"{population}/{scope['scope_id']}/{target}")
                write_json(
                    args.out / "checkpoint.json",
                    {
                        "schema": "aln_device_median_b04_checkpoint_v1",
                        "status": "RUNNING",
                        "completed_cells": completed_cells,
                        "next_cell_index": len(completed_cells),
                        "inner_fits_completed": len(inner_fits),
                        "final_outer_train_fits_completed": len(final_fits),
                        "source_hashes": source_hashes,
                        "configuration_unchanged": True,
                    },
                )
                if time.monotonic() - last_resource_checkpoint >= int(
                    protocol["resource_policy"]["checkpoint_seconds"]
                ):
                    write_json(
                        args.out / "resource.json",
                        _resource_record(
                            protocol,
                            started_at,
                            started_monotonic,
                            "RUNNING_CHECKPOINT",
                            len(inner_fits),
                            len(final_fits),
                        ),
                    )
                    last_resource_checkpoint = time.monotonic()

    write_csv(args.out / "inner_predictions.csv", inner_predictions, INNER_PREDICTION_FIELDS)
    write_csv(args.out / "fold_assignments.csv", fold_assignments, FOLD_ASSIGNMENT_FIELDS)
    write_csv(args.out / "selection.csv", selections, SELECTION_FIELDS)
    write_json(
        args.out / "inner_fit_index.json",
        {
            "schema": "aln_device_median_b04_inner_fit_index_v1",
            "fit_scope": "inner training fold only; validation IDs are prediction-only",
            "fits": inner_fits,
        },
    )
    write_json(
        args.out / "final_fit_index.json",
        {
            "schema": "aln_device_median_b04_final_fit_index_v1",
            "fit_scope": "full eligible outer-training IDs only; no outer predictions",
            "fits": final_fits,
        },
    )
    write_json(
        args.out / "selection.json",
        {
            "schema": "aln_device_median_b04_selection_v1",
            "unit": "raw target-unit MAE pooled over eligible inner out-of-fold structures",
            "candidate_order_and_tie_priority": list(CANDIDATE_ORDER),
            "outer_test_used": False,
            "selections": selections,
        },
    )

    after = {name: file_sha256(Path(path)) for name, path in input_paths.items()}
    if before != after:
        raise RuntimeError("Registered B04 input hash changed during fitting")
    write_json(
        args.out / "checkpoint.json",
        {
            "schema": "aln_device_median_b04_checkpoint_v1",
            "status": "COMPLETE_PENDING_A4",
            "completed_cells": completed_cells,
            "next_cell_index": len(completed_cells),
            "inner_fits_completed": len(inner_fits),
            "final_outer_train_fits_completed": len(final_fits),
            "source_hashes": source_hashes,
            "configuration_unchanged": True,
        },
    )
    write_json(
        args.out / "resource.json",
        _resource_record(
            protocol,
            started_at,
            started_monotonic,
            "COMPLETE_PENDING_A4",
            len(inner_fits),
            len(final_fits),
        ),
    )
    selected_by_model = {
        model: sum(row["selected_model"] == model for row in selections)
        for model in CANDIDATE_ORDER
    }
    fit_counts = {
        "inner": len(inner_fits),
        "final_outer_train": len(final_fits),
        "total": len(inner_fits) + len(final_fits),
    }
    fit_counts_by_model = {
        model: {
            "inner": sum(row["model"] == model for row in inner_fits),
            "final_outer_train": sum(row["model"] == model for row in final_fits),
        }
        for model in CANDIDATE_ORDER
    }
    output_files = [
        file_record(str(path.relative_to(args.out)), path)
        for path in sorted(args.out.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    model_hashes = {
        str(path.relative_to(args.out)): file_sha256(path)
        for path in sorted(args.out.rglob("model_bundle.joblib"))
    }
    manifest = {
        "schema": "aln_device_median_b04_model_manifest_v1",
        "schema_version": "1.0.0",
        "task_id": "B04",
        "attempt_id": protocol["attempt_id"],
        "status": "PENDING_A4",
        "scientific_status": "REJECTED",
        "trusted": False,
        "next_task": None,
        "fit_counts": fit_counts,
        "fit_counts_by_model": fit_counts_by_model,
        "selection_counts": {
            "total": len(selections),
            "selected": sum(row["status"] == "SELECTED" for row in selections),
            "not_evaluable": sum(row["status"] == "NOT_EVALUABLE" for row in selections),
            "selected_by_model": selected_by_model,
        },
        "inner_prediction_count": len(inner_predictions),
        "fold_assignment_count": len(fold_assignments),
        "label_sources": {name: loaded[name]["source"] for name in POPULATIONS},
        "source_hashes": source_hashes,
        "inputs_unchanged_before_after": True,
        "outer_test_used_for_selection_or_fitting": False,
        "outer_predictions_generated": False,
        "old_weights_loaded": False,
        "prediction_intervals_generated": False,
        "model_hashes": model_hashes,
        "inputs": [file_record(name, Path(path)) for name, path in input_paths.items()],
        "source_files": [file_record("fit_cli", Path(__file__).resolve())],
        "outputs": output_files,
    }
    write_json(args.out / "manifest.json", manifest)


EVALUATION_PREDICTION_FIELDS = [
    "population",
    "scope",
    "target",
    "model",
    "model_role",
    "prediction_id",
    "structure_key",
    "p3_structure_id",
    "p3_design_group_id",
    "p3_branch_id",
    "unit",
    "actual",
    "prediction",
    "signed_error",
    "absolute_error",
    "squared_error",
    "scored",
    "eligible_main",
    "support_layer",
    "status",
    "rejection_reason",
    "n_valid",
    "low_support",
    "eg_um",
    "fl_um",
    "area_um2",
    "has_pf",
    "model_bundle_sha256",
]
METRIC_FIELDS = [
    "population",
    "scope",
    "target",
    "model",
    "model_role",
    "unit",
    "aggregation",
    "subset_id",
    "MAE",
    "RMSE",
    "bias",
    "n",
]
GATE_INPUT_FIELDS = [
    "population",
    "scope",
    "target",
    "evaluation_status",
    "selected_model",
    "selected_mae",
    "constant_mae",
    "mae_reduction_pct",
    "mae_reduction_status",
    "eligible_n",
    "selected_n",
    "constant_n",
    "gate_applicable",
    "reporting_required",
    "reporting_complete",
    "cell_gate_pass",
    "failure_reason",
]


def _load_evaluation_populations(
    protocol_path: Path, protocol: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    registered = {
        item["id"]: item
        for item in protocol["data_version"]["inputs"]
        if item.get("id") in {f"structure_targets_{name}" for name in POPULATIONS}
    }
    paths = []
    for population in POPULATIONS:
        item = registered.get(f"structure_targets_{population}")
        if item is None or not item.get("path"):
            raise RuntimeError(f"Missing registered label path: {population}")
        path = Path(item["path"])
        paths.append(path if path.is_absolute() else protocol_path.parent / path)
    if len({path.parent.resolve() for path in paths}) != 1:
        raise RuntimeError("Registered primary/sensitivity labels must share a data directory")
    return load_populations(protocol, paths[0].parent)


def _evaluation_splits_path(protocol_path: Path, protocol: dict[str, Any]) -> Path:
    promoted = protocol_path.parent / "splits.json"
    if promoted.is_file():
        return promoted.resolve()
    registered = protocol.get("split_protocol", {}).get("splits_path")
    if not registered:
        raise RuntimeError("Frozen splits path is not registered")
    path = Path(registered)
    return (path if path.is_absolute() else protocol_path.parent / path).resolve()


def _score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row["scored"]]
    if not scored:
        return {"MAE": None, "RMSE": None, "bias": None, "n": 0}
    errors = np.asarray([row["signed_error"] for row in scored], dtype=float)
    return {
        "MAE": float(np.mean(np.abs(errors), dtype=float)),
        "RMSE": float(np.sqrt(np.mean(errors**2, dtype=float))),
        "bias": float(np.mean(errors, dtype=float)),
        "n": len(scored),
    }


def _metric_record(
    row: dict[str, Any], aggregation: str, subset_id: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "population": row["population"],
        "scope": row["scope"],
        "target": row["target"],
        "model": row["model"],
        "model_role": row["model_role"],
        "unit": row["unit"],
        "aggregation": aggregation,
        "subset_id": subset_id,
        **_score_rows(rows),
    }


def summarize_overall_gate(
    gate_rows: list[dict[str, Any]], protocol: dict[str, Any]
) -> dict[str, Any]:
    required_scopes = set(protocol["research_gate"]["required_scopes"])
    required_targets = set(protocol["research_gate"]["required_targets"])
    expected = {
        (population, scope, target)
        for population in POPULATIONS
        for scope in required_scopes
        for target in required_targets
    }
    actual = {
        (str(row["population"]), str(row["scope"]), str(row["target"]))
        for row in gate_rows
        if row.get("reporting_required") is True
    }
    failures = [
        f"missing required evaluation: {'/'.join(key)}" for key in sorted(expected - actual)
    ]
    for row in gate_rows:
        key = f"{row['population']}/{row['scope']}/{row['target']}"
        if row.get("reporting_required") is True and row.get("reporting_complete") is not True:
            failures.append(f"incomplete required evaluation: {key}")
        if row.get("gate_applicable") is True and row.get("cell_gate_pass") is not True:
            failures.append(f"research gate failed: {key}: {row.get('failure_reason')}")
    return {
        "status": "REJECTED" if failures else "PASS",
        "failure_count": len(failures),
        "failures": failures,
        "gate_population": "primary",
        "required_scopes": sorted(required_scopes),
        "required_targets": sorted(required_targets),
        "sensitivity_substitutes_for_primary": False,
    }


def _not_evaluable_gate_row(
    population: str, scope: str, target: str, reason: str, protocol: dict[str, Any]
) -> dict[str, Any]:
    reporting_required = (
        scope in protocol["research_gate"]["required_scopes"]
        and target in protocol["research_gate"]["required_targets"]
    )
    gate_applicable = reporting_required and population == "primary"
    return {
        "population": population,
        "scope": scope,
        "target": target,
        "evaluation_status": "NOT_EVALUABLE",
        "selected_model": None,
        "selected_mae": None,
        "constant_mae": None,
        "mae_reduction_pct": None,
        "mae_reduction_status": "not_computed",
        "eligible_n": 0,
        "selected_n": 0,
        "constant_n": 0,
        "gate_applicable": gate_applicable,
        "reporting_required": reporting_required,
        "reporting_complete": False,
        "cell_gate_pass": False if gate_applicable else None,
        "failure_reason": reason,
    }


def run_evaluate(args: argparse.Namespace) -> None:
    protocol_path = args.protocol.resolve()
    models_root = args.models.resolve()
    args.out = args.out.resolve()
    prepare_output_directory(args.out)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    splits_path = _evaluation_splits_path(protocol_path, protocol)
    splits = json.loads(splits_path.read_text(encoding="utf-8"))
    _validate_protocol(protocol, splits)
    loaded = _load_evaluation_populations(protocol_path, protocol)
    model_manifest_path = models_root / "manifest.json"
    model_manifest = json.loads(model_manifest_path.read_text(encoding="utf-8"))
    source_hashes = {
        "protocol": file_sha256(protocol_path),
        "splits": file_sha256(splits_path),
        "model_manifest": file_sha256(model_manifest_path),
    }
    for key in ("protocol", "splits"):
        if model_manifest["source_hashes"].get(key) != source_hashes[key]:
            raise RuntimeError(f"Frozen model input hash mismatch: {key}")
    for population in POPULATIONS:
        source = loaded[population]["source"]
        source_hashes[f"structure_targets_{population}"] = source["sha256"]
        if model_manifest["label_sources"][population]["sha256"] != source["sha256"]:
            raise RuntimeError(f"Frozen model label hash mismatch: {population}")
    selection_path = models_root / "selection.json"
    selection_document = json.loads(selection_path.read_text(encoding="utf-8"))
    source_hashes["selection"] = file_sha256(selection_path)
    selections = {
        (row["population"], row["scope"], row["target"]): row
        for row in selection_document["selections"]
    }

    predictions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    bundle_hashes: dict[str, str] = {}
    readback_count = 0
    threshold = int(protocol["validity_and_support"]["n_valid_threshold"])
    for population in POPULATIONS:
        frame = loaded[population]["frame"]
        for scope in splits["scopes"]:
            scope_id = scope["scope_id"]
            test_ids = set(_role_ids(scope["outer"]["test"], "structure_ids"))
            for target in protocol["core_targets"]:
                key = (population, scope_id, target)
                selection = selections.get(key)
                if selection is None:
                    raise RuntimeError(f"Missing frozen selection: {'/'.join(key)}")
                if selection["status"] != "SELECTED":
                    gate_rows.append(
                        _not_evaluable_gate_row(
                            population,
                            scope_id,
                            target,
                            selection["not_evaluable_reason"] or "frozen cell is not evaluable",
                            protocol,
                        )
                    )
                    continue
                cell_dir = models_root / population / scope_id / target
                bundle_path = cell_dir / "model_bundle.joblib"
                metadata_path = cell_dir / "metadata.json"
                relative_bundle = str(bundle_path.relative_to(models_root))
                bundle_hash = file_sha256(bundle_path)
                if model_manifest["model_hashes"].get(relative_bundle) != bundle_hash:
                    raise RuntimeError(f"Frozen model bundle hash mismatch: {relative_bundle}")
                bundle = joblib.load(bundle_path)
                sidecar = json.loads(metadata_path.read_text(encoding="utf-8"))
                bundle_metadata = bundle.get("metadata", {})
                for field in (
                    "schema",
                    "attempt_id",
                    "population",
                    "scope",
                    "target",
                    "selected_model",
                    "training_structure_ids",
                    "label_source",
                    "source_hashes",
                ):
                    if bundle_metadata.get(field) != sidecar.get(field):
                        raise RuntimeError(f"Bundle/sidecar metadata mismatch: {relative_bundle}")
                if sidecar["model_bundle"]["sha256"] != bundle_hash:
                    raise RuntimeError(f"Sidecar model hash mismatch: {relative_bundle}")
                if sidecar["selected_model"] != selection["selected_model"]:
                    raise RuntimeError(f"Bundle/selection mismatch: {relative_bundle}")
                if sidecar["label_source"]["sha256"] != loaded[population]["source"]["sha256"]:
                    raise RuntimeError(f"Bundle label hash mismatch: {relative_bundle}")
                if sidecar["outer_test_used_for_selection"] is not False:
                    raise RuntimeError(f"Outer contamination in frozen bundle: {relative_bundle}")
                selected_name = selection["selected_model"]
                models = bundle["models"]
                if selected_name not in models or "training_target_median_constant" not in models:
                    raise RuntimeError(f"Frozen selected/constant model missing: {relative_bundle}")
                bundle_hashes[relative_bundle] = bundle_hash
                source_hashes[f"model_bundle:{relative_bundle}"] = bundle_hash
                source_hashes[f"model_metadata:{relative_bundle}"] = file_sha256(metadata_path)
                readback_count += 1

                test = frame.loc[
                    frame["parameter"].eq(target) & frame["structure_key"].isin(test_ids)
                ].copy()
                test = test.sort_values("structure_key").reset_index(drop=True)
                test[target] = test["median"]
                cell_rows: list[dict[str, Any]] = []
                for role, model_name in (
                    ("selected", selected_name),
                    ("constant", "training_target_median_constant"),
                ):
                    predicted = models[model_name].predict(test)
                    if len(predicted) != len(test):
                        raise RuntimeError(f"Outer prediction length mismatch: {relative_bundle}")
                    role_rows: list[dict[str, Any]] = []
                    for row, prediction in zip(test.itertuples(), predicted, strict=True):
                        actual = float(getattr(row, target))
                        prediction = float(prediction)
                        valid_actual = bool(np.isfinite(actual) and actual > 0)
                        supported = bool(np.isfinite(prediction) and prediction > 0)
                        eligible_main = bool(
                            valid_actual and row.n_valid >= threshold and not row.low_support
                        )
                        support_layer = (
                            "main"
                            if eligible_main
                            else ("low_support" if valid_actual else "missing_or_invalid")
                        )
                        scored = valid_actual and supported
                        error = prediction - actual if scored else None
                        output_row = {
                            "population": population,
                            "scope": scope_id,
                            "target": target,
                            "model": model_name,
                            "model_role": role,
                            "prediction_id": stable_id(
                                "outer", [population, scope_id, target, role, row.structure_key]
                            ),
                            "structure_key": row.structure_key,
                            "p3_structure_id": row.p3_structure_id,
                            "p3_design_group_id": row.p3_design_group_id,
                            "p3_branch_id": row.p3_branch_id,
                            "unit": protocol.get("units", {}).get(target),
                            "actual": actual,
                            "prediction": prediction if supported else None,
                            "signed_error": error,
                            "absolute_error": abs(error) if error is not None else None,
                            "squared_error": error**2 if error is not None else None,
                            "scored": scored,
                            "eligible_main": eligible_main,
                            "support_layer": support_layer,
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
                            "model_bundle_sha256": bundle_hash,
                        }
                        role_rows.append(output_row)
                    cell_rows.extend(role_rows)
                    main_rows = [row for row in role_rows if row["eligible_main"]]
                    metrics.append(_metric_record(role_rows[0] if role_rows else {
                        "population": population,
                        "scope": scope_id,
                        "target": target,
                        "model": model_name,
                        "model_role": role,
                        "unit": protocol.get("units", {}).get(target),
                    }, "outer_scope", "all_main_eligible", main_rows))
                    for branch in sorted({row["p3_branch_id"] for row in main_rows}):
                        branch_rows = [row for row in main_rows if row["p3_branch_id"] == branch]
                        metrics.append(_metric_record(role_rows[0], "branch", branch, branch_rows))
                    for structure_id in sorted({row["structure_key"] for row in main_rows}):
                        structure_rows = [
                            row for row in main_rows if row["structure_key"] == structure_id
                        ]
                        metrics.append(
                            _metric_record(role_rows[0], "structure", structure_id, structure_rows)
                        )
                    low_rows = [row for row in role_rows if row["support_layer"] == "low_support"]
                    metrics.append(
                        _metric_record(role_rows[0], "low_support", "low_support", low_rows)
                    )
                    difficult_rows = [
                        row
                        for row in main_rows
                        if scope_id == "area_lower"
                        and row["area_um2"] == 700
                        and row["has_pf"] == 1
                    ]
                    metrics.append(
                        _metric_record(
                            role_rows[0], "700_pf", "area_um2==700;has_pf==1", difficult_rows
                        )
                    )
                predictions.extend(cell_rows)
                role_metrics = {
                    row["model_role"]: row
                    for row in metrics
                    if row["population"] == population
                    and row["scope"] == scope_id
                    and row["target"] == target
                    and row["aggregation"] == "outer_scope"
                }
                eligible_n = len(
                    {
                        row["structure_key"]
                        for row in cell_rows
                        if row["model_role"] == "selected" and row["eligible_main"]
                    }
                )
                selected_n = int(role_metrics["selected"]["n"])
                constant_n = int(role_metrics["constant"]["n"])
                reporting_complete = (
                    eligible_n > 0 and selected_n == eligible_n and constant_n == eligible_n
                )
                reporting_required = (
                    scope_id in protocol["research_gate"]["required_scopes"]
                    and target in protocol["research_gate"]["required_targets"]
                )
                gate_applicable = reporting_required and population == "primary"
                if reporting_complete:
                    comparison = gate_mae_comparison(
                        role_metrics["selected"]["MAE"],
                        role_metrics["constant"]["MAE"],
                        normal_holdout=gate_applicable and scope_id == "normal_holdout",
                    )
                else:
                    comparison = {
                        "mae_reduction_pct": None,
                        "mae_reduction_status": "not_computed_missing_predictions",
                        "cell_gate_pass": False,
                        "failure_reason": "missing or invalid main outer-test predictions",
                    }
                gate_rows.append(
                    {
                        "population": population,
                        "scope": scope_id,
                        "target": target,
                        "evaluation_status": "COMPLETE" if reporting_complete else "INCOMPLETE",
                        "selected_model": selected_name,
                        "selected_mae": role_metrics["selected"]["MAE"],
                        "constant_mae": role_metrics["constant"]["MAE"],
                        "mae_reduction_pct": comparison["mae_reduction_pct"],
                        "mae_reduction_status": comparison["mae_reduction_status"],
                        "eligible_n": eligible_n,
                        "selected_n": selected_n,
                        "constant_n": constant_n,
                        "gate_applicable": gate_applicable,
                        "reporting_required": reporting_required,
                        "reporting_complete": reporting_complete,
                        "cell_gate_pass": (
                            comparison["cell_gate_pass"] if gate_applicable else None
                        ),
                        "failure_reason": (
                            comparison["failure_reason"] if gate_applicable else None
                        ),
                    }
                )

    common_metrics: list[dict[str, Any]] = []
    for scope in splits["scopes"]:
        for target in protocol["core_targets"]:
            cell_rows = [
                row
                for row in predictions
                if row["scope"] == scope["scope_id"] and row["target"] == target
            ]
            for role in ("selected", "constant"):
                common_ids = None
                for population in POPULATIONS:
                    ids = {
                        row["structure_key"]
                        for row in cell_rows
                        if row["population"] == population
                        and row["model_role"] == role
                        and row["eligible_main"]
                        and row["scored"]
                    }
                    common_ids = ids if common_ids is None else common_ids & ids
                for population in POPULATIONS:
                    population_rows = [
                        row
                        for row in cell_rows
                        if row["population"] == population
                        and row["model_role"] == role
                        and row["structure_key"] in (common_ids or set())
                    ]
                    if not population_rows:
                        continue
                    common_metrics.append(
                        _metric_record(
                            population_rows[0],
                            "common_sample",
                            "primary_sensitivity_main_scored_intersection",
                            population_rows,
                        )
                    )

    for relative, before_hash in bundle_hashes.items():
        if file_sha256(models_root / relative) != before_hash:
            raise RuntimeError(f"Frozen bundle changed during evaluation: {relative}")
    write_csv(args.out / "predictions.csv", predictions, EVALUATION_PREDICTION_FIELDS)
    write_csv(args.out / "metrics.csv", metrics, METRIC_FIELDS)
    write_csv(args.out / "common_sample_metrics.csv", common_metrics, METRIC_FIELDS)
    write_csv(args.out / "gate_inputs.csv", gate_rows, GATE_INPUT_FIELDS)
    overall_gate = summarize_overall_gate(gate_rows, protocol)
    output_files = [
        file_record(str(path.relative_to(args.out)), path)
        for path in sorted(args.out.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    manifest = {
        "schema": "aln_device_median_b05_evaluation_v1",
        "task_id": "B05_INTERFACE",
        "attempt_id": protocol["attempt_id"],
        "status": "COMPLETE",
        "scientific_status": "RETROSPECTIVE_ONLY",
        "trusted": False,
        "overall_gate": overall_gate,
        "source_hashes": source_hashes,
        "label_sources": {name: loaded[name]["source"] for name in POPULATIONS},
        "models_root": str(models_root),
        "model_bundle_hashes": bundle_hashes,
        "model_bundle_readback_count": readback_count,
        "model_bundles_unchanged_before_after": True,
        "training_performed": False,
        "outer_tuning_performed": False,
        "selection_inputs": "inner selections only; outer labels used for scoring only",
        "prediction_count": len(predictions),
        "metric_count": len(metrics),
        "common_sample_metric_count": len(common_metrics),
        "prediction_intervals": {"generated": False, "status": "not_evaluated"},
        "inputs": [
            file_record("protocol", protocol_path),
            file_record("splits", splits_path),
            file_record("model_manifest", model_manifest_path),
        ],
        "outputs": output_files,
        "source_files": [file_record("model_cli", Path(__file__).resolve())],
    }
    write_json(args.out / "manifest.json", manifest)


class ResearchScalarBundle:
    """Read-only research inference adapter with explicit branch and range rejection."""

    def __init__(
        self,
        models: dict[str, Any],
        training: dict[str, Any],
        target_units: dict[str, Any],
    ):
        self.models = models
        self.training = training
        self.target_units = target_units

    @staticmethod
    def _unsupported(structure_key: Any, target: str, reason: str) -> dict[str, Any]:
        return {
            "structure_key": structure_key,
            "target": target,
            "status": "unsupported",
            "prediction": None,
            "reason": reason,
        }

    def predict_records(self, target: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
        if target not in self.models:
            raise KeyError(f"Unknown research target: {target}")
        records = []
        for position, row in enumerate(frame.itertuples()):
            structure_key = getattr(row, "structure_key", None)
            branch = getattr(row, "p3_branch_id", None)
            branch_range = self.training[target]["branches"].get(branch)
            if branch_range is None:
                records.append(self._unsupported(structure_key, target, "unsupported_branch"))
                continue
            try:
                geometry = {
                    name: float(getattr(row, name)) for name in ("eg_um", "fl_um", "area_um2")
                }
                has_pf = int(row.has_pf)
            except (AttributeError, TypeError, ValueError):
                records.append(self._unsupported(structure_key, target, "invalid_geometry"))
                continue
            values = np.asarray(list(geometry.values()), dtype=float)
            if (
                not np.isfinite(values).all()
                or values[0] < 0
                or values[1] < 0
                or values[2] <= 0
                or has_pf not in (0, 1)
            ):
                records.append(self._unsupported(structure_key, target, "invalid_geometry"))
                continue
            expected_branch = (
                f"pf{has_pf}_eg{int(geometry['eg_um'] > 0)}_fl{int(geometry['fl_um'] > 0)}"
            )
            if branch != expected_branch:
                records.append(self._unsupported(structure_key, target, "branch_geometry_mismatch"))
                continue
            if any(
                geometry[name] < branch_range[name]["min"]
                or geometry[name] > branch_range[name]["max"]
                for name in geometry
            ):
                records.append(self._unsupported(structure_key, target, "outside_training_range"))
                continue
            prediction = float(self.models[target].predict(frame.iloc[[position]])[0])
            if not np.isfinite(prediction) or prediction <= 0:
                records.append(self._unsupported(structure_key, target, "invalid_model_prediction"))
                continue
            records.append(
                {
                    "structure_key": structure_key,
                    "target": target,
                    "status": "ok",
                    "prediction": prediction,
                    "unit": self.target_units.get(target),
                    "reason": None,
                }
            )
        return records


def _optional_csv_boolean(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    mapped = {"True": True, "False": False, "true": True, "false": False}.get(str(value))
    if mapped is None:
        raise ValueError(f"Invalid gate boolean: {value}")
    return mapped


def _read_gate_inputs(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for field in (
            "gate_applicable",
            "reporting_required",
            "reporting_complete",
            "cell_gate_pass",
        ):
            row[field] = _optional_csv_boolean(row.get(field))
        for field in ("selected_mae", "constant_mae", "mae_reduction_pct"):
            value = row.get(field)
            row[field] = float(value) if value not in (None, "") else None
        for field in ("eligible_n", "selected_n", "constant_n"):
            value = row.get(field)
            row[field] = int(value) if value not in (None, "") else 0
        row["failure_reason"] = row.get("failure_reason") or None
    return rows


def _research_training_record(frame: pd.DataFrame) -> dict[str, Any]:
    branches = {}
    for branch, group in frame.groupby("p3_branch_id", sort=True):
        branches[branch] = {
            name: {"min": float(group[name].min()), "max": float(group[name].max())}
            for name in ("eg_um", "fl_um", "area_um2")
        }
    return {
        "structure_count": len(frame),
        "structure_ids": sorted(frame["structure_key"].tolist()),
        "design_group_ids": sorted(frame["p3_design_group_id"].unique().tolist()),
        "branches": branches,
    }


def _finalize_rejection(
    out: Path,
    protocol: dict[str, Any],
    overall_gate: dict[str, Any],
    source_hashes: dict[str, str],
    reasons: list[str],
) -> None:
    write_json(
        out / "receipt.json",
        {
            "schema": "aln_device_median_b06_finalize_rejection_v1",
            "task_id": "B06_INTERFACE",
            "attempt_id": protocol["attempt_id"],
            "status": "REJECTED",
            "retrospective_status": "RETROSPECTIVE_ONLY",
            "trusted": False,
            "overall_gate": overall_gate,
            "rejection_reasons": reasons,
            "research_bundle_created": False,
            "fit_performed": False,
            "source_hashes": source_hashes,
        },
    )


def _recomputed_gate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for original in rows:
        row = dict(original)
        applicable = row.get("gate_applicable") is True
        selected_mae = row.get("selected_mae")
        constant_mae = row.get("constant_mae")
        complete = bool(
            row.get("reporting_complete") is True
            and row["eligible_n"] > 0
            and row["selected_n"] == row["eligible_n"]
            and row["constant_n"] == row["eligible_n"]
            and selected_mae is not None
            and constant_mae is not None
            and np.isfinite(selected_mae)
            and np.isfinite(constant_mae)
            and min(selected_mae, constant_mae) >= 0
        )
        row["reporting_complete"] = complete
        if complete:
            comparison = gate_mae_comparison(
                selected_mae,
                constant_mae,
                normal_holdout=applicable and row["scope"] == "normal_holdout",
            )
            row["mae_reduction_pct"] = comparison["mae_reduction_pct"]
            row["mae_reduction_status"] = comparison["mae_reduction_status"]
            row["cell_gate_pass"] = comparison["cell_gate_pass"] if applicable else None
            row["failure_reason"] = comparison["failure_reason"] if applicable else None
        else:
            row["mae_reduction_pct"] = None
            row["mae_reduction_status"] = "not_computed_missing_predictions"
            row["cell_gate_pass"] = False if applicable else None
            if applicable:
                row["failure_reason"] = "missing or invalid main outer-test predictions"
        result.append(row)
    return result


def run_finalize(args: argparse.Namespace) -> None:
    protocol_path = args.protocol.resolve()
    evaluation_root = args.evaluation.resolve()
    args.out = args.out.resolve()
    prepare_output_directory(args.out)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    evaluation_manifest_path = evaluation_root / "manifest.json"
    evaluation = json.loads(evaluation_manifest_path.read_text(encoding="utf-8"))
    if evaluation.get("schema") != "aln_device_median_b05_evaluation_v1":
        raise RuntimeError("Unexpected evaluation schema")
    if evaluation.get("attempt_id") != protocol["attempt_id"]:
        raise RuntimeError("Protocol/evaluation attempt mismatch")
    gate_path = evaluation_root / "gate_inputs.csv"
    source_hashes = {
        "protocol": file_sha256(protocol_path),
        "evaluation_manifest": file_sha256(evaluation_manifest_path),
        "gate_inputs": file_sha256(gate_path),
    }
    if evaluation["source_hashes"].get("protocol") != source_hashes["protocol"]:
        raise RuntimeError("Evaluation protocol hash mismatch")
    for record in evaluation.get("outputs", []):
        output_path = evaluation_root / record["id"]
        if not output_path.is_file() or file_sha256(output_path) != record["sha256"]:
            raise RuntimeError(f"Evaluation output hash mismatch: {record['id']}")
    gate_rows = _recomputed_gate_rows(_read_gate_inputs(gate_path))
    overall_gate = summarize_overall_gate(gate_rows, protocol)
    if evaluation.get("overall_gate", {}).get("status") != overall_gate["status"]:
        raise RuntimeError("Evaluation overall-gate summary does not match gate inputs")
    if overall_gate["status"] != "PASS":
        _finalize_rejection(
            args.out,
            protocol,
            overall_gate,
            source_hashes,
            overall_gate["failures"] or ["frozen overall gate did not pass"],
        )
        return
    if evaluation.get("trusted") is not False:
        _finalize_rejection(
            args.out, protocol, overall_gate, source_hashes, ["evaluation trust marker is invalid"]
        )
        return

    selected_by_target = {}
    selection_failures = []
    for target in protocol["core_targets"]:
        rows = [
            row
            for row in gate_rows
            if row["population"] == "primary" and row["target"] == target
        ]
        selected = {row["selected_model"] for row in rows}
        if not rows or None in selected or len(selected) != 1:
            selection_failures.append(f"no unanimous complete inner selection for {target}")
        elif next(iter(selected)) not in CANDIDATE_ORDER:
            selection_failures.append(f"unknown selected model for {target}")
        else:
            selected_by_target[target] = next(iter(selected))
    if selection_failures:
        _finalize_rejection(args.out, protocol, overall_gate, source_hashes, selection_failures)
        return

    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        if os.environ.get(name) != "1":
            raise RuntimeError(f"B06 requires {name}=1")
    loaded = _load_evaluation_populations(protocol_path, protocol)
    primary = loaded["primary"]["frame"]
    for population in POPULATIONS:
        source = loaded[population]["source"]
        source_hashes[f"structure_targets_{population}"] = source["sha256"]
        registered = evaluation.get("label_sources", {}).get(population)
        if registered is not None and registered["sha256"] != source["sha256"]:
            raise RuntimeError(f"Evaluation/finalize label hash mismatch: {population}")
    threshold = int(protocol["validity_and_support"]["n_valid_threshold"])
    models = {}
    training = {}
    transforms = {}
    readback_inputs = {}
    for target in protocol["core_targets"]:
        eligible = eligible_target_frame(
            primary, target, set(primary["structure_key"]), threshold
        )
        if eligible.empty:
            _finalize_rejection(
                args.out,
                protocol,
                overall_gate,
                source_hashes,
                [f"no eligible full-new-label structures for {target}"],
            )
            return
        models[target] = fit_model(selected_by_target[target], eligible, target, protocol)
        training[target] = _research_training_record(eligible)
        transforms[target] = models[target].transform_records()
        readback_inputs[target] = eligible.iloc[[0]].copy()

    metadata = {
        "schema": "aln_device_median_b06_research_bundle_v1",
        "task_id": "B06_INTERFACE",
        "attempt_id": protocol["attempt_id"],
        "status": "RETROSPECTIVE_ONLY",
        "trusted": False,
        "overall_gate": overall_gate,
        "population": "primary",
        "selected_models": selected_by_target,
        "selection_basis": "unanimous per-target inner selections across frozen outer scopes",
        "target_units": protocol.get("units", {}),
        "training": training,
        "label_version": protocol["data_version"].get("version"),
        "label_source": loaded["primary"]["source"],
        "missing_policy": protocol["validity_and_support"],
        "branch_rejection": {
            "rule": "unsupported or geometry-inconsistent p3_branch_id returns unsupported",
            "silent_branch_merge": False,
        },
        "range_rejection": {
            "rule": "geometry outside the target/branch training min-max returns unsupported",
            "silent_clipping": False,
            "extrapolation_claimed": False,
        },
        "feature_target_transforms": transforms,
        "configurations": protocol["models"],
        "source_hashes": source_hashes,
        "evaluation_source_hashes": evaluation["source_hashes"],
        "full_new_label_fit": True,
        "outer_test_used_for_selection": False,
        "new_blind_test": False,
        "prediction_intervals": {"generated": False, "status": "not_evaluated"},
    }
    research_model = ResearchScalarBundle(models, training, protocol.get("units", {}))
    before_readback = {
        target: research_model.predict_records(target, readback_inputs[target])[0]
        for target in models
    }
    bundle_path = args.out / "research_bundle.joblib"
    joblib.dump({"metadata": metadata, "research_model": research_model}, bundle_path, compress=3)
    bundle_hash = file_sha256(bundle_path)
    source_hashes["research_bundle"] = bundle_hash
    loaded_bundle = joblib.load(bundle_path)
    readback_records = []
    readback_passed = True
    unsupported_range_passed = True
    unsupported_branch_passed = True
    for target, fixed in readback_inputs.items():
        after = loaded_bundle["research_model"].predict_records(target, fixed)[0]
        before = before_readback[target]
        delta = (
            abs(after["prediction"] - before["prediction"])
            if after["prediction"] is not None and before["prediction"] is not None
            else None
        )
        matched = (
            after["status"] == before["status"] == "ok"
            and delta is not None
            and bool(np.isclose(after["prediction"], before["prediction"], rtol=1e-12, atol=1e-12))
        )
        readback_passed = readback_passed and matched
        row = fixed.iloc[0]
        readback_records.append(
            {
                "target": target,
                "structure_key": row["structure_key"],
                "fixed_input": {
                    name: float(row[name])
                    for name in ("eg_um", "fl_um", "area_um2", "has_pf")
                },
                "p3_branch_id": row["p3_branch_id"],
                "before": before,
                "after": after,
                "absolute_delta": delta,
                "passed": matched,
            }
        )
        outside = fixed.copy()
        maximum = training[target]["branches"][row["p3_branch_id"]]["area_um2"]["max"]
        outside["area_um2"] = maximum + 1.0
        unsupported_range_passed = (
            loaded_bundle["research_model"].predict_records(target, outside)[0]["status"]
            == "unsupported"
        ) and unsupported_range_passed
        unknown_branch = fixed.copy()
        unknown_branch["p3_branch_id"] = "unsupported_branch"
        unsupported_branch_passed = (
            loaded_bundle["research_model"].predict_records(target, unknown_branch)[0]["status"]
            == "unsupported"
        ) and unsupported_branch_passed
    for name, path in (
        ("protocol", protocol_path),
        ("evaluation_manifest", evaluation_manifest_path),
        ("gate_inputs", gate_path),
    ):
        if file_sha256(path) != source_hashes[name]:
            raise RuntimeError(f"Finalize input changed during fitting: {name}")
    for population in POPULATIONS:
        source = loaded[population]["source"]
        if file_sha256(Path(source["path"])) != source["sha256"]:
            raise RuntimeError(f"Finalize label changed during fitting: {population}")
    readback_status = (
        "PASS"
        if readback_passed and unsupported_range_passed and unsupported_branch_passed
        else "FAILED"
    )
    readback_document = {
        "schema": "aln_device_median_b06_readback_v1",
        "status": readback_status,
        "rtol": 1e-12,
        "atol": 1e-12,
        "records": readback_records,
        "unsupported_range_rejection_passed": unsupported_range_passed,
        "unsupported_branch_rejection_passed": unsupported_branch_passed,
        "research_bundle_sha256": bundle_hash,
        "source_hashes": source_hashes,
    }
    write_json(args.out / "readback.json", readback_document)
    if readback_status != "PASS":
        raise RuntimeError("Research bundle readback failed")
    write_json(
        args.out / "receipt.json",
        {
            "schema": "aln_device_median_b06_research_receipt_v1",
            "task_id": "B06_INTERFACE",
            "attempt_id": protocol["attempt_id"],
            "status": "RETROSPECTIVE_ONLY",
            "trusted": False,
            "overall_gate": overall_gate,
            "research_bundle_created": True,
            "fit_performed": True,
            "research_bundle_sha256": bundle_hash,
            "readback_status": readback_status,
            "label_version": metadata["label_version"],
            "target_units": metadata["target_units"],
            "training": training,
            "missing_policy": metadata["missing_policy"],
            "branch_rejection": metadata["branch_rejection"],
            "range_rejection": metadata["range_rejection"],
            "source_hashes": source_hashes,
            "outputs": [
                file_record("research_bundle", bundle_path),
                file_record("readback", args.out / "readback.json"),
            ],
        },
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    fit_parser = subparsers.add_parser("fit")
    fit_parser.add_argument("--protocol", type=Path, required=True)
    fit_parser.add_argument("--data", type=Path, required=True)
    fit_parser.add_argument("--splits", type=Path, required=True)
    fit_parser.add_argument("--out", type=Path, required=True)
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--protocol", type=Path, required=True)
    evaluate_parser.add_argument("--models", type=Path, required=True)
    evaluate_parser.add_argument("--out", type=Path, required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--protocol", type=Path, required=True)
    finalize_parser.add_argument("--evaluation", type=Path, required=True)
    finalize_parser.add_argument("--out", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "fit":
        run_fit(args)
        return 0
    if args.command == "evaluate":
        run_evaluate(args)
        return 0
    if args.command == "finalize":
        run_finalize(args)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(importlib.import_module(CANONICAL_MODULE).main())
