from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from app.ml.optimization_p13.contracts import (
    O05_SPLITS_SHA256,
    ContractError,
    sha256_file,
)

SCOPES = {
    "structure_holdout": ("train", "validation"),
    "area_edges": ("train",),
    "eg_upper": ("train",),
    "fl_upper": ("train",),
}
PF0_BRANCHES = (
    "pf0_eg1_fl0",
    "pf0_eg1_fl1",
    "pf0_eg0_fl1",
    "pf0_eg0_fl0",
)
SUPPORTED_BOUNDARIES = {
    "eg_upper": ("pf0_eg1_fl0", "pf0_eg1_fl1"),
    "fl_lower": ("pf0_eg1_fl1", "pf0_eg0_fl1"),
    "joint_eg_upper_fl_lower": ("pf0_eg1_fl1",),
}
GEOMETRY = ["eg_um", "fl_um", "area_um2", "has_pf"]
IDENTITY = ["structure_id", "design_group_id", "branch_id"]
MIN_FIT_LEVELS = 2
MIN_FIT_GROUPS = 2
MIN_EVALUATION_GROUPS = 1
HARD_MIN_GROUPS = 20
HARD_MIN_UNITS = 2
ROLES = {
    "fit",
    "evaluation",
    "embargo",
    "baseline_only",
    "outer_calibration",
    "outer_test",
    "outer_embargo",
}
SPLIT_COLUMNS = [
    "structure_id",
    "design_group_id",
    "scope",
    "fold",
    "unit_id",
    "role",
    "family",
    "branch_id",
    "unit_branch_id",
    "direction",
    "variant",
    *GEOMETRY,
    "original_scope",
    "original_role",
    "original_fit_roles",
    "original_partition",
    "baseline_usage",
    "eg_held_out_levels",
    "eg_fit_remaining_levels",
    "fl_held_out_levels",
    "fl_fit_remaining_levels",
    "eg_fit_remaining_level_count",
    "fl_fit_remaining_level_count",
    "min_fit_levels",
    "counts_toward_hard_capacity",
    "source_file_id",
    "source_sha256",
    "source_split_file_id",
    "source_split_sha256",
    "source_structure_file_id",
    "source_structure_sha256",
    "source_fold_file_id",
    "source_fold_sha256",
    "source_fold_index",
]
CAPACITY_COLUMNS = [
    "unit_id",
    "fold",
    "scope",
    "family",
    "branch_id",
    "direction",
    "variant",
    "valid",
    "invalid_reason",
    "counts_toward_hard_capacity",
    "original_fit_structure_count",
    "original_fit_design_group_count",
    "eligible_fit_structure_count",
    "eligible_fit_design_group_count",
    "fit_structure_count",
    "fit_design_group_count",
    "evaluation_structure_count",
    "independent_evaluation_design_group_count",
    "embargo_structure_count",
    "embargo_design_group_count",
    "all_branch_fit_structure_count",
    "all_branch_fit_design_group_count",
    "baseline_excluded_structure_count",
    "baseline_excluded_design_group_count",
    "eg_available_level_count",
    "eg_available_levels",
    "requested_eg_holdout_level_count",
    "eg_held_out_levels",
    "eg_fit_remaining_level_count",
    "eg_fit_remaining_levels",
    "fl_available_level_count",
    "fl_available_levels",
    "requested_fl_holdout_level_count",
    "fl_held_out_levels",
    "fl_fit_remaining_level_count",
    "fl_fit_remaining_levels",
    "min_fit_levels",
    "min_fit_design_groups",
    "min_evaluation_design_groups",
    "ordinary_fold_index",
    "source_split_file_id",
    "source_split_sha256",
    "source_structure_file_id",
    "source_structure_sha256",
    "source_fold_file_id",
    "source_fold_sha256",
]


def _levels(values) -> list[float]:
    return sorted(float(value) for value in values)


def _level_text(values) -> str:
    return ";".join(format(float(value), ".15g") for value in _levels(values))


def _parse_levels(text: str) -> list[float]:
    return [float(value) for value in str(text).split(";") if value]


def _unit_id(scope: str, family: str, branch: str, variant: str) -> str:
    return f"{scope}__{family}__{branch}__{variant}"


def _check_inputs(
    catalog: pd.DataFrame,
    split: pd.DataFrame,
    ordinary_folds: pd.DataFrame,
    fit_roles: tuple[str, ...],
) -> None:
    required = {*IDENTITY, *GEOMETRY, "role"}
    if not required <= set(split.columns) or not {*IDENTITY, *GEOMETRY} <= set(catalog.columns):
        raise ValueError("Missing catalog or original split columns")
    if catalog.structure_id.duplicated().any() or split.structure_id.duplicated().any():
        raise ValueError("Duplicate structure_id")
    if set(catalog.structure_id) != set(split.structure_id):
        raise ValueError("Original split does not cover the exact catalog")
    left = catalog.sort_values("structure_id").reset_index(drop=True)
    right = split[[*IDENTITY, *GEOMETRY]].sort_values("structure_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(
        left[[*IDENTITY, *GEOMETRY]], right, check_dtype=False, check_exact=True
    )
    original_fit = split.role.isin(fit_roles)
    partition = original_fit.map({True: "fit", False: "outer"})
    if partition.groupby(split.design_group_id).nunique().max() > 1:
        raise ValueError("Original fit and outer roles cross a design group")
    expected = set(split.loc[original_fit, "structure_id"])
    if set(ordinary_folds.structure_id) != expected:
        raise ValueError("P6C folds do not cover the exact original fit set")
    if ordinary_folds.structure_id.duplicated().any():
        raise ValueError("Duplicate P6C fold structure")
    if ordinary_folds.groupby("design_group_id").fold.nunique().max() != 1:
        raise ValueError("P6C fold crosses a design group")
    if set(ordinary_folds.fold.astype(int)) != {0, 1, 2, 3}:
        raise ValueError("P6C ordinary folds must be 0,1,2,3")


