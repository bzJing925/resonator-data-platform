"""P8 frequency-contract arm: frozen trend+GP recipe with measured fs/fp as extra features."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern, WhiteKernel
from sklearn.linear_model import Ridge

from app.ml.optimization_data import TARGETS

ARMS = ("B0_control", "B1_freq_trend_gp")
FEATURES_FREQ = ("eg_um", "fl_um", "area_um2", "fs_ghz", "fp_ghz")
CONTRACT = {"B0_control": "geometry_only", "B1_freq_trend_gp": "measured_frequency"}
PRIMARY_MIN_IMPROVEMENT = 0.20
GUARD_MAX_DEGRADATION = 0.05


class FreqTrendGPModel:
    """B1: P6C trend_gp_log_area recipe with measured fs/fp appended; no other change."""

    def __init__(self, target):
        if target not in TARGETS:
            raise ValueError("Unknown target")
        self.target = target

    def transform(self, frame):
        frame = frame.copy()
        cols = list(FEATURES_FREQ) + [self.target]
        if not np.isfinite(frame[cols]).all().all() or (frame[self.target] <= 0).any():
            raise ValueError("Finite positive targets and finite features required")
        if (frame.area_um2 <= 0).any():
            raise ValueError("Finite positive area required")
        frame["area_um2"] = np.log(frame.area_um2)
        return frame

    def fit(self, frame):
        transformed = self.transform(frame)
        self.trends, self.branches = {}, {}
        for branch, group in transformed.groupby("branch_id"):
            x = group[list(FEATURES_FREQ)].to_numpy(float)
            low, high = x.min(axis=0), x.max(axis=0)
            scale = np.where(high > low, high - low, 1.0)
            trend = Ridge(alpha=1.0).fit((x - low) / scale, np.log(group[self.target]))
            kernel = Matern(length_scale=0.5, nu=2.5, length_scale_bounds="fixed") + WhiteKernel(
                noise_level=0.05, noise_level_bounds="fixed"
            )
            gp = GaussianProcessRegressor(
                kernel=kernel, optimizer=None, alpha=1e-10, normalize_y=True
            )
            resid_log = np.log(group[self.target]) - trend.predict((x - low) / scale)
            gp.fit((x - low) / scale, resid_log)
            self.branches[branch] = (low, scale, trend, gp)
        return self

    def predict_log(self, frame):
        transformed = self.transform(frame)
        mean = np.full(len(frame), np.nan)
        std = np.full(len(frame), np.nan)
        for branch, (low, scale, trend, gp) in self.branches.items():
            where = np.flatnonzero(transformed.branch_id.eq(branch).to_numpy())
            if not len(where):
                continue
            x = (transformed.iloc[where][list(FEATURES_FREQ)].to_numpy(float) - low) / scale
            m, s = gp.predict(x, return_std=True)
            mean[where] = m + trend.predict(x)
            std[where] = np.maximum(s, 0.001)
        return mean, std


def select_arm_p8(primary, guard):
    """Frozen P8 rule: identical thresholds to P7, B-arm names."""
    base_p, base_g = primary["B0_control"], guard["B0_control"]
    eligible = [
        arm
        for arm in ARMS
        if primary[arm] <= base_p * (1 - PRIMARY_MIN_IMPROVEMENT)
        and guard[arm] <= base_g * (1 + GUARD_MAX_DEGRADATION)
    ]
    if eligible:
        selected = min(eligible, key=lambda a: (primary[a], ARMS.index(a)))
    else:
        selected = "B0_control"
    return {
        "selected": selected,
        "primary_scores": {k: float(v) for k, v in primary.items()},
        "guard_scores": {k: float(v) for k, v in guard.items()},
        "eligible": eligible,
        "primary_min_improvement": PRIMARY_MIN_IMPROVEMENT,
        "guard_max_degradation": GUARD_MAX_DEGRADATION,
        "selection_uses_outer_test": False,
    }


def inner_cv_predictions_p8(fit, folds):
    """Four-fold grouped inner predictions for B1 on the five-feature contract."""
    if folds.groupby("design_group_id").fold.nunique().max() != 1:
        raise ValueError("Fold crosses design groups")
    if not set(folds.structure_id) == set(fit.structure_id):
        raise ValueError("Folds must cover the exact fit set")
    rows = []
    for fold in range(4):
        held = set(folds.loc[folds.fold.eq(fold), "structure_id"])
        train, valid = fit[~fit.structure_id.isin(held)], fit[fit.structure_id.isin(held)]
        for target in TARGETS:
            mean, _ = FreqTrendGPModel(target).fit(train).predict_log(valid)
            if not np.isfinite(mean).all():
                raise ValueError("Nonfinite inner prediction")
            frame = valid[["structure_id", "design_group_id", "branch_id"]].copy()
            frame["fold"], frame["arm"], frame["target"] = fold, "B1_freq_trend_gp", target
            frame["actual"], frame["prediction"] = valid[target], np.exp(mean)
            frame["abs_log_error"] = np.abs(np.log(valid[target]) - mean)
            rows.append(frame)
    return pd.concat(rows, ignore_index=True)


def area_stress_predictions_p8(fit, pseudo_fn):
    """Area-axis pseudo-boundary predictions for B1; P6D capacity rules reused via pseudo_fn."""
    manifests, _ = pseudo_fn(fit)
    manifests = manifests[manifests.scenario.isin(["area_um2_low", "area_um2_high"])]
    rows = []
    for scenario, m in manifests.groupby("scenario", sort=True):
        train = fit[fit.structure_id.isin(m.loc[m.role.eq("train"), "structure_id"])]
        evaluation = fit[fit.structure_id.isin(m.loc[m.role.eq("evaluation"), "structure_id"])]
        if evaluation.empty:
            continue
        if set(train.design_group_id) & set(evaluation.design_group_id):
            raise ValueError("Pseudo-boundary group leakage")
        for target in TARGETS:
            mean, _ = FreqTrendGPModel(target).fit(train).predict_log(evaluation)
            if not np.isfinite(mean).all():
                raise ValueError("Nonfinite stress prediction")
            frame = evaluation[["structure_id", "design_group_id", "branch_id"]].copy()
            frame["scenario"], frame["arm"], frame["target"] = scenario, "B1_freq_trend_gp", target
            frame["actual"], frame["prediction"] = evaluation[target], np.exp(mean)
            frame["abs_log_error"] = np.abs(np.log(evaluation[target]) - mean)
            rows.append(frame)
    if not rows:
        raise ValueError("No eligible area pseudo-boundary cells")
    return pd.concat(rows, ignore_index=True)
