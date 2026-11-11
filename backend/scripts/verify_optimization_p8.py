"""Independent P8 verification: recompute selection scores and control correspondences."""

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
from app.ml.optimization_freq_p8 import ARMS, CONTRACT, select_arm_p8

SCOPES = ["structure_holdout", "area_edges", "eg_upper", "fl_upper"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--p6c-root", type=Path, required=True)
    ap.add_argument("--p6d-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)

    frozen = json.loads((args.root / "frozen_inputs.json").read_text())
    checks = {}
    checks["source_files_unchanged"] = bool(
        all(sha256(Path(p)) == h for p, h in frozen["sources"].items() if Path(p).is_file())
    )

    inner = pd.read_csv(args.root / "inner_predictions.csv")
    stress = pd.read_csv(args.root / "area_stress_predictions.csv")
    control_ok = []
    for scope in SCOPES:
        ref = pd.read_csv(args.p6c_root / "experiment" / f"{scope}_inner_predictions.csv")
        ref = ref[ref.recipe.eq("trend_gp_log_area")].sort_values(["structure_id", "target"])
        got = (
            inner[inner.arm.eq("B0_control") & inner.scope.eq(scope)]
            .sort_values(["structure_id", "target"])
            .reset_index(drop=True)
        )
        control_ok.append(
            len(ref) == len(got)
            and np.allclose(
                ref.abs_log_error.to_numpy(), got.abs_log_error.to_numpy(), rtol=1e-12, atol=0
            )
        )
        refs = pd.read_csv(args.p6d_root / "experiment" / f"{scope}_stress_predictions.csv")
        refs = refs[
            refs.recipe.eq("trend_gp_log_area")
            & refs.scenario.isin(["area_um2_low", "area_um2_high"])
        ].sort_values(["structure_id", "scenario", "target"])
        gots = (
            stress[stress.arm.eq("B0_control") & stress.scope.eq(scope)]
            .sort_values(["structure_id", "scenario", "target"])
            .reset_index(drop=True)
        )
        control_ok.append(
            len(refs) == len(gots)
            and np.allclose(
                refs.abs_log_error.to_numpy(), gots.abs_log_error.to_numpy(), rtol=1e-12, atol=0
            )
        )
    checks["b0_matches_p6c_p6d_rtol_1e-12"] = bool(all(control_ok))
    checks["inner_folds_group_indivisible"] = bool(
        inner.groupby(["scope", "design_group_id"]).fold.nunique().eq(1).all()
    )

    primary, _ = primary_score(stress)
    guard, _ = guard_score(inner)
    expected = select_arm_p8(primary, guard)
    saved = json.loads((args.root / "selection.json").read_text())
    checks["selection_recomputed_match"] = bool(
        expected["selected"] == saved["selected"]
        and all(np.isclose(expected["primary_scores"][a], saved["primary_scores"][a]) for a in ARMS)
        and all(np.isclose(expected["guard_scores"][a], saved["guard_scores"][a]) for a in ARMS)
    )
    checks["selection_file_older_than_predictions"] = (
        (args.root / "selection.json").stat().st_mtime
        <= (args.root / "predictions.csv").stat().st_mtime
    )

    predictions = pd.read_csv(args.root / "predictions.csv")
    checks["trusted_flag_false_everywhere"] = bool(
        (~predictions.trusted_for_unmeasured_optimization).all()
    )
    checks["contract_labels_match_arm"] = bool(
        all(
            set(g.contract.unique()) == {CONTRACT[arm]}
            for arm, g in predictions.groupby("arm")
        )
    )
    outer_p6c = pd.read_csv(args.p6c_root / "experiment/predictions.csv")
    b0_rows = predictions[predictions.arm.eq("B0_control")]
    ref_rows = outer_p6c[outer_p6c.recipe.eq("trend_gp_log_area")]
    merged = b0_rows.merge(
        ref_rows,
        on=["structure_id", "scope", "interval", "target"],
        suffixes=("", "_ref"),
    )
    checks["b0_outer_rows_match_p6c_rtol_1e-12"] = bool(
        len(merged) == len(b0_rows)
        and np.allclose(merged.prediction, merged.prediction_ref, rtol=1e-12, atol=0)
        and np.allclose(merged.lower, merged.lower_ref, rtol=1e-12, atol=0)
        and np.allclose(merged.upper, merged.upper_ref, rtol=1e-12, atol=0)
    )
    small = pd.read_csv(args.root / "small_area_pf_qp.csv")
    checks["small_area_pf_qp_completeness"] = bool(
        small.groupby(["arm", "interval"]).structure_id.nunique().eq(8).all()
        and set(small.arm.unique()) == set(ARMS)
    )
    manifest = json.loads((args.root / "outputs_manifest.json").read_text())
    checks["outputs_manifest_intact"] = all(
        sha256(args.root / name) == h
        for name, h in manifest.items()
        if name != "outputs_manifest.json"
    )
    write_json(args.out, dict(checks=checks, all_passed=all(checks.values())))
    print(json.dumps(checks, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
