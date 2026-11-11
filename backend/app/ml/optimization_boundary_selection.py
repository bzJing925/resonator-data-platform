"""Training-only branch-balanced and pseudo-boundary selection; no new model recipes."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.ml.optimization_ablation import RECIPES, AblationModel
from app.ml.optimization_data import FEATURES, TARGETS


def pseudo_boundaries(fit):
    if (
        fit.structure_id.duplicated().any()
        or fit.groupby("design_group_id").branch_id.nunique().max() != 1
    ):
        raise ValueError("Invalid unique catalog or cross-branch groups")
    manifests, capacity = [], []
    for axis in FEATURES:
        for side in ["low", "high"]:
            scenario = f"{axis}_{side}"
            manifest = fit[["structure_id", "design_group_id", "branch_id", *FEATURES]].copy()
            manifest["scenario"], manifest["role"] = "", "train"
            manifest["scenario"] = scenario
            for branch, group in fit.groupby("branch_id"):
                levels = sorted(group[axis].unique())
                edge = levels[0] if side == "low" else levels[-1]
                selected = group[group[axis].eq(edge)]
                held = set(selected.design_group_id)
                train = group[~group.design_group_id.isin(held)]
                reason = (
                    "fewer_than_three_axis_levels"
                    if len(levels) < 3
                    else "fewer_than_two_training_groups"
                    if train.design_group_id.nunique() < 2
                    else "fewer_than_two_training_levels"
                    if train[axis].nunique() < 2
                    else ""
                )
                capacity.append(
                    dict(
                        scenario=scenario,
                        branch_id=branch,
                        axis=axis,
                        edge=float(edge),
                        n_axis_levels=len(levels),
                        n_train_groups=train.design_group_id.nunique(),
                        n_evaluation_groups=selected.design_group_id.nunique(),
                        skip_reason=reason,
                    )
                )
                if reason:
                    continue
                mask = manifest.design_group_id.isin(held)
                manifest.loc[mask, "role"] = "embargo"
                manifest.loc[manifest.structure_id.isin(selected.structure_id), "role"] = (
                    "evaluation"
                )
            manifests.append(manifest)
    return pd.concat(manifests, ignore_index=True), pd.DataFrame(capacity)


def balanced_scores(inner, fit):
    predictions = inner.merge(
        fit[["structure_id", "branch_id"]], on="structure_id", validate="many_to_one"
    )
    cells = (
        predictions.groupby(["recipe", "branch_id", "target", "design_group_id"])
        .abs_log_error.mean()
        .groupby(["recipe", "branch_id", "target"])
        .mean()
        .reset_index()
    )
    scores = cells.groupby("recipe").abs_log_error.mean().to_dict()
    return min(RECIPES, key=lambda r: scores[r]), scores, cells


def stress_selection(fit):
    manifests, capacity = pseudo_boundaries(fit)
    predictions = []
    for scenario, m in manifests.groupby("scenario", sort=True):
        train = fit[fit.structure_id.isin(m.loc[m.role.eq("train"), "structure_id"])]
        evaluation = fit[fit.structure_id.isin(m.loc[m.role.eq("evaluation"), "structure_id"])]
        if evaluation.empty:
            continue
        assert not set(train.design_group_id) & set(evaluation.design_group_id)
        for recipe in RECIPES:
            for target in TARGETS:
                model = AblationModel(recipe, target).fit(train)
                mean, _ = model.predict_log(evaluation)
                if not np.isfinite(mean).all():
                    raise ValueError("Nonfinite pseudo-boundary prediction")
                row = evaluation[["structure_id", "design_group_id", "branch_id"]].copy()
                row["scenario"], row["recipe"], row["target"] = scenario, recipe, target
                row["actual"], row["prediction"] = evaluation[target], np.exp(mean)
                row["abs_log_error"] = np.abs(np.log(evaluation[target]) - mean)
                predictions.append(row)
    if not predictions:
        raise ValueError("No eligible pseudo-boundary cells")
    predictions = pd.concat(predictions, ignore_index=True)
    keys = ["recipe", "scenario", "branch_id", "target"]
    groups = predictions.groupby([*keys, "design_group_id"]).abs_log_error.mean()
    cells = (
        groups.groupby(keys)
        .agg(["mean", "size"])
        .reset_index()
        .rename(columns={"mean": "mean_abs_log_error", "size": "n_design_groups"})
    )
    scores = {
        r: {
            "worst_cell": float(g.mean_abs_log_error.max()),
            "mean_cells": float(g.mean_abs_log_error.mean()),
        }
        for r, g in cells.groupby("recipe")
    }
    selected = min(
        RECIPES, key=lambda r: (scores[r]["worst_cell"], scores[r]["mean_cells"], RECIPES.index(r))
    )
    return selected, scores, manifests, capacity, predictions, cells
