"""Independent P11 verification: grid, frontier, baselines, rankings, plan contents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_data import sha256, write_json
from app.ml.optimization_p11 import build_candidates, measured_frontier


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--p4-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)

    frozen = json.loads((args.root / "frozen_inputs.json").read_text())
    checks = {}
    checks["source_files_unchanged"] = bool(
        all(sha256(Path(p)) == h for p, h in frozen["sources"].items() if Path(p).is_file())
    )
    candidates = pd.read_csv(args.root / "candidates.csv")
    checks["grid_matches_frozen_definition"] = bool(
        build_candidates().candidate_id.equals(candidates.candidate_id)
    )
    measured = pd.read_csv(args.p4_root / "measured/candidates.csv")
    front = measured_frontier(measured)
    saved_front = pd.read_csv(args.root / "measured_frontier.csv")
    checks["frontier_recomputed_match"] = bool(
        front.structure_id.tolist() == saved_front.structure_id.tolist() and len(front) == 6
    )
    checks["baselines_match_frozen_champions"] = bool(
        np.isclose(
            measured[measured.marks.eq("C6-1") & measured.area_um2.eq(5500.0)].iloc[0].qp,
            1395.957083,
        )
        and np.isclose(
            measured[measured.marks.eq("E1-1") & measured.area_um2.eq(5500.0)].iloc[0].k2eff_pct,
            14.674470,
        )
    )
    checks["no_candidate_predicted_above_baseline_point"] = bool(
        (candidates.qp_prediction <= 1395.957083).all()
        and (candidates.k2eff_pct_prediction <= 14.674470).all()
    )
    checks["c_demo_not_recommended_and_flagged"] = bool(
        candidates[candidates.group.eq("C")].recommendation.str.contains("不推荐").all()
        and candidates[candidates.group.eq("C")].risk_flags.str.contains("失败史").all()
    )
    rankings = pd.read_csv(args.root / "rankings.csv")
    point = rankings[rankings.caliber.eq("point")].sort_values("rank_qp_first")
    work = candidates[candidates.group.isin(["A", "B"])].reset_index(drop=True)
    checks["rankings_consistent_with_candidates"] = bool(
        point.candidate_id.tolist()
        == work.sort_values("qp_prediction", ascending=False).candidate_id.tolist()
        and len(rankings) == 2 * len(work)
    )
    plan = (args.root / "falsification_plan.md").read_text()
    checks["falsification_plan_covers_all_ab_candidates"] = bool(
        all(cid in plan for cid in work.candidate_id)
        and "C6-1" in plan
        and "E1-1" in plan
        and "original/vz" in plan
    )
    excluded = pd.read_csv(args.root / "excluded.csv")
    checks["exclusion_directions_kept"] = bool(len(excluded) >= 4)
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
