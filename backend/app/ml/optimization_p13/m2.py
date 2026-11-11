from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from app.ml.optimization_p13.contracts import (
    GEOMETRY_FIELDS,
    INTERFACE_VERSION,
    ContractError,
    m2_inverse_prediction,
    validate_inference_input,
)

M2_FIT_COLUMNS = (
    "structure_id",
    "design_group_id",
    "branch_id",
    *GEOMETRY_FIELDS,
    "fs_ghz",
    "fp_ghz",
)


@dataclass(frozen=True)
class M2Config:
    grid_index: int
    config_id: str
    feature_recipe: str
    alpha: float

    @classmethod
    def from_mapping(cls, value: Mapping) -> M2Config:
        return cls(
            grid_index=int(value["grid_index"]),
            config_id=str(value["config_id"]),
            feature_recipe=str(value["feature_recipe"]),
            alpha=float(value["alpha"]),
        )


def configurations_from_protocol(protocol: Mapping) -> list[M2Config]:
    configurations = [
        M2Config.from_mapping(value) for value in protocol["models"]["M2"]["configurations"]
    ]
    expected = protocol["models"]["M2"]["model_configuration"]["estimator"]
    if len(configurations) != 10 or expected["configuration_count"] != 10:
        raise ValueError("M2 requires exactly ten frozen configurations")
    if [value.config_id for value in configurations] != expected[
        "configuration_order_for_exact_ties"
    ]:
        raise ValueError("M2 configuration order changed")
    if sorted({value.alpha for value in configurations}) != sorted(expected["alpha_grid"]):
        raise ValueError("M2 alpha grid changed")
    if {value.feature_recipe for value in configurations} != {
        "linear",
        "additive_quadratic",
    }:
        raise ValueError("M2 feature recipes changed")
    return configurations


def null_m2_prediction(reason: str) -> dict:
    return {
        "pred_a": None,
        "pred_z": None,
        "pred_fs_ghz": None,
        "pred_fp_ghz": None,
        "pred_frequency_ratio": None,
        "pred_k2eff_pct_native": None,
        "prediction": None,
        "valid_prediction": False,
        "invalid_reason": reason,
        "frequency_valid": False,
        "native_k2_valid": False,
        "aggregation_estimand_match": False,
        "qualified": False,
        "p3_comparable_prediction": None,
        "p3_comparable_null_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
    }


