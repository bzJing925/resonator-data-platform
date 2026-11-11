from __future__ import annotations

import csv
import importlib.util
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
RUN = BACKEND / "surrogate_data" / "optimization_p13_20261027_r01"
FORMAL = RUN / "delivery"
SCRIPT_PATH = BACKEND / "scripts" / "verify_optimization_p13_failure_delivery.py"

SPEC = importlib.util.spec_from_file_location("p13_failure_delivery_numerical", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
numerical = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(numerical)


def _copy_delivery(destination: Path) -> Path:
    target = destination / "delivery"
    shutil.copytree(FORMAL, target, ignore=shutil.ignore_patterns("t15_work", "__pycache__"))
    return target


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_real_delivery_matches_independent_sources() -> None:
    result = numerical.inspect_independent_sources(FORMAL)

    assert result["mismatches"] == []
    assert result["counts"]["candidate_rows"] == 20
    assert result["counts"]["metric_rows"] == 160
    assert result["counts"]["valued_metric_rows"] == 82
    assert result["counts"]["null_metric_rows"] == 78
    assert result["counts"]["source_chain_full_precision_cells"] == 1920
    assert result["counts"]["registered_input_hashes"] == 25
    assert result["counts"]["registered_c19_same_structure_values"] == 2
    assert [row["value"] for row in result["c19_same_structure_values"]] == [
        "942.3123232596486",
        "926.111784408804",
    ]


def test_real_delivery_self_comparison_counts() -> None:
    result = numerical.compare_deliveries(FORMAL, FORMAL)

    assert result["differences"] == []
    assert result["byte_identical"] is True
    assert result["counts"] == {
        "candidate_rows": 20,
        "metric_rows": 160,
        "nonnegative_control_rows": 17,
        "negative_control_rows": 3,
        "valued_metric_rows": 82,
        "null_metric_rows": 78,
        "recommendation_count": 0,
        "grade_A": 0,
        "grade_B": 0,
        "grade_C": 0,
    }


def test_metric_values_use_both_numeric_tolerances(tmp_path: Path) -> None:
    rebuilt = _copy_delivery(tmp_path)
    path = rebuilt / "metrics.csv"
    fields, rows = _read_csv(path)
    original = Decimal(rows[0]["value"])

    rows[0]["value"] = str(original + Decimal("0.0000000000005"))
    _write_csv(path, fields, rows)
    close = numerical.compare_deliveries(FORMAL, rebuilt)
    assert close["differences"] == []
    assert close["numeric_within_tolerance_count"] == 1

    rows[0]["value"] = str(original + Decimal("0.0000000001"))
    _write_csv(path, fields, rows)
    different = numerical.compare_deliveries(FORMAL, rebuilt)
    assert len(different["differences"]) == 1
    assert different["differences"][0]["field"] == "value"
    assert different["differences"][0]["kind"] == "numeric"


def test_candidate_geometry_uses_numeric_tolerance(tmp_path: Path) -> None:
    rebuilt = _copy_delivery(tmp_path)
    path = rebuilt / "candidates.csv"
    fields, rows = _read_csv(path)
    original = Decimal(rows[0]["eg_um"])

    rows[0]["eg_um"] = str(original + Decimal("0.0000000000005"))
    _write_csv(path, fields, rows)
    assert numerical.compare_deliveries(FORMAL, rebuilt)["differences"] == []

    rows[0]["eg_um"] = str(original + Decimal("0.000000000002"))
    _write_csv(path, fields, rows)
    result = numerical.compare_deliveries(FORMAL, rebuilt)
    assert len(result["differences"]) == 1
    assert result["differences"][0]["field"] == "eg_um"


@pytest.mark.parametrize(
    ("row_index", "field", "replacement"),
    [
        (0, "source_sha256", "0" * 64),
        (1, "null_reason", "different_reason"),
        (0, "candidate_id", "renamed_candidate"),
    ],
)
def test_metric_identity_null_reason_and_source_are_exact(
    tmp_path: Path, row_index: int, field: str, replacement: str
) -> None:
    rebuilt = _copy_delivery(tmp_path)
    path = rebuilt / "metrics.csv"
    fields, rows = _read_csv(path)
    rows[row_index][field] = replacement
    _write_csv(path, fields, rows)

    result = numerical.compare_deliveries(FORMAL, rebuilt)

    assert any(item["field"] == field for item in result["differences"])


def test_candidate_order_and_decision_grade_are_exact(tmp_path: Path) -> None:
    rebuilt = _copy_delivery(tmp_path)
    path = rebuilt / "candidates.csv"
    fields, rows = _read_csv(path)
    rows[0], rows[1] = rows[1], rows[0]
    _write_csv(path, fields, rows)
    decision_path = rebuilt / "decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["grade"] = "A"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")

    result = numerical.compare_deliveries(FORMAL, rebuilt)
    locations = {(item["artifact"], item["field"]) for item in result["differences"]}

    assert ("candidates.csv", "candidate_id") in locations
    assert ("decision.json", "grade") in locations


def test_registered_write_scope_classification() -> None:
    owned = numerical.classify_write_path(SCRIPT_PATH)
    forbidden = numerical.classify_write_path(RUN / "delivery" / "generate.py")
    unknown = numerical.classify_write_path(BACKEND / "app" / "ml" / "unregistered.py")

    assert owned["allowed"] is True
    assert owned["task"] == "T17"
    assert forbidden["allowed"] is True
    assert forbidden["task"] == "T15"
    assert unknown["allowed"] is False
