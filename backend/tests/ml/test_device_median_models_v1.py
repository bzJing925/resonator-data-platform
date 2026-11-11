import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from scripts.run_device_median_models_v1 import (
    fit_model,
    gate_mae_comparison,
    load_populations,
    main,
    select_model,
    validate_scope_for_fit,
)

TARGETS = ("k2eff_pct", "qp", "qs", "bodeq_fitted")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def structure_rows(has_pf=0, area_start=700.0):
    rows = []
    for index in range(6):
        structure_key = f"struct_{index:03d}"
        for target in TARGETS:
            rows.append(
                {
                    "structure_key": structure_key,
                    "mode": "deembedded",
                    "parameter": target,
                    "p3_structure_id": f"p3_{index:03d}",
                    "p3_branch_id": f"pf{has_pf}_eg1_fl1",
                    "p3_design_group_id": f"group_{index:03d}",
                    "eg_um": 1.0,
                    "fl_um": 1.0,
                    "area_um2": area_start + index,
                    "has_pf": has_pf,
                    "ag_status": "unrecorded",
                    "cohort": "standard_eg_fl_unrecorded_ag",
                    "topology_status": "standard_mapping",
                    "median": float(index + 1) * (100 if target == "qp" else 1),
                    "n_valid": 10,
                    "low_support": False,
                }
            )
    return rows


def write_population_csvs(data: Path, has_pf=0, area_start=700.0) -> dict[str, Path]:
    data.mkdir()
    paths = {}
    primary = structure_rows(has_pf=has_pf, area_start=area_start)
    sensitivity = [dict(row) for row in primary]
    for row in sensitivity:
        if row["parameter"] == "qp" and row["structure_key"] == "struct_000":
            row["median"] = 50.0
            row["n_valid"] = 9
    for population, rows in (("primary", primary), ("sensitivity", sensitivity)):
        path = data / f"structure_targets_{population}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        paths[population] = path
    return paths


def make_protocol(paths: dict[str, Path]) -> dict:
    return {
        "schema": "aln_device_median_b03_frozen_experiment_protocol_v1",
        "attempt_id": "attempt_001",
        "core_targets": list(TARGETS),
        "data_version": {
            "inputs": [
                {
                    "id": f"structure_targets_{population}",
                    "path": str(path),
                    "sha256": file_sha256(path),
                }
                for population, path in paths.items()
            ]
        },
        "model_selection": {
            "candidate_order_and_tie_priority": [
                "p3_extra_trees_scalar",
                "p6c_trend_gp_log_area_scalar",
                "training_target_median_constant",
            ]
        },
        "models": {
            "p3_extra_trees_scalar": {
                "feature_order": ["eg_um", "fl_um", "area_um2"],
                "max_features": 1.0,
                "min_samples_leaf": 2,
                "n_estimators": 192,
                "n_jobs": 2,
                "random_state": 20261102,
            },
            "p6c_trend_gp_log_area_scalar": {
                "feature_order_after_transform": ["eg_um", "fl_um", "log_area_um2"],
                "trend": {"alpha": 1.0},
                "gp": {
                    "alpha": 1e-10,
                    "length_scale": 0.5,
                    "length_scale_bounds": "fixed",
                    "noise_level": 0.05,
                    "noise_level_bounds": "fixed",
                    "normalize_y": True,
                    "nu": 2.5,
                    "optimizer": None,
                    "random_state": None,
                },
            },
            "training_target_median_constant": {},
        },
        "resource_policy": {"seed": 20261102, "checkpoint_seconds": 1800},
        "validity_and_support": {"n_valid_threshold": 5},
        "research_gate": {
            "required_scopes": ["normal_holdout"],
            "required_targets": ["k2eff_pct", "qp"],
        },
        "units": {
            "k2eff_pct": "percentage points; source target values are expressed in percent",
            "qp": "dimensionless",
            "qs": "dimensionless",
            "bodeq_fitted": "dimensionless",
        },
    }


