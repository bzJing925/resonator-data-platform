"""Finite-catalog P4 search. No fitting, extrapolation claim, or mode mixing."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd

from app.ml.optimization_baselines import predict_bundle
from app.ml.optimization_data import (
    FEATURES,
    GEOMETRY,
    STANDARD_COHORT,
    TARGETS,
    branch_key,
    structure_key,
)

MAX_CANDIDATES = 10000
FREQUENCIES = ("fs_ghz", "fp_ghz")


def validate_config(config: dict) -> dict:
    allowed = {"mode", "grid", "bounds", "minimums", "top_n", "min_coordinates"}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError("Unknown configuration fields")
    cfg = {
        "mode": "measured",
        "bounds": {},
        "minimums": {},
        "top_n": 10,
        "min_coordinates": 10,
        **config,
    }
    if cfg["mode"] not in ("measured", "exploratory"):
        raise ValueError("mode must be measured or exploratory")
    for key in ("top_n", "min_coordinates"):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if cfg["min_coordinates"] < 10:
        raise ValueError("min_coordinates cannot weaken the frozen minimum of 10")
    for name, allowed_keys in (("bounds", (*GEOMETRY, *FREQUENCIES)), ("minimums", TARGETS)):
        if not isinstance(cfg[name], dict) or set(cfg[name]) - set(allowed_keys):
            raise ValueError(f"Unknown {name} keys")
        for key, value in cfg[name].items():
            values = value if name == "bounds" else [value]
            if not isinstance(values, list) or len(values) != (2 if name == "bounds" else 1):
                raise ValueError(f"Invalid {name}: {key}")
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise ValueError(f"Non-finite or nonnumeric {name}: {key}")
            if name == "bounds" and values[0] > values[1]:
                raise ValueError(f"Reversed bounds: {key}")
    if cfg["mode"] == "measured" and "grid" in cfg:
        raise ValueError("Measured mode enumerates the frozen catalog; grid is not allowed")
    if cfg["mode"] == "exploratory":
        if set(cfg["bounds"]) & set(FREQUENCIES):
            raise ValueError("No frequency surrogate: frequency bounds require measured mode")
        if not isinstance(cfg.get("grid"), list) or not cfg["grid"]:
            raise ValueError("Exploratory mode requires an explicit nonempty grid")
    return cfg


def enumerate_grid(grids: list[dict]) -> pd.DataFrame:
    rows = []
    size = 0
    for grid in grids:
        if not isinstance(grid, dict) or set(grid) != set(GEOMETRY):
            raise ValueError(
                "Each grid needs exactly EG, FL, area and PF; no AG or topology guesses"
            )
        levels = []
        for key in GEOMETRY:
            values = grid[key]
            if not isinstance(values, list) or not values:
                raise ValueError(f"Empty or invalid grid: {key}")
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise ValueError(f"Invalid grid numbers: {key}")
            if key == "has_pf" and any(v not in (0, 1) for v in values):
                raise ValueError("PF must be discrete 0/1")
            if key != "has_pf" and any(v < 0 or (key == "area_um2" and v == 0) for v in values):
                raise ValueError("Invalid geometry dimensions")
            levels.append(sorted(set(values)))
        size += math.prod(len(v) for v in levels)
        if size > MAX_CANDIDATES:
            raise ValueError(f"Grid exceeds {MAX_CANDIDATES} candidate budget before deduplication")
        rows.extend(itertools.product(*levels))
    frame = pd.DataFrame(rows, columns=GEOMETRY).drop_duplicates().reset_index(drop=True)
    frame["structure_id"] = [structure_key(row) for row in frame[list(GEOMETRY)].to_numpy()]
    if frame.structure_id.duplicated().any():
        raise ValueError("Grid precision exceeds stable structure ID precision")
    frame["branch_id"] = [branch_key(r.eg_um, r.fl_um, int(r.has_pf)) for r in frame.itertuples()]
    frame["cohort"] = STANDARD_COHORT
    return frame


def local_cell_support(state: dict | None, point: np.ndarray) -> dict:
    """Check adjacent fitted levels, including fixed dimensions; never certify accuracy."""
    result = {
        "local_support": "unknown_branch",
        "required_corners": 0,
        "present_corners": 0,
        "corner_structure_ids": "",
    }
    if state is None:
        return result
    x = state["x"]
    choices = []
    for i, value in enumerate(point):
        levels = np.unique(x[:, i])
        exact = levels[np.isclose(levels, value, rtol=0, atol=1e-9)]
        if len(exact):
            choices.append([exact[0]])
        elif value < levels[0] or value > levels[-1]:
            return {**result, "local_support": "outside_training_bounds"}
        else:
            choices.append([levels[levels < value][-1], levels[levels > value][0]])
    corners = list(itertools.product(*choices))
    ids = []
    for corner in corners:
        matches = np.flatnonzero(np.isclose(x, corner, rtol=0, atol=1e-9).all(axis=1))
        if len(matches):
            ids.append(state["structure_ids"][int(matches[0])])
    complete = len(ids) == len(corners)
    label = "exact_fitted_structure" if complete and len(corners) == 1 else "complete_local_cell"
    return {
        "local_support": label if complete else "missing_local_corners",
        "required_corners": len(corners),
        "present_corners": len(ids),
        "corner_structure_ids": "|".join(ids),
    }


def pareto_mask(values: np.ndarray) -> np.ndarray:
    """Maximize every column; equal vectors do not dominate one another."""
    if not np.isfinite(values).all():
        raise ValueError("Pareto inputs must be finite")
    keep = np.ones(len(values), dtype=bool)
    for i, point in enumerate(values):
        keep[i] = not np.any(np.all(values >= point, axis=1) & np.any(values > point, axis=1))
    return keep


def search_catalog(structures: pd.DataFrame, config: dict, bundle: dict | None = None) -> dict:
    cfg = validate_config(config)
    if (
        not structures.cohort.eq(STANDARD_COHORT).all()
        or structures.structure_id.duplicated().any()
    ):
        raise ValueError("Expected a unique frozen standard-cohort catalog")
    measured = cfg["mode"] == "measured"
    if measured:
        frame = structures.copy().reset_index(drop=True)
        frame["value_source"] = "observed_deembedded_structure_typical"
        frame["interval_kind"] = "coordinate_bootstrap_95pct_not_selection_adjusted"
        details = pd.DataFrame()
    else:
        if bundle is None or set(bundle["models"]) != set(TARGETS):
            raise ValueError("All four frozen scalar models are required")
        frame = enumerate_grid(cfg["grid"])
        details_list = []
        for start in range(0, len(frame), 512):
            chunk = frame.iloc[start : start + 512]
            details_part = predict_bundle(bundle, chunk)
            details_part["input_index"] += start
            details_list.append(details_part)
        details = pd.concat(details_list, ignore_index=True)
        details["structure_id"] = frame.structure_id.to_numpy()[details.input_index]
        details["branch_id"] = frame.branch_id.to_numpy()[details.input_index]
        local = [
            local_cell_support(
                bundle["models"][r.target].branches.get(r.branch_id),
                np.array([getattr(r, c) for c in FEATURES]),
            )
            for r in details.itertuples()
        ]
        details = pd.concat([details, pd.DataFrame(local)], axis=1)
        frame["in_measured_catalog"] = frame.structure_id.isin(structures.structure_id)
        frame["value_source"] = "frozen_p3_surrogate_exploratory"
        frame["interval_kind"] = "p3_marginal_group_90pct_no_shift_or_selection_guarantee"
        for target in TARGETS:
            target_rows = details[details.target.eq(target)].sort_values("input_index")
            frame[target] = target_rows.prediction.to_numpy()
            for col in (
                "lower",
                "upper",
                "support",
                "local_support",
                "nearest_structure_id",
                "nearest_scaled_distance",
                "required_corners",
                "present_corners",
            ):
                frame[f"{target}_{col}"] = target_rows[col].to_numpy()
    frame["trusted_for_unmeasured_optimization"] = False
    reasons = [[] for _ in range(len(frame))]

    def reject(mask: np.ndarray | pd.Series, reason: str) -> None:
        for i in np.flatnonzero(np.asarray(mask)):
            reasons[i].append(reason)

    for target in TARGETS:
        reject(~np.isfinite(frame[target]) | (frame[target] <= 0), f"invalid_{target}")
        if measured:
            reject(
                frame[f"{target}_n_coords"].fillna(0) < cfg["min_coordinates"],
                f"insufficient_coordinates_{target}",
            )
        else:
            reject(
                ~frame[f"{target}_local_support"].isin(
                    ["exact_fitted_structure", "complete_local_cell"]
                ),
                f"unsupported_{target}",
            )
    for key, (low, high) in cfg["bounds"].items():
        reject(~np.isfinite(frame[key]) | ~frame[key].between(low, high), f"bounds_{key}")
    for key, minimum in cfg["minimums"].items():
        reject(~np.isfinite(frame[key]) | (frame[key] < minimum), f"minimum_{key}")
    frame["rejection_reasons"] = [";".join(r) for r in reasons]
    frame["eligible"] = [not r for r in reasons]
    feasible = frame[frame.eligible].copy()
    feasible["pareto"] = pareto_mask(feasible[list(TARGETS)].to_numpy(float))
    ranks = {}
    for target in TARGETS:
        ranked = (
            feasible.sort_values([target, "structure_id"], ascending=[False, True])
            .head(cfg["top_n"])
            .copy()
        )
        ranked.insert(0, "rank", np.arange(1, len(ranked) + 1))
        ranks[target] = ranked
    counts = pd.Series([reason for row in reasons for reason in row], dtype=str).value_counts()
    summary = {
        "mode": cfg["mode"],
        "status": "complete" if len(feasible) else "no_feasible_candidates",
        "n_candidates": len(frame),
        "n_feasible": len(feasible),
        "n_rejected": int((~frame.eligible).sum()),
        "n_pareto": int(feasible.pareto.sum()),
        "rejection_counts_overlapping": counts.to_dict(),
        "trusted_for_unmeasured_optimization": False,
        "scope": "measured_catalog_only" if measured else "exploratory_grid_only",
        "statistic": "median_of_coordinate_medians",
        "all_objectives": "maximize",
        "constraint_semantics": "typical_point_values_only_not_device_yield_or_robust_guarantee",
        "evaluation_status": "retrospective_P3_reuse_no_new_blind_test",
        "tie_break": "structure_id; does not establish a unique statistical winner",
        "config": cfg,
    }
    return {
        "candidates": frame,
        "feasible": feasible,
        "pareto": feasible[feasible.pareto],
        "ranks": ranks,
        "prediction_details": details,
        "summary": summary,
    }
