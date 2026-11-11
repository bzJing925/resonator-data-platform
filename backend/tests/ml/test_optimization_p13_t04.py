from __future__ import annotations

import pandas as pd
import pytest

from app.ml.optimization_p13.evaluation import (
    aggregate_hard_capacity,
    build_scope_splits,
    validate_outputs,
)

BASELINE = "g00"
SOURCE = {
    "split_file_id": "P3_SPLIT_STRUCTURE_HOLDOUT",
    "split_sha256": "a" * 64,
    "fold_file_id": "P6C_STRUCTURE_HOLDOUT_INNER_FOLDS",
    "fold_sha256": "b" * 64,
    "structure_file_id": "P3_STRUCTURES",
    "structure_sha256": "c" * 64,
}


def inputs(specs, scope="structure_holdout"):
    catalog_rows, split_rows, fold_rows = [], [], []
    fit_roles = ("train", "validation") if scope == "structure_holdout" else ("train",)
    for group, eg, fl, branch, role, fold in specs:
        for port, area in (("S11", 700.0), ("S22", 5500.0)):
            structure = f"{group}_{port}"
            row = {
                "structure_id": structure,
                "design_group_id": group,
                "branch_id": branch,
                "eg_um": float(eg),
                "fl_um": float(fl),
                "area_um2": area,
                "has_pf": 0,
            }
            catalog_rows.append(row)
            split_rows.append(row | {"role": role})
            if role in fit_roles:
                fold_rows.append(
                    {
                        "structure_id": structure,
                        "design_group_id": group,
                        "branch_id": branch,
                        "fold": fold,
                    }
                )
    source = SOURCE | {
        "split_file_id": f"P3_SPLIT_{scope.upper()}",
        "fold_file_id": f"P6C_{scope.upper()}_INNER_FOLDS",
    }
    return (
        pd.DataFrame(catalog_rows),
        pd.DataFrame(split_rows),
        pd.DataFrame(fold_rows),
        source,
    )


def ordinary_specs():
    return [
        (BASELINE, 2.0, 0.5, "pf0_eg1_fl1", "train", 0),
        ("g01", 0.75, 0.5, "pf0_eg1_fl1", "train", 0),
        ("g02", 1.0, 0.5, "pf0_eg1_fl1", "validation", 1),
        ("g03", 1.5, 0.5, "pf0_eg1_fl1", "train", 2),
        ("g04", 2.0, 0.5, "pf0_eg1_fl1", "train", 3),
        ("g05", 0.75, 1.0, "pf0_eg1_fl1", "train", 0),
        ("g06", 1.0, 1.0, "pf0_eg1_fl1", "calibration", None),
        ("g07", 1.5, 1.0, "pf0_eg1_fl1", "test", None),
    ]


def build(scope="structure_holdout", specs=None):
    catalog, split, folds, source = inputs(specs or ordinary_specs(), scope)
    splits, capacity = build_scope_splits(
        scope=scope,
        catalog=catalog,
        split=split,
        ordinary_folds=folds,
        baseline_group=BASELINE,
        source=source,
    )
    return catalog, split, folds, source, splits, capacity


def validator_inputs(catalog, split, folds, source, scope="structure_holdout"):
    return {
        "catalogs": {scope: catalog},
        "original_splits": {scope: split},
        "ordinary_folds": {scope: folds},
        "baseline_group": BASELINE,
        "sources": {scope: source},
    }


def test_ordinary_folds_preserve_groups_outer_roles_and_baseline_exclusion():
    catalog, split, folds, source, splits, capacity = build()
    ordinary = capacity[capacity.family.eq("ordinary")]
    assert ordinary.valid.all()
    assert len(ordinary) == 4
    for unit in ordinary.unit_id:
        rows = splits[splits.unit_id.eq(unit)]
        fit_groups = set(rows.loc[rows.role.eq("fit"), "design_group_id"])
        evaluation_groups = set(rows.loc[rows.role.eq("evaluation"), "design_group_id"])
        assert not fit_groups & evaluation_groups
        assert set(rows.loc[rows.design_group_id.eq(BASELINE), "role"]) == {"baseline_only"}
        assert set(rows.loc[rows.design_group_id.eq(BASELINE), "baseline_usage"]) == {
            "final_difference_only"
        }
        assert set(rows.loc[rows.design_group_id.eq("g06"), "role"]) == {"outer_calibration"}
        assert set(rows.loc[rows.design_group_id.eq("g07"), "role"]) == {"outer_test"}
    evaluated = splits[splits.family.eq("ordinary") & splits.role.eq("evaluation")]
    assert BASELINE not in set(evaluated.design_group_id)
    assert set(evaluated.design_group_id) == {"g01", "g02", "g03", "g04", "g05"}
    assert not evaluated.structure_id.duplicated().any()
    report = validate_outputs(
        splits=splits,
        capacity=capacity,
        **validator_inputs(catalog, split, folds, source),
    )
    assert report["status"] == "PASS"
    assert report["fit_evaluation_leak_count"] == 0


