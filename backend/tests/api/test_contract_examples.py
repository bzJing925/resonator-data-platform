"""Keep the shared C01/C02/C05/C09 JSON example aligned with Pydantic schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.analysis import AnalysisSpec
from app.schemas.capabilities import Capabilities, SyncStatus
from app.schemas.processing import ProcessingEvent
from app.schemas.result_manifest import ResultManifest
from app.schemas.source import SourceRef


@pytest.fixture(scope="module")
def examples() -> dict[str, Any]:
    path = (
        Path(__file__).parents[3]
        / "frontend"
        / "src"
        / "types"
        / "platformContracts.example.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixed_contract_examples_validate(examples: dict[str, Any]) -> None:
    assert SourceRef.model_validate(examples["source_ref"])
    assert ProcessingEvent.model_validate(examples["processing_event"])
    assert AnalysisSpec.model_validate(examples["analysis_spec"])
    assert ResultManifest.model_validate(examples["result_manifest"])
    assert Capabilities.model_validate(examples["capabilities"])
    assert SyncStatus.model_validate(examples["sync_status"])


def test_nullable_legacy_source_fields_remain_explicit(examples: dict[str, Any]) -> None:
    source = SourceRef.model_validate(examples["source_ref"])

    assert source.source_device_key is None
    assert source.source_revision is None


@pytest.mark.parametrize(
    ("key", "path", "value"),
    [
        ("processing_event", ("status",), "queued"),
        ("processing_event", ("stage",), "validate"),
        ("analysis_spec", ("chart", "type"), "heatmap"),
        ("analysis_spec", ("chart", "z_mode"), "gradient"),
        ("result_manifest", ("consistency",), "probably_verified"),
        ("capabilities", ("mode",), "worker"),
        ("sync_status", ("state",), "maybe_synced"),
    ],
)
def test_unknown_contract_values_are_rejected(
    examples: dict[str, Any], key: str, path: tuple[str, ...], value: str
) -> None:
    example = json.loads(json.dumps(examples[key]))
    current = example
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value
    model = {
        "processing_event": ProcessingEvent,
        "analysis_spec": AnalysisSpec,
        "result_manifest": ResultManifest,
        "capabilities": Capabilities,
        "sync_status": SyncStatus,
    }[key]

    with pytest.raises(ValidationError):
        model.model_validate(example)


def test_empty_required_source_identity_is_rejected(examples: dict[str, Any]) -> None:
    example = json.loads(json.dumps(examples["source_ref"]))
    example["source_batch_id"] = ""

    with pytest.raises(ValidationError):
        SourceRef.model_validate(example)


def test_schema_version_is_required_and_fixed(examples: dict[str, Any]) -> None:
    example = json.loads(json.dumps(examples["processing_event"]))
    del example["schema_version"]
    with pytest.raises(ValidationError):
        ProcessingEvent.model_validate(example)

    example["schema_version"] = 2
    with pytest.raises(ValidationError):
        ProcessingEvent.model_validate(example)


def test_result_manifest_does_not_call_an_unknown_total_exact(examples: dict[str, Any]) -> None:
    example = json.loads(json.dumps(examples["result_manifest"]))
    example["total_relation"] = "unknown"

    with pytest.raises(ValidationError):
        ResultManifest.model_validate(example)


def test_capability_mode_matches_database_kind(examples: dict[str, Any]) -> None:
    example = json.loads(json.dumps(examples["capabilities"]))
    example["mode"] = "static"

    with pytest.raises(ValidationError):
        Capabilities.model_validate(example)
