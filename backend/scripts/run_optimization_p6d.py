"""P6D selector-only retrospective stress test using frozen P6C recipes/predictions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_boundary_pilot import summarize
from app.ml.optimization_boundary_selection import balanced_scores, stress_selection
from app.ml.optimization_data import sha256, write_json


def verify_previous(root):
    receipt = json.loads((root / "delivery_manifest.json").read_text())
    for name, info in receipt["files"].items():
        assert sha256(root / name) == info["sha256"], name
    for key in ["sources", "documents"]:
        for name, h in receipt[key].items():
            assert sha256(Path(name)) == h, name
    return sha256(root / "delivery_manifest.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--p3-root", type=Path, required=True)
    ap.add_argument("--p6c-root", type=Path, required=True)
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    old_hash = verify_previous(args.p6c_root)
    frozen = json.loads((args.p6c_root / "experiment/frozen_inputs.json").read_text())
    assert (
        sha256(args.p3_root / "data/structures.csv")
        == frozen["p3_files"]["data/structures.csv"]["sha256"]
    )
    structures = pd.read_csv(args.p3_root / "data/structures.csv")
    old = args.p6c_root / "experiment"
    receipt = json.loads((old / "fit_receipts.json").read_text())
    previous = json.loads((old / "selection.json").read_text())
    backend = Path(__file__).resolve().parents[1]
    sources = [
        Path(__file__).resolve(),
        backend / "app/ml/optimization_boundary_selection.py",
        backend / "app/ml/optimization_ablation.py",
        backend / "app/ml/optimization_boundary_pilot.py",
        args.protocol,
    ]
    hashes = {str(p): sha256(p) for p in sources}
    args.out.mkdir(parents=True)
    write_json(
        args.out / "frozen_inputs.json",
        dict(
            sources=hashes,
            p6c_receipt_sha256=old_hash,
            structures_sha256=sha256(args.p3_root / "data/structures.csv"),
            new_blind_test=False,
            simulation_used=False,
        ),
    )
    (args.out / "protocol.md").write_bytes(args.protocol.read_bytes())
    selections = {}
    with threadpool_limits(limits=1):
        for scope, ids in receipt.items():
            fit = structures[structures.structure_id.isin(ids["fit"])]
            inner = pd.read_csv(old / f"{scope}_inner_predictions.csv")
            balanced, bscores, bcells = balanced_scores(inner, fit)
            minimax, mscores, manifests, capacity, pred, cells = stress_selection(fit)
            selections[scope] = {
                "p6c_average": previous[scope]["selected"],
                "balanced_inner_cv": balanced,
                "minimax_pseudo_boundary": minimax,
                "balanced_scores": bscores,
                "minimax_scores": mscores,
            }
            for name, table in [
                ("balanced_cells", bcells),
                ("stress_manifest", manifests),
                ("capacity", capacity),
                ("stress_predictions", pred),
                ("stress_cells", cells),
            ]:
                table.to_csv(args.out / f"{scope}_{name}.csv", index=False)
            write_json(args.out / "selection.json", selections)
            print(f"{scope}: balanced={balanced}, minimax={minimax}", flush=True)
    # Only now read frozen outer predictions: cannot influence selection.
    outer = pd.read_csv(old / "predictions.csv")
    rows = []
    for scope, selected in selections.items():
        for policy in ["p6c_average", "balanced_inner_cv", "minimax_pseudo_boundary"]:
            frame = outer[outer.scope.eq(scope) & outer.recipe.eq(selected[policy])].copy()
            frame["policy"] = policy
            frame["model"] = policy + "__" + frame.interval
            rows.append(frame)
    predictions = pd.concat(rows, ignore_index=True)
    predictions.to_csv(args.out / "outer_predictions.csv", index=False)
    metrics, joint, failures = summarize(predictions)
    metrics.to_csv(args.out / "outer_metrics.csv", index=False)
    joint.to_csv(args.out / "joint_coverage.csv", index=False)
    failures.to_csv(args.out / "interval_failures.csv", index=False)
    small = predictions[
        predictions.scope.eq("area_edges")
        & predictions.area_um2.eq(700)
        & predictions.has_pf.eq(1)
        & predictions.target.eq("qp")
    ]
    small.to_csv(args.out / "small_area_pf_qp.csv", index=False)
    assert verify_previous(args.p6c_root) == old_hash
    assert all(sha256(Path(p)) == h for p, h in hashes.items())
    write_json(
        args.out / "summary.json",
        dict(
            status="P6D_selector_diagnostic_complete_no_deployment",
            outer_rows=len(predictions),
            new_outer_models_fitted=False,
            new_calibration=False,
            new_blind_test=False,
            simulation_used=False,
        ),
    )
    write_json(
        args.out / "outputs_manifest.json",
        {p.name: sha256(p) for p in sorted(args.out.iterdir()) if p.is_file()},
    )


if __name__ == "__main__":
    main()
