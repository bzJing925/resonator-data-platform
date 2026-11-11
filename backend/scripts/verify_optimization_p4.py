"""Independent artifact acceptance for the delivered P4 examples; no predictive validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

TARGETS = ["k2eff_pct", "qs", "qp", "bodeq_fitted"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p3-root", type=Path, required=True)
    parser.add_argument("--p4-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    p3, p4 = args.p3_root.resolve(), args.p4_root.resolve()
    checks = {}
    receipt = json.loads((p3 / "receipts/delivery_manifest.json").read_text())
    for name, entry in receipt["files"].items():
        assert digest(p3 / name) == entry["sha256"], name
    before = json.loads((p4 / "receipts/pre_run.json").read_text())
    for name, expected in before["protected_sources"].items():
        assert digest(Path(name)) == expected, name
    checks["p3_and_preexisting_sources_unchanged"] = True
    source = pd.read_csv(p3 / "data/structures.csv").set_index("structure_id")
    for name in (
        "measured",
        "constrained_example",
        "constrained_feasible_example",
        "infeasible_example",
        "exploratory",
        "exploratory_replay",
    ):
        directory = p4 / name
        manifest = json.loads((directory / "outputs_manifest.json").read_text())
        for file, expected in manifest.items():
            assert digest(directory / file) == expected, file
        candidates = pd.read_csv(directory / "candidates.csv")
        feasible = pd.read_csv(directory / "feasible.csv")
        summary = json.loads((directory / "summary.json").read_text())
        assert len(candidates) == summary["n_candidates"]
        assert len(feasible) == summary["n_feasible"]
        assert candidates.structure_id.is_unique
        assert set(candidates[candidates.eligible].structure_id) == set(feasible.structure_id)
        assert not candidates.trusted_for_unmeasured_optimization.any()
        if summary["mode"] == "measured":
            np.testing.assert_allclose(
                candidates[TARGETS], source.loc[candidates.structure_id, TARGETS], atol=0, rtol=0
            )
            mask = np.ones(len(source), dtype=bool)
            cfg = summary["config"]
            for key, (low, high) in cfg["bounds"].items():
                mask &= (source[key] >= low) & (source[key] <= high)
            for key, low in cfg["minimums"].items():
                mask &= source[key] >= low
            for target in TARGETS:
                mask &= source[target].notna() & source[target].gt(0)
                mask &= source[target + "_n_coords"].ge(cfg["min_coordinates"])
            assert set(source.index[mask]) == set(feasible.structure_id)
        # Independently derive dominance via the sign of every pairwise difference.
        vectors = feasible[TARGETS].to_numpy()
        expected = []
        for sid, vector in zip(feasible.structure_id, vectors, strict=True):
            delta = vectors - vector
            dominated = ((delta.min(axis=1) >= 0) & (delta.max(axis=1) > 0)).any()
            if not dominated:
                expected.append(sid)
        actual = pd.read_csv(directory / "pareto.csv")
        assert set(expected) == set(actual.structure_id)
        for target in TARGETS:
            ranked = pd.read_csv(directory / f"top_{target}.csv")
            records = feasible.to_dict("records")
            expected_ids = [
                r["structure_id"]
                for r in sorted(records, key=lambda r: (-r[target], r["structure_id"]))[
                    : summary["config"]["top_n"]
                ]
            ]
            assert ranked.structure_id.tolist() == expected_ids
            if len(ranked):
                np.testing.assert_allclose(
                    ranked[TARGETS],
                    feasible.set_index("structure_id").loc[ranked.structure_id, TARGETS],
                    atol=0,
                    rtol=0,
                )
        checks[name] = {"n_feasible": len(feasible), "n_pareto": len(actual)}
    a = pd.read_csv(p4 / "exploratory/candidates.csv")
    b = pd.read_csv(p4 / "exploratory_replay/candidates.csv")
    np.testing.assert_allclose(a[TARGETS], b[TARGETS], atol=1e-9, rtol=0, equal_nan=True)
    assert a.structure_id.tolist() == b.structure_id.tolist()
    for file in ["pareto.csv", *[f"top_{t}.csv" for t in TARGETS]]:
        assert (
            pd.read_csv(p4 / "exploratory" / file).structure_id.tolist()
            == pd.read_csv(p4 / "exploratory_replay" / file).structure_id.tolist()
        )
    checks["fresh_process_reproduction_absolute_tolerance"] = 1e-9
    details = pd.read_csv(p4 / "exploratory/prediction_details.csv")
    split = pd.read_csv(p3 / "evaluation/split_manifest.csv")
    fit_ids = set(split[split.role.isin(["train", "validation"])].structure_id)
    for value in details.corner_structure_ids.dropna():
        assert set(value.split("|")) <= fit_ids
    p3_preds = pd.read_csv(p3 / "evaluation/test_predictions.csv")
    p3_preds = p3_preds[p3_preds.model.eq("extra_trees_log")]
    paired = details.merge(p3_preds, on=["structure_id", "target"], suffixes=("_p4", "_p3"))
    assert len(paired) == 68 * 4
    for column in ("prediction", "lower", "upper"):
        np.testing.assert_allclose(
            paired[column + "_p4"], paired[column + "_p3"], atol=1e-9, rtol=0
        )
    checks["p3_predictions_unchanged_and_only_fit_corners_used"] = True
    script = Path(__file__).with_name("run_optimization_p4.py")
    command = [
        sys.executable,
        str(script),
        "search",
        "--p3-root",
        str(p3),
        "--config",
        str(p4 / "configs/measured.json"),
        "--out",
        str(p4 / "measured"),
    ]
    manifest_path = p4 / "measured/outputs_manifest.json"
    original = digest(manifest_path)
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode != 0 and "Refusing to overwrite" in run.stderr
    assert digest(manifest_path) == original
    checks["cli_refuses_overwrite"] = True
    with tempfile.TemporaryDirectory() as temp:
        cfg, out = Path(temp) / "bad.json", Path(temp) / "out"
        cfg.write_text(json.dumps({"mode": "exploratory", "bounds": {"fs_ghz": [4, 5]}}))
        run = subprocess.run(
            [
                sys.executable,
                str(script),
                "search",
                "--p3-root",
                str(p3),
                "--config",
                str(cfg),
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        assert run.returncode != 0 and "No frequency surrogate" in run.stderr
        assert not out.exists()
    checks["cli_rejects_unmodeled_frequency_constraint"] = True
    args.out.write_text(json.dumps({"status": "passed", "checks": checks}, indent=2) + "\n")
    print(json.dumps({"status": "passed", "checks": len(checks)}))


if __name__ == "__main__":
    main()
