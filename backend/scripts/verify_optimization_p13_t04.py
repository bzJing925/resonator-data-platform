from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

SCOPES = {
    "structure_holdout": {
        "fit_roles": ("train", "validation"),
        "split": "P3_SPLIT_STRUCTURE_HOLDOUT",
        "fold": "P6C_STRUCTURE_HOLDOUT_INNER_FOLDS",
    },
    "area_edges": {
        "fit_roles": ("train",),
        "split": "P3_SPLIT_AREA_EDGES",
        "fold": "P6C_AREA_EDGES_INNER_FOLDS",
    },
    "eg_upper": {
        "fit_roles": ("train",),
        "split": "P3_SPLIT_EG_UPPER",
        "fold": "P6C_EG_UPPER_INNER_FOLDS",
    },
    "fl_upper": {
        "fit_roles": ("train",),
        "split": "P3_SPLIT_FL_UPPER",
        "fold": "P6C_FL_UPPER_INNER_FOLDS",
    },
}
BRANCHES = ("pf0_eg1_fl0", "pf0_eg1_fl1", "pf0_eg0_fl1", "pf0_eg0_fl0")
SUPPORTED = {
    "eg_upper": ("pf0_eg1_fl0", "pf0_eg1_fl1"),
    "fl_lower": ("pf0_eg1_fl1", "pf0_eg0_fl1"),
    "joint_eg_upper_fl_lower": ("pf0_eg1_fl1",),
}
REQUIRED_SPLIT_COLUMNS = {
    "structure_id",
    "design_group_id",
    "scope",
    "fold",
    "role",
    "branch_id",
    "eg_um",
    "fl_um",
    "area_um2",
    "has_pf",
    "original_role",
    "source_split_file_id",
    "source_split_sha256",
    "source_structure_file_id",
    "source_structure_sha256",
}
REQUIRED_CAPACITY_COLUMNS = {
    "unit_id",
    "scope",
    "family",
    "branch_id",
    "valid",
    "invalid_reason",
    "independent_evaluation_design_group_count",
    "eg_available_levels",
    "eg_held_out_levels",
    "eg_fit_remaining_levels",
    "fl_available_levels",
    "fl_held_out_levels",
    "fl_fit_remaining_levels",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def level_text(values) -> str:
    return ";".join(format(float(value), ".15g") for value in sorted(float(v) for v in values))


def parse_levels(value: str) -> list[float]:
    return [float(item) for item in str(value).split(";") if item]


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if str(value) == "True":
        return True
    if str(value) == "False":
        return False
    raise ValueError(f"Invalid boolean {value!r}")


def verify(run: Path, attempt: str) -> dict:
    o01_path = run / "audit/input_manifest.json"
    o01 = json.loads(o01_path.read_text())
    allowed = set(o01["downstream_handoff"]["T04"]["file_ids"])
    registry = {entry["file_id"]: entry for entry in o01["inputs"]}
    used_ids = {"P3_STRUCTURES"}
    for scope in SCOPES.values():
        used_ids.update((scope["split"], scope["fold"]))
    for file_id in used_ids:
        if file_id not in allowed:
            raise ValueError(f"Unregistered T04 input: {file_id}")
        entry = registry[file_id]
        if sha256(Path(entry["absolute_path"])) != entry["sha256"]:
            raise ValueError(f"Input hash mismatch: {file_id}")
    split_path = run / "evaluation/splits.csv"
    capacity_path = run / "evaluation/capacity.csv"
    splits = pd.read_csv(split_path, keep_default_na=False)
    capacity = pd.read_csv(capacity_path, keep_default_na=False)
    if not REQUIRED_SPLIT_COLUMNS <= set(splits.columns):
        raise ValueError("Missing splits columns")
    if not REQUIRED_CAPACITY_COLUMNS <= set(capacity.columns):
        raise ValueError("Missing capacity columns")
    if capacity.unit_id.duplicated().any() or not splits.fold.eq(splits.unit_id).all():
        raise ValueError("Duplicate capacity units or nonunique fold IDs")
    if set(splits.unit_id) != {
        row.unit_id for row in capacity.itertuples(index=False) if as_bool(row.valid)
    }:
        raise ValueError("Valid capacity units differ from split units")
    structure_entry = registry["P3_STRUCTURES"]
    catalog = pd.read_csv(
        structure_entry["absolute_path"],
        usecols=[
            "structure_id",
            "design_group_id",
            "branch_id",
            "eg_um",
            "fl_um",
            "area_um2",
            "has_pf",
        ],
    )
    positive_eg = sorted(
        float(value) for value in catalog.loc[catalog.eg_um.gt(0), "eg_um"].unique()
    )
    if positive_eg != [0.75, 1.0, 1.5, 2.0]:
        raise ValueError("Positive EG catalog levels changed")
    baseline_group = o01["baseline"]["design_group_id"]
    role_mismatches = []
    capacity_mismatches = []
    leakage = []
    baseline_violations = []
    outer_violations = []
    source_violations = []
    checked_units = 0
    checked_invalid_units = 0
    expected_capacity_units = set()
    for scope, scope_info in SCOPES.items():
        split_entry = registry[scope_info["split"]]
        fold_entry = registry[scope_info["fold"]]
        original = pd.read_csv(split_entry["absolute_path"])
        p6c_folds = pd.read_csv(fold_entry["absolute_path"])
        fit_roles = scope_info["fit_roles"]
        original_fit = original[original.role.isin(fit_roles)]
        eligible = original_fit[original_fit.design_group_id.ne(baseline_group)]
        scope_splits = splits[splits.scope.eq(scope)]
        scope_capacity = capacity[capacity.scope.eq(scope)]
        expected_units = {
            f"{scope}__ordinary__all_branches__fold_{index}" for index in range(4)
        }
        for family in SUPPORTED:
            for branch in BRANCHES:
                for count in (1, 2):
                    variant = {
                        "eg_upper": f"top_{count}",
                        "fl_lower": f"bottom_{count}",
                        "joint_eg_upper_fl_lower": f"top_{count}__bottom_{count}",
                    }[family]
                    expected_units.add(f"{scope}__{family}__{branch}__{variant}")
        expected_capacity_units.update(expected_units)
        if set(scope_capacity.unit_id) != expected_units:
            raise ValueError(f"Candidate capacity unit coverage mismatch: {scope}")
        for unit in scope_capacity.itertuples(index=False):
            family = unit.family
            branch = unit.branch_id
            original_target = (
                original_fit
                if branch == "all_branches"
                else original_fit[original_fit.branch_id.eq(branch)]
            )
            eligible_target = (
                eligible if branch == "all_branches" else eligible[eligible.branch_id.eq(branch)]
            )
            if family == "ordinary":
                fold_index = int(unit.ordinary_fold_index)
                fold_by_structure = dict(
                    zip(p6c_folds.structure_id, p6c_folds.fold.astype(int), strict=True)
                )
                evaluation = eligible[eligible.structure_id.map(fold_by_structure).eq(fold_index)]
                held_groups = set(evaluation.design_group_id)
                fit = eligible[~eligible.design_group_id.isin(held_groups)]
                embargo = eligible.iloc[0:0]
                reasons = []
                if fit.design_group_id.nunique() < 2:
                    reasons.append("fewer_than_two_fit_design_groups")
                if evaluation.design_group_id.empty:
                    reasons.append("no_evaluation_design_group")
                for eval_branch in evaluation.branch_id.unique():
                    if fit[fit.branch_id.eq(eval_branch)].design_group_id.nunique() < 2:
                        reasons.append(f"fewer_than_two_fit_design_groups__{eval_branch}")
                held_eg, held_fl = [], []
            else:
                supported = branch in SUPPORTED[family] and not eligible_target.empty
                count = int(
                    unit.requested_eg_holdout_level_count
                    if family in {"eg_upper", "joint_eg_upper_fl_lower"}
                    else unit.requested_fl_holdout_level_count
                )
                held_eg = (
                    sorted(eligible_target.eg_um.unique())[-count:]
                    if supported and family in {"eg_upper", "joint_eg_upper_fl_lower"}
                    else []
                )
                held_fl = (
                    sorted(eligible_target.fl_um.unique())[:count]
                    if supported and family in {"fl_lower", "joint_eg_upper_fl_lower"}
                    else []
                )
                if supported and family == "eg_upper":
                    evaluation = eligible_target[eligible_target.eg_um.isin(held_eg)]
                    withheld = evaluation
                elif supported and family == "fl_lower":
                    evaluation = eligible_target[eligible_target.fl_um.isin(held_fl)]
                    withheld = evaluation
                elif supported:
                    eg_edge = eligible_target.eg_um.isin(held_eg)
                    fl_edge = eligible_target.fl_um.isin(held_fl)
                    evaluation = eligible_target[eg_edge & fl_edge]
                    withheld = eligible_target[eg_edge | fl_edge]
                else:
                    evaluation = eligible_target.iloc[0:0]
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
                    if (
                        family in {"eg_upper", "joint_eg_upper_fl_lower"}
                        and branch_fit.eg_um.nunique() < 2
                    ):
                        reasons.append("fewer_than_two_fit_levels__eg_um")
                    if (
                        family in {"fl_lower", "joint_eg_upper_fl_lower"}
                        and branch_fit.fl_um.nunique() < 2
                    ):
                        reasons.append("fewer_than_two_fit_levels__fl_um")
                    if branch_fit.design_group_id.nunique() < 2:
                        reasons.append("fewer_than_two_fit_design_groups")
                    if evaluation.design_group_id.nunique() < 1:
                        reasons.append("no_evaluation_design_group")
            expected_valid = not reasons
            if as_bool(unit.valid) != expected_valid or unit.invalid_reason != ";".join(reasons):
                capacity_mismatches.append((unit.unit_id, "validity", reasons, unit.invalid_reason))
            target_fit = fit if branch == "all_branches" else fit[fit.branch_id.eq(branch)]
            expected_counts = {
                "original_fit_structure_count": len(original_target),
                "original_fit_design_group_count": original_target.design_group_id.nunique(),
                "eligible_fit_structure_count": len(eligible_target),
                "eligible_fit_design_group_count": eligible_target.design_group_id.nunique(),
                "fit_structure_count": len(target_fit),
                "fit_design_group_count": target_fit.design_group_id.nunique(),
                "evaluation_structure_count": len(evaluation),
                "independent_evaluation_design_group_count": evaluation.design_group_id.nunique(),
                "embargo_structure_count": len(embargo),
                "embargo_design_group_count": embargo.design_group_id.nunique(),
                "all_branch_fit_structure_count": len(fit),
                "all_branch_fit_design_group_count": fit.design_group_id.nunique(),
                "baseline_excluded_structure_count": int(
                    original_target.design_group_id.eq(baseline_group).sum()
                ),
                "baseline_excluded_design_group_count": int(
                    original_target.design_group_id.eq(baseline_group).any()
                ),
                "eg_available_level_count": eligible_target.eg_um.nunique(),
                "eg_fit_remaining_level_count": target_fit.eg_um.nunique(),
                "fl_available_level_count": eligible_target.fl_um.nunique(),
                "fl_fit_remaining_level_count": target_fit.fl_um.nunique(),
            }
            for column, expected in expected_counts.items():
                if int(getattr(unit, column)) != int(expected):
                    actual = getattr(unit, column)
                    capacity_mismatches.append((unit.unit_id, column, expected, actual))
            expected_level_text = {
                "eg_available_levels": level_text(eligible_target.eg_um.unique()),
                "eg_held_out_levels": level_text(held_eg),
                "eg_fit_remaining_levels": level_text(target_fit.eg_um.unique()),
                "fl_available_levels": level_text(eligible_target.fl_um.unique()),
                "fl_held_out_levels": level_text(held_fl),
                "fl_fit_remaining_levels": level_text(target_fit.fl_um.unique()),
            }
            for column, expected in expected_level_text.items():
                if getattr(unit, column) != expected:
                    actual = getattr(unit, column)
                    capacity_mismatches.append((unit.unit_id, column, expected, actual))
            hard_flag = as_bool(unit.counts_toward_hard_capacity)
            if hard_flag != (expected_valid and family != "ordinary"):
                capacity_mismatches.append((unit.unit_id, "counts_toward_hard_capacity"))
            if (
                unit.source_split_file_id != scope_info["split"]
                or unit.source_split_sha256 != split_entry["sha256"]
            ):
                source_violations.append((unit.unit_id, "capacity_split_source"))
            if (
                unit.source_structure_file_id != "P3_STRUCTURES"
                or unit.source_structure_sha256 != structure_entry["sha256"]
            ):
                source_violations.append((unit.unit_id, "capacity_structure_source"))
            rows = scope_splits[scope_splits.unit_id.eq(unit.unit_id)]
            if not expected_valid:
                checked_invalid_units += 1
                if not rows.empty:
                    role_mismatches.append((unit.unit_id, "invalid unit has rows"))
                continue
            checked_units += 1
            if len(rows) != len(original) or set(rows.structure_id) != set(original.structure_id):
                role_mismatches.append((unit.unit_id, "catalog coverage"))
                continue
            actual_roles = {
                role: set(rows.loc[rows.role.eq(role), "structure_id"])
                for role in rows.role.unique()
            }
            baseline_ids = original.loc[
                original.design_group_id.eq(baseline_group), "structure_id"
            ]
            expected_roles = {
                "fit": set(fit.structure_id),
                "evaluation": set(evaluation.structure_id),
                "embargo": set(embargo.structure_id),
                "baseline_only": set(baseline_ids),
            }
            for role, expected in expected_roles.items():
                actual = actual_roles.get(role, set())
                if actual != expected:
                    role_mismatches.append((unit.unit_id, role, len(expected), len(actual)))
            for original_row in original.itertuples(index=False):
                if original_row.design_group_id == baseline_group:
                    continue
                expected_role = f"outer_{original_row.role}"
                if original_row.role in fit_roles:
                    continue
                actual = rows.loc[rows.structure_id.eq(original_row.structure_id), "role"]
                if len(actual) != 1 or actual.iloc[0] != expected_role:
                    outer_violations.append((unit.unit_id, original_row.structure_id))
            by_id = rows.set_index("structure_id")
            original_by_id = original.set_index("structure_id")
            if not by_id.original_role.equals(original_by_id.role):
                role_mismatches.append((unit.unit_id, "original_role"))
            identity_columns = (
                "design_group_id",
                "branch_id",
                "eg_um",
                "fl_um",
                "area_um2",
                "has_pf",
            )
            for column in identity_columns:
                if not by_id[column].equals(original_by_id[column]):
                    role_mismatches.append((unit.unit_id, f"identity:{column}"))
            fit_groups = set(rows.loc[rows.role.eq("fit"), "design_group_id"])
            nonfit_roles = (
                "evaluation",
                "embargo",
                "outer_calibration",
                "outer_test",
                "outer_embargo",
                "baseline_only",
            )
            for role in nonfit_roles:
                other = set(rows.loc[rows.role.eq(role), "design_group_id"])
                if fit_groups & other:
                    leakage.append((unit.unit_id, role, sorted(fit_groups & other)))
            baseline_rows = rows[rows.design_group_id.eq(baseline_group)]
            if (
                not baseline_rows.role.eq("baseline_only").all()
                or not baseline_rows.baseline_usage.eq("final_difference_only").all()
            ):
                baseline_violations.append(unit.unit_id)
            if (
                not rows.source_split_file_id.eq(scope_info["split"]).all()
                or not rows.source_split_sha256.eq(split_entry["sha256"]).all()
            ):
                source_violations.append((unit.unit_id, "split_source"))
            if (
                not rows.source_structure_file_id.eq("P3_STRUCTURES").all()
                or not rows.source_structure_sha256.eq(structure_entry["sha256"]).all()
            ):
                source_violations.append((unit.unit_id, "structure_source"))
            if family == "ordinary":
                if (
                    not rows.source_fold_file_id.eq(scope_info["fold"]).all()
                    or not rows.source_fold_sha256.eq(fold_entry["sha256"]).all()
                ):
                    source_violations.append((unit.unit_id, "fold_source"))
                expected_fold = dict(
                    zip(p6c_folds.structure_id, p6c_folds.fold.astype(int), strict=True)
                )
                for row in rows[rows.role.eq("evaluation")].itertuples(index=False):
                    if int(row.source_fold_index) != expected_fold[row.structure_id]:
                        role_mismatches.append((unit.unit_id, row.structure_id, "p6c_fold_index"))
        ordinary_evaluation = scope_splits[
            scope_splits.family.eq("ordinary") & scope_splits.role.eq("evaluation")
        ]
        if ordinary_evaluation.structure_id.duplicated().any() or set(
            ordinary_evaluation.structure_id
        ) != set(eligible.structure_id):
            role_mismatches.append((scope, "ordinary_evaluation_coverage"))
    if set(capacity.unit_id) != expected_capacity_units:
        raise ValueError("Global capacity candidate coverage mismatch")
    errors = {
        "role_mismatch_count": len(role_mismatches),
        "capacity_mismatch_count": len(capacity_mismatches),
        "group_leak_count": len(leakage),
        "baseline_violation_count": len(baseline_violations),
        "outer_role_violation_count": len(outer_violations),
        "source_reference_violation_count": len(source_violations),
    }
    hard_capacity = []
    for scope in SCOPES:
        for family, branches in SUPPORTED.items():
            for branch in branches:
                candidates = capacity[
                    capacity.scope.eq(scope)
                    & capacity.family.eq(family)
                    & capacity.branch_id.eq(branch)
                ]
                valid = candidates[candidates.valid.map(as_bool)]
                unit_ids = sorted(valid.unit_id)
                rows = splits[splits.unit_id.isin(unit_ids) & splits.role.eq("evaluation")]
                groups = sorted(rows.design_group_id.unique())
                hard_capacity.append(
                    {
                        "capacity_key": f"{scope}__{family}__{branch}",
                        "scope": scope,
                        "family": family,
                        "branch_id": branch,
                        "valid_boundary_unit_count": len(unit_ids),
                        "valid_boundary_unit_ids": unit_ids,
                        "independent_design_group_count": len(groups),
                        "design_group_ids": groups,
                        "evaluation_structure_count": rows.structure_id.nunique(),
                        "meets_20_independent_design_groups": len(groups) >= 20,
                        "meets_2_valid_boundary_units": len(unit_ids) >= 2,
                        "meets_hard_capacity": len(groups) >= 20 and len(unit_ids) >= 2,
                        "invalid_unit_reasons": sorted(
                            {
                                reason
                                for row in candidates.itertuples(index=False)
                                if not as_bool(row.valid)
                                for reason in [row.invalid_reason]
                                if reason
                            }
                        ),
                        "aggregation_rule": (
                            "Union design_group_id once across valid units within this "
                            "scope/family/branch; repeated folds never increment capacity."
                        ),
                    }
                )
    if any(errors.values()):
        raise ValueError(
            json.dumps(
                {
                    "errors": errors,
                    "role_examples": role_mismatches[:5],
                    "capacity_examples": capacity_mismatches[:5],
                    "leak_examples": leakage[:5],
                },
                ensure_ascii=False,
            )
        )
    return {
        "schema": "aln_optimization_p13_t04_independence_v1",
        "task_id": "T04",
        "attempt_id": attempt,
        "status": "PASS",
        "created_at": datetime.now(UTC).isoformat(),
        "checks": {
            **errors,
            "valid_units_recomputed": checked_units,
            "invalid_units_recomputed": checked_invalid_units,
            "split_row_count": len(splits),
            "capacity_row_count": len(capacity),
            "input_hashes_verified": len(used_ids) + 1,
            "positive_eg_levels": positive_eg,
            "baseline_rule_verified": True,
            "original_outer_roles_verified": True,
            "ordinary_p6c_folds_verified": True,
            "label_error_data_used": False,
        },
        "hard_capacity": hard_capacity,
        "output_sha256": {
            "evaluation/splits.csv": sha256(split_path),
            "evaluation/capacity.csv": sha256(capacity_path),
        },
        "o01_sha256": sha256(o01_path),
        "model_training_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--attempt", default="attempt_001")
    args = parser.parse_args()
    report_path = args.run / "evaluation/t04_work" / args.attempt / "independence_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = verify(args.run, args.attempt)
    except Exception as error:
        report = {
            "schema": "aln_optimization_p13_t04_independence_v1",
            "task_id": "T04",
            "attempt_id": args.attempt,
            "status": "FAILED",
            "created_at": datetime.now(UTC).isoformat(),
            "error": f"{type(error).__name__}: {error}",
        }
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        os.replace(temporary, report_path)
        raise
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, report_path)
    print(json.dumps({"status": report["status"], "checks": report["checks"]}, indent=2))


if __name__ == "__main__":
    main()