def test_validator_rejects_design_group_leakage():
    catalog, split, folds, source, splits, capacity = build()
    unit = "structure_holdout__ordinary__all_branches__fold_1"
    rows = splits[splits.unit_id.eq(unit)]
    structure = rows.loc[rows.role.eq("fit"), "structure_id"].iloc[0]
    splits.loc[
        splits.unit_id.eq(unit) & splits.structure_id.eq(structure), "role"
    ] = "evaluation"
    with pytest.raises(ValueError, match="leakage|coverage mismatch"):
        validate_outputs(
            splits=splits,
            capacity=capacity,
            **validator_inputs(catalog, split, folds, source),
        )


def test_positive_eg_top_two_has_two_remaining_levels():
    specs = [("g00", 2.0, 0.0, "pf0_eg1_fl0", "train", 0)]
    specs += [
        (f"g{index:02d}", eg, 0.0, "pf0_eg1_fl0", "train", index % 4)
        for index, eg in enumerate(
            [0.75, 0.75, 1.0, 1.0, 1.5, 1.5, 2.0, 2.0], start=1
        )
    ]
    _, _, _, _, splits, capacity = build(specs=specs)
    unit = "structure_holdout__eg_upper__pf0_eg1_fl0__top_2"
    row = capacity.set_index("unit_id").loc[unit]
    assert row.valid
    assert row.eg_available_levels == "0.75;1;1.5;2"
    assert row.eg_held_out_levels == "1.5;2"
    assert row.eg_fit_remaining_levels == "0.75;1"
    assert row.eg_fit_remaining_level_count == 2
    rows = splits[splits.unit_id.eq(unit)]
    assert set(rows.loc[rows.role.eq("evaluation"), "eg_um"]) == {1.5, 2.0}
    assert BASELINE not in set(rows.loc[rows.role.eq("evaluation"), "design_group_id"])
    assert set(rows.loc[rows.role.eq("fit") & rows.branch_id.eq("pf0_eg1_fl0"), "eg_um"]) == {
        0.75,
        1.0,
    }


def test_eg_outer_scope_cannot_hold_two_of_three_fit_levels():
    specs = []
    for index, eg in enumerate([0.75, 0.75, 1.0, 1.0, 1.5, 1.5, 2.0, 2.0]):
        role = "test" if eg == 2.0 else "train"
        specs.append((f"g{index:02d}", eg, 0.0, "pf0_eg1_fl0", role, index % 4))
    specs[0] = (BASELINE, 0.75, 0.0, "pf0_eg1_fl0", "train", 0)
    _, _, _, _, splits, capacity = build(scope="eg_upper", specs=specs)
    unit = "eg_upper__eg_upper__pf0_eg1_fl0__top_2"
    row = capacity.set_index("unit_id").loc[unit]
    assert not row.valid
    assert "fewer_than_two_fit_levels__eg_um" in row.invalid_reason
    assert row.eg_fit_remaining_level_count == 1
    assert splits[splits.unit_id.eq(unit)].empty


def test_fl_zero_branch_is_invalid_not_a_positive_fl_lower_boundary():
    _, _, _, _, splits, capacity = build(
        specs=[
            ("g00", 2.0, 0.0, "pf0_eg1_fl0", "train", 0),
            *[
                (f"g{index:02d}", eg, 0.0, "pf0_eg1_fl0", "train", index % 4)
                for index, eg in enumerate([0.75, 1.0, 1.5, 2.0], start=1)
            ],
        ]
    )
    rows = capacity[capacity.family.eq("fl_lower")]
    assert not rows.valid.any()
    assert rows.invalid_reason.str.contains("supported_positive_pf0_branch").all()
    assert splits[splits.family.eq("fl_lower")].empty


