"""P6B read-only measured residual and simulation peak sensitivity diagnostics."""

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
from app.ml.optimization_data import FEATURES, TARGETS, sha256, write_json
from app.ml.optimization_peak_diagnostics import direction, spectrum_probe


def verify_delivery(root):
    receipt = json.loads((root / "delivery_manifest.json").read_text())
    for name, info in receipt["files"].items():
        assert sha256(root / name) == info["sha256"], name
    for field in ["sources", "documents"]:
        for path, expected in receipt[field].items():
            assert sha256(Path(path)) == expected, path
    return sha256(root / "delivery_manifest.json")


def measured_diagnostics(p3, p6a, out):
    structures = pd.read_csv(p3 / "data/structures.csv")
    models = joblib.load(p6a / "pilot/retrospective_models.joblib")
    calibrations = json.loads((p6a / "pilot/calibration.json").read_text())
    frames = []
    with threadpool_limits(limits=1):
        for scope in ["area_edges", "eg_upper"]:
            manifest = pd.read_csv(p6a / f"pilot/split_{scope}.csv")
            for role in ["calibration", "test"]:
                frame = structures.merge(manifest[["structure_id", "role"]], validate="one_to_one")
                frame = frame[frame.role.eq(role)].copy()
                for target in ["qp"] if scope == "area_edges" else TARGETS:
                    model = models[scope][("gp", target)]
                    mean, scale = model.predict_log(frame)
                    q = calibrations[scope][f"gp_matern_scaled_joint:{target}"]["quantile"]
                    output = frame.copy()
                    output["scope"], output["target"] = scope, target
                    output["actual"], output["prediction"] = frame[target], np.exp(mean)
                    output["log_scale"], output["threshold"] = scale, q
                    output["signed_standardized_log_residual"] = (
                        np.log(frame[target]) - mean
                    ) / scale
                    output["abs_standardized_log_residual"] = np.abs(
                        output.signed_standardized_log_residual
                    )
                    output["lower"], output["upper"] = (
                        np.exp(mean - q * scale),
                        np.exp(mean + q * scale),
                    )
                    output["covered"] = output.actual.between(output.lower, output.upper)
                    output["signed_error"] = output.prediction - output.actual
                    output["absolute_error"] = output.signed_error.abs()
                    output["ape"] = output.absolute_error / output.actual
                    output["band"] = (
                        np.where(
                            output.area_um2.eq(700),
                            "700",
                            np.where(output.area_um2.eq(5500), "5500", "interior"),
                        )
                        if scope == "area_edges"
                        else role
                    )
                    output["observation_relative_mad"] = (
                        output[f"{target}_position_mad"] / output.actual
                    )
                    output["prediction_inside_observation_ci"] = output.prediction.between(
                        output[f"{target}_ci_low"], output[f"{target}_ci_high"]
                    )
                    for branch, positions in output.groupby("branch_id").groups.items():
                        state = model.branches[branch]
                        fitted = state["model"]
                        x = (
                            output.loc[positions, list(FEATURES)].to_numpy() - state["low"]
                        ) / state["scale"]
                        similarity = fitted.kernel_.k1(x, fitted.X_train_)
                        output.loc[positions, "max_signal_kernel_similarity"] = similarity.max(
                            axis=1
                        )
                        output.loc[positions, "prior_log_mean"] = float(fitted._y_train_mean)
                    output["prediction_log_distance_to_prior"] = np.abs(
                        mean - output.prior_log_mean
                    )
                    output["actual_log_distance_to_prior"] = np.abs(
                        np.log(output.actual) - output.prior_log_mean
                    )
                    frames.append(output)
    combined = pd.concat(frames, ignore_index=True)
    combined.to_csv(out / "measured_residuals.csv", index=False)
    metrics = []
    for keys, group in combined.groupby(["scope", "target", "role", "band", "branch_id"]):
        metrics.append(
            dict(zip(["scope", "target", "role", "band", "branch_id"], keys, strict=True))
            | dict(
                n=len(group),
                n_design_groups=group.design_group_id.nunique(),
                covered=int(group.covered.sum()),
                mean_signed_error=group.signed_error.mean(),
                mae=group.absolute_error.mean(),
                median_ape=group.ape.median(),
                median_abs_normalized_residual=group.abs_standardized_log_residual.median(),
                max_abs_normalized_residual=group.abs_standardized_log_residual.max(),
                joint_threshold=group.threshold.iloc[0],
                median_observation_relative_mad=group.observation_relative_mad.median(),
                predictions_inside_observation_ci=int(group.prediction_inside_observation_ci.sum()),
                median_kernel_similarity=group.max_signal_kernel_similarity.median(),
            )
        )
    pd.DataFrame(metrics).to_csv(out / "measured_branch_summary.csv", index=False)
    small = combined[
        (combined.scope == "area_edges") & (combined.role == "test") & (combined.band == "700")
    ]
    small.to_csv(out / "small_area_qp.csv", index=False)
    old = pd.read_csv(p3 / "evaluation/boundary_predictions.csv")
    old = old[old.boundary.eq("area_edges") & old.target.eq("qp") & old.area_um2.eq(700)]
    old.to_csv(out / "small_area_qp_frozen_baselines.csv", index=False)
    return combined, small