class M2FrequencyModel:
    interface_version = INTERFACE_VERSION
    model_family = "M2"

    def __init__(self, config: M2Config, branch_id: str, model_id: str = "M2_O05_INNER"):
        self.config = config
        self.config_id = config.config_id
        self.branch_id = branch_id
        self.model_id = model_id
        self.is_fitted = False
        self.single_target_fit_calls = 0

    def _raw_features(self, frame: pd.DataFrame) -> np.ndarray:
        eg = frame["eg_um"].to_numpy(dtype=float)
        fl = frame["fl_um"].to_numpy(dtype=float)
        log_area = np.log(frame["area_um2"].to_numpy(dtype=float))
        base = np.column_stack((eg, fl, log_area))
        if self.config.feature_recipe == "linear":
            return base
        if self.config.feature_recipe == "additive_quadratic":
            return np.column_stack((base, base**2))
        raise ValueError("Unknown M2 feature recipe")

    @staticmethod
    def _weighted_moments(values: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mean = np.sum(values * weights[:, None], axis=0)
        variance = np.sum((values - mean) ** 2 * weights[:, None], axis=0)
        return mean, variance

    def fit(self, frame: pd.DataFrame) -> M2FrequencyModel:
        if set(frame.columns) != set(M2_FIT_COLUMNS):
            raise ValueError("M2 fit requires only identity, geometry, and frequency supervision")
        if frame.empty or not frame.branch_id.eq(self.branch_id).all():
            raise ValueError("M2 fit branch mismatch")
        if len(frame) < 4 or frame.design_group_id.nunique() < 2:
            raise ValueError("FIT_UNAVAILABLE")
        if frame.structure_id.duplicated().any():
            raise ValueError("M2 fit structure IDs must be unique within a branch")
        numeric = frame[[*GEOMETRY_FIELDS, "fs_ghz", "fp_ghz"]].to_numpy(dtype=float)
        if not np.isfinite(numeric).all():
            raise ValueError("M2 fit requires finite geometry and frequencies")
        if (frame.eg_um < 0).any() or (frame.fl_um < 0).any() or (frame.area_um2 <= 0).any():
            raise ValueError("Invalid M2 fit geometry")
        if not frame.has_pf.isin([0, 1]).all():
            raise ValueError("Invalid M2 fit PF flag")
        expected_branch = (
            "pf"
            + frame.has_pf.astype(int).astype(str)
            + "_eg"
            + frame.eg_um.gt(0).astype(int).astype(str)
            + "_fl"
            + frame.fl_um.gt(0).astype(int).astype(str)
        )
        if not expected_branch.eq(self.branch_id).all():
            raise ValueError("M2 fit geometry and branch differ")
        fs = frame.fs_ghz.to_numpy(dtype=float)
        fp = frame.fp_ghz.to_numpy(dtype=float)
        if (fs <= 0).any() or (fp <= fs).any():
            raise ValueError("M2 fit requires 0 < fs < fp")

        group_sizes = frame.groupby("design_group_id").structure_id.transform("size")
        group_count = frame.design_group_id.nunique()
        weights = 1.0 / (group_count * group_sizes.to_numpy(dtype=float))
        if not np.isclose(weights.sum(), 1.0, atol=1e-12, rtol=0):
            raise ValueError("M2 design-group weights do not sum to one")

        raw_x = self._raw_features(frame)
        x_mean, x_variance = self._weighted_moments(raw_x, weights)
        keep = x_variance > 0
        x_scale = np.sqrt(x_variance[keep])
        transformed = np.column_stack((np.log(fs), np.log(fp / fs - 1)))
        y_mean, y_variance = self._weighted_moments(transformed, weights)
        y_scale = np.sqrt(y_variance)
        y_scale[y_scale == 0] = 1.0
        x = (raw_x[:, keep] - x_mean[keep]) / x_scale
        y = (transformed - y_mean) / y_scale

        self.estimators_ = []
        for output_index in range(2):
            estimator = Ridge(
                alpha=self.config.alpha,
                fit_intercept=False,
                solver="cholesky",
            )
            estimator.fit(x, y[:, output_index], sample_weight=weights)
            self.estimators_.append(estimator)
            self.single_target_fit_calls += 1
        self.keep_ = keep
        self.x_mean_ = x_mean
        self.x_scale_ = x_scale
        self.y_mean_ = y_mean
        self.y_scale_ = y_scale
        self.fit_statistics_ = {
            "structure_count": len(frame),
            "design_group_count": group_count,
            "weights": weights.copy(),
            "group_weight_sums": pd.Series(weights)
            .groupby(frame.design_group_id.to_numpy())
            .sum()
            .to_dict(),
            "raw_x_mean": x_mean.copy(),
            "raw_x_variance": x_variance.copy(),
            "kept_feature_indices": np.flatnonzero(keep).tolist(),
            "output_mean": y_mean.copy(),
            "output_scale": y_scale.copy(),
        }
        self.is_fitted = True
        return self

    def predict(self, record: Mapping) -> dict:
        if not self.is_fitted:
            raise RuntimeError("M2 model is not fitted")
        geometry = validate_inference_input(record, "M2")
        frame = pd.DataFrame([geometry])
        branch = (
            f"pf{geometry['has_pf']}_eg{int(geometry['eg_um'] > 0)}"
            f"_fl{int(geometry['fl_um'] > 0)}"
        )
        if branch != self.branch_id:
            raise ContractError("UNSUPPORTED_BRANCH", f"Expected {self.branch_id}, got {branch}")
        raw_x = self._raw_features(frame)
        x = (raw_x[:, self.keep_] - self.x_mean_[self.keep_]) / self.x_scale_
        transformed = np.array(
            [estimator.predict(x)[0] for estimator in self.estimators_], dtype=float
        )
        latent = self.y_mean_ + self.y_scale_ * transformed
        return m2_inverse_prediction(float(latent[0]), float(latent[1]))

    def predict_frame(self, frame: pd.DataFrame) -> list[dict]:
        if set(frame.columns) != set(GEOMETRY_FIELDS):
            raise ValueError("M2 inference accepts exactly four geometry fields")
        return [self.predict(record) for record in frame.to_dict("records")]


class UnavailableM2FrequencyModel(M2FrequencyModel):
    def __init__(self, config: M2Config, branch_id: str, reason: str = "FIT_UNAVAILABLE"):
        super().__init__(config, branch_id)
        self.reason = reason
        self.is_fitted = True

    def predict(self, record: Mapping) -> dict:
        geometry = validate_inference_input(record, "M2")
        branch = (
            f"pf{geometry['has_pf']}_eg{int(geometry['eg_um'] > 0)}"
            f"_fl{int(geometry['fl_um'] > 0)}"
        )
        if branch != self.branch_id:
            raise ContractError("UNSUPPORTED_BRANCH", f"Expected {self.branch_id}, got {branch}")
        return null_m2_prediction(self.reason)
