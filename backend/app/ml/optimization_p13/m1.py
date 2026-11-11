from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import SplineTransformer

from app.ml.optimization_p13.contracts import (
    INTERFACE_VERSION,
    validate_geometry,
    validate_inference_input,
)

MODEL_FEATURES = ("eg_um", "fl_um", "log_area_um2")
RAW_FEATURES = ("eg_um", "fl_um", "area_um2")


def _validate_frame(frame: pd.DataFrame, require_target: bool) -> None:
    required = {
        "structure_id",
        "design_group_id",
        "branch_id",
        "eg_um",
        "fl_um",
        "area_um2",
        "has_pf",
    }
    if require_target:
        required.add("k2eff_pct")
    missing = sorted(required - set(frame.columns)
    )
    if missing:
        raise ValueError(f"Missing M1 fit/prediction fields: {missing}")
    if frame.empty:
        raise ValueError("Empty M1 frame")
    numeric = frame[["eg_um", "fl_um", "area_um2", "has_pf"]].apply(
        pd.to_numeric, errors="coerce"
    )
    values = numeric.to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("invalid_training_geometry")
    if (numeric.eg_um < 0).any() or (numeric.fl_um < 0).any():
        raise ValueError("negative_eg_or_fl")
    if (numeric.area_um2 <= 0).any():
        raise ValueError("nonpositive_area")
    if not numeric.has_pf.isin([0, 1]).all():
        raise ValueError("invalid_has_pf")
    expected = (
        "pf"
        + numeric.has_pf.astype(int).astype(str)
        + "_eg"
        + (numeric.eg_um > 0).astype(int).astype(str)
        + "_fl"
        + (numeric.fl_um > 0).astype(int).astype(str)
    )
    if not expected.equals(frame.branch_id):
        raise ValueError("branch_mismatch")
    if require_target:
        target = pd.to_numeric(frame.k2eff_pct, errors="coerce").to_numpy(float)
        if not np.isfinite(target).all() or (target <= 0).any() or (target >= 100).any():
            raise ValueError("invalid_training_target")


class M1SplineRidgeModel:
    interface_version = INTERFACE_VERSION
    model_family = "M1"

    def __init__(self, config: Mapping):
        self.config_id = str(config["config_id"])
        self.model_id = f"m1_spline_ridge__{self.config_id}"
        self.degree = int(config["degree"])
        self.n_knots = int(config["n_knots"])
        self.alpha = float(config["alpha"])
        self.grid_index = int(config["grid_index"])
        self.branches = {}

    def fit(self, frame: pd.DataFrame) -> M1SplineRidgeModel:
        _validate_frame(frame, require_target=True)
        self.branches = {}
        ordered = frame.sort_values(
            ["branch_id", "design_group_id", "structure_id"], kind="stable"
        )
        for branch, group in ordered.groupby("branch_id", sort=True):
            group = group.copy()
            group["log_area_um2"] = np.log(group.area_um2.to_numpy(float))
            x = group[list(MODEL_FEATURES)].to_numpy(float)
            features = [
                index for index, name in enumerate(MODEL_FEATURES) if np.ptp(x[:, index]) > 0
            ]
            dropped = [name for index, name in enumerate(MODEL_FEATURES) if index not in features]
            state = {
                "dropped_features": dropped,
                "fit_structure_count": len(group),
                "fit_design_group_count": group.design_group_id.nunique(),
            }
            if not features:
                state.update(
                    {
                        "intercept_only": True,
                        "log_mean": float(np.log(group.k2eff_pct.to_numpy(float)).mean()),
                        "features": [],
                    }
                )
            else:
                names = [MODEL_FEATURES[index] for index in features]
                spline = SplineTransformer(
                    n_knots=self.n_knots,
                    degree=self.degree,
                    knots="uniform",
                    extrapolation="linear",
                    include_bias=False,
                    order="C",
                    handle_missing="error",
                    sparse_output=False,
                )
                transformed = spline.fit_transform(x[:, features])
                ridge = Ridge(
                    alpha=self.alpha,
                    fit_intercept=True,
                    copy_X=True,
                    max_iter=None,
                    solver="cholesky",
                    positive=False,
                    random_state=None,
                )
                ridge.fit(transformed, np.log(group.k2eff_pct.to_numpy(float)))
                state.update(
                    {
                        "intercept_only": False,
                        "features": names,
                        "spline": spline,
                        "ridge": ridge,
                    }
                )
            self.branches[branch] = state
        return self

    def predict_log(self, frame: pd.DataFrame) -> np.ndarray:
        _validate_frame(frame, require_target=False)
        mean = np.full(len(frame), np.nan, dtype=float)
        for branch, state in self.branches.items():
            where = np.flatnonzero(frame.branch_id.eq(branch).to_numpy())
            if not len(where):
                continue
            if state["intercept_only"]:
                mean[where] = state["log_mean"]
                continue
            group = frame.iloc[where].copy()
            group["log_area_um2"] = np.log(group.area_um2.to_numpy(float))
            names = state["features"]
            x = group[names].to_numpy(float)
            mean[where] = state["ridge"].predict(state["spline"].transform(x))
        return mean

    def predict(self, record: Mapping) -> dict:
        geometry = validate_inference_input(record)
        branch = validate_geometry(record)["branch_id"]
        frame = pd.DataFrame(
            [
                {
                    **geometry,
                    "branch_id": branch,
                    "structure_id": "inference",
                    "design_group_id": "inference",
                }
            ]
        )
        if branch not in self.branches:
            return self._invalid("unknown_or_empty_branch", branch)
        mean = self.predict_log(frame)[0]
        if not math.isfinite(mean):
            return self._invalid("nonfinite_model_output", branch)
        with np.errstate(over="ignore", invalid="ignore"):
            prediction = float(np.exp(mean))
        if not math.isfinite(prediction):
            return self._invalid("nonfinite_model_output", branch)
        if not 0 < prediction < 100:
            return self._invalid("non_physical_k2_prediction", branch)
        return {
            "prediction": prediction,
            "prediction_log": float(mean),
            "valid_prediction": True,
            "invalid_reason": None,
            "branch_id": branch,
            "model_id": self.model_id,
            "config_id": self.config_id,
            "target": "k2eff_pct",
            "unit": "percent_number",
        }

    def _invalid(self, reason: str, branch: str) -> dict:
        return {
            "prediction": None,
            "prediction_log": None,
            "valid_prediction": False,
            "invalid_reason": reason,
            "branch_id": branch,
            "model_id": self.model_id,
            "config_id": self.config_id,
            "target": "k2eff_pct",
            "unit": "percent_number",
        }

    def fit_receipt(self) -> dict:
        return {
            "config_id": self.config_id,
            "model_id": self.model_id,
            "grid_index": self.grid_index,
            "branch_states": {
                branch: {
                    "fit_structure_count": state["fit_structure_count"],
                    "fit_design_group_count": state["fit_design_group_count"],
                    "intercept_only": state["intercept_only"],
                    "dropped_features": state["dropped_features"],
                    "features": state["features"],
                }
                for branch, state in sorted(self.branches.items())
            },
        }
