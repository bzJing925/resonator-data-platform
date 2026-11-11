"""Measured-only P6C: nested grouped recipe selection, then retrospective calibration/test."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_ablation import RECIPES, AblationModel, select_recipe
from app.ml.optimization_boundary_pilot import group_quantile, log_intervals, summarize
from app.ml.optimization_data import GEOMETRY, TARGETS, sha256, write_json


def partitions(structures, manifest, scope):
    if manifest.structure_id.duplicated().any() or set(manifest.structure_id) != set(
        structures.structure_id
    ):
        raise ValueError("Invalid manifest")
    ids = structures.set_index("structure_id").design_group_id
    if not manifest.design_group_id.eq(manifest.structure_id.map(ids)).all():
        raise ValueError("Forged design groups")
    roles = manifest.role.replace({"validation": "train", "embargo": "test"})
    if roles.groupby(manifest.design_group_id).nunique().max() > 1:
        raise ValueError("Group leakage")
    frame = structures.merge(manifest[["structure_id", "role"]], validate="one_to_one")
    fit = frame[
        frame.role.isin(["train", "validation"] if scope == "structure_holdout" else ["train"])
    ]
    return fit, frame[frame.role.eq("calibration")], frame[frame.role.eq("test")]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--p3-root", type=Path, required=True)
    ap.add_argument("--p6a-root", type=Path, required=True)
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    p3manifest = json.loads((args.p3_root / "receipts/delivery_manifest.json").read_text())
    for name, info in p3manifest["files"].items():
        assert sha256(args.p3_root / name) == info["sha256"], name
    backend = Path(__file__).resolve().parents[1]
    sources = [
        Path(__file__).resolve(),
        backend / "app/ml/optimization_ablation.py",
        backend / "app/ml/optimization_boundary_pilot.py",
        backend / "app/ml/optimization_baselines.py",
        backend / "app/ml/optimization_data.py",
        args.protocol,
    ]
    hashes = {str(p): sha256(p) for p in sources}
    args.out.mkdir(parents=True)
    write_json(
        args.out / "frozen_inputs.json",
        dict(
            sources=hashes,
            p3_files=p3manifest["files"],
            recipes=RECIPES,
            simulation_used=False,
            new_blind_test=False,
        ),
    )
    (args.out / "protocol.md").write_bytes(args.protocol.read_bytes())
    structures = pd.read_csv(args.p3_root / "data/structures.csv")
    selections, fit_receipts, calibrations, models, outputs = {}, {}, {}, {}, []
    control = pd.read_csv(args.p6a_root / "pilot/new_predictions.csv")
    control = control[control.model.eq("gp_matern_scaled_joint")]
    with threadpool_limits(limits=1):
        for scope, file in [
            ("structure_holdout", "split_manifest.csv"),
            ("area_edges", "split_area_edges.csv"),
            ("eg_upper", "split_eg_upper.csv"),
            ("fl_upper", "split_fl_upper.csv"),
        ]:
            manifest = pd.read_csv(args.p3_root / "evaluation" / file)
            fit, cal, test = partitions(structures, manifest, scope)
            selected, scores, folds, inner = select_recipe(fit)
            selections[scope] = {"selected": selected, "scores": scores}
            # Persist selection before touching calibration labels or test targets in fitting.
            write_json(args.out / "selection.json", selections)
            folds.to_csv(args.out / f"{scope}_inner_folds.csv", index=False)
            inner.to_csv(args.out / f"{scope}_inner_predictions.csv", index=False)
            fit_receipts[scope] = {
                role: part.structure_id.tolist()
                for role, part in [("fit", fit), ("calibration", cal), ("test", test)]
            }
            for recipe in RECIPES:
                residuals, scales, test_outputs = {}, {}, {}
                for target in TARGETS:
                    model = AblationModel(recipe, target).fit(fit)
                    cm, cs = model.predict_log(cal)
                    residuals[target] = np.abs(np.log(cal[target].to_numpy()) - cm)
                    scales[target] = cs
                    test_outputs[target] = model.predict_log(test)
                    models[(scope, recipe, target)] = model
                for interval in ["joint_global", "joint_scaled"]:
                    errors = np.column_stack(
                        [
                            residuals[t] / (scales[t] if interval == "joint_scaled" else 1)
                            for t in TARGETS
                        ]
                    )
                    q = group_quantile(cal.design_group_id.to_numpy(), errors)
                    calibrations[f"{scope}:{recipe}:{interval}"] = q
                    for target in TARGETS:
                        mean, std = test_outputs[target]
                        lo, hi = log_intervals(
                            mean, std if interval == "joint_scaled" else np.ones(len(test)), q
                        )
                        frame = test[
                            ["structure_id", "design_group_id", "branch_id", *GEOMETRY]
                        ].copy()
                        frame["scope"], frame["recipe"], frame["interval"] = scope, recipe, interval
                        frame["model"] = f"{recipe}__{interval}"
                        frame["target"], frame["actual"] = target, test[target]
                        frame["prediction"], frame["lower"], frame["upper"] = np.exp(mean), lo, hi
                        frame["log_scale"] = std
                        frame["selected_by_inner_cv"] = recipe == selected
                        frame["trusted_for_unmeasured_optimization"] = False
                        outputs.append(frame)
                        if recipe == "gp_raw" and interval == "joint_scaled":
                            ref = (
                                control[control.scope.eq(scope) & control.target.eq(target)]
                                .set_index("structure_id")
                                .loc[test.structure_id]
                            )
                            for key in ["prediction", "lower", "upper"]:
                                np.testing.assert_allclose(frame[key], ref[key], rtol=1e-12)
            print(f"{scope}: selected {selected}; control reproduced", flush=True)
    predictions = pd.concat(outputs, ignore_index=True)
    predictions.to_csv(args.out / "predictions.csv", index=False)
    metrics, joint, failures = summarize(predictions)
    metrics.to_csv(args.out / "metrics.csv", index=False)
    joint.to_csv(args.out / "joint_coverage.csv", index=False)
    failures.to_csv(args.out / "interval_failures.csv", index=False)
    old = pd.read_csv(args.p6a_root / "pilot/comparison_to_p3.csv")
    baseline = old[old.model.eq("extra_trees_log")][["scope", "target", "mae"]]
    comparison = metrics[metrics.branch.eq("all")].merge(
        baseline, on=["scope", "target"], suffixes=("", "_p3"), validate="many_to_one"
    )
    comparison["mae_ratio_to_p3"] = comparison.mae / comparison.mae_p3
    comparison["selected_by_inner_cv"] = [
        row.model.split("__")[0] == selections[row.scope]["selected"]
        for row in comparison.itertuples()
    ]
    comparison.to_csv(args.out / "comparison_to_p3.csv", index=False)
    small = predictions[
        predictions.scope.eq("area_edges")
        & predictions.area_um2.eq(700)
        & predictions.has_pf.eq(1)
        & predictions.target.eq("qp")
    ]
    small.to_csv(args.out / "small_area_pf_qp.csv", index=False)
    joblib.dump(models, args.out / "models.joblib")
    write_json(args.out / "fit_receipts.json", fit_receipts)
    write_json(args.out / "calibration.json", calibrations)
    for name, info in p3manifest["files"].items():
        assert sha256(args.p3_root / name) == info["sha256"], name
    assert all(sha256(Path(p)) == h for p, h in hashes.items())
    write_json(
        args.out / "summary.json",
        dict(
            status="P6C_retrospective_complete_not_deployment",
            new_predictions=len(predictions),
            control_p6a_reproduced=True,
            selection_uses_outer_test=False,
            simulation_labels_used=False,
            new_blind_test=False,
            production_changed=False,
        ),
    )
    write_json(
        args.out / "outputs_manifest.json",
        {p.name: sha256(p) for p in sorted(args.out.iterdir()) if p.is_file()},
    )


if __name__ == "__main__":
    main()
