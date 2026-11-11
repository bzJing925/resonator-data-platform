"""Small, frozen scalar baselines and honest held-out evaluation for P3.

No optimizer, hyperparameter search, GP, or sim-to-real extrapolation is implemented.
Calibration concerns structure medians, not future individual devices.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.spatial import ConvexHull, QhullError
from scipy.stats import spearmanr
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, r2_score

from app.ml.optimization_data import (
    FEATURES,
    GEOMETRY,
    STANDARD_COHORT,
    TARGETS,
    branch_key,
    sha256,
    write_json,
)

SEED = 20261015
CANDIDATES = ("branch_median", "nearest_measured", "extra_trees_log")
NOMINAL_COVERAGE = 0.90


def _order(group: str) -> str:
    return hashlib.sha256(f"{SEED}:{group}".encode()).hexdigest()


def make_split_manifest(structures: pd.DataFrame) -> pd.DataFrame:
    if structures.groupby("design_group_id").branch_id.nunique().max() != 1:
        raise ValueError("A connected design group crosses topology branches")
    roles = {}
    for _, branch in structures.groupby("branch_id"):
        groups = sorted(branch.design_group_id.unique(), key=_order)
        n = len(groups)
        if n < 4:
            raise ValueError("Each branch needs at least four independent design groups")
        nt = max(1, min(n - 3, int(n * 0.60)))
        nv = max(1, min(n - nt - 2, int(n * 0.15)))
        nc = max(1, min(n - nt - nv - 1, int(n * 0.15)))
        labels = ["train"] * nt + ["validation"] * nv + ["calibration"] * nc
        labels += ["test"] * (n - len(labels))
        for group, role in zip(groups, labels, strict=True):
            roles[group] = role
    result = structures[["structure_id", "design_group_id", "branch_id", *GEOMETRY]].copy()
    result["role"] = result.design_group_id.map(roles)
    return result


def boundary_manifests(structures: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Freeze geometric edge tests, embargoing the associated opposite port."""
    selectors = {
        "fl_upper": structures.fl_um.eq(structures.fl_um.max()),
        "eg_upper": structures.eg_um.eq(structures.eg_um.max()),
        "area_edges": structures.area_um2.isin(
            [structures.area_um2.min(), structures.area_um2.max()]
        ),
    }
    result = {}
    for name, selected in selectors.items():
        holdout_groups = set(structures.loc[selected, "design_group_id"])
        m = structures[["structure_id", "design_group_id", "branch_id", *GEOMETRY]].copy()
        m["role"] = "train"
        m.loc[m.design_group_id.isin(holdout_groups), "role"] = "embargo"
        m.loc[selected, "role"] = "test"
        for _, branch in m[m.role.eq("train")].groupby("branch_id"):
            groups = sorted(branch.design_group_id.unique(), key=_order)
            if len(groups) >= 2:
                ncal = min(len(groups) - 1, max(1, int(len(groups) * 0.2)))
                m.loc[m.design_group_id.isin(groups[:ncal]), "role"] = "calibration"
        result[name] = m
    return result


