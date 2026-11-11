"""P9 label-uncertainty arms: heteroscedastic GP noise and heteroscedastic calibration."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern
from sklearn.linear_model import Ridge

from app.ml.optimization_ablation import AblationModel
from app.ml.optimization_data import FEATURES, TARGETS

ARMS = ("C0_control", "C1_hetero_gp", "C2_hetero_calibration")
PF_QP_BRANCHES = ("pf1_eg0_fl0", "pf1_eg0_fl1")
SUBSET_MIN_COVERAGE_GAIN = 0.25
BALANCED_MAX_COVERAGE_DROP = 0.05
SUBSET_MAX_WIDTH_RATIO = 2.0
POINT_GUARD_MAX_DEGRADATION = 0.05


def dispersion(frame, target):
    """Frozen proxy: per-structure position MAD over the label, relative to the label."""
    disp = frame[f"{target}_position_mad"].to_numpy(float) / frame[target].to_numpy(float)
    if not np.isfinite(disp).all() or (disp < 0).any():
        raise ValueError("Dispersion proxy must be finite and nonnegative")
    return disp


class HeteroTrendGPModel:
    """C1: frozen trend+GP log-area recipe; per-point alpha from the dispersion proxy."""

    def __init__(self, target):
        if target not in TARGETS:
            raise ValueError("Unknown target")
        self.target = target

    def fit(self, frame):
        if not np.isfinite(frame[list(FEATURES)]).all().all():
            raise ValueError("Finite features required")
        if not np.isfinite(frame[self.target]).all() or (frame[self.target] <= 0).any():
            raise ValueError("Finite positive targets required")
        disp = dispersion(frame, self.target)
        transformed = frame.copy()
        transformed["area_um2"] = np.log(frame.area_um2)
        self.branches = {}
        for branch, group in transformed.groupby("branch_id"):
            x = group[list(FEATURES)].to_numpy(float)
            low, high = x.min(axis=0), x.max(axis=0)
            scale = np.where(high > low, high - low, 1.0)
            xn = (x - low) / scale
            trend = Ridge(alpha=1.0).fit(xn, np.log(group[self.target]))
            resid_log = np.log(group[self.target].to_numpy(float)) - trend.predict(xn)
            s = float(np.std(resid_log))
            if s <= 0:
                raise ValueError("Degenerate fold target spread")
            pos = frame.index.get_indexer(group.index)
            alpha = 1e-10 + (disp[pos] / s) ** 2
            kernel = Matern(length_scale=0.5, nu=2.5, length_scale_bounds="fixed")
            gp = GaussianProcessRegressor(
                kernel=kernel, optimizer=None, normalize_y=True, alpha=alpha
            )
            gp.fit(xn, resid_log)
            self.branches[branch] = (low, scale, trend, gp)
        return self

    def predict_log(self, frame):
        transformed = frame.copy()
        transformed["area_um2"] = np.log(frame.area_um2)
        mean = np.full(len(frame), np.nan)
        std = np.full(len(frame), np.nan)
        for branch, (low, scale, trend, gp) in self.branches.items():
            where = np.flatnonzero(transformed.branch_id.eq(branch).to_numpy())
            if not len(where):
                continue
            x = (transformed.iloc[where][list(FEATURES)].to_numpy(float) - low) / scale
            m, s = gp.predict(x, return_std=True)
            mean[where] = m + trend.predict(x)
            std[where] = np.maximum(s, 0.001)
        return mean, std


def inner_interval_predictions(fit, folds, arm):
    """Four-fold out-of-fold predictions with log_scale; C0 refits the frozen recipe."""
    if folds.groupby("design_group_id").fold.nunique().max() != 1:
        raise ValueError("Fold crosses design groups")
    if not set(folds.structure_id) == set(fit.structure_id):
        raise ValueError("Folds must cover the exact fit set")
    rows = []
    for fold in range(4):
        held = set(folds.loc[folds.fold.eq(fold), "structure_id"])
        train, valid = fit[~fit.structure_id.isin(held)], fit[fit.structure_id.isin(held)]
        for target in TARGETS:
            if arm == "C0_control":
                model = AblationModel("trend_gp_log_area", target).fit(train)
            elif arm == "C1_hetero_gp":
                model = HeteroTrendGPModel(target).fit(train)
            else:
                raise ValueError("Unknown interval arm")
            mean, std = model.predict_log(valid)
            if not np.isfinite(mean).all() or not np.isfinite(std).all():
                raise ValueError("Nonfinite inner interval prediction")
            frame = valid[["structure_id", "design_group_id", "branch_id"]].copy()
            frame["fold"], frame["arm"], frame["target"] = fold, arm, target
            frame["actual"] = valid[target].to_numpy(float)
            frame["log_mean"], frame["log_scale"] = mean, std
            frame["abs_log_error"] = np.abs(np.log(valid[target].to_numpy(float)) - mean)
            frame["disp"] = dispersion(valid, target)
            rows.append(frame)
    return pd.concat(rows, ignore_index=True)


def loo_quantile(scores, groups, rank_ratio=0.9):
    """Per-row leave-one-group-out ceil((n+1)*rank_ratio) quantile of group-max scores."""
    frame = pd.DataFrame({"g": groups, "s": scores})
    group_max = frame.groupby("g").s.max()
    order = np.argsort(group_max.to_numpy(), kind="stable")
    sorted_vals = group_max.to_numpy()[order]
    sorted_groups = group_max.index.to_numpy()[order]
    n = len(sorted_vals)
    n_loo = n - 1
    rank = math.ceil((n_loo + 1) * rank_ratio)
    if not 0 < rank <= n_loo:
        return np.full(len(frame), np.nan)
    pos = {g: i for i, g in enumerate(sorted_groups)}
    p = np.array([pos[g] for g in frame.g])
    idx = np.where(p <= rank - 1, rank, rank - 1)
    return sorted_vals[idx]


def method_scores(arm, g):
    """(score, scale) per method; C2 reuses C0 points with dispersion-adjusted scales."""
    if arm in ("C0_control", "C1_hetero_gp"):
        return {
            "global": (g.abs_log_error.to_numpy(), np.ones(len(g))),
            "scaled": (
                g.abs_log_error.to_numpy() / g.log_scale.to_numpy(),
                g.log_scale.to_numpy(),
            ),
        }
    if arm == "C2_hetero_calibration":
        disp = np.maximum(g.disp.to_numpy(), 1e-12)
        combined = np.sqrt(g.log_scale.to_numpy() ** 2 + disp**2)
        return {
            "global": (g.abs_log_error.to_numpy() / disp, disp),
            "scaled": (g.abs_log_error.to_numpy() / combined, combined),
        }
    raise ValueError("Unknown arm")


def inner_interval_metrics(inner):
    """Frozen inner metrics per scope: subset coverage, balanced coverage, median width."""
    frames = {
        "C0_control": inner[inner.arm.eq("C0_control")],
        "C1_hetero_gp": inner[inner.arm.eq("C1_hetero_gp")],
    }
    frames["C2_hetero_calibration"] = frames["C0_control"]
    metrics = []
    for arm, g_all in frames.items():
        for method, (scores, scales) in method_scores(arm, g_all).items():
            covered = np.zeros(len(g_all), dtype=bool)
            half = np.full(len(g_all), np.nan)
            scopes = g_all.scope.to_numpy()
            for scope in np.unique(scopes):
                where = np.flatnonzero(scopes == scope)
                q = loo_quantile(scores[where], g_all.design_group_id.to_numpy()[where])
                half[where] = q * scales[where]
                resid = np.abs(
                    np.log(g_all.actual.to_numpy()[where]) - g_all.log_mean.to_numpy()[where]
                )
                covered[where] = resid <= half[where]
            work = g_all[["scope", "branch_id", "target", "area_um2"]].copy()
            work["covered"], work["half_width"] = covered, half
            min_area = work.groupby("scope").area_um2.transform("min")
            subset = work[
                work.branch_id.isin(PF_QP_BRANCHES)
                & work.area_um2.eq(min_area)
                & work.target.eq("qp")
            ]
            cells = work.groupby(["scope", "branch_id", "target"]).covered.mean()
            metrics.append(
                dict(
                    arm=arm,
                    method=method,
                    subset_qp_coverage=float(subset.covered.mean()),
                    subset_n=int(len(subset)),
                    balanced_coverage=float(cells.mean()),
                    subset_median_half_width=float(subset.half_width.median()),
                )
            )
    return pd.DataFrame(metrics)


def select_arm_p9(metrics, point_primary, point_guard):
    """Frozen P9 rule on inner interval metrics plus the point guard for C1."""
    c0 = metrics[(metrics.arm.eq("C0_control")) & (metrics.method.eq("scaled"))].iloc[0]
    verdicts = {}
    for arm in ARMS[1:]:
        m = metrics[(metrics.arm.eq(arm)) & (metrics.method.eq("scaled"))].iloc[0]
        width_limit = SUBSET_MAX_WIDTH_RATIO * c0.subset_median_half_width
        width_ok = m.subset_median_half_width <= width_limit
        balanced_ok = m.balanced_coverage >= c0.balanced_coverage - BALANCED_MAX_COVERAGE_DROP
        gain_ok = m.subset_qp_coverage >= c0.subset_qp_coverage + SUBSET_MIN_COVERAGE_GAIN
        point_ok = True
        if arm == "C1_hetero_gp":
            point_ok = (
                point_primary[arm]
                <= point_primary["C0_control"] * (1 + POINT_GUARD_MAX_DEGRADATION)
                and point_guard[arm]
                <= point_guard["C0_control"] * (1 + POINT_GUARD_MAX_DEGRADATION)
            )
        verdicts[arm] = bool(gain_ok and width_ok and balanced_ok and point_ok)
    eligible = [a for a in ARMS[1:] if verdicts[a]]
    if eligible:
        sub = metrics[metrics.arm.isin(eligible) & metrics.method.eq("scaled")]
        best_cov = sub.subset_qp_coverage.max()
        best = sub[sub.subset_qp_coverage.eq(best_cov)]
        selected = str(best.sort_values("subset_median_half_width").iloc[0].arm)
    else:
        selected = "C0_control"
    return {
        "selected": selected,
        "verdicts": verdicts,
        "eligible": eligible,
        "metrics": metrics.to_dict("records"),
        "point_primary": point_primary,
        "point_guard": point_guard,
        "selection_uses_outer_test": False,
    }