def simulation_diagnostics(p6a, out):
    import skrf as rf

    simulation = pd.read_csv(p6a / "simulation_audit_v2/simulation_extraction.csv").set_index(
        "Training_Row_ID"
    )
    trends = pd.read_csv(p6a / "simulation_audit_v2/trend_series.csv")
    selected = trends[
        (trends.cohort == "all_audited")
        & (trends.axis == "Train_L_EG_um")
        & (trends.metric == "fp_ghz")
    ]
    ids = sorted({sid for rows in selected.row_ids for sid in rows.split(";")})
    records = []
    for sid in ids:
        row = simulation.loc[sid]
        path = Path(row.resolved_path)
        assert sha256(path) == row.source_sha256, path
        network = rf.Network(str(path))
        probe = spectrum_probe(network.f, network.z[:, 0, 0])
        assert np.isclose(probe["fs_hz"], row.fs_ghz * 1e9, atol=1, rtol=0)
        assert np.isclose(probe["fp_hz"], row.fp_ghz * 1e9, atol=1, rtol=0)
        records.append(dict(Training_Row_ID=sid, source_sha256=row.source_sha256, **probe))
    spectra = pd.DataFrame(records).set_index("Training_Row_ID")
    spectra.to_csv(out / "simulation_peak_probes.csv")
    rows, pairs = [], []
    for series in selected.itertuples():
        sids = series.row_ids.split(";")
        g = simulation.loc[sids].join(spectra, rsuffix="_probe").sort_values("Train_L_EG_um")
        fp_track, fs_track = None, None
        track_mismatches = []
        for sid, row in g.iterrows():
            current = []
            for kind, previous in [("fp", fp_track), ("fs", fs_track)]:
                candidates = row[f"{kind}_candidates_hz"]
                chosen = (
                    row[f"{kind}_hz"]
                    if previous is None
                    else min(candidates, key=lambda x: abs(x - previous))
                )
                current.append(chosen)
                if (
                    previous is not None
                    and abs(chosen - row[f"{kind}_hz"]) > row[f"{kind}_step_hz"] / 2
                ):
                    track_mismatches.append(f"{sid}:{kind}")
            fp_track, fs_track = current
        for metric, discrete, refined, low, high in [
            (
                "fp_ghz",
                g.fp_hz / 1e9,
                g.fp_refined_hz / 1e9,
                (g.fp_hz - g.fp_step_hz / 2) / 1e9,
                (g.fp_hz + g.fp_step_hz / 2) / 1e9,
            ),
            ("k2eff_pct", g.k2_discrete, g.k2_refined, g.k2_grid_low, g.k2_grid_high),
        ]:
            original = trends[
                (trends.cohort == "all_audited")
                & (trends.series_id == series.series_id)
                & (trends.metric == metric)
            ].iloc[0]
            assert direction(discrete) == original.direction
            deltas = np.diff(discrete)
            delta_low = low.to_numpy()[1:] - high.to_numpy()[:-1]
            delta_high = high.to_numpy()[1:] - low.to_numpy()[:-1]
            rows.append(
                dict(
                    series_id=series.series_id,
                    metric=metric,
                    n_levels=len(g),
                    controls=series.controls,
                    row_ids=series.row_ids,
                    original_direction=original.direction,
                    refined_direction=direction(refined),
                    n_negative_discrete=int((deltas < -1e-9).sum()),
                    n_negative_beyond_half_bin_envelope=int((delta_high < -1e-9).sum()),
                    smallest_delta=float(deltas.min()),
                    n_competitive_fp=int(g.fp_competitor_gap_db.le(3).sum()),
                    n_competitive_fs=int(g.fs_competitor_gap_db.le(3).sum()),
                    nearest_peak_continuity_mismatches=";".join(track_mismatches),
                    physical_mode_identity_verified=False,
                )
            )
            for i in range(len(g) - 1):
                pairs.append(
                    dict(
                        series_id=series.series_id,
                        metric=metric,
                        low_id=g.index[i],
                        high_id=g.index[i + 1],
                        low_eg=g.Train_L_EG_um.iloc[i],
                        high_eg=g.Train_L_EG_um.iloc[i + 1],
                        delta=float(deltas[i]),
                        refined_delta=float(np.diff(refined)[i]),
                        half_bin_delta_low=float(delta_low[i]),
                        half_bin_delta_high=float(delta_high[i]),
                        half_bin_envelope_contains_zero=bool(delta_low[i] <= 0 <= delta_high[i]),
                    )
                )
    pd.DataFrame(rows).to_csv(out / "eg_series_diagnostics.csv", index=False)
    pd.DataFrame(pairs).to_csv(out / "eg_pair_diagnostics.csv", index=False)
    return pd.DataFrame(rows), pd.DataFrame(pairs), spectra


