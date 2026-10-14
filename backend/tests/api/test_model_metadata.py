"""Missing provenance is unknown; only existing, matching evaluation is verified."""

import json

import numpy as np

from app.services import model_metadata as svc


def artifacts(tmp_path):
    data, ckpt, evaluation = [tmp_path / name for name in ("data", "ckpt", "eval")]
    for folder in (data, ckpt, evaluation):
        folder.mkdir()
    for name in ("stage1.pt", "stage2.pt", "stage2_config.json"):
        (ckpt / name).write_bytes(name.encode())
    (data / "baseline.npz").write_bytes(b"baseline")
    (data / "meta.json").write_text(
        json.dumps(
            {
                "groups": [{"split": "train"}, {"split": "test"}],
                "training_source": {"kind": "measured", "evidence": "registered measured inputs"},
            }
        )
    )
    np.savez(
        data / "dataset.npz",
        x_struct=np.array([[1, 2, 1, 700, 0], [9, 9, 9, 900, 1]]),
        group_idx=np.array([0, 1]),
    )
    return data, ckpt, evaluation


def test_missing_and_legacy_artifacts(tmp_path):
    metadata = svc.describe(tmp_path, tmp_path, tmp_path)
    assert metadata.model_digest is None and metadata.validation_metrics is None
    assert metadata.training_source == "unknown" and metadata.training_range is None
    data, ckpt, evaluation = artifacts(tmp_path)
    (data / "meta.json").write_text("{}")
    metadata = svc.describe(data, ckpt, evaluation)
    assert metadata.model_digest is not None
    assert metadata.training_range is None and metadata.validation_status == "unknown"


def test_known_evaluation_range_and_changes(tmp_path):
    data, ckpt, evaluation = artifacts(tmp_path)
    report = {
        "rmse_full_db": 0.3,
        "fs_mae_mhz": 4.2,
        "targets": {"rmse_full_db": 0.01},
        "n_test_devices": 1,
        "n_test_groups": 1,
        "model_digest": svc.artifact_digest(svc.model_files(data, ckpt)),
        "validation_dataset_digest": svc.artifact_digest(
            [("dataset", data / "dataset.npz"), ("split", data / "meta.json")]
        ),
    }
    (evaluation / "report.json").write_text(json.dumps(report))
    metadata = svc.describe(data, ckpt, evaluation)
    assert metadata.validation_status == "verified"
    assert metadata.validation_metrics == {"rmse_full_db": 0.3, "fs_mae_mhz": 4.2}
    assert metadata.training_range["eg"] == [1, 1]  # Test rows cannot widen training range.
    assert metadata.training_source == "measured"
    inputs = dict(eg=1, fl=2, ag=1, area=700, pf=0)
    assert svc.with_applicability(metadata, inputs).applicability == "within_range"
    assert svc.with_applicability(metadata, {**inputs, "eg": 9}).applicability == "out_of_range"
    (ckpt / "stage1.pt").write_bytes(b"new-model")
    assert svc.describe(data, ckpt, evaluation).validation_status == "unverified"


def test_old_report_is_not_current_model_evidence(tmp_path):
    data, ckpt, evaluation = artifacts(tmp_path)
    (evaluation / "report.json").write_text('{"rmse_full_db":0.5,"fs_mae_mhz":NaN}')
    metadata = svc.describe(data, ckpt, evaluation)
    assert metadata.validation_metrics == {"rmse_full_db": 0.5, "fs_mae_mhz": None}
    assert metadata.validation_dataset_digest is None and metadata.validation_status == "unverified"
    assert "NaN" not in metadata.model_dump_json()

