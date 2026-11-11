"""Check saved real-data artifacts independently of the fitting/evaluation loop."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.optimization_data import TARGETS, sha256, write_json


def verify(run: Path) -> dict:
    data, evaluation = run / "data", run / "evaluation"
    checks = []
    inputs = json.loads((data / "input_manifest.json").read_text())
    for info in inputs["inputs"].values():
        assert sha256(Path(info["path"])) == info["sha256"], info["path"]
    for info in inputs["source_code"]:
        assert sha256(Path(info["path"])) == info["sha256"], info["path"]
    for directory in (data, evaluation):
        for name, expected in json.loads((directory / "outputs_manifest.json").read_text()).items():
            assert sha256(directory / name) == expected, name
    checks.append("source inputs, preparation code and all saved data/model hashes unchanged")
    records = pd.read_csv(data / "records.csv.gz")
    structures = pd.read_csv(data / "structures.csv")
    assert len(records) == 39818 + 40800
    assert len(structures) == 576
    assert structures.structure_id.is_unique
    # Independently take the literal mark from the original filename.
    mapping_source = Path(inputs["inputs"]["mapping"]["path"])
    mapping = pd.read_excel(mapping_source, header=None, dtype=str)
    marks = set(mapping.iloc[1:, 0])
    assert len(marks) == 567
    names = records.original_filename.str.extract(r"^S(?:11|22)_2_(.+?)_X[+-]?\d+Y", expand=False)
    assert names.notna().all() and names.isin(marks).all()
    assert records.loc[records.geometry_eligible, "ag_um"].isna().all()
    assert not records.loc[
        records.cohort.ne("standard_eg_fl_unrecorded_ag"), "geometry_eligible"
    ].any()
    assert not records.loc[
        records.exclusion_reasons.fillna("").str.contains("conflict"), "geometry_eligible"
    ].any()
    assert records.loc[records.geometry_eligible, "filename_has_fail"].all()
    checks.append(
        "all literal marks matched; unresolved cohorts/conflicts isolated; "
        "no AG imputation or Fail filter"
    )
    split = pd.read_csv(evaluation / "split_manifest.csv")
    assert split.groupby("design_group_id").role.nunique().max() == 1
    grouped = records[records.geometry_eligible].merge(
        split[["structure_id", "role"]], on="structure_id"
    )
    assert grouped.groupby("physical_id").role.nunique().max() == 1
    assert grouped.groupby("mark").role.nunique().max() == 1
    assert split.structure_id.is_unique
    for path in evaluation.glob("split_*.csv"):
        if path.name == "split_manifest.csv":
            continue
        boundary = pd.read_csv(path)
        test = set(boundary.loc[boundary.role.eq("test"), "design_group_id"])
        fit = set(boundary.loc[boundary.role.isin(["train", "calibration"]), "design_group_id"])
        assert not test & fit
    checks.append("no structure, paired DUT or mark leakage; boundary counterparts embargoed")
    frame = structures.merge(split[["structure_id", "role"]], on="structure_id")
    bundle = joblib.load(evaluation / "scalar_baselines.joblib")
    calibration = frame[frame.role.eq("calibration")]
    holdout_ids = set(frame.loc[frame.role.isin(["calibration", "test"]), "structure_id"])
    test_predictions = pd.read_csv(evaluation / "test_predictions.csv")
    summary = json.loads((evaluation / "evaluation_summary.json").read_text())
    for target in TARGETS:
        model = bundle["models"][target]
        for branch in model.branches.values():
            assert not holdout_ids & set(branch["structure_ids"])
        pred = model.predict(calibration)
        scores = calibration[["design_group_id"]].copy()
        scores["residual"] = np.abs(np.log(calibration[target].to_numpy() / pred))
        maxima = np.sort(scores.groupby("design_group_id").residual.max().to_numpy())
        expected = maxima[math.ceil((len(maxima) + 1) * 0.9) - 1]
        actual = bundle["metadata"]["calibration"][target]["log_radius"]
        assert abs(expected - actual) < 1e-12
        selected = test_predictions[
            (test_predictions.target == target) & (test_predictions.model == model.kind)
        ]
        manual_mae = float(np.mean(np.abs(selected.prediction - selected.actual)))
        reported = next(v for v in summary["selected_test_metrics"] if v["target"] == target)
        assert abs(manual_mae - reported["mae"]) < 1e-9
    checks.append(
        "serialized models exclude calibration/test; "
        "conformal ranks and test MAEs independently recomputed"
    )
    cli_inputs = pd.read_csv(run / "receipts/prediction_inputs.csv")
    cli = pd.read_csv(run / "receipts/prediction_outputs.csv")
    assert len(cli) == len(cli_inputs) * 4 and not cli.trusted_for_unmeasured_optimization.any()
    assert cli[cli.input_index.eq(3)].support.eq("outside_training_bounds").all()
    assert cli[cli.input_index.eq(4)].support.eq("unknown_branch").all()
    assert cli[cli.input_index.eq(4)].prediction.isna().all()
    for target in TARGETS:
        for index, inp in cli_inputs.iloc[:3].iterrows():
            truth = test_predictions[
                (test_predictions.target == target)
                & (test_predictions.model == bundle["models"][target].kind)
            ]
            for col in ("eg_um", "fl_um", "area_um2", "has_pf"):
                truth = truth[np.isclose(truth[col], inp[col])]
            returned = cli[(cli.target == target) & cli.input_index.eq(index)].prediction.iloc[0]
            assert len(truth) == 1 and abs(returned - truth.prediction.iloc[0]) < 1e-9
    checks.append(
        "fresh-process CLI round trip agrees; "
        "unsupported cases flagged and excluded from trusted use"
    )
    peak = pd.read_csv(run / "peak_review/peak_curve_review.csv")
    assert len(peak) == 8 and peak.status.eq("raw_parent_restored_and_reextracted").all()
    assert not peak.deembedded_curve_verified.any()
    for target in ("fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp"):
        assert peak["delta_reextracted_minus_source_raw_" + target].abs().max() < 1e-8
    checks.append(
        "8 raw parent curves reproduced fs/fp/k2/Qs/Qp; "
        "deembedded peaks remain explicitly unverified"
    )
    legacy = json.loads((evaluation / "legacy_diagnostic.json").read_text())
    for path, expected in legacy["artifact_sha256"].items():
        assert sha256(Path(path)) == expected
    # Check the zip's state, not its entire unconsumed content.
    archive = inputs["raw_zip"]
    stat = Path(archive["path"]).stat()
    assert (stat.st_size, stat.st_mtime_ns) == (archive["bytes"], archive["mtime_ns"])
    checks.append("published legacy artifacts unchanged; raw zip size/mtime unchanged")
    return {
        "status": "passed",
        "checks": checks,
        "counts": {
            "records": len(records),
            "structures": len(structures),
            "design_groups": int(structures.design_group_id.nunique()),
        },
        "boundary": "P0-P3 only; no P4/P5 release or prospective extrapolation claim",
        "script_sha256": sha256(Path(__file__).resolve()),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True)
    args = p.parse_args()
    result = verify(args.run_dir)
    write_json(args.run_dir / "receipts/verification.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
