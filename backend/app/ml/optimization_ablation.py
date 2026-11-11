"""Frozen measured-only P6C ablations; selection accepts training data only."""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from app.ml.optimization_boundary_pilot import BoundaryModel
from app.ml.optimization_data import FEATURES, TARGETS

RECIPES = ("gp_raw", "gp_log_area", "trend_gp_raw", "trend_gp_log_area")


class AblationModel:
    def __init__(self, recipe, target):
        if recipe not in RECIPES:
            raise ValueError("Unknown frozen recipe")
        self.recipe, self.target = recipe, target
        self.trends = {}

    def transform(self, frame):
        frame = frame.copy()
        if not np.isfinite(frame.area_um2).all() or (frame.area_um2 <= 0).any():
            raise ValueError("Finite positive area required")
        if "log_area" in self.recipe:
            frame["area_um2"] = np.log(frame.area_um2)
        return frame

    def fit(self, frame):
        transformed = self.transform(frame)
        self.trends = {}
        if self.recipe.startswith("trend_"):
            for branch, group in transformed.groupby("branch_id"):
                x = group[list(FEATURES)].to_numpy(float)
                low, high = x.min(axis=0), x.max(axis=0)
                scale = np.where(high > low, high - low, 1)
                trend = Ridge(alpha=1).fit((x - low) / scale, np.log(group[self.target]))
                self.trends[branch] = (low, scale, trend)
                transformed.loc[group.index, self.target] = np.exp(
                    np.log(group[self.target]) - trend.predict((x - low) / scale)
                )
        self.gp = BoundaryModel("gp", self.target).fit(transformed)
        return self

    def predict_log(self, frame):
        transformed = self.transform(frame)
        mean, std = self.gp.predict_log(transformed)
        for branch, (low, scale, trend) in self.trends.items():
            where = np.flatnonzero(transformed.branch_id.eq(branch).to_numpy())
            if len(where):
                mean[where] += trend.predict(
                    (transformed.iloc[where][list(FEATURES)].to_numpy() - low) / scale
                )
        return mean, std


def inner_folds(fit):
    if fit.groupby("design_group_id").branch_id.nunique().max() != 1:
        raise ValueError("Design group crosses branches")
    assignment = {}
    for _, branch in fit.groupby("branch_id"):
        groups = sorted(
            branch.design_group_id.unique(),
            key=lambda x: hashlib.sha256(f"P6C-20261022:{x}".encode()).hexdigest(),
        )
        if len(groups) < 4:
            raise ValueError("Four groups per branch required")
        assignment.update({g: i % 4 for i, g in enumerate(groups)})
    result = fit[["structure_id", "design_group_id", "branch_id"]].copy()
    result["fold"] = result.design_group_id.map(assignment)
    return result


def select_recipe(fit):
    folds = inner_folds(fit)
    rows = []
    for fold in range(4):
        held = set(folds.loc[folds.fold.eq(fold), "structure_id"])
        train = fit[~fit.structure_id.isin(held)]
        valid = fit[fit.structure_id.isin(held)]
        for recipe in RECIPES:
            for target in TARGETS:
                model = AblationModel(recipe, target).fit(train)
                mean, _ = model.predict_log(valid)
                if not np.isfinite(mean).all():
                    raise ValueError("Invalid inner prediction")
                frame = valid[["structure_id", "design_group_id"]].copy()
                frame["fold"], frame["recipe"], frame["target"] = fold, recipe, target
                frame["actual"], frame["prediction"] = valid[target], np.exp(mean)
                frame["abs_log_error"] = np.abs(np.log(valid[target]) - mean)
                rows.append(frame)
    predictions = pd.concat(rows, ignore_index=True)
    scores = (
        predictions.groupby(["recipe", "design_group_id", "structure_id"])
        .abs_log_error.mean()
        .groupby(["recipe", "design_group_id"])
        .mean()
        .groupby("recipe")
        .mean()
    )
    selected = min(RECIPES, key=lambda recipe: scores[recipe])
    return selected, scores.to_dict(), folds, predictions
