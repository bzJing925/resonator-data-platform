"""Verify P6B against frozen predictions and raw local frequency spacings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import skrf as rf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_data import sha256, write_json
from scripts.run_optimization_p6b import verify_delivery


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--p6a-root", type=Path, required=True)
    args = ap.parse_args()
    root = args.root
    d = root / "diagnostics_v2"
    verify_delivery(args.p6a_root)
    protected = json.loads((root / "pre_run_protected.json").read_text())
    for path, h in protected.items():
        assert sha256(Path(path)) == h, path
    counts = {}
    for name in [
        "optimization_p3_20261015",
        "optimization_p4_20261016",
        "optimization_p5_20261016",
    ]:
        old = root.parent / name
        receipt = json.loads((old / "receipts/delivery_manifest.json").read_text())
        for path, info in receipt["files"].items():
            assert sha256(old / path) == info["sha256"], path
        counts[name] = len(receipt["files"])
    for path, h in json.loads((d / "outputs_manifest.json").read_text()).items():
        assert sha256(d / path) == h, path
    for path, h in json.loads((d / "frozen_inputs.json").read_text())["sources"].items():
        assert sha256(Path(path)) == h, path
    measured = pd.read_csv(d / "measured_residuals.csv")
    np.testing.assert_allclose(
        measured.signed_standardized_log_residual,
        (np.log(measured.actual) - np.log(measured.prediction)) / measured.log_scale,
        atol=1e-12,
    )
    original = pd.read_csv(args.p6a_root / "pilot/new_predictions.csv")
    original = original[original.model.eq("gp_matern_scaled_joint")]
    check = measured[measured.role.eq("test")].merge(
        original,
        on=["scope", "target", "structure_id"],
        suffixes=("_new", "_frozen"),
        validate="one_to_one",
    )
    assert len(check) == 480
    for key in ["prediction", "lower", "upper", "actual", "log_scale"]:
        np.testing.assert_allclose(check[key + "_new"], check[key + "_frozen"], rtol=1e-12)
    small = measured[
        measured.scope.eq("area_edges") & measured.role.eq("test") & measured.area_um2.eq(700)
    ]
    assert len(small) == 48 and small.covered.sum() == 30
    assert len(small[small.has_pf.eq(1)]) == 8 and not small[small.has_pf.eq(1)].covered.any()
    simulation = pd.read_csv(
        args.p6a_root / "simulation_audit_v2/simulation_extraction.csv"
    ).set_index("Training_Row_ID")
    probes = pd.read_csv(d / "simulation_peak_probes.csv").set_index("Training_Row_ID")
    for sid, row in probes.iterrows():
        path = Path(simulation.loc[sid, "resolved_path"])
        assert sha256(path) == row.source_sha256
        net = rf.Network(str(path))
        for kind in ["fs", "fp"]:
            ix = int(np.argmin(np.abs(net.f - row[f"{kind}_hz"])))
            assert abs(net.f[ix] - row[f"{kind}_hz"]) < 1
            local = max(net.f[ix + 1] - net.f[ix], net.f[ix] - net.f[ix - 1])
            np.testing.assert_allclose(local, row[f"{kind}_step_hz"], rtol=1e-10)
    pairs = pd.read_csv(d / "eg_pair_diagnostics.csv")
    for metric, rows in pairs.groupby("metric"):
        lo = probes.loc[rows.low_id]
        hi = probes.loc[rows.high_id]
        field = "fp_hz" if metric == "fp_ghz" else "k2_discrete"
        divisor = 1e9 if metric == "fp_ghz" else 1
        np.testing.assert_allclose(
            rows.delta, (hi[field].to_numpy() - lo[field].to_numpy()) / divisor, atol=2e-15
        )
    failed = pairs[pairs.metric.eq("k2eff_pct") & pairs.half_bin_delta_high.lt(-1e-9)]
    assert len(failed) == 4 and failed.series_id.nunique() == 3
    write_json(
        root / "verification.json",
        dict(
            status="passed",
            protected_files=len(protected),
            preserved_old_artifact_counts=counts,
            preserved_p6a_files=74,
            measured_rows=len(measured),
            frozen_test_predictions_checked=len(check),
            raw_simulations_local_grid_checked=len(probes),
            simulation_pairs_checked=len(pairs),
            negative_k2_pairs_beyond_local_half_bin=4,
            original_diagnostic_v1_superseded=True,
            model_changed=False,
            new_blind_test=False,
        ),
    )
    print((root / "verification.json").read_text())


if __name__ == "__main__":
    main()