def test_loader_reads_hash_bound_new_labels_and_masks_only_qp(tmp_path):
    paths = write_population_csvs(tmp_path / "data")
    protocol = make_protocol(paths)
    (tmp_path / "data" / "structures.csv").write_text("obsolete labels\n", encoding="utf-8")

    loaded = load_populations(protocol, tmp_path / "data")

    assert set(loaded) == {"primary", "sensitivity"}
    primary = loaded["primary"]["frame"]
    sensitivity = loaded["sensitivity"]["frame"]
    for target in ("k2eff_pct", "qs", "bodeq_fitted"):
        left = primary.loc[primary.parameter.eq(target)].reset_index(drop=True)
        right = sensitivity.loc[sensitivity.parameter.eq(target)].reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right)
    changed = primary[["structure_key", "parameter", "median"]].merge(
        sensitivity[["structure_key", "parameter", "median"]],
        on=["structure_key", "parameter"],
        suffixes=("_primary", "_sensitivity"),
    )
    assert changed.median_primary.ne(changed.median_sensitivity).sum() == 1
    changed_row = changed.loc[changed.median_primary.ne(changed.median_sensitivity)].iloc[0]
    assert changed_row["parameter"] == "qp"
    qp = loaded["sensitivity"]["frame"].query("parameter == 'qp'")
    assert qp.loc[qp.structure_key.eq("struct_000"), "median"].item() == 50
    assert loaded["primary"]["source"]["sha256"] == file_sha256(paths["primary"])
    assert loaded["sensitivity"]["source"]["path"] == str(paths["sensitivity"].resolve())

    updated = paths["primary"].read_text(encoding="utf-8") + "\n"
    paths["primary"].write_text(updated, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Label hash mismatch"):
        load_populations(protocol, tmp_path / "data")


def test_exact_inner_mae_tie_uses_frozen_priority():
    scores = {
        "training_target_median_constant": 2.0,
        "p6c_trend_gp_log_area_scalar": 2.0,
        "p3_extra_trees_scalar": 2.0,
    }
    assert select_model(scores) == "p3_extra_trees_scalar"
    assert select_model({**scores, "p3_extra_trees_scalar": 3.0}) == "p6c_trend_gp_log_area_scalar"


def test_zero_constant_baseline_requires_zero_selected_mae_without_division():
    passed = gate_mae_comparison(0.0, 0.0, normal_holdout=True)
    assert passed["cell_gate_pass"] is True
    assert passed["mae_reduction_pct"] is None
    assert passed["mae_reduction_status"] == "not_computed_zero_constant_baseline"

    failed = gate_mae_comparison(1e-12, 0.0, normal_holdout=False)
    assert failed["cell_gate_pass"] is False
    assert failed["mae_reduction_pct"] is None
    assert failed["failure_reason"] == "zero constant baseline requires selected MAE == 0"


def test_insufficient_groups_or_empty_outer_test_is_not_evaluable():
    scope = {
        "status": "EVALUABLE",
        "outer": {
            "train": {"counts": {"design_groups": 2}},
            "test": {"counts": {"structures": 1}},
        },
        "inner_group_kfold": {"actual_splits": 2, "folds": [{}, {}, {}]},
    }
    assert "fewer than 3" in validate_scope_for_fit(scope)
    scope["outer"]["train"]["counts"]["design_groups"] = 3
    scope["inner_group_kfold"]["actual_splits"] = 3
    assert validate_scope_for_fit(scope) is None
    scope["outer"]["test"]["counts"]["structures"] = 0
    assert "empty outer test" in validate_scope_for_fit(scope)


def wide_target(paths, protocol, tmp_path, population="primary", target="qp"):
    loaded = load_populations(protocol, tmp_path / "data")
    frame = loaded[population]["frame"]
    result = frame.loc[frame.parameter.eq(target)].copy()
    return result.rename(columns={"median": target})


def test_fixed_models_use_fold_local_transforms_and_raw_unit_predictions(tmp_path):
    paths = write_population_csvs(tmp_path / "data")
    protocol = make_protocol(paths)
    frame = wide_target(paths, protocol, tmp_path)
    train = frame.iloc[:4].copy()

    constant = fit_model("training_target_median_constant", train, "qp", protocol)
    assert constant.value == train.qp.median()
    assert np.all(constant.predict(frame) == train.qp.median())

    trees = fit_model("p3_extra_trees_scalar", train, "qp", protocol)
    state = trees.branches["pf0_eg1_fl1"]
    np.testing.assert_allclose(state["high"], train[["eg_um", "fl_um", "area_um2"]].max())
    assert state["feature_order"] == ["eg_um", "fl_um", "area_um2"]
    assert state["target_transform"] == "natural_log"
    assert state["prediction_transform"] == "exp"
    assert state["model"].n_estimators == 192
    assert state["model"].min_samples_leaf == 2
    assert state["model"].max_features == 1.0
    assert state["model"].random_state == 20261102
    assert state["model"].n_jobs == 2
    assert np.isfinite(trees.predict(frame)).all()
    assert (trees.predict(frame) > 0).all()

    trend_gp = fit_model("p6c_trend_gp_log_area_scalar", train, "qp", protocol)
    gp_state = trend_gp.branches["pf0_eg1_fl1"]
    assert gp_state["feature_order"] == ["eg_um", "fl_um", "log_area_um2"]
    assert gp_state["low"][2] == np.log(train.area_um2.min())
    assert gp_state["high"][2] == np.log(train.area_um2.max())
    assert gp_state["trend"].alpha == 1.0
    assert gp_state["gp"].optimizer is None
    assert gp_state["gp"].alpha == 1e-10
    assert gp_state["gp"].normalize_y is True
    assert gp_state["target_transform"] == "natural_log_residual"
    assert np.isfinite(trend_gp.predict(frame)).all()
    assert (trend_gp.predict(frame) > 0).all()


def make_splits(train_count=5, scope_id="normal_holdout"):
    train_ids = [f"struct_{index:03d}" for index in range(train_count)]
    test_ids = [f"struct_{index:03d}" for index in range(train_count, 6)]

    def role(ids):
        return {
            "counts": {"design_groups": len(ids), "devices": 0, "structures": len(ids)},
            "design_group_ids": [f"group_{int(value[-3:]):03d}" for value in ids],
            "device_ids": [],
            "structure_ids": ids,
        }

    folds = []
    for fold, validation_id in enumerate(train_ids):
        folds.append(
            {
                "fold": fold,
                "train": role([value for value in train_ids if value != validation_id]),
                "validation": role([validation_id]),
            }
        )
    return {
        "schema": "aln_device_median_b03_frozen_splits_v1",
        "attempt_id": "attempt_001",
        "seed": 20261102,
        "scopes": [
            {
                "scope_id": scope_id,
                "status": "EVALUABLE",
                "not_evaluable_reason": None,
                "outer": {"train": role(train_ids), "test": role(test_ids)},
                "inner_group_kfold": {
                    "actual_splits": len(folds),
                    "requested_max_splits": 5,
                    "minimum_outer_training_design_groups": 3,
                    "shuffle": False,
                    "folds": folds,
                },
            }
        ],
    }


def test_evaluate_cli_reads_frozen_bundles_and_reports_registered_layers(
    tmp_path, monkeypatch
):
    paths = write_population_csvs(tmp_path / "data", has_pf=1, area_start=695.0)
    primary = pd.read_csv(paths["primary"])
    low_support = primary["structure_key"].eq("struct_005") & primary["parameter"].eq("qp")
    primary.loc[low_support, "n_valid"] = 4
    primary.loc[low_support, "low_support"] = True
    primary.to_csv(paths["primary"], index=False)
    protocol = make_protocol(paths)
    protocol["core_targets"] = ["qp"]
    protocol["research_gate"]["required_targets"] = ["qp"]
    protocol["research_gate"]["required_scopes"] = ["area_lower"]
    protocol_path = tmp_path / "protocol.json"
    splits_path = tmp_path / "splits.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    splits_path.write_text(
        json.dumps(make_splits(train_count=4, scope_id="area_lower")), encoding="utf-8"
    )
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        monkeypatch.setenv(name, "1")
    models = tmp_path / "models"
    main(
        [
            "fit",
            "--protocol",
            str(protocol_path),
            "--data",
            str(tmp_path / "data"),
            "--splits",
            str(splits_path),
            "--out",
            str(models),
        ]
    )
    bundle_paths = sorted(models.rglob("model_bundle.joblib"))
    before = {str(path): file_sha256(path) for path in bundle_paths}

    evaluation = tmp_path / "evaluation"
    main(
        [
            "evaluate",
            "--protocol",
            str(protocol_path),
            "--models",
            str(models),
            "--out",
            str(evaluation),
        ]
    )

    assert {str(path): file_sha256(path) for path in bundle_paths} == before
    predictions = pd.read_csv(evaluation / "predictions.csv")
    assert len(predictions) == 8
    assert set(predictions["model_role"]) == {"selected", "constant"}
    low = predictions.loc[
        predictions["population"].eq("primary")
        & predictions["structure_key"].eq("struct_005")
    ]
    assert low["support_layer"].eq("low_support").all()
    assert low["scored"].all()
    metrics = pd.read_csv(evaluation / "metrics.csv")
    assert {"outer_scope", "branch", "structure", "low_support", "700_pf"} <= set(
        metrics["aggregation"]
    )
    difficult = metrics.loc[
        metrics["aggregation"].eq("700_pf") & metrics["population"].eq("sensitivity")
    ]
    assert difficult["n"].eq(1).all()
    common = pd.read_csv(evaluation / "common_sample_metrics.csv")
    assert len(common) == 4
    assert common["n"].eq(1).all()
    gate = pd.read_csv(evaluation / "gate_inputs.csv")
    assert len(gate) == 2
    assert {"selected_mae", "constant_mae", "mae_reduction_pct", "cell_gate_pass"} <= set(
        gate.columns
    )
    manifest = json.loads((evaluation / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["training_performed"] is False
    assert manifest["outer_tuning_performed"] is False
    assert manifest["scientific_status"] == "RETROSPECTIVE_ONLY"
    assert manifest["trusted"] is False
    assert manifest["model_bundle_readback_count"] == 2


def write_finalization_evaluation(
    tmp_path: Path, protocol_path: Path, protocol: dict, gate_status: str
) -> Path:
    evaluation = tmp_path / f"evaluation_{gate_status.lower()}"
    evaluation.mkdir()
    passed = gate_status == "PASS"
    rows = []
    for population in ("primary", "sensitivity"):
        applicable = population == "primary"
        rows.append(
            {
                "population": population,
                "scope": "normal_holdout",
                "target": "qp",
                "evaluation_status": "COMPLETE",
                "selected_model": "training_target_median_constant",
                "selected_mae": 0.0 if passed else 2.0,
                "constant_mae": 1.0,
                "mae_reduction_pct": 100.0 if passed else -100.0,
                "mae_reduction_status": "computed",
                "eligible_n": 1,
                "selected_n": 1,
                "constant_n": 1,
                "gate_applicable": applicable,
                "reporting_required": True,
                "reporting_complete": True,
                "cell_gate_pass": passed if applicable else None,
                "failure_reason": (
                    None
                    if passed or not applicable
                    else "selected MAE exceeds constant-model MAE"
                ),
            }
        )
    gate_path = evaluation / "gate_inputs.csv"
    with gate_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "schema": "aln_device_median_b05_evaluation_v1",
        "attempt_id": protocol["attempt_id"],
        "status": "COMPLETE",
        "scientific_status": "RETROSPECTIVE_ONLY",
        "trusted": False,
        "overall_gate": {"status": gate_status},
        "source_hashes": {"protocol": file_sha256(protocol_path)},
        "outputs": [
            {
                "id": "gate_inputs.csv",
                "sha256": file_sha256(gate_path),
            }
        ],
    }
    (evaluation / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return evaluation


def finalize_protocol(paths):
    protocol = make_protocol(paths)
    protocol["core_targets"] = ["qp"]
    protocol["research_gate"]["required_targets"] = ["qp"]
    return protocol


def test_finalize_rejects_frozen_gate_failure_without_creating_a_model(tmp_path):
    paths = write_population_csvs(tmp_path / "data")
    protocol = finalize_protocol(paths)
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    evaluation = write_finalization_evaluation(tmp_path, protocol_path, protocol, "REJECTED")
    out = tmp_path / "research_bundle"

    main(
        [
            "finalize",
            "--protocol",
            str(protocol_path),
            "--evaluation",
            str(evaluation),
            "--out",
            str(out),
        ]
    )

    receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "REJECTED"
    assert receipt["research_bundle_created"] is False
    assert receipt["fit_performed"] is False
    assert receipt["trusted"] is False
    assert not list(out.rglob("*.joblib"))


def test_finalize_pass_fits_full_new_labels_and_readback_rejects_out_of_range(
    tmp_path, monkeypatch
):
    paths = write_population_csvs(tmp_path / "data")
    protocol = finalize_protocol(paths)
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    evaluation = write_finalization_evaluation(tmp_path, protocol_path, protocol, "PASS")
    out = tmp_path / "research_bundle"
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        monkeypatch.setenv(name, "1")

    main(
        [
            "finalize",
            "--protocol",
            str(protocol_path),
            "--evaluation",
            str(evaluation),
            "--out",
            str(out),
        ]
    )

    bundle_path = out / "research_bundle.joblib"
    bundle = joblib.load(bundle_path)
    metadata = bundle["metadata"]
    assert metadata["status"] == "RETROSPECTIVE_ONLY"
    assert metadata["trusted"] is False
    assert metadata["label_version"] == protocol["data_version"].get("version")
    assert len(metadata["training"]["qp"]["structure_ids"]) == 6
    assert metadata["target_units"]["qp"] == "dimensionless"
    research = bundle["research_model"]
    labels = pd.read_csv(paths["primary"]).query("parameter == 'qp'")
    prediction = research.predict_records("qp", labels.iloc[:1])[0]
    assert prediction["status"] == "ok"
    assert prediction["prediction"] == 350.0
    outside = labels.iloc[:1].copy()
    outside["area_um2"] = 999.0
    rejected = research.predict_records("qp", outside)[0]
    assert rejected["status"] == "unsupported"
    assert rejected["prediction"] is None
    readback = json.loads((out / "readback.json").read_text(encoding="utf-8"))
    assert readback["status"] == "PASS"
    assert readback["unsupported_range_rejection_passed"] is True
    receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["research_bundle_created"] is True
    assert receipt["research_bundle_sha256"] == file_sha256(bundle_path)


def test_fit_cli_selects_only_on_inner_ids_and_persists_hash_bound_bundles(
    tmp_path, monkeypatch
):
    paths = write_population_csvs(tmp_path / "data")
    protocol = make_protocol(paths)
    protocol["core_targets"] = ["qp"]
    protocol_path = tmp_path / "protocol.json"
    splits_path = tmp_path / "splits.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    splits_path.write_text(json.dumps(make_splits()), encoding="utf-8")
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        monkeypatch.setenv(name, "1")
    out = tmp_path / "models"

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts" / "run_device_median_models_v1.py"),
            "fit",
            "--protocol",
            str(protocol_path),
            "--data",
            str(tmp_path / "data"),
            "--splits",
            str(splits_path),
            "--out",
            str(out),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fit_counts"] == {
        "inner": 30,
        "final_outer_train": 6,
        "total": 36,
    }
    assert manifest["selection_counts"]["total"] == 2
    predictions = pd.read_csv(out / "inner_predictions.csv")
    assert len(predictions) == 30
    assert set(predictions.structure_key) == {f"struct_{index:03d}" for index in range(5)}
    assert "struct_005" not in set(predictions.structure_key)
    assert predictions.groupby(["population", "model"]).size().eq(5).all()
    bundle_path = out / "primary" / "normal_holdout" / "qp" / "model_bundle.joblib"
    bundle = joblib.load(bundle_path)
    assert bundle["metadata"]["training_structure_ids"] == [
        f"struct_{index:03d}" for index in range(5)
    ]
    assert bundle["metadata"]["label_source"]["sha256"] == file_sha256(paths["primary"])
    assert bundle["metadata"]["outer_test_predictions_generated"] is False
    assert bundle["metadata"]["outer_test_used_for_selection"] is False
    assert set(bundle["models"]) == set(make_protocol(paths)["models"])
    assert (out / "primary" / "normal_holdout" / "qp" / "metadata.json").is_file()
    with pytest.raises(FileExistsError, match="non-empty"):
        main(
            [
                "fit",
                "--protocol",
                str(protocol_path),
                "--data",
                str(tmp_path / "data"),
                "--splits",
                str(splits_path),
                "--out",
                str(out),
            ]
        )
