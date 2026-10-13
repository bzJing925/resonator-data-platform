"""Read existing artifacts without loading torch, training, or estimating metrics."""

from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.schemas.model_metadata import ModelMetadata

INPUTS = ("eg", "fl", "ag", "area", "pf")
METRICS = (
    "rmse_full_db",
    "rmse_aligned_db",
    "rmse_cellmean_db",
    "rmse_dense_db",
    "rmse_transition_db",
    "rmse_far_db",
    "fs_mae_mhz",
    "fp_mae_mhz",
    "fs_reextract_mae_mhz",
    "fp_reextract_mae_mhz",
    "improvement_vs_b0_pct",
)


def artifact_digest(files):
    paths = [(name, Path(path)) for name, path in files]
    if not paths or any(not path.is_file() for _, path in paths):
        return None
    digest = hashlib.sha256()
    for name, path in sorted(paths):
        digest.update(name.encode() + b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def model_files(data, ckpt):
    stage2 = ckpt / "stage2_ft.pt"
    if not stage2.exists():
        stage2 = ckpt / "stage2.pt"
    return [
        ("stage1", ckpt / "stage1.pt"),
        ("stage2", stage2),
        ("stage2_config", ckpt / "stage2_config.json"),
        ("baseline", data / "baseline.npz"),
        ("normalization", data / "meta.json"),
    ]


def _read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def describe(data: Path, ckpt: Path, evaluation: Path):
    paths = [path for _, path in model_files(data, ckpt)] + [
        data / "dataset.npz",
        data / "sim_extension.npz",
        evaluation / "report.json",
    ]
    fingerprint = tuple(
        (str(p), p.stat().st_mtime_ns, p.stat().st_size) if p.is_file() else (str(p), None, None)
        for p in paths
    )
    return ModelMetadata.model_validate(
        _describe_cached(str(data), str(ckpt), str(evaluation), fingerprint)
    )


@lru_cache(maxsize=8)
def _describe_cached(data_name, ckpt_name, evaluation_name, fingerprint):
    data, ckpt, evaluation = Path(data_name), Path(ckpt_name), Path(evaluation_name)
    result = ModelMetadata(model_digest=artifact_digest(model_files(data, ckpt)))
    meta = _read(data / "meta.json")
    source = meta.get("training_source")
    if isinstance(source, dict) and source.get("kind") in {"measured", "simulation", "mixed"}:
        if isinstance(source.get("evidence"), str) and source["evidence"].strip():
            result.training_source, result.source_evidence = source["kind"], source["evidence"]
    try:
        with np.load(data / "dataset.npz", allow_pickle=False) as dataset:
            groups = meta.get("groups", [])
            train = {i for i, group in enumerate(groups) if group.get("split") == "train"}
            xs = dataset["x_struct"]
            indices = dataset["group_idx"]
            rows = xs[np.isin(indices, list(train))]
            if len(rows) and rows.ndim == 2 and rows.shape[1] == 5 and np.isfinite(rows).all():
                result.training_range = {
                    name: [float(rows[:, i].min()), float(rows[:, i].max())]
                    for i, name in enumerate(INPUTS)
                }
                result.training_range_status = "known"
    except (OSError, ValueError, KeyError, IndexError):
        pass
    try:
        with np.load(data / "sim_extension.npz", allow_pickle=False) as extension:
            result.simulation_only_areas = sorted(
                {float(area) for area in extension["cells"][:, 2] if math.isfinite(float(area))}
            )
    except (OSError, ValueError, KeyError, IndexError):
        pass
    report = _read(evaluation / "report.json")
    metrics = {
        key: float(report[key]) if math.isfinite(float(report[key])) else None
        for key in METRICS
        if isinstance(report.get(key), (int, float)) and not isinstance(report[key], bool)
    }
    if metrics:
        result.validation_metrics, result.validation_status = metrics, "unverified"
        result.validation_summary = {
            key: value
            for key in ("n_test_devices", "n_test_groups")
            if isinstance(value := report.get(key), int)
            and not isinstance(value, bool)
            and value >= 0
        }
        claimed = report.get("validation_dataset_digest")
        if (
            isinstance(claimed, str)
            and len(claimed) == 64
            and all(c in "0123456789abcdef" for c in claimed)
        ):
            result.validation_dataset_digest = claimed
        if (
            result.model_digest
            and report.get("model_digest") == result.model_digest
            and result.validation_dataset_digest
            == artifact_digest([("dataset", data / "dataset.npz"), ("split", data / "meta.json")])
        ):
            result.validation_status = "verified"
    return result.model_dump(mode="json")


def with_applicability(metadata, inputs):
    result = metadata.model_copy(deep=True)
    if result.training_range is not None:
        result.applicability = (
            "within_range"
            if all(
                math.isfinite(float(inputs[name])) and bounds[0] <= float(inputs[name]) <= bounds[1]
                for name, bounds in result.training_range.items()
            )
            else "out_of_range"
        )
    return result