class ScalarBaseline:
    """Separate branch models; explicit zero dimensions never blend across topology."""

    def __init__(self, kind: str, target: str):
        if kind not in CANDIDATES or target not in TARGETS:
            raise ValueError("Unknown candidate or target")
        self.kind = kind
        self.target = target
        self.branches: dict[str, dict] = {}

    def fit(self, frame: pd.DataFrame) -> ScalarBaseline:
        self.branches = {}
        good = frame[np.isfinite(frame[self.target]) & (frame[self.target] > 0)]
        for branch, group in good.groupby("branch_id"):
            x = group[list(FEATURES)].to_numpy(float)
            y = group[self.target].to_numpy(float)
            low, high = x.min(axis=0), x.max(axis=0)
            scale = np.where(high > low, high - low, 1.0)
            xn = (x - low) / scale
            active = high > low
            equations = None
            if active.sum() > 1 and len(xn) > active.sum():
                try:
                    equations = ConvexHull(xn[:, active]).equations
                except QhullError:
                    # Degenerate joint coverage is not assumed to fill its bounding box.
                    equations = np.empty((0, int(active.sum()) + 1))
            model = None
            if self.kind == "extra_trees_log":
                model = ExtraTreesRegressor(
                    n_estimators=192,
                    min_samples_leaf=2,
                    max_features=1.0,
                    random_state=SEED,
                    n_jobs=2,
                ).fit(xn, np.log(y))
            self.branches[branch] = {
                "x": x,
                "xn": xn,
                "y": y,
                "low": low,
                "high": high,
                "scale": scale,
                "active": active,
                "equations": equations,
                "model": model,
                "structure_ids": group.structure_id.tolist(),
            }
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        result = np.full(len(frame), np.nan)
        for branch in frame.branch_id.unique():
            where = np.flatnonzero(frame.branch_id.eq(branch).to_numpy())
            state = self.branches.get(branch)
            if state is None:
                continue
            x = frame.iloc[where][list(FEATURES)].to_numpy(float)
            xn = (x - state["low"]) / state["scale"]
            if self.kind == "branch_median":
                pred = np.full(len(x), np.median(state["y"]))
            elif self.kind == "nearest_measured":
                distance = ((xn[:, None] - state["xn"][None]) ** 2).sum(axis=2)
                pred = state["y"][distance.argmin(axis=1)]
            else:
                pred = np.exp(state["model"].predict(xn))
            result[where] = pred
        return result

    def support(self, frame: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for row in frame.itertuples():
            state = self.branches.get(row.branch_id)
            info = {
                "support": "unknown_branch",
                "nearest_structure_id": None,
                "nearest_scaled_distance": None,
            }
            if state is not None:
                x = np.array([getattr(row, c) for c in FEATURES], dtype=float)
                xn = (x - state["low"]) / state["scale"]
                distances = np.sqrt(((state["xn"] - xn) ** 2).sum(axis=1))
                ni = int(distances.argmin())
                info.update(
                    nearest_structure_id=state["structure_ids"][ni],
                    nearest_scaled_distance=float(distances[ni]),
                )
                within = np.all(x >= state["low"] - 1e-9) and np.all(x <= state["high"] + 1e-9)
                eq = state["equations"]
                in_hull = eq is None or (
                    len(eq) > 0 and np.all(eq[:, :-1] @ xn[state["active"]] + eq[:, -1] <= 1e-9)
                )
                known_levels = all(np.isclose(state["x"][:, i], v).any() for i, v in enumerate(x))
                if distances[ni] <= 1e-10:
                    info["support"] = "exact_training_structure"
                elif not within:
                    info["support"] = "outside_training_bounds"
                elif not in_hull:
                    info["support"] = "outside_joint_hull"
                elif not known_levels:
                    info["support"] = "within_hull_new_level"
                else:
                    info["support"] = "within_hull_unmeasured_combination"
            rows.append(info)
        return pd.DataFrame(rows)


def conformal_radius(cal: pd.DataFrame, predictions: np.ndarray, target: str) -> dict:
    y = cal[target].to_numpy(float)
    ok = np.isfinite(y) & (y > 0) & np.isfinite(predictions) & (predictions > 0)
    residual = pd.DataFrame(
        {
            "design": cal.design_group_id.to_numpy()[ok],
            "residual": np.abs(np.log(y[ok] / predictions[ok])),
        }
    )
    scores = np.sort(residual.groupby("design").residual.max().to_numpy())
    n = len(scores)
    rank = math.ceil((n + 1) * NOMINAL_COVERAGE)
    return {
        "n_calibration_design_groups": n,
        "rank": rank,
        "log_radius": float(scores[rank - 1]) if 0 < rank <= n else None,
        "nominal_coverage": NOMINAL_COVERAGE,
        "unit": "joint structure medians within one design group",
        "caveat": "exchangeable design groups assumed; no boundary-shift guarantee",
    }


def intervals(predictions: np.ndarray, calibration: dict) -> tuple[np.ndarray, np.ndarray]:
    radius = calibration["log_radius"]
    if radius is None:
        return np.full(len(predictions), np.nan), np.full(len(predictions), np.nan)
    return predictions * np.exp(-radius), predictions * np.exp(radius)


def score(
    frame: pd.DataFrame,
    target: str,
    pred: np.ndarray,
    lower: np.ndarray | None = None,
    upper: np.ndarray | None = None,
) -> dict:
    y = frame[target].to_numpy(float)
    ok = np.isfinite(y) & (y > 0) & np.isfinite(pred) & (pred > 0)
    actual, estimate = y[ok], pred[ok]
    result = {"n": int(ok.sum()), "n_missing_predictions": int((np.isfinite(y) & ~ok).sum())}
    if not len(actual):
        return result
    k = min(5, len(actual))
    best_true, best_pred = np.argsort(-actual)[:k], np.argsort(-estimate)[:k]
    corr = None
    if len(actual) > 2 and np.ptp(actual) > 0 and np.ptp(estimate) > 0:
        corr = float(spearmanr(actual, estimate).statistic)
    result.update(
        mae=float(mean_absolute_error(actual, estimate)),
        median_ape=float(np.median(np.abs(estimate - actual) / actual)),
        r2=float(r2_score(actual, estimate)) if len(actual) > 1 and np.ptp(actual) else None,
        spearman=corr,
        top_k=k,
        top_k_overlap=float(len(set(best_true) & set(best_pred)) / k),
        top_k_regret_fraction=float((actual.max() - actual[best_pred].max()) / actual.max()),
    )
    if lower is not None and upper is not None:
        finite = ok & np.isfinite(lower) & np.isfinite(upper)
        inside = (y[finite] >= lower[finite]) & (y[finite] <= upper[finite])
        result["n_finite_intervals"] = int(finite.sum())
        result["interval_coverage"] = float(inside.mean()) if len(inside) else None
        result["median_interval_width"] = (
            float(np.median(upper[finite] - lower[finite])) if len(inside) else None
        )
        joint = pd.DataFrame({"design": frame.design_group_id.to_numpy()[finite], "inside": inside})
        result["design_joint_coverage"] = (
            float(joint.groupby("design").inside.all().mean()) if len(joint) else None
        )
    return result


def _prediction_frame(
    frame: pd.DataFrame,
    target: str,
    kind: str,
    model: ScalarBaseline,
    calibration: dict | None = None,
) -> pd.DataFrame:
    pred = model.predict(frame)
    lo, hi = intervals(pred, calibration) if calibration else (pred * np.nan, pred * np.nan)
    result = frame[["structure_id", "design_group_id", "branch_id", "role", *GEOMETRY]].copy()
    result["target"], result["model"] = target, kind
    result["actual"], result["prediction"] = frame[target].to_numpy(), pred
    result["lower"], result["upper"] = lo, hi
    support = model.support(frame)
    for col in support:
        result[col] = support[col].to_numpy()
    return result


def _scores_by_branch(
    frame: pd.DataFrame, target: str, model: ScalarBaseline, calibration: dict, scope: str
) -> list[dict]:
    results = []
    for name, subset in [("all", frame), *list(frame.groupby("branch_id"))]:
        pred = model.predict(subset)
        lo, hi = intervals(pred, calibration)
        results.append(
            {
                "scope": scope,
                "target": target,
                "model": model.kind,
                "branch": name,
                **score(subset, target, pred, lo, hi),
            }
        )
    return results


def legacy_diagnostic(structures: pd.DataFrame, legacy_root: Path, out: Path) -> dict:
    """Never describe this frozen, possibly contaminated model as an independent test."""
    import torch

    from app.ml.surrogate.baseline import BaselineTable
    from app.ml.surrogate.model import BaselineResidualMLP

    data = legacy_root / "backend/surrogate_data"
    checkpoint = legacy_root / "backend/checkpoints_surrogate/stage1.pt"
    meta = json.loads((data / "meta.json").read_text())
    cfg = torch.load(checkpoint, map_location="cpu", weights_only=True)
    model = BaselineResidualMLP(
        **{k: cfg[k] for k in ("in_dim", "hidden", "n_res_blocks", "n_params", "k")}
    )
    model.load_state_dict(cfg["model_state"])
    model.eval()
    baseline = BaselineTable.load(data / "baseline.npz")
    x = np.column_stack(
        [
            structures.eg_um,
            structures.fl_um,
            np.ones(len(structures)),
            structures.area_um2,
            structures.has_pf,
        ]
    ).astype(np.float32)
    xn = (x - np.array(meta["x_mean"])) / np.array(meta["x_std"])
    pm, ps = np.array(meta["params_mean"]), np.array(meta["params_std"])
    pb = np.array([baseline.lookup(row.area_um2, row.has_pf)[0] for row in structures.itertuples()])
    with torch.no_grad():
        pred = (
            model(
                torch.tensor(xn, dtype=torch.float32),
                torch.tensor((pb - pm) / ps, dtype=torch.float32),
                torch.zeros(len(x), cfg["k"]),
            )["params"].numpy()
            * ps
            + pm
        )
    old = np.load(data / "dataset.npz")
    old_inputs = {tuple(np.round(v.astype(float), 6)) for v in old["x_struct"]}
    overlap = [tuple(np.round(v.astype(float), 6)) in old_inputs for v in x]
    splits: dict[tuple, set] = {}
    for xi, gi in zip(old["x_struct"], old["group_idx"], strict=True):
        splits.setdefault(tuple(xi), set()).add(meta["groups"][int(gi)]["split"])
    old.close()
    output = structures[["structure_id", *GEOMETRY]].copy()
    output["input_in_legacy_dataset"] = overlap
    metrics = {}
    for target, index in (("k2eff_pct", 2), ("qs", 3), ("qp", 4)):
        output[target + "_prediction"] = pred[:, index]
        output[target + "_actual"] = structures[target]
        metrics[target] = score(structures, target, pred[:, index])
    output.to_csv(out / "legacy_descriptive_predictions.csv", index=False)
    info = {
        "status": "descriptive_only_not_independent_validation",
        "training_source": "unknown",
        "n_new_cohort_structures_in_legacy_inputs": int(sum(overlap)),
        "n_legacy_inputs_spanning_old_splits": sum(len(s) > 1 for s in splits.values()),
        "ag_used_for_legacy": "1.0 placeholder only; absent from the new model",
        "bodeq_fitted": "not an output",
        "metrics": metrics,
        "artifact_sha256": {
            str(p): sha256(p)
            for p in (checkpoint, data / "meta.json", data / "baseline.npz", data / "dataset.npz")
        },
    }
    write_json(out / "legacy_diagnostic.json", info)
    return info


def evaluate_baselines(data_dir: Path, out: Path, legacy_root: Path | None = None) -> dict:
    structures = pd.read_csv(data_dir / "structures.csv")
    manifest = make_split_manifest(structures)
    manifest.to_csv(out / "split_manifest.csv", index=False)
    boundaries = boundary_manifests(structures)
    for name, m in boundaries.items():
        m.to_csv(out / f"split_{name}.csv", index=False)
    frozen = {
        "seed": SEED,
        "candidates": list(CANDIDATES),
        "nominal_coverage": NOMINAL_COVERAGE,
        "structures_sha256": sha256(data_dir / "structures.csv"),
        "split_sha256": sha256(out / "split_manifest.csv"),
        "boundary_split_sha256": {n: sha256(out / f"split_{n}.csv") for n in boundaries},
        "selection": "per target validation MAE; no hyperparameter tuning",
        "extra_trees": {"n_estimators": 192, "min_samples_leaf": 2, "n_jobs": 2},
        "accuracy_budget": "not engineering-approved; no unseen optimization authorization",
    }
    write_json(out / "frozen_protocol.json", frozen)
    frame = structures.merge(manifest[["structure_id", "role"]], validate="one_to_one")
    train, val = frame[frame.role.eq("train")], frame[frame.role.eq("validation")]
    calibration, test = frame[frame.role.eq("calibration")], frame[frame.role.eq("test")]
    validation_scores, test_scores, predictions, noise = [], [], [], {}
    selected, models, radii = {}, {}, {}
    for target in TARGETS:
        print(f"P3 fitting {target}", flush=True)
        for kind in CANDIDATES:
            model = ScalarBaseline(kind, target).fit(train)
            validation_scores.append(
                {"target": target, "model": kind, **score(val, target, model.predict(val))}
            )
        options = [
            v for v in validation_scores if v["target"] == target and v.get("mae") is not None
        ]
        choice = min(options, key=lambda v: (v["mae"], CANDIDATES.index(v["model"])))
        selected[target] = choice["model"]
        noise[target] = float(train[target + "_position_mad"].median())
    # The choice is persisted before calibration and test evaluation.
    write_json(
        out / "selection.json", {"selected": selected, "training_position_mad_reference": noise}
    )
    pd.DataFrame(validation_scores).to_csv(out / "validation_metrics.csv", index=False)
    fit = frame[frame.role.isin(["train", "validation"])]
    for target in TARGETS:
        for kind in CANDIDATES:
            model = ScalarBaseline(kind, target).fit(fit)
            radius = conformal_radius(calibration, model.predict(calibration), target)
            results = _scores_by_branch(test, target, model, radius, "structure_holdout")
            for result in results:
                result["selected_on_validation"] = kind == selected[target]
            test_scores.extend(results)
            predictions.append(_prediction_frame(test, target, kind, model, radius))
            if kind == selected[target]:
                models[target], radii[target] = model, radius
    pd.DataFrame(test_scores).to_csv(out / "test_metrics.csv", index=False)
    pd.concat(predictions, ignore_index=True).to_csv(out / "test_predictions.csv", index=False)
    boundary_scores, boundary_predictions = [], []
    for name, m in boundaries.items():
        print(f"P3 boundary {name}", flush=True)
        bf = structures.merge(m[["structure_id", "role"]], validate="one_to_one")
        btrain, bcal, btest = (bf[bf.role.eq(r)] for r in ("train", "calibration", "test"))
        for target in TARGETS:
            for kind in CANDIDATES:
                model = ScalarBaseline(kind, target).fit(btrain)
                radius = conformal_radius(bcal, model.predict(bcal), target)
                boundary_scores.extend(_scores_by_branch(btest, target, model, radius, name))
                bp = _prediction_frame(btest, target, kind, model, radius)
                bp["boundary"] = name
                boundary_predictions.append(bp)
    pd.DataFrame(boundary_scores).to_csv(out / "boundary_metrics.csv", index=False)
    pd.concat(boundary_predictions, ignore_index=True).to_csv(
        out / "boundary_predictions.csv", index=False
    )
    metadata = {
        "schema_version": "optimization_scalar_p3_v1",
        "feature_order": list(FEATURES),
        "targets": list(TARGETS),
        "target_statistic": "median_of_coordinate_medians",
        "cohort": STANDARD_COHORT,
        "ag": "unrecorded; not a feature",
        "selected_models": selected,
        "calibration": radii,
        "frozen_protocol": frozen,
        "training_roles": ["train", "validation"],
        "trusted_for_unmeasured_optimization": False,
        "limitations": [
            "engineering error tolerances not confirmed",
            "one measured batch",
            "no prospective unseen-structure measurement",
            "scalar outputs not a joint complex curve",
            "quarantined cohorts and AG variants excluded",
        ],
    }
    joblib.dump({"metadata": metadata, "models": models}, out / "scalar_baselines.joblib")
    write_json(out / "model_metadata.json", metadata)
    legacy = legacy_diagnostic(structures, legacy_root, out) if legacy_root else None
    summary = {
        "status": "P3_baseline_evaluation_complete_not_optimization_release",
        "n_structures": len(structures),
        "split_counts": manifest.role.value_counts().to_dict(),
        "design_group_counts": manifest.groupby("role").design_group_id.nunique().to_dict(),
        "selected_models": selected,
        "training_position_mad_reference": noise,
        "selected_test_metrics": [
            s for s in test_scores if s["branch"] == "all" and s["selected_on_validation"]
        ],
        "legacy_status": legacy["status"] if legacy else "not_evaluated",
        "trusted_for_unmeasured_optimization": False,
    }
    write_json(out / "evaluation_summary.json", summary)
    return summary


def predict_bundle(bundle: dict, inputs: pd.DataFrame) -> pd.DataFrame:
    """Batch scalar prediction for handoff, deliberately not a search or ranking API."""
    if missing := set(GEOMETRY) - set(inputs.columns):
        raise ValueError(f"Missing inputs: {sorted(missing)}")
    frame = inputs.copy()
    for col in GEOMETRY:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    x = frame[list(GEOMETRY)].to_numpy(float)
    if not np.isfinite(x).all() or (x[:, :2] < 0).any() or (x[:, 2] <= 0).any():
        raise ValueError("Dimensions must be finite, EG/FL nonnegative, area positive")
    if not frame.has_pf.isin([0, 1]).all():
        raise ValueError("has_pf is a discrete 0/1 geometry flag")
    if "cohort" in frame and not frame.cohort.eq(STANDARD_COHORT).all():
        raise ValueError("Unsupported cohort")
    if "ag_um" in frame and frame.ag_um.notna().any():
        raise ValueError("AG is unrecorded in this model; it cannot accept AG design values")
    frame["branch_id"] = [branch_key(r.eg_um, r.fl_um, int(r.has_pf)) for r in frame.itertuples()]
    results = []
    for target, model in bundle["models"].items():
        pred = model.predict(frame)
        lo, hi = intervals(pred, bundle["metadata"]["calibration"][target])
        result = frame[list(GEOMETRY)].reset_index(drop=True)
        result["input_index"], result["target"] = np.arange(len(frame)), target
        result["prediction"], result["lower"], result["upper"] = pred, lo, hi
        support = model.support(frame)
        for col in support:
            result[col] = support[col]
        result["trusted_for_unmeasured_optimization"] = False
        result["model_version"] = bundle["metadata"]["schema_version"]
        result["interval_unit"] = "structure_typical_value_not_individual_device"
        results.append(result)
    return pd.concat(results, ignore_index=True)
