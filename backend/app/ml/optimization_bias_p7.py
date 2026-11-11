"""P7 small-area PF Qp bias arms on frozen P6C/P6D machinery; selection uses training data only."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.ml.optimization_ablation import RECIPES, AblationModel
from app.ml.optimization_boundary_selection import pseudo_boundaries
from app.ml.optimization_data import FEATURES, TARGETS

ARMS = ("A0_control", "A1_trend_area_clip", "A2_per_target_recipe", "A3_branch_area_offset")
PF_QP_BRANCHES = ("pf1_eg0_fl0", "pf1_eg0_fl1")
SHRINK_K = 4.0
PRIMARY_MIN_IMPROVEMENT = 0.20
GUARD_MAX_DEGRADATION = 0.05


class TrendAreaClipModel:
    """A1: frozen trend+GP log-area recipe; trend area input clipped to the fitted range."""

    def __init__(self, target):
        if target not in TARGETS:
            raise ValueError("Unknown target")
        self.target = target

    def fit(self, frame):
        self.base = AblationModel("trend_gp_log_area", self.target).fit(frame)
        return self

    def predict_log(self, frame):
        mean, std = self.base.predict_log(frame)
        transformed = self.base.transform(frame)
        area_axis = list(FEATURES).index("area_um2")
        for branch, (low, scale, trend) in self.base.trends.items():
            where = np.flatnonzero(transformed.branch_id.eq(branch).to_numpy())
            if not len(where):
                continue
            x = (transformed.iloc[where][list(FEATURES)].to_numpy(float) - low) / scale
            clipped = x.copy()
            clipped[:, area_axis] = np.clip(x[:, area_axis], 0.0, 1.0)
            mean[where] += trend.predict(clipped) - trend.predict(x)
        return mean, std


class BranchAreaOffsetModel:
    """A3: A0 plus shrunken branch x area mean log-residual offsets; zero outside fit levels."""

    def __init__(self, target):
        if target not in TARGETS:
            raise ValueError("Unknown target")
        self.target = target

    def fit(self, frame):
        self.base = AblationModel("trend_gp_log_area", self.target).fit(frame)
        mean, _ = self.base.predict_log(frame)
        resid = np.log(frame[self.target].to_numpy(float)) - mean
        cells = frame[["branch_id", "area_um2"]].assign(resid=resid)
        self.offsets = {
            (str(branch), float(area)): float(g.resid.mean() * len(g) / (len(g) + SHRINK_K))
            for (branch, area), g in cells.groupby(["branch_id", "area_um2"])
        }
        return self

    def predict_log(self, frame):
        mean, std = self.base.predict_log(frame)
        keys = zip(frame.branch_id.to_numpy(), frame.area_um2.to_numpy(float), strict=True)
        add = np.array([self.offsets.get((str(b), float(a)), 0.0) for b, a in keys])
        return mean + add, std


ARM_MODELS = {
    "A1_trend_area_clip": TrendAreaClipModel,
    "A3_branch_area_offset": BranchAreaOffsetModel,
}


def inner_cv_predictions(fit, folds):
    """Four-fold grouped inner predictions for the two new arms; folds must be group-safe."""
    if folds.groupby("design_group_id").fold.nunique().max() != 1:
        raise ValueError("Fold crosses design groups")
    if not set(folds.structure_id) == set(fit.structure_id):
        raise ValueError("Folds must cover the exact fit set")
    rows = []
    for fold in range(4):
        held = set(folds.loc[folds.fold.eq(fold), "structure_id"])
        train, valid = fit[~fit.structure_id.isin(held)], fit[fit.structure_id.isin(held)]
        for arm, cls in ARM_MODELS.items():
            for target in TARGETS:
                mean, _ = cls(target).fit(train).predict_log(valid)
                if not np.isfinite(mean).all():
                    raise ValueError("Nonfinite inner prediction")
                frame = valid[["structure_id", "design_group_id", "branch_id"]].copy()
                frame["fold"], frame["arm"], frame["target"] = fold, arm, target
                frame["actual"], frame["prediction"] = valid[target], np.exp(mean)
                frame["abs_log_error"] = np.abs(np.log(valid[target]) - mean)
                rows.append(frame)
    return pd.concat(rows, ignore_index=True)


def area_stress_predictions(fit):
    """Area-axis pseudo-boundary predictions for the two new arms; P6D capacity rules reused."""
    manifests, capacity = pseudo_boundaries(fit)
    manifests = manifests[manifests.scenario.isin(["area_um2_low", "area_um2_high"])]
    rows = []
    for scenario, m in manifests.groupby("scenario", sort=True):
        train = fit[fit.structure_id.isin(m.loc[m.role.eq("train"), "structure_id"])]
        evaluation = fit[fit.structure_id.isin(m.loc[m.role.eq("evaluation"), "structure_id"])]
        if evaluation.empty:
            continue
        if set(train.design_group_id) & set(evaluation.design_group_id):
            raise ValueError("Pseudo-boundary group leakage")
        for arm, cls in ARM_MODELS.items():
            for target in TARGETS:
                mean, _ = cls(target).fit(train).predict_log(evaluation)
                if not np.isfinite(mean).all():
                    raise ValueError("Nonfinite stress prediction")
                frame = evaluation[["structure_id", "design_group_id", "branch_id"]].copy()
                frame["scenario"], frame["arm"], frame["target"] = scenario, arm, target
                frame["actual"], frame["prediction"] = evaluation[target], np.exp(mean)
                frame["abs_log_error"] = np.abs(np.log(evaluation[target]) - mean)
                rows.append(frame)
    if not rows:
        raise ValueError("No eligible area pseudo-boundary cells")
    return pd.concat(rows, ignore_index=True)


def per_target_recipe_selection(inner):
    """A2: P6C four recipes scored per target on saved inner predictions; no refitting."""
    scores = (
        inner.groupby(["target", "recipe", "design_group_id"])
        .abs_log_error.mean()
        .groupby(["target", "recipe"])
        .mean()
    )
    selected = {t: min(RECIPES, key=lambda r: scores[t][r]) for t in TARGETS}
    return selected, {f"{t}:{r}": float(scores[t][r]) for t in TARGETS for r in RECIPES}


def assemble_a2(inner, stress, selected):
    """A2 predictions are the per-target chosen recipe's saved rows; nothing is refit."""
    pick = inner.merge(
        pd.DataFrame({"target": list(selected), "recipe": list(selected.values())}),
        on=["target", "recipe"],
    )
    out_inner = pick.assign(arm="A2_per_target_recipe").drop(columns="recipe")
    pick_s = stress.merge(
        pd.DataFrame({"target": list(selected), "recipe": list(selected.values())}),
        on=["target", "recipe"],
    )
    out_stress = pick_s.assign(arm="A2_per_target_recipe").drop(columns="recipe")
    return out_inner, out_stress