def figures(p6a, out, small, series):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 10})
    fig, ax = plt.subplots(figsize=(7, 5))
    for pf, colour in [(0, "#0072B2"), (1, "#D55E00")]:
        g = small[small.has_pf.eq(pf)]
        ax.errorbar(
            g.actual,
            g.prediction,
            yerr=[g.prediction - g.lower, g.upper - g.prediction],
            fmt="o",
            markersize=4,
            alpha=0.75,
            color=colour,
            label=f"PF={pf}",
        )
    limits = [
        min(small.actual.min(), small.lower.min()) * 0.95,
        max(small.actual.max(), small.upper.max()) * 1.05,
    ]
    ax.plot(limits, limits, "k--", lw=1)
    ax.set(
        xlabel="Observed Qp (structure median)",
        ylabel="Frozen GP Qp with joint interval",
        xlim=limits,
        ylim=limits,
    )
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "small_area_qp.png", dpi=180)
    plt.close(fig)
    simulation = pd.read_csv(p6a / "simulation_audit_v2/simulation_extraction.csv").set_index(
        "Training_Row_ID"
    )
    import skrf as rf

    cases = series[series.metric.eq("fp_ghz") & series.original_direction.eq("nonmonotonic")]
    fig, axes = plt.subplots(len(cases), 2, figsize=(10, 4 * len(cases)), squeeze=False)
    for i, case in enumerate(cases.itertuples()):
        group = simulation.loc[case.row_ids.split(";")].sort_values("Train_L_EG_um")
        for row in group.itertuples():
            network = rf.Network(row.resolved_path)
            f = network.f / 1e9
            zdb = 20 * np.log10(np.abs(network.z[:, 0, 0]))
            for ax in axes[i]:
                ax.plot(f, zdb, lw=1, label=f"EG={row.Train_L_EG_um:g} um")
            axes[i, 0].set_xlim(group.fs_ghz.min() - 0.015, group.fp_ghz.max() + 0.015)
            axes[i, 1].set_xlim(group.fp_ghz.min() - 0.003, group.fp_ghz.max() + 0.003)
        for ax in axes[i]:
            ax.set(xlabel="Frequency (GHz)", ylabel="20 log10 |Z / ohm|")
            ax.legend(fontsize=8)
        axes[i, 0].set_title(
            f"Area={group.Train_A_res_um2.iloc[0]:g} um2, FL={group.Train_L_FL_um.iloc[0]:g} um"
        )
        axes[i, 1].set_title("Anti-resonance detail; mode identity unverified")
    fig.tight_layout()
    fig.savefig(out / "eg_fp_counterexamples.png", dpi=180)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--p3-root", type=Path, required=True)
    ap.add_argument("--p6a-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--protocol", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    old_hash = verify_delivery(args.p6a_root)
    sources = [
        Path(__file__).resolve(),
        Path(__file__).resolve().parents[1] / "app/ml/optimization_peak_diagnostics.py",
        args.protocol,
    ]
    hashes = {str(p): sha256(p) for p in sources}
    args.out.mkdir(parents=True)
    write_json(
        args.out / "frozen_inputs.json",
        dict(
            p6a_delivery_sha256=old_hash,
            sources=hashes,
            new_blind_test=False,
            model_fitting=False,
            simulation_used_in_measured_model=False,
        ),
    )
    (args.out / "protocol.md").write_bytes(args.protocol.read_bytes())
    measured, small = measured_diagnostics(args.p3_root, args.p6a_root, args.out)
    print("Measured diagnostics complete", flush=True)
    series, pairs, spectra = simulation_diagnostics(args.p6a_root, args.out)
    figures(args.p6a_root, args.out, small, series)
    assert verify_delivery(args.p6a_root) == old_hash
    assert all(sha256(Path(p)) == h for p, h in hashes.items())
    summary = dict(
        status="P6B_diagnostics_complete_no_model_change",
        measured_rows=len(measured),
        small_area_qp_n=len(small),
        small_area_qp_covered=int(small.covered.sum()),
        pf_small_qp_n=int(small.has_pf.sum()),
        pf_small_qp_covered=int(small[small.has_pf.eq(1)].covered.sum()),
        n_simulation_files=len(spectra),
        n_eg_sequences=series.series_id.nunique(),
        n_simulation_metric_pairs=len(pairs),
        physical_mode_identity_verified=False,
    )
    write_json(args.out / "summary.json", summary)
    write_json(
        args.out / "outputs_manifest.json",
        {p.name: sha256(p) for p in sorted(args.out.iterdir()) if p.is_file()},
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