def _capacity_row(
    *,
    unit_id: str,
    scope: str,
    family: str,
    branch: str,
    direction: str,
    variant: str,
    valid: bool,
    invalid_reason: str,
    original_fit: pd.DataFrame,
    eligible: pd.DataFrame,
    fit: pd.DataFrame,
    evaluation: pd.DataFrame,
    embargo: pd.DataFrame,
    baseline_group: str,
    held_eg: list[float],
    held_fl: list[float],
    requested_eg: int,
    requested_fl: int,
    ordinary_fold_index: int | None,
    source: Mapping[str, str],
) -> dict:
    target_original = (
        original_fit
        if branch == "all_branches"
        else original_fit[original_fit.branch_id.eq(branch)]
    )
    target_eligible = (
        eligible if branch == "all_branches" else eligible[eligible.branch_id.eq(branch)]
    )
    target_fit = fit if branch == "all_branches" else fit[fit.branch_id.eq(branch)]
    boundary = family != "ordinary"
    row = {column: "" for column in CAPACITY_COLUMNS}
    row.update(
        {
            "unit_id": unit_id,
            "fold": unit_id,
            "scope": scope,
            "family": family,
            "branch_id": branch,
            "direction": direction,
            "variant": variant,
            "valid": valid,
            "invalid_reason": invalid_reason,
            "counts_toward_hard_capacity": valid and boundary,
            "original_fit_structure_count": len(target_original),
            "original_fit_design_group_count": target_original.design_group_id.nunique(),
            "eligible_fit_structure_count": len(target_eligible),
            "eligible_fit_design_group_count": target_eligible.design_group_id.nunique(),
            "fit_structure_count": len(target_fit),
            "fit_design_group_count": target_fit.design_group_id.nunique(),
            "evaluation_structure_count": len(evaluation),
            "independent_evaluation_design_group_count": evaluation.design_group_id.nunique(),
            "embargo_structure_count": len(embargo),
            "embargo_design_group_count": embargo.design_group_id.nunique(),
            "all_branch_fit_structure_count": len(fit),
            "all_branch_fit_design_group_count": fit.design_group_id.nunique(),
            "baseline_excluded_structure_count": int(
                target_original.design_group_id.eq(baseline_group).sum()
            ),
            "baseline_excluded_design_group_count": int(
                target_original.design_group_id.eq(baseline_group).any()
            ),
            "eg_available_level_count": target_eligible.eg_um.nunique(),
            "eg_available_levels": _level_text(target_eligible.eg_um.unique()),
            "requested_eg_holdout_level_count": requested_eg,
            "eg_held_out_levels": _level_text(held_eg),
            "eg_fit_remaining_level_count": target_fit.eg_um.nunique(),
            "eg_fit_remaining_levels": _level_text(target_fit.eg_um.unique()),
            "fl_available_level_count": target_eligible.fl_um.nunique(),
            "fl_available_levels": _level_text(target_eligible.fl_um.unique()),
            "requested_fl_holdout_level_count": requested_fl,
            "fl_held_out_levels": _level_text(held_fl),
            "fl_fit_remaining_level_count": target_fit.fl_um.nunique(),
            "fl_fit_remaining_levels": _level_text(target_fit.fl_um.unique()),
            "min_fit_levels": MIN_FIT_LEVELS if boundary else "",
            "min_fit_design_groups": MIN_FIT_GROUPS,
            "min_evaluation_design_groups": MIN_EVALUATION_GROUPS,
            "ordinary_fold_index": "" if ordinary_fold_index is None else ordinary_fold_index,
            "source_split_file_id": source["split_file_id"],
            "source_split_sha256": source["split_sha256"],
            "source_structure_file_id": source["structure_file_id"],
            "source_structure_sha256": source["structure_sha256"],
            "source_fold_file_id": source.get("fold_file_id", "") if family == "ordinary" else "",
            "source_fold_sha256": source.get("fold_sha256", "") if family == "ordinary" else "",
        }
    )
    return row


def _split_rows(
    *,
    split: pd.DataFrame,
    fit_roles: tuple[str, ...],
    baseline_group: str,
    eligible: pd.DataFrame,
    fit: pd.DataFrame,
    evaluation: pd.DataFrame,
    embargo: pd.DataFrame,
    capacity: Mapping[str, object],
    source: Mapping[str, str],
    fold_by_structure: Mapping[str, int] | None = None,
) -> list[dict]:
    fit_ids = set(fit.structure_id)
    evaluation_ids = set(evaluation.structure_id)
    embargo_ids = set(embargo.structure_id)
    eligible_ids = set(eligible.structure_id)
    rows = []
    for original in split.itertuples(index=False):
        structure_id = original.structure_id
        group_id = original.design_group_id
        if group_id == baseline_group:
            role = "baseline_only"
        elif structure_id in evaluation_ids:
            role = "evaluation"
        elif structure_id in embargo_ids:
            role = "embargo"
        elif structure_id in fit_ids:
            role = "fit"
        elif structure_id in eligible_ids:
            raise ValueError("Eligible structure lost from fold")
        else:
            role = f"outer_{original.role}"
        if role not in ROLES:
            raise ValueError(f"Unexpected role {role}")
        original_partition = "original_fit" if original.role in fit_roles else "original_outer"
        row = {
            "structure_id": structure_id,
            "design_group_id": group_id,
            "scope": original.scope if hasattr(original, "scope") else capacity["scope"],
            "fold": capacity["unit_id"],
            "unit_id": capacity["unit_id"],
            "role": role,
            "family": capacity["family"],
            "branch_id": original.branch_id,
            "unit_branch_id": capacity["branch_id"],
            "direction": capacity["direction"],
            "variant": capacity["variant"],
            **{column: getattr(original, column) for column in GEOMETRY},
            "original_scope": capacity["scope"],
            "original_role": original.role,
            "original_fit_roles": "|".join(fit_roles),
            "original_partition": original_partition,
            "baseline_usage": "final_difference_only" if group_id == baseline_group else "",
            "eg_held_out_levels": capacity["eg_held_out_levels"],
            "eg_fit_remaining_levels": capacity["eg_fit_remaining_levels"],
            "fl_held_out_levels": capacity["fl_held_out_levels"],
            "fl_fit_remaining_levels": capacity["fl_fit_remaining_levels"],
            "eg_fit_remaining_level_count": capacity["eg_fit_remaining_level_count"],
            "fl_fit_remaining_level_count": capacity["fl_fit_remaining_level_count"],
            "min_fit_levels": capacity["min_fit_levels"],
            "counts_toward_hard_capacity": capacity["counts_toward_hard_capacity"],
            "source_file_id": source["split_file_id"],
            "source_sha256": source["split_sha256"],
            "source_split_file_id": source["split_file_id"],
            "source_split_sha256": source["split_sha256"],
            "source_structure_file_id": source["structure_file_id"],
            "source_structure_sha256": source["structure_sha256"],
            "source_fold_file_id": (
                source.get("fold_file_id", "") if capacity["family"] == "ordinary" else ""
            ),
            "source_fold_sha256": (
                source.get("fold_sha256", "") if capacity["family"] == "ordinary" else ""
            ),
            "source_fold_index": (
                fold_by_structure.get(structure_id, "")
                if fold_by_structure is not None and capacity["family"] == "ordinary"
                else ""
            ),
        }
        rows.append(row)
    return rows


