"""Shared source/snapshot numbers, verifiable conditions and complete cohort scope."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, Device, ProcessingRun, StagedDeviceRow, UploadTask
from app.schemas.comparison import ComparisonRequest
from app.services import comparison_service as service
from app.services.processing_retry import configuration_digest
from app.services.source_identity import assign_batch_identity, batch_source_ref
from tests.fixtures.platform_db import DATABASES, platform_database

CASES = json.loads(
    (
        Path(__file__).parents[3] / "frontend/src/features/analysis/"
        "__fixtures__/platformComparison.json"
    ).read_text()
)


def certify_results(db, batch):
    run = db.scalar(select(ProcessingRun).where(ProcessingRun.batch_id == batch.id))
    for row in db.scalars(select(Device).where(Device.batch_id == batch.id)):
        payload = {
            field: getattr(row, field)
            for field in ["qs", "eg", "fl", "ag", "s_param_port", "source_device_key"]
        }
        db.merge(
            StagedDeviceRow(
                run_id=run.id,
                source_device_key=row.source_device_key,
                payload_json=payload,
                payload_digest=hashlib.sha256(
                    json.dumps(payload, sort_keys=True, allow_nan=False, default=str).encode()
                ).hexdigest(),
            )
        )
    db.commit()


def add_cohort(db, batch, values):
    assign_batch_identity(db, batch)
    batch.result_revision, batch.f_start_ghz, batch.f_end_ghz = 1, 1, 3
    config = {
        "f_start_ghz": 1,
        "f_end_ghz": 3,
        "deembed": False,
        "deembed_method": "default",
        "process_type": "AUTO",
    }
    config["accepted_config_digest"] = configuration_digest(config)
    task = UploadTask(
        batch_no=batch.batch_no,
        status="success",
        progress_pct=100,
        stage="done",
        stage_progress_pct=100,
        kind="upload",
    )
    db.add(task)
    db.flush()
    db.add(
        ProcessingRun(
            task_id=task.id,
            batch_id=batch.id,
            kind="upload",
            attempt=1,
            status="success",
            published_count=len(values),
            quality_status="complete",
            base_result_revision=0,
            config_json=config,
            algorithm_digest="a" * 64,
            mapping_digest="b" * 64,
        )
    )
    db.add_all(
        [
            Device(
                batch_id=batch.id,
                original_filename=f"{i}.s1p",
                qs=value,
                source_device_key=str(uuid4()),
                s_param_port="S11",
                eg=1,
                fl=2,
                ag=None,
                pf="N" if value is not None and value >= 2 else "Y",
            )
            for i, value in enumerate(values)
        ]
    )
    db.commit()
    certify_results(db, batch)


@pytest.fixture(params=DATABASES)
def cohorts(request, tmp_path):
    with platform_database(request.param, tmp_path) as (db, a):
        b = Batch(batch_no="other", mapping_id=a.mapping_id, file_path="files")
        db.add(b)
        db.commit()
        add_cohort(db, a, CASES["simple"]["a"])
        add_cohort(db, b, CASES["simple"]["b"])
        yield db, a, b


def request_body(db, a, b):
    return {
        "comparison": {
            "scope": "source",
            "a": {
                "source": batch_source_ref(db, a),
                "dataset_id": None,
                "query_spec": {"filters": {}, "fields": [], "order_by": None},
            },
            "b": {
                "source": batch_source_ref(db, b),
                "dataset_id": None,
                "query_spec": {"filters": {}, "fields": [], "order_by": None},
            },
            "metrics": ["qs"],
            "group_fields": ["eg", "s_param_port"],
            "common_frequency_ghz": None,
            "condition_policy": "require_equal",
            "wafer_metric": None,
            "unified_color_scale": True,
        },
        "quality_rule": None,
    }


def compare(db, a, b, mutate=None):
    body = request_body(db, a, b)
    if mutate:
        mutate(body)
    return service.compare(db.bind, ComparisonRequest.model_validate(body))


def test_shared_statistics_self_comparison_and_source_rows(cohorts):
    db, a, b = cohorts
    result = compare(db, a, b)
    stats = result["cohorts"]["a"]["statistics"]["qs"]
    for key in ["valid_n", "missing_n", "mean", "median", "p25", "p75", "std"]:
        assert stats[key] == pytest.approx(CASES["simple"][key], abs=1e-9, rel=1e-8)
    assert result["deltas"][0]["mean_delta"] == 0
    assert compare(db, a, a)["deltas"][0]["mean_delta"] == 0
    assert result["cohorts"]["a"]["manifest"]["matched_total"] == 4
    assert all(row["_source_ref"]["source_device_key"] for row in result["cohorts"]["a"]["rows"])
    assert result["units"]["qs"] == CASES["unit"]


def test_platform_fixture_matches_static_expected_numbers(cohorts):
    db, a, b = cohorts
    for batch, values in [(a, CASES["platform"]["a"]), (b, CASES["platform"]["b"])]:
        for row, value in zip(
            db.scalars(select(Device).where(Device.batch_id == batch.id).order_by(Device.id)),
            values,
            strict=True,
        ):
            row.qs = value
    db.commit()
    certify_results(db, a)
    certify_results(db, b)
    result = compare(db, a, b)
    for name in ["a", "b"]:
        assert result["cohorts"][name]["statistics"]["qs"]["mean"] == pytest.approx(
            CASES["platform"][name + "_mean"], abs=1e-9, rel=1e-8
        )
    assert result["deltas"][0]["mean_delta"] == pytest.approx(CASES["platform"]["delta"])


def test_missing_all_values_pf_and_quality_are_separate(cohorts):
    db, a, b = cohorts

    def rule(body):
        body["quality_rule"] = {
            "id": "q",
            "version": 1,
            "label": "QS",
            "predicates": [{"field": "qs", "op": "gte", "value": 2, "unit": "1"}],
            "combine": "and",
        }

    result = compare(db, a, b, rule)
    assert result["cohorts"]["a"]["quality"]["pass"] == 2
    assert result["cohorts"]["a"]["quality"]["unknown"] == 1
    assert result["cohorts"]["a"]["quality"]["pass_rate"] == pytest.approx(2 / 3)
    assert result["cohorts"]["a"]["pf"]["N"] == 2
    for row in db.scalars(select(Device).where(Device.batch_id == b.id)):
        row.qs = None
    db.commit()
    certify_results(db, b)
    result = compare(db, a, b, rule)
    assert result["cohorts"]["b"]["statistics"]["qs"]["missing_n"] == 4
    assert result["cohorts"]["b"]["statistics"]["qs"]["mean"] is None
    assert result["deltas"][0]["mean_delta"] is None


def test_different_port_requires_explicit_common_subset(cohorts):
    db, a, b = cohorts
    db.scalar(
        select(Device).where(Device.batch_id == b.id).order_by(Device.id)
    ).s_param_port = "S22"
    db.commit()
    certify_results(db, b)
    with pytest.raises(HTTPException) as exc:
        compare(db, a, b)
    assert exc.value.detail["code"] == "CONDITIONS_DIFFER"
    result = compare(
        db, a, b, lambda body: body["comparison"].update(condition_policy="common_subset")
    )
    assert result["conditions"]["excluded_rows"] == {"a": 0, "b": 1}
    assert result["cohorts"]["b"]["manifest"]["matched_total"] == 3
    assert result["cohorts"]["b"]["manifest"]["query_spec"]["filters"]["children"][-1]["op"] == "or"


@pytest.mark.parametrize("mismatch", ["frequency", "unknown", "unit", "limit", "structure_field"])
def test_invalid_conditions_units_and_full_scope(cohorts, monkeypatch, mismatch):
    db, a, b = cohorts
    body = request_body(db, a, b)
    if mismatch == "frequency":
        body["comparison"]["condition_policy"] = "common_subset"
        body["comparison"]["common_frequency_ghz"] = [2, 3]
    elif mismatch == "unknown":
        db.scalar(
            select(ProcessingRun).where(ProcessingRun.batch_id == b.id)
        ).algorithm_digest = None
        db.commit()
    elif mismatch == "unit":
        body["quality_rule"] = {
            "id": "q",
            "version": 1,
            "label": "wrong",
            "predicates": [{"field": "qs", "op": "gte", "value": 2, "unit": "MHz"}],
            "combine": "and",
        }
    elif mismatch == "structure_field":
        body["comparison"]["group_fields"] = ["structureType"]
    else:
        from app.config import Settings

        monkeypatch.setattr("app.config.get_settings", lambda: Settings(COMPARISON_MAX_ROWS=2))
    with pytest.raises(HTTPException) as exc:
        service.compare(db.bind, ComparisonRequest.model_validate(body))
    assert exc.value.status_code == (413 if mismatch == "limit" else 422)


def test_change_during_read_returns_409(cohorts, monkeypatch):
    db, a, b = cohorts
    original = service._summarize
    changed = False

    def mutation(*args):
        nonlocal changed
        result = original(*args)
        if not changed:
            with Session(db.bind) as writer:
                writer.scalar(select(Device).order_by(Device.id)).qs = 100
                writer.commit()
            changed = True
        return result

    monkeypatch.setattr(service, "_summarize", mutation)
    with pytest.raises(HTTPException) as exc:
        compare(db, a, b)
    assert exc.value.detail["code"] == "QUERY_SOURCE_CHANGED"


def test_changed_metrics_do_not_reuse_prior_algorithm_proof(cohorts):
    db, a, b = cohorts
    db.scalar(select(Device).where(Device.batch_id == b.id).order_by(Device.id)).qs = 100
    db.commit()
    with pytest.raises(HTTPException) as exc:
        compare(db, a, b)
    assert exc.value.detail["code"] == "CONDITIONS_UNKNOWN"


def test_statistics_nulls_nonfinite_and_large_finite_are_json_safe():
    result = service.statistics(
        [{"qs": 1e308}, {"qs": 1e308}, {"qs": float("nan")}, {"qs": None}], "qs"
    )
    assert result["valid_n"] == 2 and result["missing_n"] == 2
    assert result["mean"] == 1e308 and result["std"] == 0
    json.dumps(result, allow_nan=False)


def test_inspection_reads_evidence_and_shapes_without_statistics(cohorts, monkeypatch):
    db, a, b = cohorts

    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must not compute statistics")

    monkeypatch.setattr(service, "statistics", forbidden)
    monkeypatch.setattr(service, "_summarize", forbidden)
    result = service.inspect_conditions(db.bind,
        ComparisonRequest.model_validate(request_body(db, a, b)))
    assert result["units"] == {"qs": "1"}
    assert result["endpoints"]["a"]["conditions"]["frequency_ghz"] == [1, 3]
    assert result["endpoints"]["a"]["structures"] == [[1, 2, None, "S11"]]
    assert result["endpoints"]["a"]["matched_total"] == 4
    assert result["endpoints"]["b"]["issues"] == []
    assert "rows" not in result["endpoints"]["a"]
    assert "statistics" not in result["endpoints"]["a"]


def test_inspection_returns_unknown_for_changed_payload_and_keeps_other_source(cohorts):
    db, a, b = cohorts
    row = db.scalar(select(Device).where(Device.batch_id == a.id))
    row.qs = 999
    db.commit()
    result = service.inspect_conditions(db.bind,
        ComparisonRequest.model_validate(request_body(db, a, b)))
    assert result["endpoints"]["a"]["conditions"] is None
    assert result["endpoints"]["a"]["issues"][0]["code"] == "CONDITIONS_UNKNOWN"
    assert result["endpoints"]["b"]["conditions"] is not None


def test_inspection_keeps_concrete_port_differences_and_rejects_stale(cohorts):
    db, a, b = cohorts
    row = db.scalar(select(Device).where(Device.batch_id == b.id))
    row.s_param_port = "S22"
    db.commit()
    certify_results(db, b)
    body = request_body(db, a, b)
    result = service.inspect_conditions(db.bind, ComparisonRequest.model_validate(body))
    assert [1, 2, None, "S22"] in result["endpoints"]["b"]["structures"]
    body["comparison"]["a"]["source"]["source_revision"] = 0
    with pytest.raises(HTTPException) as error:
        service.inspect_conditions(db.bind, ComparisonRequest.model_validate(body))
    assert error.value.status_code == 409


def test_extreme_difference_remains_json_safe(tmp_path):
    with platform_database("sqlite", tmp_path) as (db, a):
        b = Batch(batch_no="extreme", mapping_id=a.mapping_id, file_path="files")
        db.add(b)
        db.commit()
        add_cohort(db, a, [-1e308])
        add_cohort(db, b, [1e308])
        result = service.compare(db.bind, ComparisonRequest.model_validate(request_body(db, a, b)))
        assert result["deltas"][0]["mean_delta"] is None
        json.dumps(result, allow_nan=False)
