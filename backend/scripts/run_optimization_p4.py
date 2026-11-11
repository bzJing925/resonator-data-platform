"""Read-only P3 audit and bounded P4 search. Run from backend with local uv."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.optimization_data import TARGETS, sha256, write_json
from app.ml.optimization_search import search_catalog, validate_config


def verify_p3(root: Path) -> dict:
    receipt = root / "receipts/delivery_manifest.json"
    manifest = json.loads(receipt.read_text())
    hashes = {}
    for relative, entry in manifest["files"].items():
        path = root / relative
        actual = sha256(path)
        if actual != entry["sha256"]:
            raise ValueError(f"P3 receipt mismatch: {path}")
        hashes[relative] = actual
    hashes["receipts/delivery_manifest.json"] = sha256(receipt)
    # Bind data, split, and model metadata; do not accept a mixed artifact collection.
    meta = json.loads((root / "evaluation/model_metadata.json").read_text())
    frozen = meta["frozen_protocol"]
    if hashes["data/structures.csv"] != frozen["structures_sha256"]:
        raise ValueError("P3 model/catalog mismatch")
    if hashes["evaluation/split_manifest.csv"] != frozen["split_sha256"]:
        raise ValueError("P3 split mismatch")
    return hashes


def audit(root: Path, out: Path) -> dict:
    main = pd.read_csv(root / "evaluation/test_predictions.csv")
    boundary = pd.read_csv(root / "evaluation/boundary_predictions.csv")
    main["scope"] = "structure_holdout"
    boundary["scope"] = boundary.boundary
    frame = pd.concat([main, boundary], ignore_index=True)
    frame = frame[frame.model.eq("extra_trees_log")].copy()
    frame["inside"] = frame.actual.between(frame.lower, frame.upper)
    frame["absolute_error"] = (frame.prediction - frame.actual).abs()
    frame["absolute_log_error"] = np.abs(np.log(frame.actual / frame.prediction))
    frame["interval_log_radius"] = np.log(frame.upper / frame.prediction)
    frame["residual_to_radius"] = frame.absolute_log_error / frame.interval_log_radius
    results = []
    for keys in (
        ["scope", "target"],
        ["scope", "target", "branch_id"],
        ["scope", "target", "support"],
    ):
        for names, rows in frame.groupby(keys):
            groups = rows.groupby("design_group_id").inside.all()
            results.append(
                {
                    **dict(zip(keys, names, strict=True)),
                    "grouping": "+".join(keys),
                    "n_structures": len(rows),
                    "n_covered": int(rows.inside.sum()),
                    "coverage": float(rows.inside.mean()),
                    "n_design_groups": len(groups),
                    "n_joint_covered_groups": int(groups.sum()),
                    "design_joint_coverage": float(groups.mean()),
                    "mae": float(rows.absolute_error.mean()),
                }
            )
    pd.DataFrame(results).to_csv(out / "coverage_by_scope_branch_support.csv", index=False)
    joint = []
    for scope, rows in frame.groupby("scope"):
        if not rows.groupby("structure_id").target.nunique().eq(len(TARGETS)).all():
            raise ValueError("Incomplete four-target audit")
        groups = rows.groupby("design_group_id").inside.all()
        joint.append(
            {
                "scope": scope,
                "n_design_groups": len(groups),
                "n_all_four_covered_groups": int(groups.sum()),
                "all_four_group_coverage": float(groups.mean()),
            }
        )
    pd.DataFrame(joint).to_csv(out / "joint_four_target_coverage.csv", index=False)
    failures = frame[~frame.inside].sort_values("residual_to_radius", ascending=False)
    failures.to_csv(out / "interval_failures.csv", index=False)
    splits = pd.read_csv(root / "evaluation/split_manifest.csv")
    counts = splits[splits.role.eq("calibration")].groupby("branch_id").design_group_id.nunique()
    pd.DataFrame(
        [
            {
                "branch_id": branch,
                "n_calibration_groups": int(n),
                "rank_for_90pct": math.ceil((int(n) + 1) * 0.9),
                "finite_branchwise_90pct_possible": math.ceil((int(n) + 1) * 0.9) <= n,
            }
            for branch, n in counts.items()
        ]
    ).to_csv(out / "branch_calibration_capacity.csv", index=False)
    summary = {
        "status": "retrospective_audit_of_seen_P3_results",
        "selected_model": "extra_trees_log",
        "n_interval_failures": len(failures),
        "four_target_joint_coverage": joint,
        "decisions": [
            "Do not treat geometric hull membership as validated accuracy",
            "Do not multiply marginal target coverage into a joint guarantee",
            "Do not use P3 intervals as optimized-winner or individual-device guarantees",
            "Small branch calibration sets cannot support finite branchwise 90pct intervals",
            "Retain P3 models and splits; no retraining or retrospective interval inflation",
            "Use measured catalog search; keep model search exploratory and locally screened",
        ],
        "caveat": "one batch; descriptive sample fractions, not population guarantees",
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit", "search"])
    parser.add_argument("--p3-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"Refusing to overwrite {args.out}")
    if args.command == "search" and args.config is None:
        parser.error("search requires --config")
    if args.command == "audit" and args.config is not None:
        parser.error("audit does not accept search configuration")
    started = time.monotonic()
    hashes = verify_p3(args.p3_root)
    config = validate_config(json.loads(args.config.read_text())) if args.config else None
    args.out.mkdir(parents=True)
    if args.command == "audit":
        summary = audit(args.p3_root, args.out)
    else:
        structures = pd.read_csv(args.p3_root / "data/structures.csv")
        # Only load the local model after verifying the original P3 receipt.
        bundle = (
            joblib.load(args.p3_root / "evaluation/scalar_baselines.joblib")
            if (config["mode"] == "exploratory")
            else None
        )
        result = search_catalog(structures, config, bundle)
        for name in ("candidates", "feasible", "pareto"):
            result[name].to_csv(args.out / f"{name}.csv", index=False)
        if not result["prediction_details"].empty:
            result["prediction_details"].to_csv(args.out / "prediction_details.csv", index=False)
        for target, ranked in result["ranks"].items():
            ranked.to_csv(args.out / f"top_{target}.csv", index=False)
        summary = result["summary"]
        write_json(
            args.out / "results.json",
            {
                "summary": summary,
                "top": {
                    t: json.loads(df.to_json(orient="records", double_precision=15))
                    for t, df in result["ranks"].items()
                },
                "pareto": json.loads(
                    result["pareto"].to_json(orient="records", double_precision=15)
                ),
            },
        )
        write_json(args.out / "config.json", config)
    if verify_p3(args.p3_root) != hashes:
        raise RuntimeError("P3 artifacts changed during execution")
    root = Path(__file__).resolve().parents[2]
    sources = [
        Path(__file__).resolve(),
        root / "backend/app/ml/optimization_search.py",
        root / "backend/app/ml/optimization_baselines.py",
        root / "backend/app/ml/optimization_data.py",
    ]
    write_json(
        args.out / "provenance.json",
        {
            "p3_root": str(args.p3_root.resolve()),
            "p3_sha256_before_and_after": hashes,
            "source_sha256": {str(p): sha256(p) for p in sources},
            "config_input_sha256": sha256(args.config) if args.config else None,
            "argv": sys.argv,
            "python": sys.version,
            "versions": {
                n: importlib.metadata.version(n)
                for n in ("numpy", "pandas", "scikit-learn", "scipy", "joblib")
            },
            "git_head": subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip(),
            "git_status": subprocess.check_output(
                ["git", "-C", str(root), "status", "--short"], text=True
            ),
            "elapsed_seconds": time.monotonic() - started,
            "new_blind_test": False,
        },
    )
    write_json(args.out / "summary.json", summary)
    write_json(
        args.out / "outputs_manifest.json",
        {p.name: sha256(p) for p in sorted(args.out.iterdir()) if p.is_file()},
    )
    print(json.dumps({"status": summary["status"], "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