def primary_score(stress):
    """Frozen: qp, PF branches, area_low cells; group means, then equal-weight cell means."""
    sub = stress.loc[
        stress.target.eq("qp")
        & stress.branch_id.isin(PF_QP_BRANCHES)
        & stress.scenario.eq("area_um2_low")
    ]
    if sub.empty:
        raise ValueError("No valid primary-metric cells")
    cells = (
        sub.groupby(["arm", "scope", "branch_id", "design_group_id"])
        .abs_log_error.mean()
        .groupby(["arm", "scope", "branch_id"])
        .mean()
        .reset_index()
    )
    scores = cells.groupby("arm").abs_log_error.mean().to_dict()
    return scores, cells


def guard_score(inner):
    """Frozen: P6D balanced score over the four-fold inner predictions, per arm."""
    cells = (
        inner.groupby(["arm", "branch_id", "target", "design_group_id"])
        .abs_log_error.mean()
        .groupby(["arm", "branch_id", "target"])
        .mean()
        .reset_index()
    )
    scores = cells.groupby("arm").abs_log_error.mean().to_dict()
    return scores, cells


def select_arm(primary, guard):
    """Frozen rule: >=20% primary improvement and <=5% guard degradation vs A0."""
    base_p, base_g = primary["A0_control"], guard["A0_control"]
    eligible = [
        arm
        for arm in ARMS
        if primary[arm] <= base_p * (1 - PRIMARY_MIN_IMPROVEMENT)
        and guard[arm] <= base_g * (1 + GUARD_MAX_DEGRADATION)
    ]
    if eligible:
        selected = min(eligible, key=lambda a: (primary[a], ARMS.index(a)))
    else:
        selected = "A0_control"
    return {
        "selected": selected,
        "primary_scores": {k: float(v) for k, v in primary.items()},
        "guard_scores": {k: float(v) for k, v in guard.items()},
        "eligible": eligible,
        "primary_min_improvement": PRIMARY_MIN_IMPROVEMENT,
        "guard_max_degradation": GUARD_MAX_DEGRADATION,
        "selection_uses_outer_test": False,
    }


def coordinate_composition_diagnostic(records, structures, target_ids):
    """D1: label shift when restricted to coords shared by >=80% of same-branch/area structures."""
    cols = ["structure_id", "coord", "qp"]
    de = records.loc[
        records["mode"].eq("deembedded")
        & records.geometry_eligible
        & records.valid_qp
        & records.structure_id.isin(structures.structure_id),
        cols,
    ]
    meta = structures.set_index("structure_id")
    rows = []
    for (branch, area), cell in structures.groupby(["branch_id", "area_um2"]):
        sids = list(cell.structure_id)
        presence = (
            de[de.structure_id.isin(sids)]
            .drop_duplicates(["structure_id", "coord"])
            .groupby("coord")
            .size()
        )
        common = set(presence[presence >= np.ceil(0.8 * len(sids))].index)
        for sid in sids:
            block = de[de.structure_id.eq(sid)].groupby("coord").qp.median()
            label_all = block.median()
            expected = meta.loc[sid, "qp"]
            if not np.isclose(label_all, expected, rtol=1e-9):
                raise ValueError(f"Label reconstruction differs from frozen catalog: {sid}")
            label_common = block.loc[sorted(common & set(block.index))].median()
            rows.append(
                dict(
                    structure_id=sid,
                    branch_id=branch,
                    area_um2=area,
                    fl_um=meta.loc[sid, "fl_um"],
                    n_coords=len(block),
                    n_common_coords=len(common & set(block.index)),
                    qp_all_coords=label_all,
                    qp_common_coords=label_common,
                    log_diff_common_vs_all=float(np.log(label_common) - np.log(label_all)),
                    is_target8=bool(sid in target_ids),
                )
            )
    return pd.DataFrame(rows)
