"""Independent grouped-boundary and frozen-outer-prediction verification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_ablation import RECIPES
from app.ml.optimization_data import TARGETS, sha256, write_json


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    root = args.root
    out = root / "experiment"
    protected = json.loads((root / "pre_run_protected.json").read_text())
    for path, h in protected.items():
        assert sha256(Path(path)) == h, path
    preserved = {}
    for name in [
        "optimization_p3_20261015",
        "optimization_p4_20261016",
        "optimization_p5_20261016",
        "optimization_p6a_20261022",
        "optimization_p6b_20261022",
        "optimization_p6c_20261022",
    ]:
        old = root.parent / name
        manifest = old / "delivery_manifest.json"
        if not manifest.exists():
            manifest = old / "receipts/delivery_manifest.json"
        entries = json.loads(manifest.read_text())["files"]
        for path, info in entries.items():
            assert sha256(old / path) == info["sha256"], path
        preserved[name] = len(entries)
    for path, h in json.loads((out / "outputs_manifest.json").read_text()).items():
        assert sha256(out / path) == h, path
    for path, h in json.loads((out / "frozen_inputs.json").read_text())["sources"].items():
        assert sha256(Path(path)) == h, path
    structures = pd.read_csv(root.parent / "optimization_p3_20261015/data/structures.csv")
    old = root.parent / "optimization_p6c_20261022/experiment"
    receipts = json.loads((old / "fit_receipts.json").read_text())
    selections = json.loads((out / "selection.json").read_text())
    n_pred = 0
    n_cells = 0
    for scope, receipt in receipts.items():
        fit = structures[structures.structure_id.isin(receipt["fit"])]
        manifest = pd.read_csv(out / f"{scope}_stress_manifest.csv")
        capacity = pd.read_csv(out / f"{scope}_capacity.csv")
        pred = pd.read_csv(out / f"{scope}_stress_predictions.csv")
        n_pred += len(pred)
        assert set(manifest.structure_id) == set(fit.structure_id)
        assert set(pred.structure_id) <= set(fit.structure_id)
        assert not pred.duplicated(["scenario", "recipe", "target", "structure_id"]).any()
        for scenario, m in manifest.groupby("scenario"):
            assert not set(m[m.role.eq("train")].design_group_id) & set(
                m[m.role.ne("train")].design_group_id
            )
            axis, side = scenario.rsplit("_", 1)
            for branch, g in fit.groupby("branch_id"):
                edge = g[axis].min() if side == "low" else g[axis].max()
                evaluation = g[g[axis].eq(edge)]
                held = set(evaluation.design_group_id)
                train = g[~g.design_group_id.isin(held)]
                valid = (
                    g[axis].nunique() >= 3
                    and train.design_group_id.nunique() >= 2
                    and train[axis].nunique() >= 2
                )
                cap = capacity[capacity.scenario.eq(scenario) & capacity.branch_id.eq(branch)].iloc[
                    0
                ]
                assert pd.isna(cap.skip_reason) == valid
                actual = set(m[m.branch_id.eq(branch) & m.role.eq("evaluation")].structure_id)
                assert actual == (set(evaluation.structure_id) if valid else set())
            ids = set(m[m.role.eq("evaluation")].structure_id)
            for recipe in RECIPES:
                for target in TARGETS:
                    assert (
                        set(
                            pred[
                                pred.scenario.eq(scenario)
                                & pred.recipe.eq(recipe)
                                & pred.target.eq(target)
                            ].structure_id
                        )
                        == ids
                    )
        np.testing.assert_allclose(
            pred.abs_log_error, np.abs(np.log(pred.actual / pred.prediction)), atol=1e-14
        )
        cells = (
            pred.groupby(["recipe", "scenario", "branch_id", "target", "design_group_id"])
            .abs_log_error.mean()
            .groupby(["recipe", "scenario", "branch_id", "target"])
            .mean()
        )
        n_cells += len(cells)
        scores = {
            r: (float(cells.loc[r].max()), float(cells.loc[r].mean()), RECIPES.index(r))
            for r in RECIPES
        }
        for recipe in RECIPES:
            expected = selections[scope]["minimax_scores"][recipe]
            np.testing.assert_allclose(
                scores[recipe][:2], [expected["worst_cell"], expected["mean_cells"]], rtol=1e-12
            )
        assert min(RECIPES, key=scores.get) == selections[scope]["minimax_pseudo_boundary"]
        inner = pd.read_csv(old / f"{scope}_inner_predictions.csv").merge(
            fit[["structure_id", "branch_id"]], on="structure_id", validate="many_to_one"
        )
        balanced = (
            inner.groupby(["recipe", "branch_id", "target", "design_group_id"])
            .abs_log_error.mean()
            .groupby(["recipe", "branch_id", "target"])
            .mean()
            .groupby("recipe")
            .mean()
        )
        assert min(RECIPES, key=lambda r: balanced[r]) == selections[scope]["balanced_inner_cv"]
    outer = pd.read_csv(out / "outer_predictions.csv")
    frozen = pd.read_csv(old / "predictions.csv")
    keys = ["scope", "structure_id", "recipe", "interval", "target"]
    joined = outer.merge(frozen, on=keys, validate="many_to_one", suffixes=("", "_frozen"))
    assert len(joined) == len(outer)
    for col in ["actual", "prediction", "lower", "upper", "log_scale"]:
        np.testing.assert_array_equal(joined[col], joined[col + "_frozen"])
    for (scope, policy), rows in outer.groupby(["scope", "policy"]):
        assert set(rows.recipe) == {selections[scope][policy]}
        assert rows.groupby(["structure_id", "interval"]).target.nunique().eq(4).all()
    assert not outer.trusted_for_unmeasured_optimization.any()
    write_json(
        root / "verification.json",
        dict(
            status="passed",
            preserved_artifacts=preserved,
            protected_files=len(protected),
            stress_prediction_rows=n_pred,
            stress_cells=n_cells,
            outer_rows_exactly_reused=len(outer),
            new_outer_fit=False,
            new_calibration=False,
            new_blind_test=False,
        ),
    )
    print((root / "verification.json").read_text())


if __name__ == "__main__":
    main()
