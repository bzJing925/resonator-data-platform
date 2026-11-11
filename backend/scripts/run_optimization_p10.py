"""P10: subset heteroscedastic intervals (E1) + Qp-only frequency submodel (E2)."""

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
from app.ml.optimization_bias_p7 import guard_score, primary_score
from app.ml.optimization_boundary_pilot import group_quantile, log_intervals, summarize
from app.ml.optimization_data import GEOMETRY, TARGETS, sha256, write_json
from app.ml.optimization_freq_p8 import FreqTrendGPModel
from app.ml.optimization_p10 import (
    E1_ARMS,
    E2_ARMS,
    assemble_e1_arms,
    eval_e1_inner,
    select_e1,
    select_e2,
    subset_mask,
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
    files = manifest.get("files", manifest)
    for name, info in files.items():
        digest = info["sha256"] if isinstance(info, dict) else info
        assert sha256(root / name) == digest, f"{root.name}/{name}"
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ["p3-root", "p6c-root", "p6d-root", "p8-root", "p9-root", "protocol", "out"]:
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    manifests = {
        "p3": check_manifest(args.p3_root, "receipts/delivery_manifest.json"),
        "p6c": check_manifest(args.p6c_root, "delivery_manifest.json"),
        "p6d": check_manifest(args.p6d_root, "delivery_manifest.json"),
        "p8": check_manifest(args.p8_root, "outputs_manifest.json"),
        "p9": check_manifest(args.p9_root, "outputs_manifest.json"),
    }
    backend = Path(__file__).resolve().parents[1]
    sources = [
        Path(__file__).resolve(),
        backend / "app/ml/optimization_p10.py",
        backend / "app/ml/optimization_label_p9.py",
        backend / "app/ml/optimization_freq_p8.py",
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
    (args.out / "experiment1_intervals").mkdir()
    (args.out / "experiment2_qp_freq").mkdir()
    write_json(
        args.out / "frozen_inputs.json",
        dict(
            sources=hashes,
            **{
                f"{k}_files": {
                    n: (v["sha256"] if isinstance(v, dict) else v) for n, v in m["files"].items()
                }
                if "files" in m
                else m
                for k, m in manifests.items()
            },
            e1_arms=E1_ARMS,
            e2_arms=E2_ARMS,
            simulation_used=False,
            new_blind_test=False,
        ),
    )
    (args.out / "protocol.md").write_bytes(args.protocol.read_bytes())
    structures = pd.read_csv(args.p3_root / "data/structures.csv")

    # ---------------- E1 inner: assemble D-arms from frozen P9 out-of-fold rows ----------------
    p9_inner = pd.read_csv(args.p9_root / "inner_interval_predictions.csv")
    c0_inner = p9_inner[p9_inner.arm.eq("C0_control")].reset_index(drop=True)
    c1_inner = p9_inner[p9_inner.arm.eq("C1_hetero_gp")].reset_index(drop=True)
    e1_arms = assemble_e1_arms(c0_inner, c1_inner)
    e1_metrics = eval_e1_inner(e1_arms)
    # D2 point guard: point frames switch to C1 only on the geometric subset.
    p9_stress = pd.read_csv(args.p9_root / "area_stress_predictions.csv")
    s_c0 = p9_stress[p9_stress.arm.eq("C0_control")].copy()
    s_c1 = p9_stress[p9_stress.arm.eq("C1_hetero_gp")].copy()
    skeys = ["scope", "structure_id", "scenario", "target"]
    s_c0 = s_c0.sort_values(skeys).reset_index(drop=True)
    s_c1 = s_c1.sort_values(skeys).reset_index(drop=True)
    assert s_c0[skeys].equals(s_c1[skeys]), "C0/C1 stress rows must align"
    area_by_id = structures.set_index("structure_id").area_um2
    s_d2 = s_c0.copy()
    s_d2["area_um2"] = s_d2.structure_id.map(area_by_id)
    take = subset_mask(s_d2).to_numpy()
    for col in ["prediction", "abs_log_error"]:
        s_d2.loc[take, col] = s_c1[col].to_numpy()[take]
    s_d2 = s_d2.drop(columns=["area_um2"])
    s_c0 = s_c0.drop(columns=["area_um2"], errors="ignore")
    i_d2 = c0_inner.copy()
    take_i = subset_mask(i_d2).to_numpy()
    i_d2.loc[take_i, "abs_log_error"] = c1_inner.abs_log_error.to_numpy()[take_i]
    stress_points = pd.concat(
        [
            s_c0.assign(arm="D0_control"),
            s_c0.assign(arm="D1_subset_hetero_interval"),
            s_d2.assign(arm="D2_subset_c1_switch"),
        ],
        ignore_index=True,
    )
    inner_points = pd.concat(
        [
            c0_inner.assign(arm="D0_control"),
            c0_inner.assign(arm="D1_subset_hetero_interval"),
            i_d2.assign(arm="D2_subset_c1_switch"),
        ],
        ignore_index=True,
    )
    e1_primary, _ = primary_score(stress_points)
    e1_guard, _ = guard_score(inner_points)
    selection_e1 = select_e1(e1_metrics, e1_primary, e1_guard)
    # E1 selection persisted before any outer assembly touches outer labels.
    write_json(args.out / "selection_e1.json", selection_e1)
    e1_metrics.to_csv(args.out / "experiment1_intervals/inner_interval_metrics.csv", index=False)
    print("E1 selected:", selection_e1["selected"], flush=True)

    # ---------------- E2 inner: assemble F-arms from frozen P6C/P8/P6D rows ----------------
    p8_inner = pd.read_csv(args.p8_root / "inner_predictions.csv")
    p8_stress = pd.read_csv(args.p8_root / "area_stress_predictions.csv")
    inner_e2, stress_e2 = [], []
    for scope in SCOPES:
        ref = pd.read_csv(args.p6c_root / "experiment" / f"{scope}_inner_predictions.csv")
        geo = ref[ref.recipe.eq("trend_gp_log_area")].copy()
        geo["scope"] = scope
        b1 = p8_inner[p8_inner.arm.eq("B1_freq_trend_gp") & p8_inner.scope.eq(scope)].copy()
        keys = ["scope", "structure_id", "target"]
        assert len(geo) == len(b1) and geo[keys].reset_index(drop=True).equals(
            b1[keys].reset_index(drop=True)
        ), scope
        f1 = geo.copy()
        qp = f1.target.eq("qp").to_numpy()
        f1.loc[qp, "abs_log_error"] = b1.abs_log_error.to_numpy()[b1.target.eq("qp").to_numpy()]
        f1.loc[qp, "prediction"] = b1.prediction.to_numpy()[b1.target.eq("qp").to_numpy()]
        inner_e2 += [geo.assign(arm="F0_control"), f1.assign(arm="F1_qp_frequency")]
        refs = pd.read_csv(args.p6d_root / "experiment" / f"{scope}_stress_predictions.csv")
        geo_s = refs[
            refs.recipe.eq("trend_gp_log_area")
            & refs.scenario.isin(["area_um2_low", "area_um2_high"])
        ].copy()
        geo_s["scope"] = scope
        b1_s = p8_stress[
            p8_stress.arm.eq("B1_freq_trend_gp") & p8_stress.scope.eq(scope)
        ].copy()
        keys_s = ["scope", "structure_id", "scenario", "target"]
        assert len(geo_s) == len(b1_s) and geo_s[keys_s].reset_index(drop=True).equals(
            b1_s[keys_s].reset_index(drop=True)
        ), scope
        f1_s = geo_s.copy()
        qp_s = f1_s.target.eq("qp").to_numpy()
        f1_s.loc[qp_s, "abs_log_error"] = b1_s.abs_log_error.to_numpy()[
            b1_s.target.eq("qp").to_numpy()
        ]
        f1_s.loc[qp_s, "prediction"] = b1_s.prediction.to_numpy()[b1_s.target.eq("qp").to_numpy()]
        stress_e2 += [geo_s.assign(arm="F0_control"), f1_s.assign(arm="F1_qp_frequency")]
    inner_e2 = pd.concat(inner_e2, ignore_index=True)
    branch_by_id = structures.set_index("structure_id").branch_id
    inner_e2["branch_id"] = inner_e2.structure_id.map(branch_by_id)
    stress_e2 = pd.concat(stress_e2, ignore_index=True)
    inner_e2.to_csv(args.out / "experiment2_qp_freq/inner_predictions.csv", index=False)
    stress_e2.to_csv(args.out / "experiment2_qp_freq/area_stress_predictions.csv", index=False)
    e2_primary, e2_primary_cells = primary_score(stress_e2)
    e2_guard, e2_guard_cells = guard_score(inner_e2)
    e2_primary_cells.to_csv(args.out / "experiment2_qp_freq/primary_cells.csv", index=False)
    e2_guard_cells.to_csv(args.out / "experiment2_qp_freq/guard_cells.csv", index=False)
    selection_e2 = select_e2(e2_primary, e2_guard)
    # E2 selection persisted before any outer fit touches outer labels.
    write_json(args.out / "selection_e2.json", selection_e2)
    print("E2 selected:", selection_e2["selected"], flush=True)

    # ---------------- E1 outer: pure assembly from frozen P9 outer rows ----------------
    p9_pred = pd.read_csv(args.p9_root / "predictions.csv")
    p9_cal = json.loads((args.p9_root / "calibration.json").read_text())
    outer_e1 = []
    c0_rows = p9_pred[p9_pred.arm.eq("C0_control")]
    c1_rows = p9_pred[p9_pred.arm.eq("C1_hetero_gp")]
    for interval in INTERVALS:
        c0i = c0_rows[c0_rows.interval.eq(interval)]
        d0 = c0i.copy()
        d0["arm"] = "D0_control"
        d0["model"] = f"D0_control__{interval}"
        outer_e1.append(d0)
        d1 = c0i.copy()
        sub = subset_mask(d1).to_numpy()
        for scope in SCOPES:
            q = p9_cal[f"{scope}:C0_control:{interval}"]["quantile"]
            in_scope = d1.scope.eq(scope).to_numpy() & sub
            base_scale = (
                d1.log_scale.to_numpy() if interval == "joint_scaled" else np.ones(len(d1))
            )
            new_scale = np.sqrt(base_scale**2 + d1.disp.to_numpy() ** 2)
            mean = np.log(d1.prediction.to_numpy())
            d1.loc[in_scope, "lower"] = np.exp(mean[in_scope] - q * new_scale[in_scope])
            d1.loc[in_scope, "upper"] = np.exp(mean[in_scope] + q * new_scale[in_scope])
        d1["arm"] = "D1_subset_hetero_interval"
        d1["model"] = f"D1_subset_hetero_interval__{interval}"
        outer_e1.append(d1)
        c1i = c1_rows[c1_rows.interval.eq(interval)].set_index(
            ["scope", "structure_id", "target"]
        )
        d2 = c0i.copy()
        sub2 = subset_mask(d2).to_numpy()
        key = ["scope", "structure_id", "target"]
        idx = d2.loc[sub2].set_index(key).index
        for col in ["prediction", "lower", "upper", "log_scale", "disp"]:
            d2.loc[sub2, col] = c1i[col].loc[idx].to_numpy()
        d2["arm"] = "D2_subset_c1_switch"
        d2["model"] = f"D2_subset_c1_switch__{interval}"
        outer_e1.append(d2)
    outer_e1 = pd.concat(outer_e1, ignore_index=True)
    for arm in ["D1_subset_hetero_interval", "D2_subset_c1_switch"]:
        ns = outer_e1[outer_e1.arm.eq(arm) & ~subset_mask(outer_e1)]
        ref = outer_e1[outer_e1.arm.eq("D0_control") & ~subset_mask(outer_e1)]
        assert ns[["prediction", "lower", "upper"]].reset_index(drop=True).equals(
            ref[["prediction", "lower", "upper"]].reset_index(drop=True)
        ), arm
    outer_e1["selected_arm"] = outer_e1.arm.eq(selection_e1["selected"])
    e1_dir = args.out / "experiment1_intervals"
    outer_e1.to_csv(e1_dir / "predictions.csv", index=False)
    metrics_e1, joint_e1, failures_e1 = summarize(outer_e1)
    metrics_e1.to_csv(e1_dir / "metrics.csv", index=False)
    joint_e1.to_csv(e1_dir / "joint_coverage.csv", index=False)
    failures_e1.to_csv(e1_dir / "interval_failures.csv", index=False)
    small_e1 = outer_e1[
        outer_e1.scope.eq("area_edges")
        & outer_e1.area_um2.eq(700)
        & outer_e1.has_pf.eq(1)
        & outer_e1.target.eq("qp")
    ]
    small_e1.to_csv(e1_dir / "small_area_pf_qp.csv", index=False)
    print("E1 outer assembled", flush=True)

    # ---------------- E2 outer: refit A0 (4 targets) + B1 (qp only) ----------------
    outer_p6c = pd.read_csv(args.p6c_root / "experiment/predictions.csv")
    models, outputs_e2, calibrations = {}, [], {}
    with threadpool_limits(limits=1):
        for scope, file in SCOPES.items():
            manifest = pd.read_csv(args.p3_root / "evaluation" / file)
            fit, cal, test = partitions(structures, manifest, scope)
            residuals, scales, test_outputs = {}, {}, {}
            for target in TARGETS:
                model = AblationModel("trend_gp_log_area", target).fit(fit)
                models[(scope, "F0_geometry_part", target)] = model
                cm, cs = model.predict_log(cal)
                residuals[target] = np.abs(np.log(cal[target].to_numpy()) - cm)
                scales[target] = cs
                test_outputs[target] = model.predict_log(test)
                ref = outer_p6c[
                    outer_p6c.scope.eq(scope)
                    & outer_p6c.recipe.eq("trend_gp_log_area")
                    & outer_p6c.interval.eq("joint_scaled")
                    & outer_p6c.target.eq(target)
                ].set_index("structure_id")
                got = pd.Series(np.exp(test_outputs[target][0]), index=test.structure_id)
                np.testing.assert_allclose(
                    got.loc[ref.index].to_numpy(), ref.prediction.to_numpy(), rtol=1e-12
                )
            b1 = FreqTrendGPModel("qp").fit(fit)
            models[(scope, "F1_freq_part", "qp")] = b1
            cm, cs = b1.predict_log(cal)
            residuals["qp"] = np.abs(np.log(cal.qp.to_numpy()) - cm)
            scales["qp"] = cs
            test_outputs["qp"] = b1.predict_log(test)
            for interval in INTERVALS:
                errors = np.column_stack(
                    [
                        residuals[t] / (scales[t] if interval == "joint_scaled" else 1.0)
                        for t in TARGETS
                    ]
                )
                q = group_quantile(cal.design_group_id.to_numpy(), errors)
                calibrations[f"{scope}:F1_qp_frequency:{interval}"] = q
                f0 = outer_p6c[
                    outer_p6c.scope.eq(scope)
                    & outer_p6c.recipe.eq("trend_gp_log_area")
                    & outer_p6c.interval.eq(interval)
                ].copy()
                f0["arm"] = "F0_control"
                f0["model"] = f"F0_control__{interval}"
                f0["contract"] = "geometry_only"
                f0["selected_arm"] = selection_e2["selected"] == "F0_control"
                outputs_e2.append(f0)
                for target in TARGETS:
                    mean, std = test_outputs[target]
                    lo, hi = log_intervals(
                        mean, std if interval == "joint_scaled" else np.ones(len(test)), q
                    )
                    frame = test[
                        ["structure_id", "design_group_id", "branch_id", *GEOMETRY]
                    ].copy()
                    frame["scope"], frame["arm"] = scope, "F1_qp_frequency"
                    frame["interval"] = interval
                    frame["model"] = f"F1_qp_frequency__{interval}"
                    frame["contract"] = (
                        "measured_frequency" if target == "qp" else "geometry_only"
                    )
                    frame["target"], frame["actual"] = target, test[target]
                    frame["prediction"], frame["lower"], frame["upper"] = np.exp(mean), lo, hi
                    frame["log_scale"] = std
                    frame["selected_arm"] = selection_e2["selected"] == "F1_qp_frequency"
                    frame["trusted_for_unmeasured_optimization"] = False
                    frame["evaluation_status"] = "retrospective_seen_holdouts"
                    outputs_e2.append(frame)
            print(f"{scope}: E2 outer fitted", flush=True)
    outer_e2 = pd.concat(outputs_e2, ignore_index=True)
    e2_dir = args.out / "experiment2_qp_freq"
    outer_e2.to_csv(e2_dir / "predictions.csv", index=False)
    metrics_e2, joint_e2, failures_e2 = summarize(outer_e2)
    metrics_e2.to_csv(e2_dir / "metrics.csv", index=False)
    joint_e2.to_csv(e2_dir / "joint_coverage.csv", index=False)
    failures_e2.to_csv(e2_dir / "interval_failures.csv", index=False)
    small_e2 = outer_e2[
        outer_e2.scope.eq("area_edges")
        & outer_e2.area_um2.eq(700)
        & outer_e2.has_pf.eq(1)
        & outer_e2.target.eq("qp")
    ]
    small_e2.to_csv(e2_dir / "small_area_pf_qp.csv", index=False)
    joblib.dump(models, e2_dir / "models.joblib")
    write_json(e2_dir / "calibration.json", calibrations)

    for key, root in [("p3", args.p3_root), ("p6c", args.p6c_root), ("p6d", args.p6d_root),
                      ("p8", args.p8_root), ("p9", args.p9_root)]:
        files = manifests[key].get("files", manifests[key])
        for name, info in files.items():
            digest = info["sha256"] if isinstance(info, dict) else info
            assert sha256(root / name) == digest, name
    assert all(sha256(Path(p)) == h for p, h in hashes.items())
    write_json(
        args.out / "summary.json",
        dict(
            status="P10_retrospective_complete_not_deployment",
            selected_e1=selection_e1["selected"],
            selected_e2=selection_e2["selected"],
            selection_uses_outer_test=False,
            simulation_labels_used=False,
            new_blind_test=False,
            production_changed=False,
        ),
    )
    write_json(
        args.out / "outputs_manifest.json",
        {
            str(p.relative_to(args.out)): sha256(p)
            for p in sorted(args.out.rglob("*"))
            if p.is_file()
        },
    )


if __name__ == "__main__":
    main()
