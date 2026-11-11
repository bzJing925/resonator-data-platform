#!/usr/bin/env python3
"""Independently reproduce and compare the P13 O14 failure delivery (T17/G6-N)."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
ML = BACKEND.parent
MAIN = Path("/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master")
RUN = BACKEND / "surrogate_data" / "optimization_p13_20261027_r01"
REC = RUN / "resume_20261029_r01"
DELIVERY = RUN / "delivery"
T15_MANIFEST = DELIVERY / "t15_artifact_manifest.json"
REPORT_PATH = RUN / "verification" / "numerical_delivery.json"
MANIFEST_PATH = REC / "verification" / "t17_artifact_manifest.json"
ATOL = 1e-12
RTOL = 1e-12
MODELS = ("P3", "P6C")
METRICS = ("k2eff_pct", "qs", "qp", "bodeq_fitted")
CANDIDATE_NUMERIC_FIELDS = {"eg_um", "fl_um", "area_um2", "legacy_nearest_scaled_distance"}
HISTORICAL_COLUMNS = {
    "P3": {"k2eff_pct": "k2eff_pct_prediction", "qp": "qp_prediction"},
    "P6C": {"k2eff_pct": "k2eff_pct_p6c", "qp": "qp_p6c"},
}
C19_ID = "C19_negative_control_EG0.0_FL1.5_A700_PF1"
P3_STRUCTURE_ID = "s_c454ac7ec8a2a7f8"
CONSERVATIVE_REASONS = (
    "no_eligible_models;P3_AGGREGATION_ESTIMAND_MISMATCH;"
    "E1_COORDINATE_OBSERVATIONS_MISSING;HARD_CAPACITY_INSUFFICIENT_13_KEYS"
)
ERROR_REASONS = "no_eligible_models;HARD_CAPACITY_INSUFFICIENT_13_KEYS"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "bytes": None, "sha256": None}
    return {
        "path": str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(
            handle,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _decimal_difference(
    formal: str, rebuilt: str, artifact: str, row: int, field: str
) -> tuple[dict[str, Any] | None, bool]:
    if not formal or not rebuilt:
        if formal == rebuilt:
            return None, False
        return (
            {
                "artifact": artifact,
                "row": row,
                "field": field,
                "kind": "null_exact",
                "formal": formal,
                "rebuilt": rebuilt,
            },
            False,
        )
    try:
        left = Decimal(formal)
        right = Decimal(rebuilt)
        if not left.is_finite() or not right.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        if formal == rebuilt:
            return None, False
        return (
            {
                "artifact": artifact,
                "row": row,
                "field": field,
                "kind": "invalid_numeric",
                "formal": formal,
                "rebuilt": rebuilt,
            },
            False,
        )
    absolute = abs(left - right)
    limit = Decimal(str(ATOL)) + Decimal(str(RTOL)) * max(abs(left), abs(right))
    if absolute <= limit:
        return None, formal != rebuilt
    relative = absolute / max(abs(left), abs(right)) if max(abs(left), abs(right)) else Decimal(0)
    return (
        {
            "artifact": artifact,
            "row": row,
            "field": field,
            "kind": "numeric",
            "formal": formal,
            "rebuilt": rebuilt,
            "absolute_difference": str(absolute),
            "relative_difference": str(relative),
            "tolerance": str(limit),
        },
        False,
    )


def _compare_json_values(
    formal: Any, rebuilt: Any, path: str, differences: list[dict[str, Any]]
) -> None:
    if isinstance(formal, dict) and isinstance(rebuilt, dict):
        for key in sorted(set(formal) | set(rebuilt)):
            if key not in formal or key not in rebuilt:
                differences.append(
                    {
                        "artifact": "decision.json",
                        "field": f"{path}.{key}" if path else key,
                        "kind": "missing_key",
                        "formal": formal.get(key, "<MISSING>"),
                        "rebuilt": rebuilt.get(key, "<MISSING>"),
                    }
                )
            else:
                child = f"{path}.{key}" if path else key
                _compare_json_values(formal[key], rebuilt[key], child, differences)
        return
    if isinstance(formal, list) and isinstance(rebuilt, list):
        if len(formal) != len(rebuilt):
            differences.append(
                {
                    "artifact": "decision.json",
                    "field": path,
                    "kind": "array_length",
                    "formal": len(formal),
                    "rebuilt": len(rebuilt),
                }
            )
        for index, (left, right) in enumerate(zip(formal, rebuilt, strict=False)):
            _compare_json_values(left, right, f"{path}[{index}]", differences)
        return
    equal = formal == rebuilt and type(formal) is type(rebuilt)
    if not equal and isinstance(formal, (int, float)) and isinstance(rebuilt, (int, float)):
        if not isinstance(formal, bool) and not isinstance(rebuilt, bool):
            equal = math.isclose(float(formal), float(rebuilt), abs_tol=ATOL, rel_tol=RTOL)
    if not equal:
        differences.append(
            {
                "artifact": "decision.json",
                "field": path,
                "kind": "exact",
                "formal": formal,
                "rebuilt": rebuilt,
            }
        )


def _compare_csv(
    artifact: str,
    formal_path: Path,
    rebuilt_path: Path,
    numeric_fields: set[str],
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]], list[str], list[dict[str, str]]]:
    formal_fields, formal_rows = read_csv(formal_path)
    rebuilt_fields, rebuilt_rows = read_csv(rebuilt_path)
    differences: list[dict[str, Any]] = []
    within_tolerance = 0
    if formal_fields != rebuilt_fields:
        differences.append(
            {
                "artifact": artifact,
                "row": 0,
                "field": "__header__",
                "kind": "exact_order",
                "formal": formal_fields,
                "rebuilt": rebuilt_fields,
            }
        )
    if len(formal_rows) != len(rebuilt_rows):
        differences.append(
            {
                "artifact": artifact,
                "row": None,
                "field": "__row_count__",
                "kind": "exact_count",
                "formal": len(formal_rows),
                "rebuilt": len(rebuilt_rows),
            }
        )
    row_results = []
    for index in range(max(len(formal_rows), len(rebuilt_rows))):
        if index >= len(formal_rows) or index >= len(rebuilt_rows):
            differences.append(
                {
                    "artifact": artifact,
                    "row": index + 1,
                    "field": "__row__",
                    "kind": "missing_row",
                    "formal": formal_rows[index] if index < len(formal_rows) else None,
                    "rebuilt": rebuilt_rows[index] if index < len(rebuilt_rows) else None,
                }
            )
            continue
        left = formal_rows[index]
        right = rebuilt_rows[index]
        before = len(differences)
        for field in dict.fromkeys([*formal_fields, *rebuilt_fields]):
            if field not in left or field not in right:
                differences.append(
                    {
                        "artifact": artifact,
                        "row": index + 1,
                        "field": field,
                        "kind": "missing_field",
                        "formal": left.get(field, "<MISSING>"),
                        "rebuilt": right.get(field, "<MISSING>"),
                    }
                )
                continue
            if field in numeric_fields:
                difference, close = _decimal_difference(
                    left[field], right[field], artifact, index + 1, field
                )
                within_tolerance += int(close)
                if difference is not None:
                    differences.append(difference)
            elif left[field] != right[field]:
                differences.append(
                    {
                        "artifact": artifact,
                        "row": index + 1,
                        "field": field,
                        "kind": "exact",
                        "formal": left[field],
                        "rebuilt": right[field],
                    }
                )
        result: dict[str, Any] = {
            "row": index + 1,
            "candidate_id": left.get("candidate_id"),
            "matches": len(differences) == before,
        }
        if artifact == "metrics.csv":
            result.update(
                model_id=left.get("model_id"),
                metric=left.get("metric"),
                value=left.get("value"),
                source_file_id=left.get("source_file_id"),
                source_row_key=left.get("source_row_key"),
                null_reason=left.get("null_reason"),
            )
        else:
            result.update(
                control_type=left.get("control_type"),
                support_label=left.get("support_label"),
                source_row_key=left.get("source_row_key"),
            )
        row_results.append(result)
    return differences, within_tolerance, row_results, formal_fields, formal_rows


def compare_deliveries(formal_dir: Path, rebuilt_dir: Path) -> dict[str, Any]:
    """Compare all O14 fields; only declared numeric fields receive tolerance."""
    candidate_differences, candidate_close, candidate_results, _, candidates = _compare_csv(
        "candidates.csv",
        formal_dir / "candidates.csv",
        rebuilt_dir / "candidates.csv",
        CANDIDATE_NUMERIC_FIELDS,
    )
    metric_differences, metric_close, metric_results, _, metrics = _compare_csv(
        "metrics.csv",
        formal_dir / "metrics.csv",
        rebuilt_dir / "metrics.csv",
        {"value"},
    )
    formal_decision = read_json(formal_dir / "decision.json")
    rebuilt_decision = read_json(rebuilt_dir / "decision.json")
    decision_differences: list[dict[str, Any]] = []
    _compare_json_values(formal_decision, rebuilt_decision, "", decision_differences)
    differences = [*candidate_differences, *metric_differences, *decision_differences]
    hashes = {
        name: {
            "formal": sha256(formal_dir / name),
            "rebuilt": sha256(rebuilt_dir / name),
            "byte_identical": (formal_dir / name).read_bytes()
            == (rebuilt_dir / name).read_bytes(),
        }
        for name in ("candidates.csv", "metrics.csv", "decision.json")
    }
    grades = formal_decision.get("grade_counts", {})
    counts = {
        "candidate_rows": len(candidates),
        "metric_rows": len(metrics),
        "nonnegative_control_rows": sum(
            row.get("control_type") == "nonnegative_control" for row in candidates
        ),
        "negative_control_rows": sum(
            row.get("control_type") == "negative_control" for row in candidates
        ),
        "valued_metric_rows": sum(bool(row.get("value")) for row in metrics),
        "null_metric_rows": sum(not bool(row.get("value")) for row in metrics),
        "recommendation_count": formal_decision.get("recommendation_count"),
        "grade_A": grades.get("A"),
        "grade_B": grades.get("B"),
        "grade_C": grades.get("C"),
    }
    return {
        "status": "PASS" if not differences else "FAILED",
        "formal_dir": str(formal_dir),
        "rebuilt_dir": str(rebuilt_dir),
        "tolerance": {
            "atol": ATOL,
            "rtol": RTOL,
            "rule": "abs(a-b) <= atol + rtol*max(abs(a),abs(b))",
            "exact_fields": "IDs/order/grades/nulls/reasons/source fields",
        },
        "counts": counts,
        "differences": differences,
        "difference_count": len(differences),
        "numeric_within_tolerance_count": candidate_close + metric_close,
        "byte_identical": all(row["byte_identical"] for row in hashes.values()),
        "output_hashes": hashes,
        "candidate_row_results": candidate_results,
        "metric_row_results": metric_results,
        "decision": formal_decision,
    }


def _expected_decision() -> dict[str, Any]:
    return {
        "scientific_status": "REJECTED",
        "decision": "evidence_insufficient",
        "reason": "no_eligible_models",
        "eligible_models": [],
        "recommendation_eligible": False,
        "recommendation_count": 0,
        "grade": None,
        "grade_null_reason": "no_eligible_models",
        "grade_counts": {"A": 0, "B": 0, "C": 0},
        "baseline": {"value": "14.674470284168734", "unit": "percent_number"},
        "absolute_target": {"value": "15.674470284168734", "unit": "percent_number"},
        "required_uplift": {"value": "1", "unit": "percentage_points"},
        "conservative_evidence": {
            "conservative_value": None,
            "conservative_value_null_reasons": CONSERVATIVE_REASONS.split(";"),
            "error_margin": None,
            "error_margin_null_reasons": ERROR_REASONS.split(";"),
            "extrapolation_distance": None,
            "extrapolation_distance_null_reasons": ["no_eligible_models"],
            "testable_max_distance": None,
            "testable_max_distance_null_reasons": ["no_eligible_models"],
        },
        "neighborhood": {
            "status": "not_executed",
            "null_reason": "no_eligible_models",
            "nine_point_prediction_executed": False,
        },
        "random_process": {
            "status": "not_executed",
            "reason": "no_eligible_models",
            "seed": 20261027,
            "seed_used": False,
            "random_draw_count": 0,
            "bootstrap_prediction_count": 0,
            "refit_count": 0,
        },
        "search_status": "not_executed",
        "trusted_for_unmeasured_optimization": False,
    }


def inspect_independent_sources(formal_dir: Path) -> dict[str, Any]:
    """Rebuild expected raw records from frozen sources without importing A0 code."""
    manifest = read_json(T15_MANIFEST)
    schema = read_json(REC / "contracts" / "failure_delivery_schema.json")
    spec = schema["x-p13-failure-delivery"]
    mismatches: list[dict[str, Any]] = []

    def check(condition: bool, location: str, expected: Any, actual: Any) -> None:
        if not condition:
            mismatches.append(
                {"location": location, "expected": expected, "actual": actual}
            )

    input_results = []
    for record in manifest["inputs"]:
        path = Path(record["path"])
        actual = file_record(path)
        matches = (
            actual["exists"]
            and actual["sha256"] == record["sha256"]
            and actual["bytes"] == record["bytes"]
        )
        input_results.append(
            {"file_id": record["file_id"], **actual, "matches": matches}
        )
        check(matches, f"registered_input:{record['file_id']}", record, actual)
    receipt_results = []
    for group in ("outputs", "temporary_outputs", "source_files"):
        for record in manifest[group]:
            actual = file_record(Path(record["path"]))
            matches = (
                actual["exists"]
                and actual["sha256"] == record["sha256"]
                and actual["bytes"] == record["bytes"]
            )
            receipt_results.append({"group": group, **actual, "matches": matches})
            check(matches, f"receipt:{group}:{record['path']}", record, actual)

    paths = {record["file_id"]: Path(record["path"]) for record in manifest["inputs"]}
    p12_fields, p12 = read_csv(paths["P12_CANDIDATES"])
    _, o11 = read_csv(paths["O11_GRID_PREDICTIONS"])
    _, o12 = read_csv(paths["O12_SHORTLIST"])
    _, o13 = read_csv(paths["O13_CANDIDATE_SCORES"])
    _, structures = read_csv(paths["P3_STRUCTURES"])
    source_cells = 0
    for index, (base, grid, short, score) in enumerate(
        zip(p12, o11, o12, o13, strict=False), start=1
    ):
        for field in p12_fields:
            for stage, row in (("O11", grid), ("O12", short), ("O13", score)):
                source_cells += 1
                check(
                    row.get(field) == base[field],
                    f"source_chain:{index}:{stage}:{field}",
                    base[field],
                    row.get(field),
                )
        check(
            score["source_row_number"] == str(index),
            f"source_row:{index}",
            str(index),
            score["source_row_number"],
        )
        check(
            score["source_file_id"] == "P12_CANDIDATES",
            f"source_id:{index}",
            "P12_CANDIDATES",
            score["source_file_id"],
        )
        check(
            score["source_sha256"] == sha256(paths["P12_CANDIDATES"]),
            f"source_hash:{index}",
            sha256(paths["P12_CANDIDATES"]),
            score["source_sha256"],
        )

    geometry_results = []
    for index, candidate in enumerate(o13, start=1):
        matches = []
        for structure in structures:
            if all(
                Decimal(structure[field]) == Decimal(candidate[field])
                for field in ("eg_um", "fl_um", "area_um2", "has_pf")
            ):
                matches.append(structure)
        ids = [row["structure_id"] for row in matches]
        expected_match = {
            "status": "exact_match" if ids else "no_exact_match",
            "match_count": len(ids),
            "structure_ids": ids,
        }
        source_match = {
            "status": "exact_match"
            if candidate["measured_exact_match"] == "True"
            else "no_exact_match",
            "match_count": int(candidate["measured_match_count"]),
            "structure_ids": candidate["measured_match_structure_ids"].split(";")
            if candidate["measured_match_structure_ids"]
            else [],
        }
        check(expected_match == source_match, f"geometry:{index}", expected_match, source_match)
        geometry_results.append(
            {"candidate_id": candidate["candidate_id"], **expected_match}
        )

    candidate_fields, actual_candidates = read_csv(formal_dir / "candidates.csv")
    check(
        candidate_fields == schema["$defs"]["candidate"]["required"],
        "candidate_header",
        schema["$defs"]["candidate"]["required"],
        candidate_fields,
    )
    check(len(actual_candidates) == 20, "candidate_count", 20, len(actual_candidates))
    for index, (actual, source, geometry) in enumerate(
        zip(actual_candidates, o13, geometry_results, strict=False), start=1
    ):
        expected: dict[str, Any] = {
            "candidate_id": source["candidate_id"],
            "control_type": source["control_type"],
            "eg_um": source["eg_um"],
            "fl_um": source["fl_um"],
            "area_um2": source["area_um2"],
            "has_pf": source["has_pf"],
            "branch_id": source["branch_id"],
            "support_label": source["p3_support"],
            "source_file_id": "P12_CANDIDATES",
            "source_sha256": sha256(paths["P12_CANDIDATES"]),
            "source_row_key": f"source_row_number={source['source_row_number']}",
            "legacy_recommendation_text": source["recommendation"],
            "legacy_nearest_scaled_distance": source["nearest_scaled_distance"],
            "recommendation_eligible": "False",
            "grade": "",
            "grade_null_reason": "no_eligible_models",
            "evidence_status": "evidence_insufficient",
            "trusted_for_unmeasured_optimization": "False",
            "new_candidate": "False",
            "model_prediction_provenance": {
                "value_origin": "legacy_model_prediction",
                "new_prediction": False,
                "prediction_recomputed": False,
                "trusted_for_unmeasured_optimization": False,
            },
            "measured_geometry_match": geometry,
            "exclusion_reason": source["exclusion_reason"],
            "conservative_value": "",
            "conservative_value_null_reason": CONSERVATIVE_REASONS,
            "error_margin": "",
            "error_margin_null_reason": ERROR_REASONS,
            "extrapolation_distance": "",
            "extrapolation_distance_null_reason": "no_eligible_models",
            "testable_max_distance": "",
            "testable_max_distance_null_reason": "no_eligible_models",
            "neighborhood_result": "",
            "neighborhood_status": "not_executed",
            "neighborhood_null_reason": "no_eligible_models",
        }
        for field in candidate_fields:
            actual_value: Any = actual[field]
            expected_value = expected[field]
            if field in {"model_prediction_provenance", "measured_geometry_match"}:
                actual_value = json.loads(actual_value)
                if field == "measured_geometry_match":
                    actual_value = {
                        key: actual_value[key]
                        for key in ("status", "match_count", "structure_ids")
                    }
                    expected_value = {
                        key: expected_value[key]
                        for key in ("status", "match_count", "structure_ids")
                    }
            check(
                actual_value == expected_value,
                f"candidate:{index}:{field}",
                expected_value,
                actual_value,
            )

    metric_fields, actual_metrics = read_csv(formal_dir / "metrics.csv")
    check(
        metric_fields == spec["metrics_csv_fields"],
        "metric_header",
        spec["metrics_csv_fields"],
        metric_fields,
    )
    structure_by_id = {row["structure_id"]: row for row in structures}
    expected_metrics = []
    for source, geometry in zip(o13, geometry_results, strict=True):
        for model in MODELS:
            for metric in METRICS:
                historical = HISTORICAL_COLUMNS[model].get(metric)
                row = {
                    "candidate_id": source["candidate_id"],
                    "model_id": model,
                    "metric": metric,
                    "value": source[historical] if historical else "",
                    "unit": spec["metric_units"][metric],
                    "source_file_id": "P12_CANDIDATES" if historical else "",
                    "source_sha256": sha256(paths["P12_CANDIDATES"]) if historical else "",
                    "source_row_key": f"source_row_number={source['source_row_number']}"
                    if historical
                    else "",
                    "scope": "p12_failure_control" if historical else "",
                    "support_label": source["p3_support"] if historical else "",
                    "evidence_status": "registered_historical_reference"
                    if historical
                    else "evidence_insufficient",
                    "null_reason": "" if historical else "NO_REGISTERED_SAME_STRUCTURE_VALUE",
                }
                if model == "P3" and metric in {"qs", "bodeq_fitted"} and geometry["structure_ids"]:
                    check(
                        len(geometry["structure_ids"]) == 1,
                        f"same_structure_ambiguity:{source['candidate_id']}:{metric}",
                        1,
                        len(geometry["structure_ids"]),
                    )
                    measured = structure_by_id[geometry["structure_ids"][0]]
                    row.update(
                        value=measured[metric],
                        source_file_id="P3_STRUCTURES",
                        source_sha256=sha256(paths["P3_STRUCTURES"]),
                        source_row_key=f"structure_id={measured['structure_id']}",
                        scope="O01_P3_deembedded_structure_aggregates",
                        support_label=source["p3_support"],
                        evidence_status="registered_historical_reference",
                        null_reason="",
                    )
                expected_metrics.append(row)
    check(len(actual_metrics) == 160, "metric_count", 160, len(actual_metrics))
    for index, (actual, expected) in enumerate(
        zip(actual_metrics, expected_metrics, strict=False), start=1
    ):
        for field in metric_fields:
            check(
                actual[field] == expected[field],
                f"metric:{index}:{field}",
                expected[field],
                actual[field],
            )

    actual_decision = read_json(formal_dir / "decision.json")
    expected_decision = _expected_decision()
    check(actual_decision == expected_decision, "decision", expected_decision, actual_decision)
    selection = read_json(paths["O09_SELECTION"])
    eligible = read_json(paths["O09_ELIGIBLE_MODELS"])
    retrospective = read_json(paths["O10_RETROSPECTIVE_REPORT"])
    protocol = read_json(paths["O11_SEARCH_PROTOCOL"])
    robustness = read_json(paths["O13_ROBUSTNESS_REPORT"])
    bootstrap = read_json(paths["O13_BOOTSTRAP_INDICES"])
    resume = read_json(paths["R05_RESUME_DECISION"])
    semantics = {
        "O09_rejected_empty_eligibility": selection["status"] == eligible["status"] == "REJECTED"
        and not selection["selected_configurations"]
        and not eligible["eligible_models"],
        "O10_failure_delivery_only": retrospective["failure_path"]["delivery_mode"]
        == "failure_delivery_only"
        and not retrospective["new_outer_fit_or_prediction_generated"],
        "O11_search_not_executed": not protocol["search_executed"]
        and protocol["new_grid_prediction_count"] == 0,
        "O13_no_random_or_refit": robustness["scientific_status"] == "REJECTED"
        and not robustness["search_executed"]
        and robustness["refit_count"] == 0
        and robustness["bootstrap_prediction_count"] == 0
        and not bootstrap["seed_consumed"]
        and not bootstrap["rng_called"]
        and bootstrap["indices"] == [],
        "R05_scoped_failure_only": resume["gr0_s_scope"] == "P13_SCOPED_FAILURE_DELIVERY_ONLY"
        and resume["eligible_models"] == []
        and resume["recommendation_count"] == 0,
    }
    for name, result in semantics.items():
        check(result, f"source_semantics:{name}", True, result)
    p12_manifest = read_json(paths["P12_OUTPUTS_MANIFEST"])
    p3_manifest = read_json(paths["P3_DATA_OUTPUTS_MANIFEST"])
    check(
        p12_manifest["candidates.csv"] == sha256(paths["P12_CANDIDATES"]),
        "P12_manifest_link",
        sha256(paths["P12_CANDIDATES"]),
        p12_manifest["candidates.csv"],
    )
    check(
        p3_manifest["structures.csv"] == sha256(paths["P3_STRUCTURES"]),
        "P3_manifest_link",
        sha256(paths["P3_STRUCTURES"]),
        p3_manifest["structures.csv"],
    )
    c19_values = [
        dict(row)
        for row in actual_metrics
        if row["candidate_id"] == C19_ID and row["source_file_id"] == "P3_STRUCTURES"
    ]
    check(len(c19_values) == 2, "C19_same_structure_count", 2, len(c19_values))
    check(
        [(row["metric"], row["value"]) for row in c19_values]
        == [("qs", "942.3123232596486"), ("bodeq_fitted", "926.111784408804")],
        "C19_same_structure_values",
        [("qs", "942.3123232596486"), ("bodeq_fitted", "926.111784408804")],
        [(row["metric"], row["value"]) for row in c19_values],
    )
    return {
        "status": "PASS" if not mismatches else "FAILED",
        "mismatches": mismatches,
        "counts": {
            "candidate_rows": len(actual_candidates),
            "nonnegative_control_rows": sum(
                row["control_type"] == "nonnegative_control" for row in actual_candidates
            ),
            "negative_control_rows": sum(
                row["control_type"] == "negative_control" for row in actual_candidates
            ),
            "metric_rows": len(actual_metrics),
            "valued_metric_rows": sum(bool(row["value"]) for row in actual_metrics),
            "null_metric_rows": sum(not bool(row["value"]) for row in actual_metrics),
            "source_chain_full_precision_cells": source_cells,
            "registered_input_hashes": len(input_results),
            "o14_receipts": len(receipt_results),
            "geometry_candidates_recomputed": len(geometry_results),
            "exact_geometry_candidates": sum(
                bool(row["structure_ids"]) for row in geometry_results
            ),
            "registered_c19_same_structure_values": len(c19_values),
        },
        "input_hash_results": input_results,
        "receipt_results": receipt_results,
        "geometry_results": geometry_results,
        "c19_same_structure_values": c19_values,
        "source_semantics": semantics,
        "decision": actual_decision,
    }


def classify_write_path(path: Path) -> dict[str, Any]:
    """Classify post-H00 drift only by the GR0-S registered write scopes."""
    resolved = path.resolve()
    explicit = {
        BACKEND / "scripts" / "run_optimization_p13_failure_contract.py": (
            "R04",
            "A4:BE_CONTRACT_SCRIPT",
        ),
        BACKEND / "tests" / "ml" / "test_optimization_p13_failure_contract.py": (
            "R04",
            "A4:BE_CONTRACT_TEST",
        ),
        BACKEND / "scripts" / "verify_optimization_p13_failure_delivery.py": (
            "T17",
            "A4:BE_NUMERICAL_VERIFIER",
        ),
        BACKEND / "tests" / "ml" / "test_optimization_p13_failure_delivery.py": (
            "T17",
            "A4:BE_NUMERICAL_TEST",
        ),
    }
    if resolved in explicit:
        task, scope = explicit[resolved]
        return {"path": str(resolved), "allowed": True, "task": task, "write_scope": scope}
    prefixes = [
        (REC / "contracts", "R04", "A4:REC/contracts"),
        (REC / "signoff", "R05", "A0:REC/signoff"),
        (RUN / "delivery", "T15", "A0:RUN/delivery"),
        (
            ML / "frontend" / "optimization" / "p13_failure",
            "T16",
            "A5:ML/frontend/optimization/p13_failure",
        ),
        (RUN / "viewer", "T16", "A5:RUN/viewer"),
        (RUN / "verification", "T17", "A4:RUN/verification"),
        (REC / "preflight", "R00", "A0:REC/preflight"),
        (REC / "ownership", "R01_GR0_S", "A0:REC/ownership"),
        (REC / "data_review", "R03", "A1:REC/data_review"),
        (REC / "science_review", "R02", "A2:REC/science_review"),
        (REC / "contract_crosscheck", "R05_GR1", "A2:REC/contract_crosscheck"),
        (MAIN / "docs" / "superpowers" / "plans", "R05_DOCUMENTATION", "A0:DOC"),
    ]
    for prefix, task, scope in prefixes:
        if resolved.is_relative_to(prefix):
            return {"path": str(resolved), "allowed": True, "task": task, "write_scope": scope}
    verification = REC / "verification"
    if resolved.is_relative_to(verification):
        relative = resolved.relative_to(verification)
        task = "T17" if relative.parts[0].startswith("t17_") else "A4_RECOVERY_VERIFICATION"
        return {
            "path": str(resolved),
            "allowed": True,
            "task": task,
            "write_scope": "A4:REC/verification",
        }
    return {"path": str(resolved), "allowed": False, "task": None, "write_scope": None}


def _git_status(root: Path) -> dict[str, dict[str, Any]]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all", "-z"],
        cwd=root,
        env=env,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git status failed for {root}: {result.stderr.decode(errors='replace')}"
        )
    records = {}
    for entry in result.stdout.decode().split("\0"):
        if not entry:
            continue
        status = entry[:2]
        relative = entry[3:]
        if " -> " in relative:
            relative = relative.split(" -> ", 1)[1]
        path = root / relative
        records[relative] = {"status": status, **file_record(path)}
    return records


def protection_state() -> dict[str, Any]:
    """Check O00/H01 protection and classify all post-H00 Git-visible drift."""
    o00 = read_json(RUN / "control" / "preflight.json")
    h00 = read_json(REC / "preflight" / "snapshot.json")
    h01 = read_json(REC / "ownership" / "disposition.json")
    gr0s = read_json(REC / "verification" / "gr0s_review_attempt_002.json")
    dispositions = {
        (row.get("repository"), row["path"]): row
        for row in h01["dispositions"]
        if row.get("repository")
    }
    o00_results = []
    for repository, repo in o00["repositories"].items():
        root = Path(repo["root"])
        for record in repo["dirty_files"]:
            path = root / record["path"]
            actual = file_record(path)
            disposition = dispositions.get((repository, record["path"]))
            exception = disposition is not None
            if (
                disposition
                and disposition["disposition"] == "PRESERVE_CURRENT_AS_REGISTERED_EXCEPTION"
            ):
                matches = actual["exists"] == disposition["current_exists"] and actual[
                    "sha256"
                ] == disposition["current_sha256"]
                classification = "REGISTERED_CURRENT_EXCEPTION"
            elif disposition and disposition["current_exists"] is False:
                matches = not actual["exists"]
                classification = "REGISTERED_HISTORICAL_UNKNOWN_ABSENT"
            else:
                matches = actual["exists"] == record["exists"] and actual["sha256"] == record[
                    "sha256"
                ]
                classification = "O00_PROTECTED"
            o00_results.append(
                {
                    "repository": repository,
                    "path": str(path),
                    "expected_sha256": record["sha256"],
                    **actual,
                    "classification": classification,
                    "registered_exception": exception,
                    "matches": matches,
                }
            )
    legacy_results = []
    for record in o00["legacy_files"]:
        actual = file_record(Path(record["path"]))
        matches = actual["exists"] == record["exists"] and actual["sha256"] == record["sha256"]
        legacy_results.append({**actual, "matches": matches})
    historical_results = []
    for record in gr0s["historical_differences"]:
        root = Path(h00["repositories"][record["repository"]]["root"])
        actual = file_record(root / record["path"])
        matches = actual["exists"] == record["current_exists"]
        historical_results.append(
            {**actual, "disposition": record["disposition"], "matches": matches}
        )

    drift = []
    for repository, repo in h00["repositories"].items():
        root = Path(repo["root"])
        current = _git_status(root)
        baseline = {row["path"]: row for row in repo["dirty_files"]}
        for relative in sorted(set(current) | set(baseline)):
            old = baseline.get(relative)
            new = current.get(relative)
            old_fingerprint = (old["exists"], old["sha256"]) if old else (False, None)
            new_fingerprint = (new["exists"], new["sha256"]) if new else (False, None)
            if old_fingerprint == new_fingerprint:
                continue
            path = root / relative
            classification = classify_write_path(path)
            drift.append(
                {
                    "repository": repository,
                    "path": str(path),
                    "before": old_fingerprint,
                    "after": new_fingerprint,
                    **classification,
                }
            )

    # RUN/REC delivery artifacts are Git-ignored, so Git status alone cannot audit them.
    # GR0-S registered the complete REC file list; classify every subsequent addition.
    registered_rec = {row["path"] for row in gr0s["resume_file_review"]["files"]}
    current_rec = {
        str(path.relative_to(REC)): path for path in REC.rglob("*") if path.is_file()
    }
    seen_drift = {(row["repository"], row["path"]) for row in drift}
    for relative, path in sorted(current_rec.items()):
        if relative in registered_rec or ("ML", str(path)) in seen_drift:
            continue
        drift.append(
            {
                "repository": "ML",
                "path": str(path),
                "before": (False, None),
                "after": (True, sha256(path)),
                **classify_write_path(path),
            }
        )
    for relative in sorted(registered_rec - set(current_rec)):
        path = REC / relative
        drift.append(
            {
                "repository": "ML",
                "path": str(path),
                "before": (True, "REGISTERED_IN_GR0_S"),
                "after": (False, None),
                "allowed": False,
                "task": None,
                "write_scope": None,
                "reason": "GR0_S_REGISTERED_FILE_MISSING",
            }
        )

    follow_up_roots = [
        (REC / "contracts", "R04"),
        (REC / "signoff", "R05"),
        (REC / "contract_crosscheck", "R05_GR1"),
        (RUN / "delivery", "T15"),
        (RUN / "viewer", "T16"),
        (ML / "frontend" / "optimization" / "p13_failure", "T16"),
        (RUN / "verification", "T17"),
    ]
    follow_up_paths = {
        path
        for root, _ in follow_up_roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file()
    }
    follow_up_paths.update(
        path
        for path in (REC / "verification").rglob("*")
        if path.is_file()
        and path.relative_to(REC / "verification").parts[0].startswith("t17_")
    )
    follow_up_paths.update(
        [
            BACKEND / "scripts" / "run_optimization_p13_failure_contract.py",
            BACKEND / "tests" / "ml" / "test_optimization_p13_failure_contract.py",
            BACKEND / "scripts" / "verify_optimization_p13_failure_delivery.py",
            BACKEND / "tests" / "ml" / "test_optimization_p13_failure_delivery.py",
            MAIN
            / "docs"
            / "superpowers"
            / "plans"
            / "2026-10-29-surrogate-p13-review-clarifications.md",
        ]
    )
    follow_up_inventory = []
    for path in sorted(follow_up_paths, key=str):
        if not path.exists():
            continue
        classification = classify_write_path(path)
        follow_up_inventory.append({**file_record(path), **classification})
    unexplained = [row for row in drift if not row["allowed"]]
    unexplained.extend(row for row in follow_up_inventory if not row["allowed"])
    task_counts = Counter(row["task"] for row in follow_up_inventory if row["allowed"])
    return {
        "status": "PASS"
        if all(row["matches"] for row in [*o00_results, *legacy_results, *historical_results])
        and not unexplained
        else "FAILED",
        "o00_results": o00_results,
        "legacy_results": legacy_results,
        "historical_exception_results": historical_results,
        "post_h00_drift": drift,
        "follow_up_file_inventory": follow_up_inventory,
        "unexplained_drift": unexplained,
        "classified_task_counts": dict(sorted(task_counts.items())),
        "counts": {
            "o00_protected": len(o00_results),
            "o00_mismatches": sum(not row["matches"] for row in o00_results),
            "legacy": len(legacy_results),
            "legacy_mismatches": sum(not row["matches"] for row in legacy_results),
            "historical_exceptions": len(historical_results),
            "historical_exception_mismatches": sum(
                not row["matches"] for row in historical_results
            ),
            "classified_post_h00_drift": len(drift),
            "follow_up_files_classified": len(follow_up_inventory),
            "unexplained_post_h00_drift": len(unexplained),
        },
    }


def critical_snapshot() -> dict[str, dict[str, Any]]:
    manifest = read_json(T15_MANIFEST)
    paths = {T15_MANIFEST}
    for group in ("inputs", "outputs", "temporary_outputs", "source_files"):
        paths.update(Path(row["path"]) for row in manifest[group])
    paths.update(
        [
            REC / "preflight" / "snapshot.json",
            REC / "ownership" / "disposition.json",
            REC / "ownership" / "gr0s_scope.json",
            REC / "verification" / "gr0s_review_attempt_002.json",
            REC / "verification" / "gd_o14_review.json",
            REC / "verification" / "gd_o14_review_manifest.json",
        ]
    )
    return {str(path): file_record(path) for path in sorted(paths, key=str)}


def _environment() -> dict[str, Any]:
    packages = {}
    for name in ("pytest", "ruff", "numpy", "scipy", "pandas", "scikit-learn", "torch"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    uv = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=False)
    git = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
    return {
        "cwd": str(BACKEND),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "packages": packages,
        "uv": uv.stdout.strip(),
        "git": git.stdout.strip(),
        "PYTHONDONTWRITEBYTECODE": os.environ.get("PYTHONDONTWRITEBYTECODE"),
        "numeric_threads": {
            key: os.environ.get(key)
            for key in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
        "dependency_install_or_sync_performed": False,
        "model_science_executed": False,
    }


def _run_command(
    name: str, argv: list[str], log_path: Path, exit_path: Path
) -> dict[str, Any]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_OPTIONAL_LOCKS="0")
    with log_path.open("xb") as log:
        result = subprocess.run(
            argv,
            cwd=BACKEND,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    exit_path.write_text(f"{result.returncode}\n", encoding="utf-8")
    return {
        "name": name,
        "command": shlex.join(argv),
        "argv": argv,
        "cwd": str(BACKEND),
        "exit_code": result.returncode,
        "log_path": str(log_path),
        "log_sha256": sha256(log_path),
        "exit_code_file": str(exit_path),
        "exit_code_file_sha256": sha256(exit_path),
        "environment": {"PYTHONDONTWRITEBYTECODE": "1", "GIT_OPTIONAL_LOCKS": "0"},
    }


def _pytest_counts(junit_path: Path) -> dict[str, int]:
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    keys = ("tests", "failures", "errors", "skipped")
    totals = {key: sum(int(suite.attrib.get(key, 0)) for suite in suites) for key in keys}
    totals["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    return totals


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    attempt = args.attempt_dir.resolve()
    if attempt.exists():
        parser.error(f"attempt directory must be new: {attempt}")
    if not attempt.is_relative_to(REC / "verification") or not attempt.name.startswith("attempt_"):
        parser.error("attempt directory must be a new attempt_* below REC/verification")
    logs = attempt / "logs"
    logs.mkdir(parents=True)
    rebuild = attempt / "rebuild"
    started = datetime.now(UTC).isoformat()
    critical_before = critical_snapshot()
    protection_before = protection_state()
    environment = _environment()

    ruff = _run_command(
        "ruff_owned",
        [
            "uv",
            "run",
            "--no-sync",
            "ruff",
            "check",
            "--no-cache",
            str(BACKEND / "scripts" / "verify_optimization_p13_failure_delivery.py"),
            str(BACKEND / "tests" / "ml" / "test_optimization_p13_failure_delivery.py"),
        ],
        logs / "ruff.log",
        logs / "ruff.exit_code",
    )
    junit = attempt / "pytest.junit.xml"
    pytest_command = _run_command(
        "pytest_owned",
        [
            "uv",
            "run",
            "--no-sync",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(attempt / "pytest_basetemp"),
            f"--junitxml={junit}",
            str(BACKEND / "tests" / "ml" / "test_optimization_p13_failure_delivery.py"),
        ],
        logs / "pytest.log",
        logs / "pytest.exit_code",
    )
    pytest_command["basetemp"] = str(attempt / "pytest_basetemp")
    pytest_command["cache_provider_disabled"] = True
    pytest_command["test_counts"] = _pytest_counts(junit) if junit.exists() else None
    generation = _run_command(
        "isolated_a0_generate_without_promotion",
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            str(DELIVERY / "generate.py"),
            "--output-dir",
            str(rebuild),
        ],
        logs / "generate.log",
        logs / "generate.exit_code",
    )
    generation["promotion"] = False
    generation["rebuild_path"] = str(rebuild)
    commands = [ruff, pytest_command, generation]
    verification_errors = []
    try:
        comparison = compare_deliveries(DELIVERY, rebuild)
    except Exception as error:
        verification_errors.append(f"comparison:{type(error).__name__}:{error}")
        comparison = {"status": "FAILED", "differences": [], "difference_count": None}
    try:
        sources = inspect_independent_sources(DELIVERY)
    except Exception as error:
        verification_errors.append(f"source_inspection:{type(error).__name__}:{error}")
        sources = {"status": "FAILED", "mismatches": [], "counts": {}}

    critical_after = critical_snapshot()
    protection_after = protection_state()
    critical_differences = [
        {"path": path, "before": critical_before.get(path), "after": critical_after.get(path)}
        for path in sorted(set(critical_before) | set(critical_after))
        if critical_before.get(path) != critical_after.get(path)
    ]
    o14_paths = [T15_MANIFEST, *(Path(row["path"]) for row in read_json(T15_MANIFEST)["outputs"])]
    o14_before = {str(path): critical_before[str(path)] for path in o14_paths}
    o14_after = {str(path): critical_after[str(path)] for path in o14_paths}
    o14_unchanged = o14_before == o14_after
    passed = (
        all(command["exit_code"] == 0 for command in commands)
        and comparison["status"] == "PASS"
        and sources["status"] == "PASS"
        and not critical_differences
        and o14_unchanged
        and protection_after["status"] == "PASS"
        and not verification_errors
    )
    status = "PASS" if passed else "FAILED"
    created = datetime.now(UTC).isoformat()
    counts = {
        **comparison.get("counts", {}),
        **sources.get("counts", {}),
        "candidate_rows_independently_compared": len(comparison.get("candidate_row_results", [])),
        "metric_rows_independently_compared": len(comparison.get("metric_row_results", [])),
        "difference_count": comparison.get("difference_count"),
        "critical_files_before_after": len(critical_before),
        "critical_differences": len(critical_differences),
        "o14_unchanged": o14_unchanged,
        "test_passed": (pytest_command.get("test_counts") or {}).get("passed"),
    }
    report = {
        "schema": "aln_optimization_p13_t17_numerical_delivery_v1",
        "task_id": "T17",
        "attempt_id": "attempt_001",
        "reviewer": "independent_A4",
        "status": status,
        "verification_status": status,
        "g6_n": {
            "status": status,
            "actual_result": status,
            "difference_count": comparison.get("difference_count"),
            "numeric_tolerance": {"atol": ATOL, "rtol": RTOL},
        },
        "g6_n_actual_result": status,
        "scientific_status": "REJECTED",
        "acceptance_scope": "Numerical O14 reproduction only; G6-S is not claimed.",
        "started_at": started,
        "created_at": created,
        "formal_delivery": str(DELIVERY),
        "rebuild_path": str(rebuild),
        "rebuild_method": "A0 generate.py --output-dir in a new attempt; no promotion",
        "comparison": comparison,
        "independent_source_inspection": sources,
        "differences": comparison.get("differences", []),
        "source_mismatches": sources.get("mismatches", []),
        "critical_differences": critical_differences,
        "unexplained_drift": protection_after["unexplained_drift"],
        "verification_errors": verification_errors,
        "counts": counts,
        "o14_hashes_before": o14_before,
        "o14_hashes_after": o14_after,
        "o14_unchanged": o14_unchanged,
        "critical_hashes_before": critical_before,
        "critical_hashes_after": critical_after,
        "protection_before": protection_before,
        "protection_after": protection_after,
        "commands": commands,
        "environment": environment,
        "not_executed": {
            "training": False,
            "search": False,
            "bootstrap": False,
            "promotion": False,
            "g6_s": False,
            "t18": False,
        },
        "g6_s_claimed": False,
        "t18_started": False,
        "exit_code": 0 if passed else 1,
    }
    _write_new_json(REPORT_PATH, report)
    manifest = {
        "schema": "aln_optimization_p13_t17_artifact_manifest_v1",
        "task_id": "T17",
        "attempt_id": "attempt_001",
        "status": status,
        "verification_status": status,
        "g6_n_actual_result": status,
        "scientific_status": "REJECTED",
        "created_at": created,
        "report_path": str(REPORT_PATH),
        "rebuild_path": str(rebuild),
        "inputs": read_json(T15_MANIFEST)["inputs"],
        "protection_inputs": [
            file_record(RUN / "control" / "preflight.json"),
            file_record(REC / "preflight" / "snapshot.json"),
            file_record(REC / "ownership" / "disposition.json"),
            file_record(REC / "ownership" / "gr0s_scope.json"),
            file_record(REC / "verification" / "gr0s_review_attempt_002.json"),
            file_record(REC / "verification" / "gd_o14_review.json"),
        ],
        "source_files": [
            file_record(BACKEND / "scripts" / "verify_optimization_p13_failure_delivery.py"),
            file_record(BACKEND / "tests" / "ml" / "test_optimization_p13_failure_delivery.py"),
        ],
        "outputs": [file_record(REPORT_PATH)],
        "rebuild_outputs": [
            file_record(rebuild / name)
            for name in ("candidates.csv", "metrics.csv", "decision.json")
        ],
        "command_logs": [file_record(Path(command["log_path"])) for command in commands],
        "commands": commands,
        "environment": environment,
        "counts": counts,
        "differences": comparison.get("differences", []),
        "unexplained_drift": protection_after["unexplained_drift"],
        "o14_hashes_before": o14_before,
        "o14_hashes_after": o14_after,
        "o14_unchanged": o14_unchanged,
        "g6_s_claimed": False,
        "t18_started": False,
        "exit_code": 0 if passed else 1,
    }
    _write_new_json(MANIFEST_PATH, manifest)
    print(
        json.dumps(
            {
                "task_id": "T17",
                "attempt_id": "attempt_001",
                "status": status,
                "g6_n_actual_result": status,
                "scientific_status": "REJECTED",
                "differences": comparison.get("difference_count"),
                "test_counts": pytest_command.get("test_counts"),
                "o14_unchanged": o14_unchanged,
                "report": str(REPORT_PATH),
                "manifest": str(MANIFEST_PATH),
                "rebuild_path": str(rebuild),
            },
            allow_nan=False,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
