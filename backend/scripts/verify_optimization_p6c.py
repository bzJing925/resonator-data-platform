"""Independent partition, selection, calibration and preserved-artifact checks for P6C."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

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
    for name, h in protected.items():
        assert sha256(Path(name)) == h, name
    preserved = {}
    for stage in [
        "optimization_p3_20261015",
        "optimization_p4_20261016",
        "optimization_p5_20261016",
        "optimization_p6a_20261022",
        "optimization_p6b_20261022",
    ]:
        old = root.parent / stage
        manifest = old / (
            "receipts/delivery_manifest.json"
            if stage.startswith(("optimization_p3_", "optimization_p4_", "optimization_p5_"))
            else "delivery_manifest.json"
        )
        entries = json.loads(manifest.read_text())["files"]
        for name, info in entries.items():
            assert sha256(old / name) == info["sha256"], name
        preserved[stage] = len(entries)
    for name, h in json.loads((out / "outputs_manifest.json").read_text()).items():
        assert sha256(out / name) == h, name
    for name, h in json.loads((out / "frozen_inputs.json").read_text())["sources"].items():
        assert sha256(Path(name)) == h, name
    structures = pd.read_csv(root.parent / "optimization_p3_20261015/data/structures.csv")
    models = joblib.load(out / "models.joblib")
    receipts = json.loads((out / "fit_receipts.json").read_text())
    selections = json.loads((out / "selection.json").read_text())
    quantiles = json.loads((out / "calibration.json").read_text())
    predictions = pd.read_csv(out / "predictions.csv")
    qchecks = 0
    with threadpool_limits(limits=1):
        for scope, receipt in receipts.items():
            fit = structures[structures.structure_id.isin(receipt["fit"])]
            cal = structures[structures.structure_id.isin(receipt["calibration"])]
            test = structures[structures.structure_id.isin(receipt["test"])]
            assert not set(fit.design_group_id) & (
                set(cal.design_group_id) | set(test.design_group_id)
            )
            folds = pd.read_csv(out / f"{scope}_inner_folds.csv")
            assert set(folds.structure_id) == set(fit.structure_id)
            assert folds.groupby("design_group_id").fold.nunique().eq(1).all()
            inner = pd.read_csv(out / f"{scope}_inner_predictions.csv")
            assert set(inner.structure_id) == set(fit.structure_id)
            assert not inner.duplicated(["recipe", "target", "structure_id"]).any()
            np.testing.assert_allclose(
                inner.abs_log_error, np.abs(np.log(inner.actual / inner.prediction)), atol=1e-14
            )
            scores = {}
            for recipe in RECIPES:
                records = inner[inner.recipe.eq(recipe)]
                assert records.groupby("structure_id").target.nunique().eq(4).all()
                perstructure = records.groupby(
                    ["design_group_id", "structure_id"]
                ).abs_log_error.mean()
                scores[recipe] = float(perstructure.groupby(level=0).mean().mean())
                np.testing.assert_allclose(
                    scores[recipe], selections[scope]["scores"][recipe], rtol=1e-12
                )
            assert min(RECIPES, key=scores.get) == selections[scope]["selected"]
            for recipe in RECIPES:
                errors, scales = {}, {}
                for target in TARGETS:
                    model = models[(scope, recipe, target)]
                    ids = {
                        sid
                        for state in model.gp.branches.values()
                        for sid in state["structure_ids"]
                    }
                    assert ids == set(fit.structure_id)
                    mean, std = model.predict_log(cal)
                    errors[target] = np.abs(np.log(cal[target].to_numpy()) - mean)
                    scales[target] = std
                    tm, ts = model.predict_log(test)
                    for interval in ["joint_global", "joint_scaled"]:
                        rows = predictions[
                            predictions.scope.eq(scope)
                            & predictions.recipe.eq(recipe)
                            & predictions.target.eq(target)
                            & predictions.interval.eq(interval)
                        ]
                        rows = rows.set_index("structure_id").loc[test.structure_id]
                        np.testing.assert_allclose(rows.prediction, np.exp(tm), rtol=1e-12)
                        q = quantiles[f"{scope}:{recipe}:{interval}"]["quantile"]
                        width = q * (ts if interval == "joint_scaled" else 1)
                        np.testing.assert_allclose(rows.lower, np.exp(tm - width), rtol=1e-12)
                        np.testing.assert_allclose(rows.upper, np.exp(tm + width), rtol=1e-12)
                for interval in ["joint_global", "joint_scaled"]:
                    err = np.column_stack(
                        [
                            errors[t] / (scales[t] if interval == "joint_scaled" else 1)
                            for t in TARGETS
                        ]
                    ).max(axis=1)
                    grouped = pd.Series(err, index=cal.design_group_id).groupby(level=0).max()
                    expected = np.sort(grouped)[math.ceil((len(grouped) + 1) * 0.9) - 1]
                    np.testing.assert_allclose(
                        expected, quantiles[f"{scope}:{recipe}:{interval}"]["quantile"], rtol=1e-12
                    )
                    qchecks += 1
    assert not predictions.trusted_for_unmeasured_optimization.any()
    write_json(
        root / "verification.json",
        dict(
            status="passed",
            protected_files=len(protected),
            preserved_artifacts=preserved,
            outer_prediction_rows=len(predictions),
            joint_quantiles_checked=qchecks,
            scopes_with_independent_inner_score_check=4,
            production_changed=False,
            new_blind_test=False,
        ),
    )
    print((root / "verification.json").read_text())


if __name__ == "__main__":
    main()
