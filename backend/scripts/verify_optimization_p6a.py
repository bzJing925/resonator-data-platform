"""Independent receipts, partition, saved-model, quantile and isolation checks for P6A."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_data import TARGETS, sha256, write_json


def check_hashes(root, hashes):
    for name, value in hashes.items():
        expected = value["sha256"] if isinstance(value, dict) else value
        path = Path(name) if Path(name).is_absolute() else root / name
        if sha256(path) != expected:
            raise AssertionError(f"Changed: {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    root = args.root
    protected = json.loads((root / "pre_run_protected.json").read_text())
    check_hashes(root, protected)
    old_counts = {}
    missing_historical_temp_documents = []
    for name in [
        "optimization_p3_20261015",
        "optimization_p4_20261016",
        "optimization_p5_20261016",
    ]:
        old = root.parent / name
        receipt = json.loads((old / "receipts/delivery_manifest.json").read_text())
        check_hashes(old, receipt["files"])
        for key in ["sources", "source_files", "documents"]:
            if key in receipt:
                entries = dict(receipt[key])
                for document_path in list(entries):
                    if (
                        key == "documents"
                        and document_path.startswith("/var/folders/")
                        and "/T/" in document_path
                        and not Path(document_path).exists()
                    ):
                        missing_historical_temp_documents.append(document_path)
                        del entries[document_path]
                check_hashes(old, entries)
        old_counts[name] = len(receipt["files"])
    for name in ["pilot", "simulation_audit_v2"]:
        path = root / name
        check_hashes(path, json.loads((path / "outputs_manifest.json").read_text()))
    pilot = root / "pilot"
    check_hashes(root, json.loads((pilot / "frozen_recipe.json").read_text())["sources"])
    sim = root / "simulation_audit_v2"
    check_hashes(root, json.loads((sim / "frozen_inputs.json").read_text())["sources"])
    models = joblib.load(pilot / "retrospective_models.joblib")
    structures = pd.read_csv(root.parent / "optimization_p3_20261015/data/structures.csv")
    pred = pd.read_csv(pilot / "new_predictions.csv")
    receipts = json.loads((pilot / "fit_receipts.json").read_text())
    calibrations = json.loads((pilot / "calibration.json").read_text())
    q_count = 0
    with threadpool_limits(limits=1):
        for scope, receipt in receipts.items():
            fit_ids = set(receipt["fit_structure_ids"])
            cal_ids = set(receipt["calibration_structure_ids"])
            test_ids = set(receipt["test_structure_ids"])
            embargo_ids = set(receipt["embargo_structure_ids"])
            assert not fit_ids & (cal_ids | test_ids | embargo_ids)
            assert not cal_ids & (test_ids | embargo_ids)
            fit = structures[structures.structure_id.isin(fit_ids)]
            cal = structures[structures.structure_id.isin(cal_ids)]
            test = structures[structures.structure_id.isin(test_ids)]
            assert not set(fit.design_group_id) & set(cal.design_group_id)
            assert not set(fit.design_group_id) & set(test.design_group_id)
            for kind, methods in [
                ("ridge", ["ridge_log_global"]),
                ("gp", ["gp_matern_scaled_marginal", "gp_matern_scaled_joint"]),
            ]:
                errors = {}
                for target in TARGETS:
                    model = models[scope][(kind, target)]
                    fitted = {
                        sid for state in model.branches.values() for sid in state["structure_ids"]
                    }
                    assert fitted == fit_ids
                    cm, cs = model.predict_log(cal)
                    errors[target] = np.abs(np.log(cal[target].to_numpy()) - cm) / cs
                    tm, ts = model.predict_log(test)
                    for method in methods:
                        rows = pred[
                            (pred.scope == scope) & (pred.model == method) & (pred.target == target)
                        ]
                        rows = rows.set_index("structure_id").loc[test.structure_id]
                        np.testing.assert_allclose(rows.prediction, np.exp(tm), rtol=1e-12)
                        q = calibrations[scope][f"{method}:{target}"]["quantile"]
                        np.testing.assert_allclose(rows.lower, np.exp(tm - q * ts), rtol=1e-12)
                        np.testing.assert_allclose(rows.upper, np.exp(tm + q * ts), rtol=1e-12)
                for method in methods:
                    for target in TARGETS:
                        residual = (
                            np.column_stack(list(errors.values())).max(axis=1)
                            if method.endswith("_joint")
                            else errors[target]
                        )
                        scores = (
                            pd.Series(residual, index=cal.design_group_id).groupby(level=0).max()
                        )
                        rank = math.ceil((len(scores) + 1) * 0.9)
                        q = np.sort(scores)[rank - 1]
                        np.testing.assert_allclose(
                            q, calibrations[scope][f"{method}:{target}"]["quantile"], rtol=1e-12
                        )
                        q_count += 1
    joint = pd.read_csv(pilot / "joint_coverage.csv")
    for (scope, model), rows in pred.groupby(["scope", "model"]):
        assert rows.groupby("structure_id").size().eq(4).all()
        inside = rows.actual.between(rows.lower, rows.upper)
        rate = inside.groupby(rows.design_group_id).all().mean()
        recorded = joint[(joint.scope == scope) & (joint.model == model)].joint_coverage.item()
        assert np.isclose(rate, recorded)
    sim_rows = pd.read_csv(sim / "simulation_extraction.csv")
    for row in sim_rows.itertuples():
        assert sha256(Path(row.resolved_path)) == row.source_sha256
    pairs = pd.read_csv(sim / "trend_pairs.csv")
    index = sim_rows.set_index("Training_Row_ID")
    physical = [c for c in sim_rows if c.startswith("Train_")]
    metadata = [
        "Stage",
        "Template",
        "Variant",
        "EG_State",
        "has_PF",
        "has_AG",
        "has_FL",
        "is_BASIC",
        "is_C_gt_B",
        "has_patterned_EG",
        "EG_material_present",
        "L_air_Rule",
        "Air_margin_per_side_um",
        "frequency_min_ghz",
        "frequency_max_ghz",
    ]
    for axis, group in pairs.groupby("axis"):
        low = index.loc[group.low_id].reset_index(drop=True)
        high = index.loc[group.high_id].reset_index(drop=True)
        assert low.trend_eligible.all() and high.trend_eligible.all()
        omitted = [axis]
        if axis == "Train_A_res_um2":
            omitted += ["Train_L_top_um", "Train_L_bot_um", "Train_L_air_um"]
        for column in set(physical + metadata) - set(omitted):
            assert low[column].eq(high[column]).all(), (axis, column)
        assert low[axis].lt(high[axis]).all()
        for metric, subset in group.groupby("metric"):
            a = index.loc[subset.low_id, metric].to_numpy()
            b = index.loc[subset.high_id, metric].to_numpy()
            np.testing.assert_allclose(subset.ratio, b / a, rtol=1e-12)
    assert not json.loads((pilot / "summary.json").read_text())["simulation_labels_used"]
    assert not pred.trusted_for_unmeasured_optimization.any()
    report = dict(
        status=(
            "passed_with_missing_historical_temp_handoff"
            if missing_historical_temp_documents
            else "passed"
        ),
        missing_historical_temp_documents=missing_historical_temp_documents,
        protected_preexisting_files=len(protected),
        preserved_artifact_counts=old_counts,
        independently_checked_quantiles=q_count,
        saved_model_prediction_rows=len(pred),
        simulation_raw_hashes=len(sim_rows),
        simulation_pairs_checked=len(pairs),
        new_blind_test=False,
        production_model_changed=False,
    )
    write_json(root / "verification.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
