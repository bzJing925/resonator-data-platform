"""P7: frozen bias-modeling arms; training-internal selection, then retrospective outer report."""

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
from app.ml.optimization_ablation import inner_folds as fresh_inner_folds
from app.ml.optimization_bias_p7 import (
    ARM_MODELS,
    ARMS,
    PF_QP_BRANCHES,
    area_stress_predictions,
    assemble_a2,
    coordinate_composition_diagnostic,
    guard_score,
    inner_cv_predictions,
    per_target_recipe_selection,
    primary_score,
    select_arm,
)
from app.ml.optimization_boundary_pilot import group_quantile, log_intervals, summarize
from app.ml.optimization_data import GEOMETRY, TARGETS, sha256, write_json
from scripts.run_optimization_p6c import partitions

SCOPES = {
    "structure_holdout": "split_manifest.csv",
    "area_edges": "split_area_edges.csv",
    "eg_upper": "split_eg_upper.csv",
    "fl_upper": "split_fl_upper.csv",
}
INTERVALS = ("joint_global", "joint_scaled")


def check_manifest(root, manifest_name):
    manifest = json.loads((root / manifest_name).read_text())
    for name, info in manifest["files"].items():
        assert sha256(root / name) == info["sha256"], f"{root.name}/{name}"
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ["p3-root", "p6c-root", "p6d-root", "protocol", "out"]:
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    p3manifest = check_manifest(args.p3_root, "receipts/delivery_manifest.json")
    p6cmanifest = check_manifest(args.p6c_root, "delivery_manifest.json")
    p6dmanifest = check_manifest(args.p6d_root, "delivery_manifest.json")
    backend = Path(__file__).resolve().parents[1]
    sources = [
        Path(__file__).resolve(),
        backend / "app/ml/optimization_bias_p7.py",
        backend / "app/ml/optimization_ablation.py",
        backend / "app/ml/optimization_boundary_pilot.py",
        backend / "app/ml/optimization_boundary_selection.py",
        backend / "app/ml/optimization_baselines.py",
        backend / "app/ml/optimization_data.py",
        backend / "scripts/run_optimization_p6c.py",
        args.protocol,
    ]
    hashes = {str(p): sha256(p) for p in sources}
    args.out.mkdir(parents=True)
    write_json(
        args.out / "frozen_inputs.json",
        dict(
            sources=hashes,
            p3_files={k: v["sha256"] for k, v in p3manifest["files"].items()},
            p6c_files={k: v["sha256"] for k, v in p6cmanifest["files"].items()},
            p6d_files={k: v["sha256"] for k, v in p6dmanifest["files"].items()},
            arms=ARMS,
            simulation_used=False,
            new_blind_test=False,
        ),
    )
    (args.out / "protocol.md").write_bytes(args.protocol.read_bytes())

    structures = pd.read_csv(args.p3_root / "data/structures.csv")
    inner_frames, stress_frames, selections_a2 = {}, {}, {}
    with threadpool_limits(limits=1):
        for scope, file in SCOPES.items():
            manifest = pd.read_csv(args.p3_root / "evaluation" / file)
            fit, cal, test = partitions(structures, manifest, scope)
            # A0 control: reuse frozen P6C/P6D files; fold assignment must reproduce exactly.
            folds = pd.read_csv(
                args.p6c_root / "experiment" / f"{scope}_inner_folds.csv"
            )
            fresh = fresh_inner_folds(fit)[["structure_id", "fold"]]
            merged = folds.merge(fresh, on="structure_id", suffixes=("", "_fresh"))
            assert len(merged) == len(folds) and merged.fold.eq(merged.fold_fresh).all(), scope
            inner_p6c = pd.read_csv(
                args.p6c_root / "experiment" / f"{scope}_inner_predictions.csv"
            )
            stress_p6d = pd.read_csv(
                args.p6d_root / "experiment" / f"{scope}_stress_predictions.csv"
            )
            a0_inner = inner_p6c[inner_p6c.recipe.eq("trend_gp_log_area")].assign(arm="A0_control")
            a0_stress = stress_p6d[
                stress_p6d.recipe.eq("trend_gp_log_area")
                & stress_p6d.scenario.isin(["area_um2_low", "area_um2_high"])
            ].assign(arm="A0_control")
            selected_a2, scores_a2 = per_target_recipe_selection(inner_p6c)
            selections_a2[scope] = {"selected_per_target": selected_a2, "scores": scores_a2}
            a2_inner, a2_stress = assemble_a2(
                inner_p6c,
                stress_p6d[stress_p6d.scenario.isin(["area_um2_low", "area_um2_high"])],
                selected_a2,
            )
            new_inner = inner_cv_predictions(fit, folds)
            new_stress = area_stress_predictions(fit)
            inner_frames[scope] = pd.concat(
                [
                    a0_inner.merge(
                        fit[["structure_id", "branch_id"]],
                        on="structure_id",
                        validate="many_to_one",
                    ),
                    a2_inner.merge(
                        fit[["structure_id", "branch_id"]],
                        on="structure_id",
                        validate="many_to_one",
                    ),
                    new_inner,
                ],
                ignore_index=True,
            ).assign(scope=scope)
            stress_frames[scope] = pd.concat(
                [a0_stress, a2_stress, new_stress], ignore_index=True
            ).assign(scope=scope)
            print(f"{scope}: inner/stress arms computed", flush=True)

    inner_all = pd.concat(inner_frames.values(), ignore_index=True)
    stress_all = pd.concat(stress_frames.values(), ignore_index=True)
    inner_all.to_csv(args.out / "inner_predictions.csv", index=False)
    stress_all.to_csv(args.out / "area_stress_predictions.csv", index=False)
    primary, primary_cells = primary_score(stress_all)
    guard, guard_cells = guard_score(inner_all)
    primary_cells.to_csv(args.out / "primary_cells.csv", index=False)
    guard_cells.to_csv(args.out / "guard_cells.csv", index=False)
    selection = select_arm(primary, guard)
    selection["a2_per_target_recipes"] = selections_a2
    # Persist selection before any outer calibration/test label is touched.
    write_json(args.out / "selection.json", selection)
    print("selected arm:", selection["selected"], flush=True)

    models, outputs, calibrations = {}, [], {}
    outer_p6c = pd.read_csv(args.p6c_root / "experiment/predictions.csv")
    with threadpool_limits(limits=1):
        for scope, file in SCOPES.items():
            manifest = pd.read_csv(args.p3_root / "evaluation" / file)
            fit, cal, test = partitions(structures, manifest, scope)
            base = outer_p6c[outer_p6c.scope.eq(scope)]
            for interval in INTERVALS:
                for arm, source in [
                    ("A0_control", "trend_gp_log_area"),
                    ("A2_per_target_recipe", None),
                ]:
                    if source:
                        rows = base[base.recipe.eq(source) & base.interval.eq(interval)]
                    else:
                        chosen = selections_a2[scope]["selected_per_target"]
                        rows = base.loc[
                            [r.recipe == chosen[r.target] for r in base.itertuples()]
                        ]
                        rows = rows[rows.interval.eq(interval)]
                    frame = rows.copy()
                    frame["arm"] = arm
                    frame["model"] = f"{arm}__{interval}"
                    frame["selected_arm"] = arm == selection["selected"]
                    frame["evaluation_status"] = "retrospective_seen_holdouts"
                    outputs.append(frame)
            for arm, cls in ARM_MODELS.items():
                residuals, scales, test_outputs = {}, {}, {}
                for target in TARGETS:
                    model = cls(target).fit(fit)
                    models[(scope, arm, target)] = model
                    cm, cs = model.predict_log(cal)
                    residuals[target] = np.abs(np.log(cal[target].to_numpy()) - cm)
                    scales[target] = cs
                    test_outputs[target] = model.predict_log(test)
                for interval in INTERVALS:
                    errors = np.column_stack(
                        [
                            residuals[t] / (scales[t] if interval == "joint_scaled" else 1)
                            for t in TARGETS
                        ]
                    )
                    q = group_quantile(cal.design_group_id.to_numpy(), errors)
                    calibrations[f"{scope}:{arm}:{interval}"] = q
                    for target in TARGETS:
                        mean, std = test_outputs[target]
                        lo, hi = log_intervals(
                            mean, std if interval == "joint_scaled" else np.ones(len(test)), q
                        )
                        frame = test[
                            ["structure_id", "design_group_id", "branch_id", *GEOMETRY]
                        ].copy()
                        frame["scope"], frame["arm"] = scope, arm
                        frame["interval"] = interval
                        frame["model"] = f"{arm}__{interval}"
                        frame["target"], frame["actual"] = target, test[target]
                        frame["prediction"], frame["lower"], frame["upper"] = np.exp(mean), lo, hi
                        frame["log_scale"] = std
                        frame["selected_arm"] = arm == selection["selected"]
                        frame["trusted_for_unmeasured_optimization"] = False
                        frame["evaluation_status"] = "retrospective_seen_holdouts"
                        outputs.append(frame)
            print(f"{scope}: outer arms fitted", flush=True)
    predictions = pd.concat(outputs, ignore_index=True)
    predictions.to_csv(args.out / "predictions.csv", index=False)
    metrics, joint, failures = summarize(predictions)
    metrics.to_csv(args.out / "metrics.csv", index=False)
    joint.to_csv(args.out / "joint_coverage.csv", index=False)
    failures.to_csv(args.out / "interval_failures.csv", index=False)
    small = predictions[
        predictions.scope.eq("area_edges")
        & predictions.area_um2.eq(700)
        & predictions.has_pf.eq(1)
        & predictions.target.eq("qp")
    ]
    small.to_csv(args.out / "small_area_pf_qp.csv", index=False)

    records = pd.read_csv(args.p3_root / "data/records.csv.gz")
    pf_structures = structures[structures.branch_id.isin(PF_QP_BRANCHES)]
    target8 = set(
        pf_structures.loc[pf_structures.area_um2.eq(700), "structure_id"]
    )
    d1 = coordinate_composition_diagnostic(records, pf_structures, target8)
    d1.to_csv(args.out / "d1_coordinate_composition.csv", index=False)

    joblib.dump(models, args.out / "models.joblib")
    write_json(args.out / "calibration.json", calibrations)
    for manifest, root in [
        (p3manifest, args.p3_root),
        (p6cmanifest, args.p6c_root),
        (p6dmanifest, args.p6d_root),
    ]:
        for name, info in manifest["files"].items():
            assert sha256(root / name) == info["sha256"], name
    assert all(sha256(Path(p)) == h for p, h in hashes.items())
    write_json(
        args.out / "summary.json",
        dict(
            status="P7_retrospective_complete_not_deployment",
            selected_arm=selection["selected"],
            selection_uses_outer_test=False,
            simulation_labels_used=False,
            new_blind_test=False,
            production_changed=False,
            new_predictions=len(predictions),
        ),
    )
    write_json(
        args.out / "outputs_manifest.json",
        {p.name: sha256(p) for p in sorted(args.out.iterdir()) if p.is_file()},
    )


if __name__ == "__main__":
    main()