def test_joint_boundary_scores_only_corner_and_embargoes_margins():
    specs = [(BASELINE, 2.0, 0.5, "pf0_eg1_fl1", "train", 0)]
    index = 1
    for eg in (0.75, 1.0, 2.0):
        for fl in (0.5, 1.0, 1.5):
            specs.append((f"g{index:02d}", eg, fl, "pf0_eg1_fl1", "train", index % 4))
            index += 1
    _, _, _, _, splits, capacity = build(specs=specs)
    unit = "structure_holdout__joint_eg_upper_fl_lower__pf0_eg1_fl1__top_1__bottom_1"
    assert capacity.set_index("unit_id").loc[unit, "valid"]
    rows = splits[splits.unit_id.eq(unit)]
    evaluation = rows[rows.role.eq("evaluation")]
    assert set(zip(evaluation.eg_um, evaluation.fl_um, strict=True)) == {(2.0, 0.5)}
    assert BASELINE not in set(evaluation.design_group_id)
    fit = rows[rows.role.eq("fit") & rows.branch_id.eq("pf0_eg1_fl1")]
    assert 2.0 not in set(fit.eg_um)
    assert 0.5 not in set(fit.fl_um)
    embargo = rows[rows.role.eq("embargo")]
    assert (2.0 in set(embargo.eg_um)) or (0.5 in set(embargo.fl_um))
    assert not set(fit.design_group_id) & set(evaluation.design_group_id)
    assert not set(fit.design_group_id) & set(embargo.design_group_id)


def test_baseline_only_overrides_even_an_original_outer_test_role():
    specs = ordinary_specs()
    specs[0] = (BASELINE, 2.0, 0.5, "pf0_eg1_fl1", "test", None)
    _, _, _, _, splits, _ = build(specs=specs)
    baseline_rows = splits[splits.design_group_id.eq(BASELINE)]
    assert set(baseline_rows.role) == {"baseline_only"}
    assert set(baseline_rows.original_role) == {"test"}
    assert set(baseline_rows.original_partition) == {"original_outer"}


def test_capacity_deduplicates_groups_and_does_not_relax_thresholds():
    unit1 = "structure_holdout__eg_upper__pf0_eg1_fl1__top_1"
    unit2 = "structure_holdout__eg_upper__pf0_eg1_fl1__top_2"
    capacity = pd.DataFrame(
        [
            {
                "unit_id": unit,
                "scope": "structure_holdout",
                "family": "eg_upper",
                "branch_id": "pf0_eg1_fl1",
                "valid": True,
                "invalid_reason": "",
            }
            for unit in (unit1, unit2)
        ]
    )

    def split_rows(groups_for_second):
        first = [
            {
                "unit_id": unit1,
                "role": "evaluation",
                "design_group_id": group,
                "structure_id": f"{group}_a",
            }
            for group in groups_for_second[:19]
        ]
        second = [
            {
                "unit_id": unit2,
                "role": "evaluation",
                "design_group_id": group,
                "structure_id": f"{group}_b",
            }
            for group in groups_for_second
        ]
        return pd.DataFrame([*first, *second])

    groups = [f"g{index:02d}" for index in range(20)]
    capacity_key = "structure_holdout__eg_upper__pf0_eg1_fl1"
    record = aggregate_hard_capacity(split_rows(groups[:19]), capacity)
    target = next(row for row in record if row["capacity_key"] == capacity_key)
    assert target["independent_design_group_count"] == 19
    assert target["valid_boundary_unit_count"] == 2
    assert not target["meets_20_independent_design_groups"]
    assert target["meets_2_valid_boundary_units"]
    assert not target["meets_hard_capacity"]
    record = aggregate_hard_capacity(split_rows(groups), capacity)
    target = next(row for row in record if row["capacity_key"] == capacity_key)
    assert target["independent_design_group_count"] == 20
    assert target["meets_hard_capacity"]


def test_required_provenance_columns_and_no_label_columns():
    _, _, _, _, splits, capacity = build()
    assert {
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
        "source_split_file_id",
        "source_split_sha256",
        "source_structure_file_id",
        "source_structure_sha256",
    } <= set(splits.columns)
    label_prefixes = ("k2eff", "prediction", "actual", "error")
    assert not any(column.startswith(label_prefixes) for column in splits)
    assert not any(column.startswith(label_prefixes) for column in capacity)


def test_verifier_creates_missing_attempt_parent(tmp_path, monkeypatch):
    import importlib.util
    import json
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts/verify_optimization_p13_t04.py"
    spec = importlib.util.spec_from_file_location("optimization_p13_t04_verifier", script)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    monkeypatch.setattr(
        verifier,
        "verify",
        lambda run, attempt: {"status": "PASS", "checks": {"synthetic": True}},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [str(script), "--run", str(tmp_path), "--attempt", "new_attempt"],
    )
    report_path = (
        tmp_path / "evaluation/t04_work/new_attempt/independence_report.json"
    )
    assert not report_path.parent.exists()
    verifier.main()
    assert json.loads(report_path.read_text())["checks"] == {"synthetic": True}
