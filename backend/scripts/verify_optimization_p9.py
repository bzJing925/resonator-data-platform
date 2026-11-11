"""Independent P9 verification: recompute interval metrics, selection, control correspondences."""

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
from app.ml.optimization_label_p9 import ARMS, inner_interval_metrics, select_arm_p9

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

    inner = pd.read_csv(args.root / "inner_interval_predictions.csv")
    stress = pd.read_csv(args.root / "area_stress_predictions.csv")
    control_inner = []
    for scope in SCOPES:
        ref = pd.read_csv(args.p6c_root / "experiment" / f"{scope}_inner_predictions.csv")
        ref = ref[ref.recipe.eq("trend_gp_log_area")].sort_values(["structure_id", "target"])
        got = (
            inner[inner.arm.eq("C0_control") & inner.scope.eq(scope)]
            .sort_values(["structure_id", "target"])
            .reset_index(drop=True)
        )
        control_inner.append(
            len(ref) == len(got)
            and np.allclose(
                ref.abs_log_error.to_numpy(), got.abs_log_error.to_numpy(), rtol=1e-12, atol=0
            )
        )
    checks["c0_inner_matches_p6c_rtol_1e-12"] = bool(all(control_inner))

    predictions = pd.read_csv(args.root / "predictions.csv")
    outer_p6c = pd.read_csv(args.p6c_root / "experiment/predictions.csv")
    c0_rows = predictions[predictions.arm.eq("C0_control")]
    ref_rows = outer_p6c[outer_p6c.recipe.eq("trend_gp_log_area")]
    merged = c0_rows.merge(
        ref_rows,
        on=["structure_id", "scope", "interval", "target"],
        suffixes=("", "_ref"),
    )
    checks["c0_outer_rows_match_p6c_rtol_1e-12"] = bool(
        len(merged) == len(c0_rows)
        and np.allclose(merged.prediction, merged.prediction_ref, rtol=1e-12, atol=0)
        and np.allclose(merged.lower, merged.lower_ref, rtol=1e-12, atol=0)
        and np.allclose(merged.upper, merged.upper_ref, rtol=1e-12, atol=0)
    )

    checks["inner_folds_group_indivisible"] = bool(
        inner.groupby(["scope", "design_group_id"]).fold.nunique().eq(1).all()
    )
    metrics = inner_interval_metrics(inner)
    point_primary, _ = primary_score(stress)
    point_guard, _ = guard_score(inner)
    point_primary["C2_hetero_calibration"] = point_primary["C0_control"]
    point_guard["C2_hetero_calibration"] = point_guard["C0_control"]
    expected = select_arm_p9(metrics, point_primary, point_guard)
    saved = json.loads((args.root / "selection.json").read_text())
    checks["selection_recomputed_match"] = bool(
        expected["selected"] == saved["selected"] and expected["verdicts"] == saved["verdicts"]
    )
    checks["selection_file_older_than_predictions"] = (
        (args.root / "selection.json").stat().st_mtime
        <= (args.root / "predictions.csv").stat().st_mtime
    )
    checks["trusted_flag_false_everywhere"] = bool(
        (~predictions.trusted_for_unmeasured_optimization).all()
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