def _ordinary_unit(
    *,
    scope: str,
    split: pd.DataFrame,
    fit_roles: tuple[str, ...],
    original_fit: pd.DataFrame,
    eligible: pd.DataFrame,
    ordinary_folds: pd.DataFrame,
    fold_index: int,
    baseline_group: str,
    source: Mapping[str, str],
) -> tuple[dict, list[dict]]:
    fold_by_structure = dict(
        zip(ordinary_folds.structure_id, ordinary_folds.fold.astype(int), strict=True)
    )
    evaluation = eligible[eligible.structure_id.map(fold_by_structure).eq(fold_index)]
    fit = eligible[~eligible.structure_id.isin(evaluation.structure_id)]
    unit = _unit_id(scope, "ordinary", "all_branches", f"fold_{fold_index}")
    reasons = []
    if fit.design_group_id.nunique() < MIN_FIT_GROUPS:
        reasons.append("fewer_than_two_fit_design_groups")
    if evaluation.design_group_id.empty:
        reasons.append("no_evaluation_design_group")
    for branch, evaluation_branch in evaluation.groupby("branch_id"):
        fit_groups = fit[fit.branch_id.eq(branch)].design_group_id.nunique()
        if fit_groups < MIN_FIT_GROUPS:
            reasons.append(f"fewer_than_two_fit_design_groups__{branch}")
        if evaluation_branch.design_group_id.nunique() < MIN_EVALUATION_GROUPS:
            reasons.append(f"no_evaluation_design_group__{branch}")
    capacity = _capacity_row(
        unit_id=unit,
        scope=scope,
        family="ordinary",
        branch="all_branches",
        direction="grouped",
        variant=f"fold_{fold_index}",
        valid=not reasons,
        invalid_reason=";".join(reasons),
        original_fit=original_fit,
        eligible=eligible,
        fit=fit,
        evaluation=evaluation,
        embargo=eligible.iloc[0:0],
        baseline_group=baseline_group,
        held_eg=[],
        held_fl=[],
        requested_eg=0,
        requested_fl=0,
        ordinary_fold_index=fold_index,
        source=source,
    )
    rows = (
        _split_rows(
            split=split,
            fit_roles=fit_roles,
            baseline_group=baseline_group,
            eligible=eligible,
            fit=fit,
            evaluation=evaluation,
            embargo=eligible.iloc[0:0],
            capacity=capacity,
            source=source,
            fold_by_structure=fold_by_structure,
        )
        if not reasons
        else []
    )
    return capacity, rows


def _boundary_unit(
    *,
    scope: str,
    family: str,
    branch: str,
    holdout_count: int,
    split: pd.DataFrame,
    fit_roles: tuple[str, ...],
    original_fit: pd.DataFrame,
    eligible: pd.DataFrame,
    baseline_group: str,
    source: Mapping[str, str],
) -> tuple[dict, list[dict]]:
    branch_rows = eligible[eligible.branch_id.eq(branch)]
    supported = branch in SUPPORTED_BOUNDARIES[family] and not branch_rows.empty
    held_eg: list[float] = []
    held_fl: list[float] = []
    if supported and family in {"eg_upper", "joint_eg_upper_fl_lower"}:
        held_eg = _levels(branch_rows.eg_um.unique())[-holdout_count:]
    if supported and family in {"fl_lower", "joint_eg_upper_fl_lower"}:
        held_fl = _levels(branch_rows.fl_um.unique())[:holdout_count]
    if supported and family == "eg_upper":
        evaluation = branch_rows[branch_rows.eg_um.isin(held_eg)]
        withheld = evaluation
    elif supported and family == "fl_lower":
        evaluation = branch_rows[branch_rows.fl_um.isin(held_fl)]
        withheld = evaluation
    elif supported:
        eg_edge = branch_rows.eg_um.isin(held_eg)
        fl_edge = branch_rows.fl_um.isin(held_fl)
        evaluation = branch_rows[eg_edge & fl_edge]
        withheld = branch_rows[eg_edge | fl_edge]
    else:
        evaluation = branch_rows.iloc[0:0]
        withheld = evaluation
    held_groups = set(withheld.design_group_id)
    fit = eligible[~eligible.design_group_id.isin(held_groups)]
    embargo = eligible[
        eligible.design_group_id.isin(held_groups)
        & ~eligible.structure_id.isin(evaluation.structure_id)
    ]
    branch_fit = fit[fit.branch_id.eq(branch)]
    reasons = []
    if not supported:
        reasons.append(f"{family}_requires_a_supported_positive_pf0_branch")
    else:
        if family in {"eg_upper", "joint_eg_upper_fl_lower"}:
            if branch_fit.eg_um.nunique() < MIN_FIT_LEVELS:
                reasons.append("fewer_than_two_fit_levels__eg_um")
        if family in {"fl_lower", "joint_eg_upper_fl_lower"}:
            if branch_fit.fl_um.nunique() < MIN_FIT_LEVELS:
                reasons.append("fewer_than_two_fit_levels__fl_um")
        if branch_fit.design_group_id.nunique() < MIN_FIT_GROUPS:
            reasons.append("fewer_than_two_fit_design_groups")
        if evaluation.design_group_id.nunique() < MIN_EVALUATION_GROUPS:
            reasons.append("no_evaluation_design_group")
    variant = {
        "eg_upper": f"top_{holdout_count}",
        "fl_lower": f"bottom_{holdout_count}",
        "joint_eg_upper_fl_lower": f"top_{holdout_count}__bottom_{holdout_count}",
    }[family]
    direction = {
        "eg_upper": "eg_upper",
        "fl_lower": "fl_lower",
        "joint_eg_upper_fl_lower": "joint_eg_upper_fl_lower",
    }[family]
    unit = _unit_id(scope, family, branch, variant)
    capacity = _capacity_row(
        unit_id=unit,
        scope=scope,
        family=family,
        branch=branch,
        direction=direction,
        variant=variant,
        valid=not reasons,
        invalid_reason=";".join(reasons),
        original_fit=original_fit,
        eligible=eligible,
        fit=fit,
        evaluation=evaluation,
        embargo=embargo,
        baseline_group=baseline_group,
        held_eg=held_eg,
        held_fl=held_fl,
        requested_eg=holdout_count if family in {"eg_upper", "joint_eg_upper_fl_lower"} else 0,
        requested_fl=holdout_count if family in {"fl_lower", "joint_eg_upper_fl_lower"} else 0,
        ordinary_fold_index=None,
        source=source,
    )
    rows = (
        _split_rows(
            split=split,
            fit_roles=fit_roles,
            baseline_group=baseline_group,
            eligible=eligible,
            fit=fit,
            evaluation=evaluation,
            embargo=embargo,
            capacity=capacity,
            source=source,
        )
        if not reasons
        else []
    )
    return capacity, rows


