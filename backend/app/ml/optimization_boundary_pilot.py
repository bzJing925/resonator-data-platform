"""P6A retrospective, fixed-recipe boundary models; no production model replacement."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern, WhiteKernel
from sklearn.linear_model import Ridge

from app.ml.optimization_baselines import ScalarBaseline, score
from app.ml.optimization_data import FEATURES, GEOMETRY, TARGETS

MODEL_RECIPES = {
    "ridge": {"alpha": 1.0},
    "gp": {
        "length_scale": 0.5,
        "nu": 2.5,
        "noise_level": 0.05,
        "alpha": 1e-10,
        "optimizer": None,
        "normalize_y": True,
        "std_floor_log": 0.001,
    },
}


class BoundaryModel:
    def __init__(self, kind: str, target: str):
        if kind not in MODEL_RECIPES or target not in TARGETS:
            raise ValueError("Unknown fixed recipe or target")
        self.kind, self.target = kind, target
        self.branches: dict[str, dict] = {}
        self.geometry = ScalarBaseline("nearest_measured", target)

    def fit(self, frame: pd.DataFrame) -> BoundaryModel:
        if frame.empty or not np.isfinite(frame[list(FEATURES)]).all().all():
            raise ValueError("Nonempty finite training geometry required")
        if not np.isfinite(frame[self.target]).all() or (frame[self.target] <= 0).any():
            raise ValueError("Finite positive training targets required")
        self.branches = {}
        self.geometry.fit(frame)
        for branch, group in frame.groupby("branch_id"):
            x = group[list(FEATURES)].to_numpy(float)
            low, high = x.min(axis=0), x.max(axis=0)
            scale = np.where(high > low, high - low, 1.0)
            if self.kind == "ridge":
                model = Ridge(alpha=MODEL_RECIPES["ridge"]["alpha"])
            else:
                recipe = MODEL_RECIPES["gp"]
                kernel = Matern(
                    length_scale=recipe["length_scale"],
                    nu=recipe["nu"],
                    length_scale_bounds="fixed",
                ) + WhiteKernel(noise_level=recipe["noise_level"], noise_level_bounds="fixed")
                model = GaussianProcessRegressor(
                    kernel=kernel, optimizer=None, alpha=recipe["alpha"], normalize_y=True
                )
            model.fit((x - low) / scale, np.log(group[self.target].to_numpy(float)))
            self.branches[branch] = {
                "low": low,
                "scale": scale,
                "model": model,
                "structure_ids": group.structure_id.tolist(),
            }
        return self

    def predict_log(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        mean, std = np.full(len(frame), np.nan), np.full(len(frame), np.nan)
        for branch in frame.branch_id.unique():
            state = self.branches.get(branch)
            if state is None:
                continue
            where = np.flatnonzero(frame.branch_id.eq(branch).to_numpy())
            xn = (frame.iloc[where][list(FEATURES)].to_numpy(float) - state["low"]) / state["scale"]
            if self.kind == "gp":
                m, s = state["model"].predict(xn, return_std=True)
                mean[where], std[where] = m, np.maximum(s, MODEL_RECIPES["gp"]["std_floor_log"])
            else:
                mean[where], std[where] = state["model"].predict(xn), 1.0
        return mean, std


def group_quantile(groups: np.ndarray, residuals: np.ndarray) -> dict:
    """Rows=structures; columns=targets for a joint score. No silent invalid-row removal."""
    residuals = np.asarray(residuals, dtype=float)
    if residuals.ndim == 1:
        residuals = residuals[:, None]
    if len(groups) != len(residuals) or residuals.ndim != 2 or residuals.shape[1] == 0:
        raise ValueError("Invalid residual shape")
    if not np.isfinite(residuals).all() or (residuals < 0).any():
        raise ValueError("Calibration residuals must be finite and nonnegative")
    frame = pd.DataFrame({"design_group_id": groups, "score": residuals.max(axis=1)})
    scores = frame.groupby("design_group_id").score.max().sort_values()
    n = len(scores)
    rank = math.ceil((n + 1) * 0.9)
    return {
        "n_groups": n,
        "rank": rank,
        "quantile": float(scores.iloc[rank - 1]) if 0 < rank <= n else None,
        "scores_by_group": {str(k): float(v) for k, v in scores.items()},
        "nominal_coverage": 0.9,
        "n_joint_targets": residuals.shape[1],
    }


def log_intervals(
    mean: np.ndarray, scale: np.ndarray, quantile: dict
) -> tuple[np.ndarray, np.ndarray]:
    q = quantile["quantile"]
    if q is None:
        return mean * np.nan, mean * np.nan
    with np.errstate(over="ignore", invalid="ignore"):
        return np.exp(mean - q * scale), np.exp(mean + q * scale)


def fit_scope(structures: pd.DataFrame, manifest: pd.DataFrame, scope: str) -> tuple:
    if scope not in {"structure_holdout", "fl_upper", "eg_upper", "area_edges"}:
        raise ValueError("Unknown frozen scope")
    if (
        set(manifest.structure_id) != set(structures.structure_id)
        or manifest.structure_id.duplicated().any()
        or structures.structure_id.duplicated().any()
    ):
        raise ValueError("Manifest must cover the exact unique catalog")
    if not manifest.role.isin(["train", "validation", "calibration", "test", "embargo"]).all():
        raise ValueError("Unknown manifest role")
    groups = structures.set_index("structure_id").design_group_id
    if not manifest.design_group_id.eq(manifest.structure_id.map(groups)).all():
        raise ValueError("Manifest design groups differ from frozen catalog")
    fit_roles = ["train", "validation"] if scope == "structure_holdout" else ["train"]
    # Embargo and test may share a design group; neither may share with train/calibration.
    roles = manifest.assign(
        partition=manifest.role.replace({"validation": "train", "embargo": "test"})
    )
    if roles.groupby("design_group_id").partition.nunique().max() > 1:
        raise ValueError("Design group leakage")
    frame = structures.merge(manifest[["structure_id", "role"]], validate="one_to_one")
    fit = frame[frame.role.isin(fit_roles)]
    cal, test = frame[frame.role.eq("calibration")], frame[frame.role.eq("test")]
    if fit.empty or cal.empty or test.empty:
        raise ValueError("Fit, calibration and test must be nonempty")
    predictions, models, calibration = [], {}, {}
    for kind in MODEL_RECIPES:
        residuals, test_outputs = {}, {}
        for target in TARGETS:
            model = BoundaryModel(kind, target).fit(fit)
            cm, cs = model.predict_log(cal)
            actual = cal[target].to_numpy(float)
            if not np.isfinite(actual).all() or (actual <= 0).any():
                raise ValueError("Calibration targets must be complete and positive")
            residuals[target] = np.abs(np.log(actual) - cm) / cs
            test_outputs[target] = model.predict_log(test)
            models[(kind, target)] = model
        joint = (
            group_quantile(
                cal.design_group_id.to_numpy(), np.column_stack([residuals[t] for t in TARGETS])
            )
            if kind == "gp"
            else None
        )
        for target in TARGETS:
            marginal = group_quantile(cal.design_group_id.to_numpy(), residuals[target])
            methods = (
                {"ridge_log_global": marginal}
                if kind == "ridge"
                else {"gp_matern_scaled_marginal": marginal, "gp_matern_scaled_joint": joint}
            )
            for name, q in methods.items():
                mean, scale = test_outputs[target]
                lower, upper = log_intervals(mean, scale, q)
                with np.errstate(over="ignore"):
                    pred = np.exp(mean)
                output = test[["structure_id", "design_group_id", "branch_id", *GEOMETRY]].copy()
                output["scope"], output["model"], output["target"] = scope, name, target
                output["actual"], output["prediction"] = test[target].to_numpy(), pred
                output["log_mean"], output["log_scale"] = mean, scale
                output["lower"], output["upper"] = lower, upper
                output["calibration_quantile"] = q["quantile"]
                support = models[(kind, target)].geometry.support(test)
                for col in support:
                    output[col] = support[col].to_numpy()
                output["trusted_for_unmeasured_optimization"] = False
                output["evaluation_status"] = "retrospective_seen_holdouts"
                predictions.append(output)
                calibration[f"{name}:{target}"] = q
    fit_receipt = {
        "fit_structure_ids": fit.structure_id.tolist(),
        "calibration_structure_ids": cal.structure_id.tolist(),
        "test_structure_ids": test.structure_id.tolist(),
        "embargo_structure_ids": frame[frame.role.eq("embargo")].structure_id.tolist(),
        "fit_design_groups": sorted(fit.design_group_id.unique()),
        "calibration_design_groups": sorted(cal.design_group_id.unique()),
        "test_design_groups": sorted(test.design_group_id.unique()),
    }
    return pd.concat(predictions, ignore_index=True), models, calibration, fit_receipt


def summarize(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metrics, joint = [], []
    for (scope, model, target), group in predictions.groupby(["scope", "model", "target"]):
        for branch, rows in [("all", group), *group.groupby("branch_id")]:
            actual, pred = rows.actual.to_numpy(), rows.prediction.to_numpy()
            labelled = rows.assign(**{target: actual})
            result = score(labelled, target, pred, rows.lower.to_numpy(), rows.upper.to_numpy())
            error = pred - actual
            finite = np.isfinite(error)
            result.update(
                signed_bias=float(np.mean(error[finite])) if finite.any() else None,
                max_absolute_error=float(np.max(np.abs(error[finite]))) if finite.any() else None,
                n_nonphysical_point=int(
                    (
                        (pred <= 0)
                        | (~np.isfinite(pred))
                        | ((pred >= 100) if target == "k2eff_pct" else False)
                    ).sum()
                ),
            )
            metrics.append(
                {"scope": scope, "model": model, "target": target, "branch": branch, **result}
            )
    evaluated = predictions.copy()
    evaluated["inside"] = (
        np.isfinite(evaluated.lower)
        & np.isfinite(evaluated.upper)
        & evaluated.actual.between(evaluated.lower, evaluated.upper)
    )
    for (scope, model), rows in evaluated.groupby(["scope", "model"]):
        if not rows.groupby("structure_id").target.nunique().eq(4).all():
            raise ValueError("Joint evaluation requires all four targets")
        groups = rows.groupby("design_group_id").inside.all()
        joint.append(
            {
                "scope": scope,
                "model": model,
                "n_groups": len(groups),
                "n_covered_groups": int(groups.sum()),
                "joint_coverage": float(groups.mean()),
            }
        )
    return pd.DataFrame(metrics), pd.DataFrame(joint), evaluated[~evaluated.inside]
