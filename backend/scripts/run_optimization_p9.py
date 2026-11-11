"""P9: label-uncertainty arms; training-internal interval evaluation, then outer report."""

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
from app.ml.optimization_ablation import AblationModel
from app.ml.optimization_ablation import inner_folds as fresh_inner_folds
from app.ml.optimization_bias_p7 import guard_score, primary_score
from app.ml.optimization_boundary_pilot import group_quantile, log_intervals, summarize
from app.ml.optimization_boundary_selection import pseudo_boundaries
from app.ml.optimization_data import GEOMETRY, TARGETS, sha256, write_json
from app.ml.optimization_label_p9 import (
    ARMS,
    HeteroTrendGPModel,
    dispersion,
    inner_interval_metrics,
    inner_interval_predictions,
    select_arm_p9,
)
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


def c1_stress_predictions(fit):
    """Area-axis pseudo-boundary point predictions for C1; P6D capacity rules reused."""
    manifests, _ = pseudo_boundaries(fit)
    manifests = manifests[manifests.scenario.isin(["area_um2_low", "area_um2_high"])]
    rows = []
    for scenario, m in manifests.groupby("scenario", sort=True):
        train = fit[fit.structure_id.isin(m.loc[m.role.eq("train"), "structure_id"])]
        evaluation = fit[fit.structure_id.isin(m.loc[m.role.eq("evaluation"), "structure_id"])]
        if evaluation.empty:
            continue
        if set(train.design_group_id) & set(evaluation.design_group_id):
            raise ValueError("Pseudo-boundary group leakage")
        for target in TARGETS:
            mean, _ = HeteroTrendGPModel(target).fit(train).predict_log(evaluation)
            if not np.isfinite(mean).all():
                raise ValueError("Nonfinite stress prediction")
            frame = evaluation[["structure_id", "design_group_id", "branch_id"]].copy()
            frame["scenario"], frame["arm"], frame["target"] = scenario, "C1_hetero_gp", target
            frame["actual"], frame["prediction"] = evaluation[target], np.exp(mean)
            frame["abs_log_error"] = np.abs(np.log(evaluation[target]) - mean)
            rows.append(frame)
    if not rows:
        raise ValueError("No eligible area pseudo-boundary cells")
    return pd.concat(rows, ignore_index=True)


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
        backend / "app/ml/optimization_label_p9.py",
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
    inner_frames, stress_frames = {}, {}
    with threadpool_limits(limits=1):
        for scope, file in SCOPES.items():
            manifest = pd.read_csv(args.p3_root / "evaluation" / file)
            fit, cal, test = partitions(structures, manifest, scope)
            folds = pd.read_csv(args.p6c_root / "experiment" / f"{scope}_inner_folds.csv")
            fresh = fresh_inner_folds(fit)[["structure_id", "fold"]]
            merged = folds.merge(fresh, on="structure_id", suffixes=("", "_fresh"))
            assert len(merged) == len(folds) and merged.fold.eq(merged.fold_fresh).all(), scope
            c0_inner = inner_interval_predictions(fit, folds, "C0_control")
            ref = pd.read_csv(args.p6c_root / "experiment" / f"{scope}_inner_predictions.csv")
            ref = ref[ref.recipe.eq("trend_gp_log_area")].sort_values(["structure_id", "target"])
            got = c0_inner.sort_values(["structure_id", "target"]).reset_index(drop=True)
            assert len(ref) == len(got) and np.allclose(
                ref.abs_log_error.to_numpy(), got.abs_log_error.to_numpy(), rtol=1e-12, atol=0
            ), scope
            c1_inner = inner_interval_predictions(fit, folds, "C1_hetero_gp")
            stress_p6d = pd.read_csv(
                args.p6d_root / "experiment" / f"{scope}_stress_predictions.csv"
            )
            c0_stress = stress_p6d[
                stress_p6d.recipe.eq("trend_gp_log_area")
                & stress_p6d.scenario.isin(["area_um2_low", "area_um2_high"])
            ].assign(arm="C0_control")
            c1_stress = c1_stress_predictions(fit)
            frame = pd.concat([c0_inner, c1_inner], ignore_index=True)
            frame["scope"] = scope
            area_by_id = structures.set_index("structure_id").area_um2
            frame["area_um2"] = frame.structure_id.map(area_by_id)
            inner_frames[scope] = frame
            stress_frames[scope] = pd.concat(
                [c0_stress, c1_stress], ignore_index=True
            ).assign(scope=scope)
            print(f"{scope}: inner interval + stress arms computed", flush=True)

    inner_all = pd.concat(inner_frames.values(), ignore_index=True)
    stress_all = pd.concat(stress_frames.values(), ignore_index=True)
    inner_all.to_csv(args.out / "inner_interval_predictions.csv", index=False)
    stress_all.to_csv(args.out / "area_stress_predictions.csv", index=False)
    metrics = inner_interval_metrics(inner_all)
    metrics.to_csv(args.out / "inner_interval_metrics.csv", index=False)
    point_primary, primary_cells = primary_score(stress_all)
    point_guard, guard_cells = guard_score(inner_all)
    primary_cells.to_csv(args.out / "point_primary_cells.csv", index=False)
    guard_cells.to_csv(args.out / "point_guard_cells.csv", index=False)
    point_primary["C2_hetero_calibration"] = point_primary["C0_control"]
    point_guard["C2_hetero_calibration"] = point_guard["C0_control"]
    selection = select_arm_p9(metrics, point_primary, point_guard)
    # Persist selection before any outer calibration/test label is touched.
    write_json(args.out / "selection.json", selection)
    print("selected arm:", selection["selected"], flush=True)

    models, outputs, calibrations = {}, [], {}
    outer_p6c = pd.read_csv(args.p6c_root / "experiment/predictions.csv")
    with threadpool_limits(limits=1):
        for scope, file in SCOPES.items():
            manifest = pd.read_csv(args.p3_root / "evaluation" / file)
            fit, cal, test = partitions(structures, manifest, scope)
            points = {}
            for arm in ["C0_control", "C1_hetero_gp"]:
                residuals, scales, test_outputs = {}, {}, {}
                for target in TARGETS:
                    cls = AblationModel if arm == "C0_control" else HeteroTrendGPModel
                    model = (
                        cls("trend_gp_log_area", target)
                        if arm == "C0_control"
                        else cls(target)
                    ).fit(fit)
                    models[(scope, arm, target)] = model
                    cm, cs = model.predict_log(cal)
                    residuals[target] = np.abs(np.log(cal[target].to_numpy()) - cm)
                    scales[target] = cs
                    test_outputs[target] = model.predict_log(test)
                points[arm] = (residuals, scales, test_outputs)
            # C0 control correspondence with frozen P6C outer predictions.
            ref = outer_p6c[
                outer_p6c.scope.eq(scope)
                & outer_p6c.recipe.eq("trend_gp_log_area")
                & outer_p6c.interval.eq("joint_scaled")
            ]
            for target in TARGETS:
                ref_t = ref[ref.target.eq(target)].set_index("structure_id")
                mean, _ = points["C0_control"][2][target]
                got = pd.Series(np.exp(mean), index=test.structure_id)
                np.testing.assert_allclose(
                    got.loc[ref_t.index].to_numpy(),
                    ref_t.prediction.to_numpy(),
                    rtol=1e-12,
                )
            disp_cal = {t: dispersion(cal, t) for t in TARGETS}
            disp_test = {t: dispersion(test, t) for t in TARGETS}
            for arm in ARMS:
                residuals, scales, test_outputs = points[
                    "C0_control" if arm == "C2_hetero_calibration" else arm
                ]
                for interval in INTERVALS:
                    scores = []
                    for t in TARGETS:
                        if arm == "C0_control" or arm == "C1_hetero_gp":
                            norm = scales[t] if interval == "joint_scaled" else 1.0
                            scores.append(residuals[t] / norm)
                        elif interval == "joint_scaled":
                            scores.append(
                                residuals[t] / np.sqrt(scales[t] ** 2 + disp_cal[t] ** 2)
                            )
                        else:
                            scores.append(residuals[t] / np.maximum(disp_cal[t], 1e-12))
                    q = group_quantile(
                        cal.design_group_id.to_numpy(), np.column_stack(scores)
                    )
                    calibrations[f"{scope}:{arm}:{interval}"] = q
                    for target in TARGETS:
                        mean, std = test_outputs[target]
                        if arm == "C2_hetero_calibration":
                            if interval == "joint_scaled":
                                scale = np.sqrt(std**2 + disp_test[target] ** 2)
                            else:
                                scale = np.maximum(disp_test[target], 1e-12)
                        else:
                            scale = std if interval == "joint_scaled" else np.ones(len(test))
                        lo, hi = log_intervals(mean, scale, q)
                        frame = test[
                            ["structure_id", "design_group_id", "branch_id", *GEOMETRY]
                        ].copy()
                        frame["scope"], frame["arm"] = scope, arm
                        frame["interval"] = interval
                        frame["model"] = f"{arm}__{interval}"
                        frame["target"], frame["actual"] = target, test[target]
                        frame["prediction"], frame["lower"], frame["upper"] = np.exp(mean), lo, hi
                        frame["log_scale"] = std
                        frame["disp"] = disp_test[target]
                        frame["selected_arm"] = arm == selection["selected"]
                        frame["trusted_for_unmeasured_optimization"] = False
                        frame["evaluation_status"] = "retrospective_seen_holdouts"
                        outputs.append(frame)
            print(f"{scope}: outer arms fitted", flush=True)
    predictions = pd.concat(outputs, ignore_index=True)
    predictions.to_csv(args.out / "predictions.csv", index=False)
    metrics_out, joint, failures = summarize(predictions)
    metrics_out.to_csv(args.out / "metrics.csv", index=False)
    joint.to_csv(args.out / "joint_coverage.csv", index=False)
    failures.to_csv(args.out / "interval_failures.csv", index=False)
    small = predictions[
        predictions.scope.eq("area_edges")
        & predictions.area_um2.eq(700)
        & predictions.has_pf.eq(1)
        & predictions.target.eq("qp")
    ]
    small.to_csv(args.out / "small_area_pf_qp.csv", index=False)

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
            status="P9_retrospective_complete_not_deployment",
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
