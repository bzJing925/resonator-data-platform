"""Independent P12 verification: grid, peaks, catalog exclusion, rankings, plan contents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_data import sha256, write_json
from app.ml.optimization_p12 import fine_grid, mark_catalog_matches


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ["root", "p4-root", "p11-root", "out"]:
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)

    frozen = json.loads((args.root / "frozen_inputs.json").read_text())
    checks = {}
    checks["source_files_unchanged"] = bool(
        all(sha256(Path(p)) == h for p, h in frozen["sources"].items() if Path(p).is_file())
    )
    grid = pd.read_csv(args.root / "grid_predictions.csv")
    expected = fine_grid()
    checks["grid_matches_frozen_definition"] = bool(
        len(grid) == len(expected) == 9801
        and np.allclose(grid.eg_um, expected.eg_um, rtol=0, atol=1e-12)
        and np.allclose(grid.fl_um, expected.fl_um, rtol=0, atol=1e-12)
    )
    candidates = pd.read_csv(args.root / "candidates.csv")
    measured = pd.read_csv(args.p4_root / "measured/candidates.csv")
    cat_keys = set(
        zip(measured.eg_um, measured.fl_um, measured.area_um2, measured.has_pf, strict=True)
    )
    viewable = candidates[candidates.group.isin(["P12", "P11"])]
    checks["no_viewable_candidate_in_measured_catalog"] = bool(
        not any(
            (r.eg_um, r.fl_um, r.area_um2, r.has_pf) in cat_keys for r in viewable.itertuples()
        )
    )
    mask = mark_catalog_matches(grid, measured)
    free = grid[~mask]
    peak_ok = []
    searches = pd.read_csv(args.root / "peak_searches.csv")
    for target in ["qp", "k2eff_pct"]:
        for model in ["p3", "p6c"]:
            col = f"{target}_{model}"
            peak = free.loc[free[col].idxmax()]
            hit = viewable[
                np.isclose(viewable.eg_um, peak.eg_um, atol=1e-6)
                & np.isclose(viewable.fl_um, peak.fl_um, atol=1e-6)
            ]
            peak_ok.append(len(hit) >= 1)  # Every model/target peak is a viewable candidate.
            row = searches[
                searches.target.eq(target)
                & searches.model.eq(model)
                & searches.method.eq("grid_argmax")
            ].iloc[0]
            peak_ok.append(
                np.isclose(row.eg_um, peak.eg_um) and np.isclose(row.fl_um, peak.fl_um)
            )
    checks["peaks_recomputed_from_grid_match"] = bool(all(peak_ok))
    p11 = pd.read_csv(args.p11_root / "candidates.csv")
    p11_ab = p11[p11.group.isin(["A", "B"])]
    retained = candidates[candidates.group.eq("P11")]
    merged = retained.merge(
        p11_ab,
        on=["eg_um", "fl_um", "area_um2", "has_pf"],
        suffixes=("", "_p11"),
    )
    checks["p11_retained_values_match_at_1e-9"] = bool(
        len(merged) == len(p11_ab) == 10
        and np.allclose(merged.qp_prediction, merged.qp_prediction_p11, rtol=0, atol=1e-9)
        and np.allclose(
            merged.k2eff_pct_prediction, merged.k2eff_pct_prediction_p11, rtol=0, atol=1e-9
        )
    )
    controls = candidates[candidates.group.eq("C")]
    checks["negative_controls_flagged_not_viewable"] = bool(
        len(controls) == 3
        and controls.recommendation.str.contains("不推荐").all()
        and controls.risk_flags.str.contains("不推荐").all()
    )
    rankings = pd.read_csv(args.root / "rankings.csv")
    work = viewable.reset_index(drop=True)
    expected_order = work.sort_values(
        "qp_prediction", ascending=False, kind="stable"
    ).candidate_id.tolist()
    checks["rankings_consistent"] = bool(
        len(rankings) == len(work)
        and rankings.sort_values("rank_qp_first_point").candidate_id.tolist() == expected_order
    )
    plan = (args.root / "falsification_plan.md").read_text()
    checks["falsification_plan_covers_candidates"] = bool(
        all(cid in plan for cid in work.candidate_id) and "C6-1" in plan and "E1-1" in plan
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