def build_scope_splits(
    *,
    scope: str,
    catalog: pd.DataFrame,
    split: pd.DataFrame,
    ordinary_folds: pd.DataFrame,
    baseline_group: str,
    source: Mapping[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if scope not in SCOPES:
        raise ValueError(f"Unknown scope {scope}")
    fit_roles = SCOPES[scope]
    _check_inputs(catalog, split, ordinary_folds, fit_roles)
    split = split.copy()
    split["scope"] = scope
    original_fit = split[split.role.isin(fit_roles)].copy()
    eligible = original_fit[original_fit.design_group_id.ne(baseline_group)].copy()
    capacities, rows = [], []
    for fold_index in range(4):
        capacity, unit_rows = _ordinary_unit(
            scope=scope,
            split=split,
            fit_roles=fit_roles,
            original_fit=original_fit,
            eligible=eligible,
            ordinary_folds=ordinary_folds,
            fold_index=fold_index,
            baseline_group=baseline_group,
            source=source,
        )
        capacities.append(capacity)
        rows.extend(unit_rows)
    for family in ("eg_upper", "fl_lower", "joint_eg_upper_fl_lower"):
        for branch in PF0_BRANCHES:
            for holdout_count in (1, 2):
                capacity, unit_rows = _boundary_unit(
                    scope=scope,
                    family=family,
                    branch=branch,
                    holdout_count=holdout_count,
                    split=split,
                    fit_roles=fit_roles,
                    original_fit=original_fit,
                    eligible=eligible,
                    baseline_group=baseline_group,
                    source=source,
                )
                capacities.append(capacity)
                rows.extend(unit_rows)
    split_frame = pd.DataFrame(rows, columns=SPLIT_COLUMNS)
    capacity_frame = pd.DataFrame(capacities, columns=CAPACITY_COLUMNS)
    return split_frame, capacity_frame


def aggregate_hard_capacity(
    splits: pd.DataFrame, capacity: pd.DataFrame
) -> list[dict]:
    records = []
    for scope in SCOPES:
        for family, branches in SUPPORTED_BOUNDARIES.items():
            for branch in branches:
                candidates = capacity[
                    capacity.scope.eq(scope)
                    & capacity.family.eq(family)
                    & capacity.branch_id.eq(branch)
                ]
                valid = candidates[candidates.valid]
                unit_ids = sorted(valid.unit_id)
                rows = splits[splits.unit_id.isin(unit_ids) & splits.role.eq("evaluation")]
                groups = sorted(rows.design_group_id.unique())
                group_count = len(groups)
                unit_count = len(unit_ids)
                records.append(
                    {
                        "capacity_key": f"{scope}__{family}__{branch}",
                        "scope": scope,
                        "family": family,
                        "branch_id": branch,
                        "valid_boundary_unit_count": unit_count,
                        "valid_boundary_unit_ids": unit_ids,
                        "independent_design_group_count": group_count,
                        "design_group_ids": groups,
                        "evaluation_structure_count": rows.structure_id.nunique(),
                        "meets_20_independent_design_groups": group_count >= HARD_MIN_GROUPS,
                        "meets_2_valid_boundary_units": unit_count >= HARD_MIN_UNITS,
                        "meets_hard_capacity": (
                            group_count >= HARD_MIN_GROUPS and unit_count >= HARD_MIN_UNITS
                        ),
                        "invalid_unit_reasons": sorted(
                            {
                                reason
                                for reason in candidates.loc[~candidates.valid, "invalid_reason"]
                                if reason
                            }
                        ),
                        "aggregation_rule": (
                            "Union design_group_id once across valid units within this "
                            "scope/family/branch; repeated folds never increment capacity."
                        ),
                    }
                )
    return records


def pooled_descriptive_capacity(
    splits: pd.DataFrame, capacity: pd.DataFrame
) -> list[dict]:
    records = []
    for family, branches in SUPPORTED_BOUNDARIES.items():
        candidates = capacity[capacity.family.eq(family) & capacity.branch_id.isin(branches)]
        valid = candidates[candidates.valid]
        rows = splits[splits.unit_id.isin(valid.unit_id) & splits.role.eq("evaluation")]
        records.append(
            {
                "family": family,
                "valid_boundary_unit_count": valid.unit_id.nunique(),
                "independent_design_group_count_across_scopes": rows.design_group_id.nunique(),
                "descriptive_only": True,
                "not_a_hard_capacity_gate": True,
            }
        )
    return records


def validate_outputs(
    *,
    splits: pd.DataFrame,
    capacity: pd.DataFrame,
    catalogs: Mapping[str, pd.DataFrame],
    original_splits: Mapping[str, pd.DataFrame],
    ordinary_folds: Mapping[str, pd.DataFrame],
    baseline_group: str,
    sources: Mapping[str, Mapping[str, str]],
) -> dict:
    if list(splits.columns) != SPLIT_COLUMNS or list(capacity.columns) != CAPACITY_COLUMNS:
        raise ValueError("Output schema or column order mismatch")
    if capacity.unit_id.duplicated().any():
        raise ValueError("Duplicate capacity unit")
    if set(splits.unit_id) != set(capacity.loc[capacity.valid, "unit_id"]):
        raise ValueError("Valid units and split rows differ")
    if not splits.fold.eq(splits.unit_id).all():
        raise ValueError("fold must be the globally unique unit_id")
    if not set(splits.role) <= ROLES:
        raise ValueError("Unknown split role")
    leakage_checks = 0
    for scope in original_splits:
        if scope not in SCOPES:
            raise ValueError(f"Unknown scope {scope}")
        original = original_splits[scope]
        catalog = catalogs[scope]
        ordinary = ordinary_folds[scope]
        source = sources[scope]
        fit_roles = SCOPES[scope]
        scope_rows = splits[splits.scope.eq(scope)]
        scope_capacity = capacity[capacity.scope.eq(scope)]
        original_fit = original[original.role.isin(fit_roles)]
        eligible = original_fit[original_fit.design_group_id.ne(baseline_group)]
        for unit in scope_capacity.itertuples(index=False):
            rows = scope_rows[scope_rows.unit_id.eq(unit.unit_id)]
            if not unit.valid:
                if not rows.empty:
                    raise ValueError(f"Invalid unit has split rows: {unit.unit_id}")
                continue
            if len(rows) != len(original) or rows.structure_id.duplicated().any():
                raise ValueError(f"Fold does not cover the unique catalog: {unit.unit_id}")
            if set(rows.structure_id) != set(original.structure_id):
                raise ValueError(f"Fold catalog mismatch: {unit.unit_id}")
            by_id = rows.set_index("structure_id")
            original_by_id = original.set_index("structure_id")
            if not by_id.original_role.equals(original_by_id.role):
                raise ValueError(f"Original role reference changed: {unit.unit_id}")
            for column in ["design_group_id", "branch_id", *GEOMETRY]:
                if not by_id[column].equals(original_by_id[column]):
                    raise ValueError(f"Identity or geometry changed: {unit.unit_id}:{column}")
            if not by_id.source_split_sha256.eq(source["split_sha256"]).all():
                raise ValueError(f"Source split hash reference changed: {unit.unit_id}")
            if not by_id.source_structure_sha256.eq(source["structure_sha256"]).all():
                raise ValueError(f"Source structure hash reference changed: {unit.unit_id}")
            fit_groups = set(by_id.loc[by_id.role.eq("fit"), "design_group_id"])
            evaluation_groups = set(by_id.loc[by_id.role.eq("evaluation"), "design_group_id"])
            if fit_groups & evaluation_groups:
                raise ValueError(f"Fit/evaluation design-group leakage: {unit.unit_id}")
            leakage_checks += 1
            for role in (
                "embargo",
                "outer_calibration",
                "outer_test",
                "outer_embargo",
                "baseline_only",
            ):
                other = set(by_id.loc[by_id.role.eq(role), "design_group_id"])
                if fit_groups & other:
                    raise ValueError(f"Fit/{role} design-group leakage: {unit.unit_id}")
                leakage_checks += 1
            if evaluation_groups & set(
                by_id.loc[by_id.role.str.startswith("outer_"), "design_group_id"]
            ):
                raise ValueError(f"Inner evaluation touched outer holdout: {unit.unit_id}")
            baseline_rows = by_id[by_id.design_group_id.eq(baseline_group)]
            if not baseline_rows.role.eq("baseline_only").all():
                raise ValueError(f"Baseline group escaped baseline_only: {unit.unit_id}")
            if not by_id.loc[by_id.design_group_id.eq(baseline_group), "baseline_usage"].eq(
                "final_difference_only"
            ).all():
                raise ValueError(f"Baseline usage rule missing: {unit.unit_id}")
            non_baseline = by_id[~by_id.design_group_id.eq(baseline_group)]
            original_outer = non_baseline[
                non_baseline.original_partition.eq("original_outer")
            ]
            expected_outer = "outer_" + original_outer.original_role
            if not original_outer.role.equals(expected_outer):
                raise ValueError(f"Outer role changed: {unit.unit_id}")
            evaluation = by_id[by_id.role.eq("evaluation")]
            expected_group_count = unit.independent_evaluation_design_group_count
            if evaluation.design_group_id.nunique() != expected_group_count:
                raise ValueError(f"Evaluation group capacity mismatch: {unit.unit_id}")
            if unit.family != "ordinary":
                if not evaluation.branch_id.eq(unit.branch_id).all():
                    raise ValueError(f"Boundary branch mismatch: {unit.unit_id}")
                if not evaluation.has_pf.eq(0).all():
                    raise ValueError(f"Non-PF0 boundary evaluation: {unit.unit_id}")
                target_fit = by_id[by_id.role.eq("fit") & by_id.branch_id.eq(unit.branch_id)]
                if target_fit.design_group_id.nunique() < MIN_FIT_GROUPS:
                    raise ValueError(f"Insufficient fit groups: {unit.unit_id}")
                if unit.family in {"eg_upper", "joint_eg_upper_fl_lower"}:
                    held = _parse_levels(unit.eg_held_out_levels)
                    remaining = _parse_levels(unit.eg_fit_remaining_levels)
                    if len(remaining) < MIN_FIT_LEVELS or not held:
                        raise ValueError(f"Insufficient EG fit levels: {unit.unit_id}")
                    if not evaluation.eg_um.isin(held).all():
                        raise ValueError(f"EG evaluation levels mismatch: {unit.unit_id}")
                    if target_fit.eg_um.isin(held).any() or max(remaining) >= min(held):
                        raise ValueError(f"EG upper direction mismatch: {unit.unit_id}")
                if unit.family in {"fl_lower", "joint_eg_upper_fl_lower"}:
                    held = _parse_levels(unit.fl_held_out_levels)
                    remaining = _parse_levels(unit.fl_fit_remaining_levels)
                    if len(remaining) < MIN_FIT_LEVELS or not held:
                        raise ValueError(f"Insufficient FL fit levels: {unit.unit_id}")
                    if not evaluation.fl_um.isin(held).all():
                        raise ValueError(f"FL evaluation levels mismatch: {unit.unit_id}")
                    if target_fit.fl_um.isin(held).any() or max(held) >= min(remaining):
                        raise ValueError(f"FL lower direction mismatch: {unit.unit_id}")
        ordinary_rows = scope_rows[scope_rows.family.eq("ordinary")]
        ordinary_evaluation = ordinary_rows[ordinary_rows.role.eq("evaluation")]
        if ordinary_evaluation.structure_id.duplicated().any():
            raise ValueError(f"Repeated ordinary evaluation structure: {scope}")
        if set(ordinary_evaluation.structure_id) != set(eligible.structure_id):
            raise ValueError(f"Ordinary evaluation coverage mismatch: {scope}")
        expected_fold = dict(zip(ordinary.structure_id, ordinary.fold.astype(int), strict=True))
        for row in ordinary_evaluation.itertuples(index=False):
            if row.source_fold_index != expected_fold[row.structure_id]:
                raise ValueError(f"P6C fold reference changed: {scope}:{row.structure_id}")
        for fold_index in range(4):
            unit = _unit_id(scope, "ordinary", "all_branches", f"fold_{fold_index}")
            if unit not in set(scope_capacity.loc[scope_capacity.valid, "unit_id"]):
                raise ValueError(f"Missing ordinary fold: {unit}")
        if len(catalog) != len(original):
            raise ValueError(f"Catalog size mismatch: {scope}")
    aggregates = aggregate_hard_capacity(splits, capacity)
    return {
        "status": "PASS",
        "split_row_count": len(splits),
        "capacity_row_count": len(capacity),
        "valid_unit_count": int(capacity.valid.sum()),
        "invalid_unit_count": int((~capacity.valid).sum()),
        "ordinary_unit_count": int((capacity.valid & capacity.family.eq("ordinary")).sum()),
        "valid_boundary_unit_count": int((capacity.valid & ~capacity.family.eq("ordinary")).sum()),
        "fit_evaluation_leak_count": 0,
        "fit_outer_or_embargo_leak_count": 0,
        "group_leak_checks": leakage_checks,
        "baseline_fit_or_inner_evaluation_count": 0,
        "hard_capacity": aggregates,
        "hard_capacity_met_count": sum(record["meets_hard_capacity"] for record in aggregates),
        "hard_capacity_total_count": len(aggregates),
        "pooled_descriptive_capacity": pooled_descriptive_capacity(splits, capacity),
    }


def write_csv_outputs(
    splits: pd.DataFrame,
    capacity: pd.DataFrame,
    output_directory: Path,
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    split_path = output_directory / "splits.csv"
    capacity_path = output_directory / "capacity.csv"
    splits.to_csv(split_path, index=False, lineterminator="\n")
    capacity.to_csv(capacity_path, index=False, lineterminator="\n")
    return split_path, capacity_path


EVALUATION_ROW_KEYS = (
    "unit_id",
    "structure_id",
    "design_group_id",
    "scope",
    "fold",
    "role",
    "branch_id",
)


def unit_design_group_mae(rows: pd.DataFrame) -> dict:
    required = {
        "unit_id",
        "structure_id",
        "design_group_id",
        "role",
        "actual",
        "prediction",
    }
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ContractError("MISSING_EVALUATION_FIELD", f"Missing fields {missing}")
    if rows.empty:
        return {"status": "FAILED", "mae": None, "invalid_reason": "EMPTY_EVALUATION_UNIT"}
    if rows.unit_id.nunique() != 1:
        raise ContractError("UNIT_ID_MISMATCH", "A metric unit must have one unit_id")
    for field in ("structure_id", "design_group_id"):
        if rows[field].map(lambda value: not isinstance(value, str) or not value).any():
            raise ContractError("IDENTITY_REQUIRED", f"{field} must be nonempty")
    if rows.structure_id.duplicated().any():
        raise ContractError("DUPLICATE_EVALUATION_STRUCTURE", "Duplicate structure in unit")
    if not rows.role.eq("evaluation").all():
        raise ContractError("EVALUATION_ROLE_REQUIRED", "Metrics require evaluation rows only")
    if "target" in rows and not rows.target.isin(["k2eff_pct", "k2eff_pct_native"]).all():
        raise ContractError("TARGET_MISMATCH", "MAE requires k2eff_pct or M2 native k2")
    if "unit" in rows and not rows.unit.eq("percent_number").all():
        raise ContractError("UNIT_MISMATCH", "k2 values must be percent numbers")
    actual = pd.to_numeric(rows.actual, errors="coerce")
    prediction = pd.to_numeric(rows.prediction, errors="coerce")
    actual_invalid = ~np_finite_series(actual) | actual.le(0) | actual.ge(100)
    if actual_invalid.any():
        return {
            "unit_id": rows.unit_id.iloc[0],
            "status": "FAILED",
            "mae": None,
            "invalid_reason": "ACTUAL_INVALID",
            "n_structures": len(rows),
            "n_design_groups": rows.design_group_id.nunique(),
        }
    prediction_invalid = ~np_finite_series(prediction) | prediction.le(0) | prediction.ge(100)
    if prediction_invalid.any():
        return {
            "unit_id": rows.unit_id.iloc[0],
            "status": "FAILED",
            "mae": None,
            "invalid_reason": "MISSING_OR_INVALID_PREDICTION",
            "n_structures": len(rows),
            "n_design_groups": rows.design_group_id.nunique(),
        }
    absolute_error = (prediction - actual).abs()
    group_errors = absolute_error.groupby(rows.design_group_id).mean()
    return {
        "unit_id": rows.unit_id.iloc[0],
        "status": "PASS",
        "mae": float(group_errors.mean()),
        "invalid_reason": None,
        "n_structures": len(rows),
        "n_design_groups": len(group_errors),
        "error_unit": "percentage_points",
    }


def np_finite_series(values: pd.Series) -> pd.Series:
    return values.map(math.isfinite)


def _valid_capacity_mask(values: pd.Series) -> pd.Series:
    return values.map(lambda value: value is True or str(value) == "True")


def _failed_metric_bundle(unit_metrics: list[dict], reasons: list[str]) -> dict:
    return {
        "status": "FAILED",
        "ordinary_mae": None,
        "boundary_mae": None,
        "worst_unit_mae": None,
        "worst_unit_id": None,
        "unit_metrics": unit_metrics,
        "ordinary_scope_metrics": [],
        "boundary_branch_metrics": [],
        "boundary_family_metrics": [],
        "boundary_scope_metrics": [],
        "invalid_reasons": sorted(set(reasons)),
    }


def evaluate_metric_bundle(rows: pd.DataFrame, capacity: pd.DataFrame) -> dict:
    required_rows = {*EVALUATION_ROW_KEYS, "actual", "prediction", "family"}
    missing_rows = sorted(required_rows - set(rows.columns))
    if missing_rows:
        raise ContractError("MISSING_EVALUATION_FIELD", f"Missing fields {missing_rows}")
    required_capacity = {"unit_id", "scope", "family", "branch_id", "valid"}
    if not required_capacity <= set(capacity.columns):
        raise ContractError("CAPACITY_SCHEMA_MISMATCH", "Missing capacity columns")
    if capacity.unit_id.duplicated().any():
        raise ContractError("DUPLICATE_CAPACITY_UNIT", "Duplicate capacity unit")
    valid_capacity = capacity[_valid_capacity_mask(capacity.valid)].copy()
    expected_units = set(valid_capacity.unit_id)
    if not expected_units:
        return _failed_metric_bundle([], ["NO_VALID_EVALUATION_UNIT"])
    if not rows.role.eq("evaluation").all():
        raise ContractError("EVALUATION_ROLE_REQUIRED", "Metrics require evaluation rows only")
    unexpected = set(rows.unit_id) - expected_units
    if unexpected:
        units = sorted(unexpected)
        raise ContractError("INVALID_UNIT_SCORE_FORBIDDEN", f"Unexpected units {units}")
    unit_metrics = []
    reasons = []
    for unit in valid_capacity.itertuples(index=False):
        unit_rows = rows[rows.unit_id.eq(unit.unit_id)]
        if unit_rows.empty:
            reasons.append(f"MISSING_EVALUATION_UNIT:{unit.unit_id}")
            unit_metrics.append(
                {
                    "unit_id": unit.unit_id,
                    "scope": unit.scope,
                    "family": unit.family,
                    "branch_id": unit.branch_id,
                    "status": "FAILED",
                    "mae": None,
                    "invalid_reason": "MISSING_EVALUATION_UNIT",
                }
            )
            continue
        if not unit_rows.fold.eq(unit.unit_id).all():
            raise ContractError("FOLD_UNIT_MISMATCH", f"fold differs from {unit.unit_id}")
        if not unit_rows.scope.eq(unit.scope).all() or not unit_rows.family.eq(unit.family).all():
            raise ContractError("UNIT_METADATA_MISMATCH", f"Metadata differs for {unit.unit_id}")
        if unit.family != "ordinary":
            if not unit_rows.branch_id.eq(unit.branch_id).all():
                raise ContractError(
                    "BOUNDARY_BRANCH_MISMATCH", f"Branch differs for {unit.unit_id}"
                )
            if "has_pf" in unit_rows and not unit_rows.has_pf.eq(0).all():
                raise ContractError("PF0_BOUNDARY_REQUIRED", "Boundary metrics require PF=0")
        metric = unit_design_group_mae(unit_rows)
        metric.update({"scope": unit.scope, "family": unit.family, "branch_id": unit.branch_id})
        if metric["status"] != "PASS":
            reasons.append(f"{metric['invalid_reason']}:{unit.unit_id}")
        unit_metrics.append(metric)
    if reasons:
        return _failed_metric_bundle(unit_metrics, reasons)
    ordinary_scope_metrics = []
    for scope in SCOPES:
        scope_rows = rows[rows.scope.eq(scope) & rows.family.eq("ordinary")]
        if scope_rows.empty:
            return _failed_metric_bundle(unit_metrics, [f"MISSING_ORDINARY_SCOPE:{scope}"])
        absolute_error = (
            pd.to_numeric(scope_rows.prediction, errors="coerce")
            - pd.to_numeric(scope_rows.actual, errors="coerce")
        ).abs()
        group_errors = absolute_error.groupby(scope_rows.design_group_id).mean()
        ordinary_scope_metrics.append(
            {
                "scope": scope,
                "mae": float(group_errors.mean()),
                "unit_count": scope_rows.unit_id.nunique(),
                "design_group_count": len(group_errors),
            }
        )
    boundary_branch_metrics = []
    for scope in SCOPES:
        for family in SUPPORTED_BOUNDARIES:
            branches = sorted(
                {
                    row["branch_id"]
                    for row in unit_metrics
                    if row["scope"] == scope
                    and row["family"] == family
                    and row["family"] != "ordinary"
                }
            )
            for branch in branches:
                values = [
                    row["mae"]
                    for row in unit_metrics
                    if row["scope"] == scope
                    and row["family"] == family
                    and row["branch_id"] == branch
                ]
                boundary_branch_metrics.append(
                    {
                        "scope": scope,
                        "family": family,
                        "branch_id": branch,
                        "mae": float(sum(values) / len(values)),
                        "unit_count": len(values),
                    }
                )
    boundary_family_metrics = []
    for scope in SCOPES:
        for family in SUPPORTED_BOUNDARIES:
            values = [
                row["mae"]
                for row in boundary_branch_metrics
                if row["scope"] == scope and row["family"] == family
            ]
            if values:
                boundary_family_metrics.append(
                    {
                        "scope": scope,
                        "family": family,
                        "mae": float(sum(values) / len(values)),
                        "branch_count": len(values),
                    }
                )
    boundary_scope_metrics = []
    for scope in SCOPES:
        values = [row["mae"] for row in boundary_family_metrics if row["scope"] == scope]
        if not values:
            return _failed_metric_bundle(unit_metrics, [f"MISSING_BOUNDARY_SCOPE:{scope}"])
        boundary_scope_metrics.append(
            {"scope": scope, "mae": float(sum(values) / len(values)), "family_count": len(values)}
        )
    boundary_units = [row for row in unit_metrics if row["family"] != "ordinary"]
    worst = max(boundary_units, key=lambda row: (row["mae"], row["unit_id"]))
    return {
        "status": "PASS",
        "ordinary_mae": float(
            sum(row["mae"] for row in ordinary_scope_metrics) / len(ordinary_scope_metrics)
        ),
        "boundary_mae": float(
            sum(row["mae"] for row in boundary_scope_metrics) / len(boundary_scope_metrics)
        ),
        "worst_unit_mae": worst["mae"],
        "worst_unit_id": worst["unit_id"],
        "unit_metrics": unit_metrics,
        "ordinary_scope_metrics": ordinary_scope_metrics,
        "boundary_branch_metrics": boundary_branch_metrics,
        "boundary_family_metrics": boundary_family_metrics,
        "boundary_scope_metrics": boundary_scope_metrics,
        "ordinary_overall_scope_count": len(ordinary_scope_metrics),
        "boundary_overall_scope_count": len(boundary_scope_metrics),
        "evaluation_row_count": len(rows),
        "evaluation_unit_count": len(unit_metrics),
        "invalid_reasons": [],
    }


def evaluation_row_differences(candidate: pd.DataFrame, control: pd.DataFrame) -> list[dict]:
    required = set(EVALUATION_ROW_KEYS)
    for name, frame in (("candidate", candidate), ("control", control)):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ContractError("MISSING_EVALUATION_FIELD", f"{name} missing {missing}")
        if frame.duplicated(subset=list(EVALUATION_ROW_KEYS)).any():
            raise ContractError("DUPLICATE_EVALUATION_ROW", f"Duplicate {name} evaluation rows")
    candidate_keys = set(candidate[list(EVALUATION_ROW_KEYS)].itertuples(index=False, name=None))
    control_keys = set(control[list(EVALUATION_ROW_KEYS)].itertuples(index=False, name=None))
    differences = []
    for row in sorted(candidate_keys - control_keys):
        differences.append({"side": "candidate_only", "row": row})
    for row in sorted(control_keys - candidate_keys):
        differences.append({"side": "control_only", "row": row})
    shared = candidate_keys & control_keys
    if "actual" in candidate.columns and "actual" in control.columns:
        left_actual = candidate.set_index(list(EVALUATION_ROW_KEYS)).actual.to_dict()
        right_actual = control.set_index(list(EVALUATION_ROW_KEYS)).actual.to_dict()
        for key in shared:
            if left_actual[key] != right_actual[key]:
                differences.append({"side": "actual_mismatch", "row": key})
    for field in ("source_split_sha256", "source_structure_sha256"):
        if field not in candidate.columns or field not in control.columns:
            differences.append(
                {
                    "side": "source_hash_coverage_mismatch",
                    "field": field,
                    "candidate_present": field in candidate.columns,
                    "control_present": field in control.columns,
                }
            )
            continue
        left = candidate.set_index(list(EVALUATION_ROW_KEYS))[field].to_dict()
        right = control.set_index(list(EVALUATION_ROW_KEYS))[field].to_dict()
        for key in shared:
            if left[key] != right[key]:
                differences.append({"side": "source_hash_mismatch", "row": key, "field": field})
    return differences


def assert_same_evaluation_rows(candidate: pd.DataFrame, control: pd.DataFrame) -> None:
    differences = evaluation_row_differences(candidate, control)
    if differences:
        raise ContractError(
            "SAME_EVALUATION_ROWS_REQUIRED",
            f"Candidate/control evaluation rows differ: {len(differences)}",
        )


def threshold_at_most(value: float, limit: float, protocol: Mapping) -> dict:
    tolerances = protocol["numerical_reproduction"]
    tolerance = tolerances["prediction_atol"] + tolerances["prediction_rtol"] * abs(limit)
    difference = abs(value - limit)
    if not math.isfinite(value) or not math.isfinite(limit):
        return {"status": "FAILED", "difference": None, "tolerance": tolerance}
    if difference <= tolerance:
        return {"status": "numerical_critical", "difference": difference, "tolerance": tolerance}
    return {
        "status": "PASS" if value <= limit else "FAILED",
        "difference": difference,
        "tolerance": tolerance,
    }


def _acceptance_fraction(value: float | str) -> float:
    if isinstance(value, str):
        match = re.fullmatch(
            r"candidate\s*<=\s*([0-9]+(?:\.[0-9]+)?)\s*\*\s*P6C", value
        )
        if match is None:
            raise ContractError("ACCEPTANCE_RULE_UNKNOWN", f"Unknown acceptance rule {value}")
        return float(match.group(1))
    return float(value)


def _acceptance_condition(
    candidate: float,
    control: float,
    fraction: float | str,
    protocol: Mapping,
    boundary: bool,
) -> dict:
    if not math.isfinite(candidate) or not math.isfinite(control) or control < 0 or candidate < 0:
        return {"status": "FAILED", "ratio": None, "limit": None}
    if control == 0:
        if boundary:
            return {
                "status": "evidence_insufficient",
                "ratio": None,
                "limit": None,
                "reason": "ZERO_CONTROL_RELATIVE_IMPROVEMENT_UNDEFINED",
            }
        return {
            "status": "PASS" if candidate == 0 else "FAILED",
            "ratio": None,
            "limit": 0.0,
            "reason": "ZERO_CONTROL_REQUIRES_ZERO_CANDIDATE",
        }
    limit = _acceptance_fraction(fraction) * control
    result = threshold_at_most(candidate, limit, protocol)
    return {
        **result,
        "ratio": candidate / control,
        "limit": limit,
        "candidate": candidate,
        "control": control,
    }


def evaluate_acceptance(
    candidate: Mapping,
    control: Mapping,
    protocol: Mapping,
    model_family: str = "M1",
) -> dict:
    acceptance = protocol["evaluation"]["acceptance"]
    if candidate.get("status") != "PASS" or control.get("status") != "PASS":
        return {
            "status": "FAILED",
            "qualified": False,
            "reason": "INVALID_OR_INCOMPLETE_METRIC_BUNDLE",
            "conditions": {},
        }
    conditions = {
        "boundary_improvement": _acceptance_condition(
            candidate["boundary_mae"],
            control["boundary_mae"],
            acceptance["boundary_improvement_fraction"],
            protocol,
            boundary=True,
        ),
        "ordinary_noninferiority": _acceptance_condition(
            candidate["ordinary_mae"],
            control["ordinary_mae"],
            acceptance["ordinary_noninferiority_fraction"],
            protocol,
            boundary=False,
        ),
        "worst_unit_noninferiority": _acceptance_condition(
            candidate["worst_unit_mae"],
            control["worst_unit_mae"],
            acceptance["worst_unit_noninferiority_fraction"],
            protocol,
            boundary=False,
        ),
    }
    all_pass = all(condition["status"] == "PASS" for condition in conditions.values())
    if model_family == "M2":
        return {
            "status": "REJECTED",
            "qualified": False,
            "reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
            "aggregation_estimand_match": False,
            "eligibility_override_allowed": False,
            "conditions": conditions,
        }
    any_failed = any(condition["status"] == "FAILED" for condition in conditions.values())
    return {
        "status": "PASS" if all_pass else "REJECTED" if any_failed else "evidence_insufficient",
        "qualified": all_pass,
        "reason": None if all_pass else "ACCEPTANCE_CONDITION_NOT_MET",
        "aggregation_estimand_match": True,
        "eligibility_override_allowed": False,
        "conditions": conditions,
    }


DEFAULT_O05_SPLITS_PATH = (
    Path(__file__).resolve().parents[3]
    / "surrogate_data/optimization_p13_20261027_r01/evaluation/splits.csv"
)


def frozen_o05_evaluation_rows(path: Path | None = None) -> pd.DataFrame:
    path = path or DEFAULT_O05_SPLITS_PATH
    if sha256_file(path) != O05_SPLITS_SHA256:
        raise ContractError("O05_HASH_MISMATCH", f"O05 splits hash mismatch: {path}")
    rows = pd.read_csv(path)
    evaluation = rows[rows.role.eq("evaluation")].copy()
    if len(evaluation) != 2882:
        raise ContractError("COMPLETE_O05_ROWS_REQUIRED", "Frozen O05 evaluation count changed")
    return evaluation


def assert_complete_evaluation_rows(
    rows: pd.DataFrame, required_evaluation_rows: pd.DataFrame
) -> None:
    for name, frame in (("candidate", rows), ("required", required_evaluation_rows)):
        missing = sorted(set(EVALUATION_ROW_KEYS) - set(frame.columns))
        if missing:
            raise ContractError("MISSING_EVALUATION_FIELD", f"{name} missing {missing}")
        if frame.duplicated(subset=list(EVALUATION_ROW_KEYS)).any():
            raise ContractError("DUPLICATE_EVALUATION_ROW", f"Duplicate {name} evaluation rows")
    actual = set(rows[list(EVALUATION_ROW_KEYS)].itertuples(index=False, name=None))
    required = set(
        required_evaluation_rows[list(EVALUATION_ROW_KEYS)].itertuples(index=False, name=None)
    )
    if actual != required:
        raise ContractError(
            "COMPLETE_O05_ROWS_REQUIRED",
            f"Required {len(required)} rows, actual {len(actual)}, "
            f"missing {len(required - actual)}, unexpected {len(actual - required)}",
        )


def compare_candidate_to_p6c(
    candidate_rows: pd.DataFrame,
    control_rows: pd.DataFrame,
    capacity: pd.DataFrame,
    protocol: Mapping,
    model_family: str = "M1",
    required_evaluation_rows: pd.DataFrame | None = None,
) -> dict:
    from app.ml.optimization_p13.contracts import p6c_control_spec

    spec = p6c_control_spec(protocol)
    if required_evaluation_rows is None:
        required_evaluation_rows = frozen_o05_evaluation_rows()
    assert_same_evaluation_rows(candidate_rows, control_rows)
    assert_complete_evaluation_rows(candidate_rows, required_evaluation_rows)
    if "target" in candidate_rows.columns:
        targets = set(candidate_rows.target)
        if targets == {"k2eff_pct_native"} and model_family != "M2":
            raise ContractError("TARGET_MISMATCH", "Native k2 is M2 side evidence only")
    candidate = evaluate_metric_bundle(candidate_rows, capacity)
    control = evaluate_metric_bundle(control_rows, capacity)
    return {
        "same_evaluation_rows": True,
        "complete_o05_rows": True,
        "required_evaluation_row_count": len(required_evaluation_rows),
        "p6c_recipe": spec.recipe,
        "candidate": candidate,
        "control": control,
        "acceptance": evaluate_acceptance(candidate, control, protocol, model_family),
    }
