"""Independent P10 verification: both sub-experiments' selections, controls, identities."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_bias_p7 import guard_score, primary_score
from app.ml.optimization_data import sha256, write_json
from app.ml.optimization_p10 import (
    assemble_e1_arms,
    eval_e1_inner,
    select_e1,
    select_e2,
    subset_mask,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ["root", "p3-root", "p6c-root", "p9-root", "out"]:
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)

    frozen = json.loads((args.root / "frozen_inputs.json").read_text())
    checks = {}
    checks["source_files_unchanged"] = bool(
        all(sha256(Path(p)) == h for p, h in frozen["sources"].items() if Path(p).is_file())
    )
    structures = pd.read_csv(args.p3_root / "data/structures.csv")
    area_by_id = structures.set_index("structure_id").area_um2

    # E1: rebuild from P9 frozen rows and recompute metrics/selection independently.
    p9_inner = pd.read_csv(args.p9_root / "inner_interval_predictions.csv")
    c0_inner = p9_inner[p9_inner.arm.eq("C0_control")].reset_index(drop=True)
    c1_inner = p9_inner[p9_inner.arm.eq("C1_hetero_gp")].reset_index(drop=True)
    metrics = eval_e1_inner(assemble_e1_arms(c0_inner, c1_inner))
    saved_metrics = pd.read_csv(args.root / "experiment1_intervals/inner_interval_metrics.csv")
    checks["e1_inner_metrics_recomputed_match"] = bool(
        np.allclose(
            metrics.subset_qp_coverage, saved_metrics.subset_qp_coverage, rtol=1e-12, atol=0
        )
        and np.allclose(
            metrics.balanced_coverage, saved_metrics.balanced_coverage, rtol=1e-12, atol=0
        )
        and np.allclose(
            metrics.subset_median_half_width,
            saved_metrics.subset_median_half_width,
            rtol=1e-12,
            atol=0,
        )
    )
    p9_stress = pd.read_csv(args.p9_root / "area_stress_predictions.csv")
    skeys = ["scope", "structure_id", "scenario", "target"]
    s_c0 = p9_stress[p9_stress.arm.eq("C0_control")].sort_values(skeys).reset_index(drop=True)
    s_c1 = p9_stress[p9_stress.arm.eq("C1_hetero_gp")].sort_values(skeys).reset_index(drop=True)
    assert s_c0[skeys].equals(s_c1[skeys])
    s_d2 = s_c0.copy()
    s_d2["area_um2"] = s_d2.structure_id.map(area_by_id)
    take = subset_mask(s_d2).to_numpy()
    for col in ["prediction", "abs_log_error"]:
        s_d2.loc[take, col] = s_c1[col].to_numpy()[take]
    s_d2 = s_d2.drop(columns=["area_um2"])
    i_d2 = c0_inner.copy()
    take_i = subset_mask(i_d2).to_numpy()
    i_d2.loc[take_i, "abs_log_error"] = c1_inner.abs_log_error.to_numpy()[take_i]
    inner_points = pd.concat(
        [
            c0_inner.assign(arm="D0_control"),
            c0_inner.assign(arm="D1_subset_hetero_interval"),
            i_d2.assign(arm="D2_subset_c1_switch"),
        ],
        ignore_index=True,
    )
    stress_points = pd.concat(
        [
            s_c0.assign(arm="D0_control"),
            s_c0.assign(arm="D1_subset_hetero_interval"),
            s_d2.assign(arm="D2_subset_c1_switch"),
        ],
        ignore_index=True,
    )
    e1_primary, _ = primary_score(stress_points)
    e1_guard, _ = guard_score(inner_points)
    expected_e1 = select_e1(metrics, e1_primary, e1_guard)
    saved_e1 = json.loads((args.root / "selection_e1.json").read_text())
    checks["e1_selection_recomputed_match"] = bool(
        expected_e1["selected"] == saved_e1["selected"]
        and expected_e1["verdicts"] == saved_e1["verdicts"]
    )

    # E1 outer: non-subset rows bit-identical to D0; D0 matches P9 C0 rows.
    outer1 = pd.read_csv(args.root / "experiment1_intervals/predictions.csv")
    d0 = outer1[outer1.arm.eq("D0_control")]
    for arm in ["D1_subset_hetero_interval", "D2_subset_c1_switch"]:
        got = outer1[outer1.arm.eq(arm) & ~subset_mask(outer1)]
        ref = d0[~subset_mask(d0)]
        checks[f"e1_outer_non_subset_identical_{arm}"] = bool(
            got[["prediction", "lower", "upper"]].reset_index(drop=True).equals(
                ref[["prediction", "lower", "upper"]].reset_index(drop=True)
            )
        )
    p9_pred = pd.read_csv(args.p9_root / "predictions.csv")
    c0p = p9_pred[p9_pred.arm.eq("C0_control")]
    merged = d0.merge(
        c0p,
        on=["structure_id", "scope", "interval", "target"],
        suffixes=("", "_ref"),
    )
    checks["e1_d0_outer_matches_p9_c0"] = bool(
        len(merged) == len(d0)
        and np.allclose(merged.prediction, merged.prediction_ref, rtol=1e-12, atol=0)
        and np.allclose(merged.lower, merged.lower_ref, rtol=1e-12, atol=0)
        and np.allclose(merged.upper, merged.upper_ref, rtol=1e-12, atol=0)
    )

    # E2: F0 rows match P6C; selection recomputed from saved predictions.
    outer2 = pd.read_csv(args.root / "experiment2_qp_freq/predictions.csv")
    p6c_pred = pd.read_csv(args.p6c_root / "experiment/predictions.csv")
    f0 = outer2[outer2.arm.eq("F0_control")]
    ref0 = p6c_pred[p6c_pred.recipe.eq("trend_gp_log_area")]
    merged2 = f0.merge(
        ref0, on=["structure_id", "scope", "interval", "target"], suffixes=("", "_ref")
    )
    checks["e2_f0_outer_matches_p6c_rtol_1e-12"] = bool(
        len(merged2) == len(f0)
        and np.allclose(merged2.prediction, merged2.prediction_ref, rtol=1e-12, atol=0)
        and np.allclose(merged2.lower, merged2.lower_ref, rtol=1e-12, atol=0)
        and np.allclose(merged2.upper, merged2.upper_ref, rtol=1e-12, atol=0)
    )
    f1 = outer2[outer2.arm.eq("F1_qp_frequency")]
    checks["e2_contract_labels"] = bool(
        (f1[f1.target.eq("qp")].contract == "measured_frequency").all()
        and (f1[~f1.target.eq("qp")].contract == "geometry_only").all()
        and (f0.contract == "geometry_only").all()
    )
    stress2 = pd.read_csv(args.root / "experiment2_qp_freq/area_stress_predictions.csv")
    inner2 = pd.read_csv(args.root / "experiment2_qp_freq/inner_predictions.csv")
    e2_primary, _ = primary_score(stress2)
    e2_guard, _ = guard_score(inner2)
    expected_e2 = select_e2(e2_primary, e2_guard)
    saved_e2 = json.loads((args.root / "selection_e2.json").read_text())
    checks["e2_selection_recomputed_match"] = bool(
        expected_e2["selected"] == saved_e2["selected"]
        and expected_e2["primary_scores"] == saved_e2["primary_scores"]
        and expected_e2["guard_scores"] == saved_e2["guard_scores"]
    )
    for name in ["selection_e1.json", "selection_e2.json"]:
        checks[f"{name}_older_than_outer"] = (
            (args.root / name).stat().st_mtime
            <= (args.root / "experiment1_intervals/predictions.csv").stat().st_mtime
            and (args.root / name).stat().st_mtime
            <= (args.root / "experiment2_qp_freq/predictions.csv").stat().st_mtime
        )
    checks["trusted_flag_false_everywhere"] = bool(
        (~outer1.trusted_for_unmeasured_optimization).all()
        and (~outer2.trusted_for_unmeasured_optimization).all()
    )
    manifest = json.loads((args.root / "outputs_manifest.json").read_text())
    checks["outputs_manifest_intact"] = all(
        sha256(args.root / name) == h for name, h in manifest.items()
    )
    write_json(args.out, dict(checks=checks, all_passed=all(checks.values())))
    print(json.dumps(checks, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
