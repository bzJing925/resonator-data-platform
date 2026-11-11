"""Run frozen P6A retrospective models using measured P3 labels only."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import time
from pathlib import Path

import joblib
import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.optimization_boundary_pilot import MODEL_RECIPES, fit_scope, summarize
from app.ml.optimization_data import sha256, write_json


def verify_p3(root: Path) -> dict:
    manifest = json.loads((root / "receipts/delivery_manifest.json").read_text())
    for name, info in manifest["files"].items():
        if sha256(root / name) != info["sha256"]:
            raise ValueError(f"P3 content changed: {name}")
    return {name: info["sha256"] for name, info in manifest["files"].items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p3-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    start = time.monotonic()
    hashes = verify_p3(args.p3_root)
    source = Path(__file__).resolve().parents[1] / "app/ml/optimization_boundary_pilot.py"
    sources = {str(p): sha256(p) for p in [Path(__file__).resolve(), source, args.protocol]}
    args.out.mkdir(parents=True)
    (args.out / "protocol.md").write_bytes(args.protocol.read_bytes())
    write_json(
        args.out / "frozen_recipe.json",
        {
            "models": MODEL_RECIPES,
            "p3_hashes": hashes,
            "sources": sources,
            "only_model_labels": "P3 measured deembedded structures.csv",
            "simulation_labels_used": False,
            "new_blind_test": False,
            "optimization_or_deployment_authorized_by_this_run": False,
        },
    )
    structures = pd.read_csv(args.p3_root / "data/structures.csv")
    prediction_frames, fit_receipts, calibrations, all_models = [], {}, {}, {}
    with threadpool_limits(limits=1):
        for scope, file in {
            "structure_holdout": "split_manifest.csv",
            "fl_upper": "split_fl_upper.csv",
            "eg_upper": "split_eg_upper.csv",
            "area_edges": "split_area_edges.csv",
        }.items():
            manifest = pd.read_csv(args.p3_root / "evaluation" / file)
            manifest.to_csv(args.out / file, index=False)
            pred, models, calibration, fit_receipt = fit_scope(structures, manifest, scope)
            prediction_frames.append(pred)
            all_models[scope], calibrations[scope], fit_receipts[scope] = (
                models,
                calibration,
                fit_receipt,
            )
            print(f"Completed {scope}", flush=True)
    new_predictions = pd.concat(prediction_frames, ignore_index=True)
    new_predictions.to_csv(args.out / "new_predictions.csv", index=False)
    old_test = pd.read_csv(args.p3_root / "evaluation/test_predictions.csv")
    old_test["scope"] = "structure_holdout"
    old_boundary = pd.read_csv(args.p3_root / "evaluation/boundary_predictions.csv")
    old_boundary["scope"] = old_boundary.boundary
    old = pd.concat([old_test, old_boundary], ignore_index=True)
    old["evaluation_status"] = "retrospective_frozen_P3_reference"
    old["trusted_for_unmeasured_optimization"] = False
    all_predictions = pd.concat([old, new_predictions], ignore_index=True)
    metrics, joint, failures = summarize(all_predictions)
    metrics.to_csv(args.out / "metrics.csv", index=False)
    joint.to_csv(args.out / "joint_coverage.csv", index=False)
    failures.to_csv(args.out / "interval_failures.csv", index=False)
    selected_metrics = metrics[metrics.branch.eq("all")]
    baseline = selected_metrics[selected_metrics.model.eq("extra_trees_log")]
    comparisons = selected_metrics.merge(
        baseline[["scope", "target", "mae", "interval_coverage", "median_interval_width"]],
        on=["scope", "target"],
        suffixes=("", "_p3_extra_trees"),
        validate="many_to_one",
    )
    comparisons["mae_ratio_to_p3"] = comparisons.mae / comparisons.mae_p3_extra_trees
    comparisons["width_ratio_to_p3"] = (
        comparisons.median_interval_width / comparisons.median_interval_width_p3_extra_trees
    )
    comparisons.to_csv(args.out / "comparison_to_p3.csv", index=False)
    write_json(args.out / "fit_receipts.json", fit_receipts)
    write_json(args.out / "calibration.json", calibrations)
    joblib.dump(all_models, args.out / "retrospective_models.joblib")
    if verify_p3(args.p3_root) != hashes:
        raise RuntimeError("P3 input changed during run")
    if any(sha256(Path(path)) != expected for path, expected in sources.items()):
        raise RuntimeError("Code or protocol changed during run")
    write_json(
        args.out / "summary.json",
        {
            "status": "P6A_retrospective_pilot_complete_not_extrapolation_acceptance",
            "new_blind_test": False,
            "simulation_labels_used": False,
            "new_models_deployed": False,
            "n_new_predictions": len(new_predictions),
            "n_combined_interval_failure_records": len(failures),
            "elapsed_seconds": time.monotonic() - start,
            "versions": {
                name: importlib.metadata.version(name)
                for name in ["numpy", "pandas", "scipy", "scikit-learn", "joblib"]
            },
            "source_sha256": sources,
        },
    )
    write_json(
        args.out / "outputs_manifest.json",
        {f.name: sha256(f) for f in sorted(args.out.iterdir()) if f.is_file()},
    )
    print(str(args.out.resolve()))


if __name__ == "__main__":
    main()
