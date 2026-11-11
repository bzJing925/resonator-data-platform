"""Independently verify B02 all-device median datasets from direct source rows.

This A4 verifier intentionally imports no A1 aggregation or CLI code. Expected
values are recomputed with local sorting and middle-element logic.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

FL0_FLAG = "FL0_QP_REVIEW"
CORE_PARAMETERS = frozenset({"k2eff_pct", "qp", "qs", "bodeq_fitted"})
MISSING_TOKENS = frozenset({"", "nan", "none", "null", "na", "n/a", "#n/a"})
TARGET_FIELDS = [
    "structure_key",
    "mode",
    "parameter",
    "p3_structure_id",
    "p3_branch_id",
    "p3_design_group_id",
    "eg_um",
    "fl_um",
    "area_um2",
    "has_pf",
    "ag_status",
    "cohort",
    "topology_status",
    "median",
    "q25",
    "q75",
    "min",
    "max",
    "n_total",
    "n_devices_total",
    "n_valid",
    "n_missing",
    "n_invalid",
    "source_device_keys",
    "low_support",
]
LEDGER_FIELDS = [
    "device_key",
    "structure_key",
    "source_row_id",
    "canonical_source_row_id",
    "mode",
    "port",
    "coord",
    "filename_has_fail",
    "parameter",
    "value",
    "valid",
    "reason",
    "audit_flag",
    "audit_reasons",
    "n_measurements",
    "device_value",
    "device_valid",
    "device_reason",
    "sensitivity_value",
    "sensitivity_valid",
    "sensitivity_reason",
    "sensitivity_device_value",
    "sensitivity_device_valid",
    "sensitivity_device_reason",
]
DIFFERENCE_FIELDS = [
    "comparison",
    "structure_key",
    "p3_structure_id",
    "mode",
    "parameter",
    "reference_value",
    "candidate_value",
    "delta",
    "abs_delta",
    "changed",
    "reference_n_valid",
    "candidate_n_valid",
    "reference_n_coords",
]
EXCLUSION_FIELDS = [
    "source_row_id",
    "device_key",
    "structure_key",
    "mode",
    "reasons",
    "alias_of_source_row_id",
]
METADATA_FIELDS = [
    "p3_structure_id",
    "p3_branch_id",
    "p3_design_group_id",
    "eg_um",
    "fl_um",
    "area_um2",
    "has_pf",
    "ag_status",
    "cohort",
    "topology_status",
]
PLAN_PATH = Path(
    "/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/"
    "2026-11-02-surrogate-executable-recovery-and-median-plan.md"
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path, identifier: str | None = None) -> dict[str, Any]:
    result = {
        "path": str(path.resolve()),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "sha256": file_sha256(path) if path.is_file() else None,
    }
    if identifier is not None:
        result["id"] = identifier
    return result


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"true", "1", "y", "yes"}:
        return True
    if text in {"false", "0", "n", "no", ""}:
        return False
    raise ValueError(f"Not a boolean: {value!r}")


def optional_float(value: Any) -> float | None:
    if value is None or str(value).strip().casefold() in MISSING_TOKENS:
        return None
    return float(value)


def source_sha256(row: dict[str, str]) -> str:
    payload = json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Checks:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def _item(self, name: str) -> dict[str, Any]:
        return self.items.setdefault(
            name,
            {
                "name": name,
                "status": "PASS",
                "mismatch_count": 0,
                "examples": [],
                "details": {},
            },
        )

    def detail(self, name: str, **details: Any) -> None:
        self._item(name)["details"].update(details)

    def require(self, name: str, condition: bool, message: str) -> None:
        if condition:
            self._item(name)
            return
        item = self._item(name)
        item["status"] = "FAILED"
        item["mismatch_count"] += 1
        if len(item["examples"]) < 30:
            item["examples"].append(message)

    def external(self, name: str, passed: bool, **details: Any) -> None:
        item = self._item(name)
        item["details"].update(details)
        if not passed:
            item["status"] = "FAILED"
            item["mismatch_count"] += 1

    @property
    def passed(self) -> bool:
        return all(item["status"] == "PASS" for item in self.items.values())

    def findings(self) -> list[dict[str, Any]]:
        return [
            {
                "check": name,
                "mismatch_count": item["mismatch_count"],
                "examples": item["examples"],
            }
            for name, item in self.items.items()
            if item["status"] != "PASS"
        ]


class DeviceSummary(NamedTuple):
    value: float | None
    valid: bool
    reason: str
    missing: int
    invalid: int


def own_median(values: list[float]) -> float | None:
    """Sort locally and select the middle element(s); no A1 helper is used."""
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2)


def own_percentile(values: list[float], probability: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def classify_source(record: dict[str, str], parameter: str) -> dict[str, Any]:
    text = str(record.get(parameter, "")).strip()
    if text.casefold() in MISSING_TOKENS:
        return {"value": None, "valid": False, "reason": "missing"}
    try:
        value = float(text)
    except ValueError:
        return {"value": None, "valid": False, "reason": "invalid_non_numeric"}
    reasons = []
    if not math.isfinite(value):
        reasons.append("non_finite")
    if not parse_bool(record.get("frequency_valid")):
        reasons.append("frequency_invalid")
    if value <= 0:
        reasons.append("non_positive")
    if parameter == "k2eff_pct":
        if value >= 100:
            reasons.append("k2_out_of_range")
        try:
            delta = float(record.get("k2_formula_delta_pct", "nan"))
        except (TypeError, ValueError):
            delta = math.nan
        if not math.isfinite(delta) or abs(delta) > 0.01:
            reasons.append("k2_formula_mismatch")
    return {"value": value, "valid": not reasons, "reason": ";".join(reasons)}


def reduce_device(
    measurements: list[dict[str, Any]], parameter: str, sensitivity: bool
) -> DeviceSummary:
    values = []
    missing = 0
    invalid = 0
    for measurement in measurements:
        masked = (
            sensitivity
            and parameter == "qp"
            and measurement["audit_flag"] == FL0_FLAG
        )
        if masked:
            missing += 1
            continue
        validity = measurement["validity"][parameter]
        if validity["valid"]:
            value = measurement["values"][parameter]
            if value is None:
                raise ValueError("Valid source measurement has null value")
            values.append(float(value))
        elif validity["reason"] == "missing":
            missing += 1
        else:
            invalid += 1
    value = own_median(values)
    if value is not None:
        return DeviceSummary(value, True, "", 0, 0)
    return DeviceSummary(
        None,
        False,
        f"no_valid_repeat_values;missing={missing};invalid={invalid}",
        int(invalid == 0),
        int(invalid > 0),
    )


def summarize_devices(
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    members: list[tuple[str, str, str, str]],
    parameter: str,
    sensitivity: bool,
    low_support_threshold: int,
) -> dict[str, Any]:
    summaries = [reduce_device(groups[key], parameter, sensitivity) for key in members]
    values = sorted(float(item.value) for item in summaries if item.valid)
    n_valid = len(values)
    n_missing = sum(item.missing for item in summaries)
    n_invalid = sum(item.invalid for item in summaries)
    return {
        "median": own_median(values),
        "q25": own_percentile(values, 0.25),
        "q75": own_percentile(values, 0.75),
        "min": float(values[0]) if values else None,
        "max": float(values[-1]) if values else None,
        "n_total": len(members),
        "n_devices_total": len(members),
        "n_valid": n_valid,
        "n_missing": n_missing,
        "n_invalid": n_invalid,
        "source_device_keys": sorted(
            key[3] for key, item in zip(members, summaries, strict=True) if item.valid
        ),
        "low_support": n_valid < low_support_threshold,
    }


def iter_identity(path: Path):
    """Yield the header, then one identity record at a time from the large JSON."""
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as handle:
        buffer = ""
        marker = None
        while marker is None:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                raise ValueError("B01 identity has no records array")
            buffer += chunk
            marker = re.search(r'"records"\s*:\s*\[', buffer)
        yield json.loads(buffer[: marker.start()] + '"records":[]}')
        buffer = buffer[marker.end() :]
        while True:
            buffer = buffer.lstrip()
            if buffer.startswith(","):
                buffer = buffer[1:].lstrip()
            if buffer.startswith("]"):
                return
            while True:
                try:
                    value, end = decoder.raw_decode(buffer)
                    break
                except json.JSONDecodeError:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        raise ValueError("Truncated B01 identity records array") from None
                    buffer += chunk
            yield value
            buffer = buffer[end:]


def scope_reasons(identity: dict[str, Any]) -> list[str]:
    reasons = []
    if not identity["is_canonical_measurement"]:
        reasons.append("exact_duplicate_import")
    if identity["mode"] != "deembedded":
        reasons.append("raw_control_only")
    cohort = identity["structure_association"]["cohort"]
    if not cohort["geometry_eligible"]:
        reasons.append("geometry_not_eligible")
        reasons.extend(cohort.get("exclusion_reasons", []))
    return sorted(set(reasons))


def metadata_from_identity(identity: dict[str, Any]) -> dict[str, Any]:
    association = identity["structure_association"]
    geometry = association["geometry"]
    return {
        "p3_structure_id": association.get("p3_structure_id"),
        "p3_branch_id": association.get("p3_branch_id"),
        "p3_design_group_id": association.get("p3_design_group_id"),
        "eg_um": geometry.get("eg_um"),
        "fl_um": geometry.get("fl_um"),
        "area_um2": geometry.get("area_um2"),
        "has_pf": geometry.get("has_pf"),
        "ag_status": association["ag"]["status"],
        "cohort": association["cohort"]["id"],
        "topology_status": association["topology"]["status"],
    }


def recompute_source(
    records_path: Path,
    identity_path: Path,
    audit_path: Path,
    parameters: list[str],
    checks: Checks,
) -> dict[str, Any]:
    name = "source_identity_exact_import_repeat_and_scope"
    records: dict[str, dict[str, str]] = {}
    source_row_count = 0
    with gzip.open(records_path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            source_row_count += 1
            row_id = row["row_id"]
            checks.require(name, row_id not in records, f"duplicate source row_id: {row_id}")
            records.setdefault(row_id, row)

    review: dict[str, dict[str, str]] = {}
    decisions = Counter()
    audit_row_count = 0
    audit_identity_fields = (
        "source_path",
        "source_sheet",
        "excel_row",
        "mode",
        "original_filename",
        "device_id",
        "port",
    )
    with audit_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            audit_row_count += 1
            decisions[row.get("decision", "")] += 1
            if row.get("decision") != "review":
                continue
            row_id = row["row_id"]
            checks.require(name, row_id not in review, f"duplicate FL0 review: {row_id}")
            review[row_id] = row
            source = records.get(row_id)
            checks.require(name, source is not None, f"FL0 source missing: {row_id}")
            if source is not None:
                mismatched = [
                    field
                    for field in audit_identity_fields
                    if row.get(field, "") != source.get(field, "")
                ]
                checks.require(name, not mismatched, f"FL0 identity mismatch: {row_id}")

    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    measurements_by_row: dict[str, dict[str, Any]] = {}
    exclusions: dict[str, dict[str, Any]] = {}
    structure_metadata: dict[tuple[str, str, str], dict[str, Any]] = {}
    identity_ids = set()
    identity_count = 0
    identity_stream = iter_identity(identity_path)
    identity_header = next(identity_stream)
    for identity in identity_stream:
        identity_count += 1
        row_id = identity["source"]["row_id"]
        checks.require(name, row_id not in identity_ids, f"duplicate identity: {row_id}")
        identity_ids.add(row_id)
        source = records.get(row_id)
        checks.require(name, source is not None, f"identity without source: {row_id}")
        if source is None:
            continue
        checks.require(
            name,
            identity["source"].get("source_row_sha256") == source_sha256(source),
            f"identity source hash mismatch: {row_id}",
        )
        checks.require(
            name,
            identity["mode"] == source["mode"],
            f"raw/de identity/source mismatch: {row_id}",
        )
        flagged = row_id in review
        checks.require(
            name,
            bool(identity.get("fl0_review", {}).get("flagged", False)) == flagged,
            f"FL0 identity/audit flag mismatch: {row_id}",
        )
        reasons = scope_reasons(identity)
        if reasons:
            checks.require(
                name,
                not flagged,
                f"FL0 review row excluded from primary: {row_id}",
            )
            exclusions[row_id] = {
                "source_row_id": row_id,
                "device_key": identity["device_key"],
                "structure_key": identity["structure_key"],
                "mode": identity["mode"],
                "reasons": reasons,
                "alias_of_source_row_id": identity.get("alias_of_source_row_id"),
            }
            continue

        classified = {parameter: classify_source(source, parameter) for parameter in parameters}
        for parameter in parameters:
            if parameter in CORE_PARAMETERS:
                checks.require(
                    name,
                    parse_bool(source["valid_" + parameter])
                    == classified[parameter]["valid"],
                    f"P3 validity mismatch: {row_id} {parameter}",
                )
        checks.require(
            name,
            identity.get("canonical_source_row_id", row_id) == row_id,
            f"canonical primary has alias canonical id: {row_id}",
        )
        review_row = review.get(row_id)
        measurement = {
            "device_key": identity["device_key"],
            "structure_key": identity["structure_key"],
            "source_row_id": row_id,
            "canonical_source_row_id": identity.get("canonical_source_row_id", row_id),
            "mode": identity["mode"],
            "batch": identity.get("batch", ""),
            "port": identity.get("port", source.get("port", "")),
            "coord": source.get("coord", ""),
            "filename_has_fail": source.get("filename_has_fail", ""),
            "audit_flag": FL0_FLAG if flagged else "",
            "audit_reasons": review_row.get("reasons", "") if review_row else "",
            "values": {key: value["value"] for key, value in classified.items()},
            "validity": {
                key: {"valid": value["valid"], "reason": value["reason"]}
                for key, value in classified.items()
            },
            **metadata_from_identity(identity),
        }
        group_key = (
            measurement["batch"],
            measurement["mode"],
            measurement["structure_key"],
            measurement["device_key"],
        )
        measurement["group_key"] = group_key
        groups[group_key].append(measurement)
        measurements_by_row[row_id] = measurement
        structure_key = group_key[:3]
        metadata = metadata_from_identity(identity)
        if structure_key in structure_metadata:
            checks.require(
                name,
                structure_metadata[structure_key] == metadata,
                f"structure metadata changed within cohort: {structure_key}",
            )
        else:
            structure_metadata[structure_key] = metadata

    for row_id in sorted(set(records) - identity_ids):
        checks.require(name, False, f"source missing from identity: {row_id}")
    checks.require(
        name,
        identity_header.get("record_count") == identity_count,
        "B01 record_count does not match streamed records",
    )
    checks.require(
        name,
        identity_header.get("identity_status") == "RESOLVED",
        "B01 identity is not RESOLVED",
    )
    unresolved = identity_header.get(
        "unresolved_identity_conflicts",
        identity_header.get("counts", {}).get("unresolved_identity_conflicts"),
    )
    checks.require(name, unresolved == 0, f"B01 unresolved identities: {unresolved}")

    structure_devices: dict[tuple[str, str, str], list[tuple[str, str, str, str]]] = (
        defaultdict(list)
    )
    for group_key, rows in groups.items():
        rows.sort(key=lambda row: row["source_row_id"])
        structure_devices[group_key[:3]].append(group_key)
        checks.require(
            name,
            len({row["port"] for row in rows}) == 1,
            f"repeat group crosses ports: {group_key}",
        )
    for members in structure_devices.values():
        members.sort()

    repeat_groups = sum(len(rows) > 1 for rows in groups.values())
    alias_rows = sum(
        "exact_duplicate_import" in row["reasons"] for row in exclusions.values()
    )
    source_zero_values = sum(
        value == 0
        for measurement in measurements_by_row.values()
        for value in measurement["values"].values()
    )
    source_valid_zero_values = sum(
        measurement["values"][parameter] == 0 and measurement["validity"][parameter]["valid"]
        for measurement in measurements_by_row.values()
        for parameter in parameters
    )
    checks.detail(
        name,
        source_rows=source_row_count,
        unique_source_rows=len(records),
        identity_rows=identity_count,
        primary_measurements=len(measurements_by_row),
        primary_device_groups=len(groups),
        primary_structures=len(structure_devices),
        repeat_groups=repeat_groups,
        exact_import_alias_rows=alias_rows,
        exclusions=len(exclusions),
        audit_rows=audit_row_count,
        audit_decision_counts=dict(sorted(decisions.items())),
        review_rows=len(review),
        retained_review_rows=sum(
            row["audit_flag"] == FL0_FLAG for row in measurements_by_row.values()
        ),
        source_zero_values=source_zero_values,
        source_valid_zero_values=source_valid_zero_values,
        raw_rows_excluded=sum(row["mode"] == "raw" for row in exclusions.values()),
        deembedded_rows_retained=sum(
            row["mode"] == "deembedded" for row in measurements_by_row.values()
        ),
    )
    return {
        "identity_header": identity_header,
        "identity_count": identity_count,
        "source_row_count": source_row_count,
        "review": review,
        "groups": groups,
        "structure_devices": structure_devices,
        "structure_metadata": structure_metadata,
        "measurements_by_row": measurements_by_row,
        "exclusions": exclusions,
        "alias_rows": alias_rows,
        "source_zero_values": source_zero_values,
        "source_valid_zero_values": source_valid_zero_values,
    }


def build_expected_targets(
    source: dict[str, Any], parameters: list[str], low_support_threshold: int
) -> dict[str, dict[tuple[str, str, str], dict[str, Any]]]:
    result: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {
        "primary": {},
        "sensitivity": {},
    }
    for structure_key in sorted(source["structure_devices"]):
        batch, mode, structure = structure_key
        metadata = source["structure_metadata"][structure_key]
        members = source["structure_devices"][structure_key]
        for parameter in parameters:
            for view in ("primary", "sensitivity"):
                key = (mode, structure, parameter)
                if key in result[view]:
                    raise ValueError(f"Target key is not unique across batches: {key}")
                result[view][key] = {
                    "structure_key": structure,
                    "mode": mode,
                    "parameter": parameter,
                    **metadata,
                    **summarize_devices(
                        source["groups"],
                        members,
                        parameter,
                        view == "sensitivity",
                        low_support_threshold,
                    ),
                }
    return result


def compare_csv_value(actual: str, expected: Any) -> tuple[bool, float | None]:
    if expected is None:
        return actual == "", None
    if isinstance(expected, bool):
        return actual == str(expected), None
    if isinstance(expected, int):
        try:
            return int(actual) == expected, None
        except ValueError:
            return False, None
    if isinstance(expected, float):
        try:
            observed = float(actual)
        except ValueError:
            return False, None
        if not math.isfinite(observed):
            return False, None
        return (
            math.isclose(observed, expected, rel_tol=1e-12, abs_tol=1e-12),
            abs(observed - expected),
        )
    if isinstance(expected, list):
        try:
            return json.loads(actual) == expected, None
        except json.JSONDecodeError:
            return False, None
    return actual == str(expected), None


def compare_row(
    checks: Checks,
    check_name: str,
    context: str,
    actual: dict[str, str],
    expected: dict[str, Any],
    fields: list[str],
) -> dict[str, Any]:
    counters = {"field_comparisons": 0, "numeric_comparisons": 0, "max_abs_delta": 0.0}
    for field in fields:
        matches, delta = compare_csv_value(actual.get(field, ""), expected.get(field))
        counters["field_comparisons"] += 1
        if delta is not None:
            counters["numeric_comparisons"] += 1
            counters["max_abs_delta"] = max(counters["max_abs_delta"], delta)
        checks.require(
            check_name,
            matches,
            f"{context} field={field} actual={actual.get(field)!r} "
            f"expected={expected.get(field)!r}",
        )
    return counters


def merge_counters(total: dict[str, Any], new: dict[str, Any]) -> None:
    total["field_comparisons"] += new["field_comparisons"]
    total["numeric_comparisons"] += new["numeric_comparisons"]
    total["max_abs_delta"] = max(total["max_abs_delta"], new["max_abs_delta"])


def target_summary(
    expected: dict[tuple[str, str, str], dict[str, Any]], parameters: list[str]
) -> dict[str, Any]:
    result = {}
    for parameter in parameters:
        rows = [row for row in expected.values() if row["parameter"] == parameter]
        result[parameter] = {
            "structures": len(rows),
            "n_total": sum(row["n_total"] for row in rows),
            "n_valid": sum(row["n_valid"] for row in rows),
            "n_missing": sum(row["n_missing"] for row in rows),
            "n_invalid": sum(row["n_invalid"] for row in rows),
            "null_statistic_structures": sum(row["median"] is None for row in rows),
            "low_support_structures": sum(row["low_support"] for row in rows),
            "valid_member_device_keys": sum(len(row["source_device_keys"]) for row in rows),
        }
    return result


def verify_targets(
    data: Path,
    expected: dict[str, dict[tuple[str, str, str], dict[str, Any]]],
    parameters: list[str],
    checks: Checks,
) -> dict[str, Any]:
    report = {"views": {}}
    for view in ("primary", "sensitivity"):
        name = f"{view}_all_structure_parameter_statistics_and_members"
        path = data / f"structure_targets_{view}.csv"
        seen = set()
        counters = {"field_comparisons": 0, "numeric_comparisons": 0, "max_abs_delta": 0.0}
        rows_read = 0
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            checks.require(name, reader.fieldnames == TARGET_FIELDS, "target header mismatch")
            for actual in reader:
                rows_read += 1
                key = (actual["mode"], actual["structure_key"], actual["parameter"])
                checks.require(name, key not in seen, f"duplicate target key: {key}")
                seen.add(key)
                wanted = expected[view].get(key)
                if wanted is None:
                    checks.require(name, False, f"unexpected target key: {key}")
                    continue
                merge_counters(
                    counters,
                    compare_row(checks, name, str(key), actual, wanted, TARGET_FIELDS),
                )
                checks.require(
                    name,
                    int(actual["n_total"])
                    == int(actual["n_valid"])
                    + int(actual["n_missing"])
                    + int(actual["n_invalid"]),
                    f"n_total does not reconcile: {key}",
                )
                checks.require(
                    name,
                    int(actual["n_total"]) == int(actual["n_devices_total"]),
                    f"n_total != n_devices_total: {key}",
                )
        missing = sorted(set(expected[view]) - seen)
        checks.require(name, not missing, f"missing target keys: {missing[:10]}")
        checks.detail(
            name,
            rows=rows_read,
            structures=len({key[1] for key in seen}),
            parameters=len({key[2] for key in seen}),
            expected_rows=len(expected[view]),
            member_device_keys=sum(
                len(row["source_device_keys"]) for row in expected[view].values()
            ),
            **counters,
        )
        report["views"][view] = {
            "rows": rows_read,
            "parameters": target_summary(expected[view], parameters),
            **counters,
        }
    return report


def verify_ledger(
    data: Path,
    source: dict[str, Any],
    parameters: list[str],
    checks: Checks,
) -> dict[str, Any]:
    name = "device_ledger_every_row_field_and_per_parameter_validity"
    measurements = source["measurements_by_row"]
    groups = source["groups"]
    seen = set()
    rows_read = 0
    flagged_rows = 0
    masked_rows = 0
    counters = {"field_comparisons": 0, "numeric_comparisons": 0, "max_abs_delta": 0.0}
    with (data / "device_ledger.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        checks.require(name, reader.fieldnames == LEDGER_FIELDS, "ledger header mismatch")
        for actual in reader:
            rows_read += 1
            row_id = actual["source_row_id"]
            parameter = actual["parameter"]
            key = (row_id, parameter)
            checks.require(name, key not in seen, f"duplicate ledger key: {key}")
            seen.add(key)
            measurement = measurements.get(row_id)
            if measurement is None or parameter not in parameters:
                checks.require(name, False, f"unexpected ledger key: {key}")
                continue
            group = groups[measurement["group_key"]]
            primary = reduce_device(group, parameter, False)
            sensitivity = reduce_device(group, parameter, True)
            masked = parameter == "qp" and measurement["audit_flag"] == FL0_FLAG
            validity = measurement["validity"][parameter]
            value = measurement["values"][parameter]
            expected = {
                "device_key": measurement["device_key"],
                "structure_key": measurement["structure_key"],
                "source_row_id": row_id,
                "canonical_source_row_id": measurement["canonical_source_row_id"],
                "mode": measurement["mode"],
                "port": measurement["port"],
                "coord": measurement["coord"],
                "filename_has_fail": measurement["filename_has_fail"],
                "parameter": parameter,
                "value": value,
                "valid": validity["valid"],
                "reason": validity["reason"],
                "audit_flag": measurement["audit_flag"],
                "audit_reasons": measurement["audit_reasons"],
                "n_measurements": len(group),
                "device_value": primary.value,
                "device_valid": primary.valid,
                "device_reason": primary.reason,
                "sensitivity_value": None if masked else value,
                "sensitivity_valid": False if masked else validity["valid"],
                "sensitivity_reason": (
                    "fl0_qp_review_masked" if masked else validity["reason"]
                ),
                "sensitivity_device_value": sensitivity.value,
                "sensitivity_device_valid": sensitivity.valid,
                "sensitivity_device_reason": sensitivity.reason,
            }
            merge_counters(
                counters, compare_row(checks, name, str(key), actual, expected, LEDGER_FIELDS)
            )
            flagged_rows += actual["audit_flag"] == FL0_FLAG
            masked_rows += actual["sensitivity_reason"] == "fl0_qp_review_masked"
    missing_count = 0
    missing_examples = []
    for row_id in measurements:
        for parameter in parameters:
            key = (row_id, parameter)
            if key not in seen:
                missing_count += 1
                if len(missing_examples) < 10:
                    missing_examples.append(key)
    checks.require(name, missing_count == 0, f"missing ledger keys: {missing_examples}")
    checks.detail(
        name,
        rows=rows_read,
        unique_source_parameter_keys=len(seen),
        expected_rows=len(measurements) * len(parameters),
        missing_rows=missing_count,
        flagged_rows=flagged_rows,
        masked_qp_rows=masked_rows,
        **counters,
    )
    return {
        "rows": rows_read,
        "expected_rows": len(measurements) * len(parameters),
        "flagged_rows": flagged_rows,
        "masked_qp_rows": masked_rows,
        **counters,
    }


def verify_exclusions(
    data: Path, source: dict[str, Any], unresolved: int, checks: Checks
) -> dict[str, Any]:
    name = "exclusions_raw_de_aliases_and_identity_conflicts"
    expected = source["exclusions"]
    seen = set()
    mode_counts = Counter()
    reason_counts = Counter()
    counters = {"field_comparisons": 0, "numeric_comparisons": 0, "max_abs_delta": 0.0}
    with (data / "exclusions.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        checks.require(name, reader.fieldnames == EXCLUSION_FIELDS, "exclusion header mismatch")
        for actual in reader:
            row_id = actual["source_row_id"]
            checks.require(name, row_id not in seen, f"duplicate exclusion: {row_id}")
            seen.add(row_id)
            wanted = expected.get(row_id)
            if wanted is None:
                checks.require(name, False, f"unexpected exclusion: {row_id}")
                continue
            merge_counters(
                counters,
                compare_row(checks, name, row_id, actual, wanted, EXCLUSION_FIELDS),
            )
            mode_counts[actual["mode"]] += 1
            for reason in json.loads(actual["reasons"]):
                reason_counts[reason] += 1
    missing = sorted(set(expected) - seen)
    checks.require(name, not missing, f"missing exclusions: {missing[:10]}")
    conflict_payload = json.loads((data / "identity_conflicts.json").read_text(encoding="utf-8"))
    checks.require(
        name,
        conflict_payload.get("schema") == "aln_device_median_b02_identity_conflicts_v1"
        and conflict_payload.get("status") == "PASS"
        and conflict_payload.get("upstream_unresolved_count") == unresolved
        and conflict_payload.get("count") == 0
        and conflict_payload.get("conflicts") == [],
        "identity_conflicts.json is not an empty resolved conflict report",
    )
    checks.detail(
        name,
        rows=len(seen),
        expected_rows=len(expected),
        exact_import_aliases=source["alias_rows"],
        mode_counts=dict(sorted(mode_counts.items())),
        reason_counts=dict(sorted(reason_counts.items())),
        independent_identity_conflicts=0,
        **counters,
    )
    return {
        "rows": len(seen),
        "mode_counts": dict(sorted(mode_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
    }


def difference_row(
    comparison: str,
    candidate: dict[str, Any],
    reference_value: float | None,
    reference_n_valid: Any,
    reference_n_coords: Any = None,
) -> dict[str, Any]:
    candidate_value = candidate["median"]
    if reference_value is None or candidate_value is None:
        changed = reference_value is not None or candidate_value is not None
        delta = None
    else:
        changed = not math.isclose(
            reference_value, candidate_value, rel_tol=1e-12, abs_tol=1e-12
        )
        delta = candidate_value - reference_value
    return {
        "comparison": comparison,
        "structure_key": candidate["structure_key"],
        "p3_structure_id": candidate.get("p3_structure_id"),
        "mode": candidate["mode"],
        "parameter": candidate["parameter"],
        "reference_value": reference_value,
        "candidate_value": candidate_value,
        "delta": delta,
        "abs_delta": abs(delta) if delta is not None else None,
        "changed": changed,
        "reference_n_valid": reference_n_valid,
        "candidate_n_valid": candidate["n_valid"],
        "reference_n_coords": reference_n_coords,
    }


def difference_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["comparison"], row["parameter"])].append(row)
    result = {}
    for (comparison, parameter), selected in sorted(grouped.items()):
        result.setdefault(comparison, {})[parameter] = {
            "compared": len(selected),
            "changed": sum(row["changed"] for row in selected),
            "reference_valid": sum(row["reference_value"] is not None for row in selected),
            "candidate_valid": sum(row["candidate_value"] is not None for row in selected),
        }
    return result


def verify_differences(
    data: Path,
    old_path: Path,
    expected: dict[str, dict[tuple[str, str, str], dict[str, Any]]],
    manifest: dict[str, Any],
    owner_receipt: dict[str, Any],
    checks: Checks,
) -> dict[str, Any]:
    """Read old coordinate labels only now, to create the comparison report."""
    name = "old_coordinate_report_and_recomputed_sensitivity_differences"
    with old_path.open(encoding="utf-8", newline="") as handle:
        old_rows = list(csv.DictReader(handle))
    old_by_id = {row["structure_id"]: row for row in old_rows}
    checks.require(name, len(old_by_id) == len(old_rows), "duplicate old structure_id")
    expected_rows = []
    for key, candidate in expected["primary"].items():
        old = old_by_id.get(str(candidate.get("p3_structure_id")))
        parameter = key[2]
        expected_rows.append(
            difference_row(
                "primary_vs_old_coordinate",
                candidate,
                optional_float(old.get(parameter)) if old else None,
                old.get(parameter + "_n") if old else None,
                old.get(parameter + "_n_coords") if old else None,
            )
        )
    for key, candidate in expected["sensitivity"].items():
        reference = expected["primary"][key]
        expected_rows.append(
            difference_row(
                "sensitivity_vs_primary",
                candidate,
                reference["median"],
                reference["n_valid"],
            )
        )
    expected_by_key = {
        (
            row["comparison"],
            row["mode"],
            row["structure_key"],
            row["parameter"],
        ): row
        for row in expected_rows
    }
    seen = set()
    counters = {"field_comparisons": 0, "numeric_comparisons": 0, "max_abs_delta": 0.0}
    with (data / "aggregation_differences.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        checks.require(name, reader.fieldnames == DIFFERENCE_FIELDS, "difference header mismatch")
        for actual in reader:
            key = (
                actual["comparison"],
                actual["mode"],
                actual["structure_key"],
                actual["parameter"],
            )
            checks.require(name, key not in seen, f"duplicate difference: {key}")
            seen.add(key)
            wanted = expected_by_key.get(key)
            if wanted is None:
                checks.require(name, False, f"unexpected difference: {key}")
                continue
            merge_counters(
                counters,
                compare_row(checks, name, str(key), actual, wanted, DIFFERENCE_FIELDS),
            )
    missing = sorted(set(expected_by_key) - seen)
    checks.require(name, not missing, f"missing differences: {missing[:10]}")
    summary = difference_summary(expected_rows)
    checks.require(
        name,
        manifest.get("difference_summary") == summary,
        "manifest difference summary differs from independent recomputation",
    )
    checks.require(
        name,
        owner_receipt.get("difference_summary") == summary,
        "owner receipt difference summary differs from independent recomputation",
    )
    old_changed = [
        row
        for row in expected_rows
        if row["comparison"] == "primary_vs_old_coordinate" and row["changed"]
    ]
    sensitivity_changed = [
        row
        for row in expected_rows
        if row["comparison"] == "sensitivity_vs_primary" and row["changed"]
    ]
    non_qp_changed = sum(row["parameter"] != "qp" for row in sensitivity_changed)
    checks.require(
        name,
        non_qp_changed == 0,
        f"sensitivity changed {non_qp_changed} non-Qp structure medians",
    )
    changed_qp_report = [
        {
            "structure_key": row["structure_key"],
            "p3_structure_id": row["p3_structure_id"],
            "primary_median": row["reference_value"],
            "sensitivity_median": row["candidate_value"],
            "delta": row["delta"],
        }
        for row in sensitivity_changed
        if row["parameter"] == "qp"
    ]
    checks.detail(
        name,
        rows=len(seen),
        old_coordinate_rows=len(old_rows),
        old_coordinate_changed_medians=len(old_changed),
        sensitivity_changed_medians=len(sensitivity_changed),
        sensitivity_changed_qp_medians=len(changed_qp_report),
        sensitivity_changed_non_qp_medians=non_qp_changed,
        **counters,
    )
    return {
        "rows": len(seen),
        "summary": summary,
        "old_coordinate_changed": old_changed,
        "sensitivity_changed_qp": changed_qp_report,
        "sensitivity_changed_non_qp_count": non_qp_changed,
        **counters,
    }


def run_rule_probes(checks: Checks) -> dict[str, Any]:
    name = "independent_nonzero_rule_probes"
    results = []

    def probe(label: str, actual: Any, wanted: Any) -> None:
        passed = actual == wanted
        results.append(
            {
                "case": label,
                "actual": actual,
                "expected": wanted,
                "status": "PASS" if passed else "FAILED",
            }
        )
        checks.require(name, passed, f"probe failed: {label}: {actual!r} != {wanted!r}")

    probe("odd_median", own_median([100, 1, 2]), 2.0)
    probe("even_median", own_median([100, 1, 3, 2]), 2.5)
    probe("q25_linear", own_percentile([1, 2, 3, 100], 0.25), 1.75)
    probe("q75_linear", own_percentile([1, 2, 3, 100], 0.75), 27.25)

    def measurement(row_id: str, value: Any, reason: str = "", flag: str = "") -> dict[str, Any]:
        return {
            "source_row_id": row_id,
            "audit_flag": flag,
            "values": {"qp": value},
            "validity": {"qp": {"valid": reason == "", "reason": reason}},
        }

    groups = {
        ("#2", "deembedded", "s", "A"): [
            measurement("a1", 1.0),
            measurement("a2", 100.0),
        ],
        ("#2", "deembedded", "s", "B"): [measurement("b1", 3.0)],
    }
    stats = summarize_devices(groups, list(groups), "qp", False, 5)
    probe("true_repeat_device_median_then_structure_median", stats["median"], 26.75)
    probe("true_repeat_device_count", stats["n_total"], 2)

    zero_groups = {
        ("#2", "deembedded", "s", "A"): [measurement("a", 0.0)],
        ("#2", "deembedded", "s", "B"): [measurement("b", None, "missing")],
        ("#2", "deembedded", "s", "C"): [measurement("c", -1.0, "non_positive")],
    }
    zero_stats = summarize_devices(zero_groups, list(zero_groups), "qp", False, 5)
    probe("valid_zero_retained", zero_stats["median"], 0.0)
    probe(
        "null_missing_invalid_reconcile",
        [zero_stats["n_valid"], zero_stats["n_missing"], zero_stats["n_invalid"]],
        [1, 1, 1],
    )
    probe("valid_zero_is_low_support_not_dropped", zero_stats["source_device_keys"], ["A"])

    flagged_group = [measurement("f", 100.0, flag=FL0_FLAG)]
    probe("fl0_primary_retained", reduce_device(flagged_group, "qp", False).value, 100.0)
    masked = reduce_device(flagged_group, "qp", True)
    probe("fl0_sensitivity_qp_masked", [masked.value, masked.missing], [None, 1])
    probe("fl0_mask_reason", masked.reason, "no_valid_repeat_values;missing=1;invalid=0")
    physical_zero = classify_source(
        {"qp": "0", "frequency_valid": "True"}, "qp"
    )
    null_value = classify_source({"qp": "", "frequency_valid": "True"}, "qp")
    probe("physical_zero_qp_invalid", physical_zero["reason"], "non_positive")
    probe("null_is_missing", null_value["reason"], "missing")
    checks.detail(name, cases=len(results), uses_a1_aggregation=False)
    return {"cases": results, "uses_a1_aggregation": False}


def registered_hash_checks(
    manifest: dict[str, Any],
    owner_receipt: dict[str, Any],
    protocol: dict[str, Any],
    protected_before: dict[str, dict[str, Any]],
    checks: Checks,
) -> dict[str, Any]:
    name = "registered_input_output_and_source_hashes"
    comparisons = 0
    collections = [
        manifest.get("inputs", []),
        manifest.get("input_hashes_after", []),
        manifest.get("outputs", []),
        owner_receipt.get("inputs", []),
        owner_receipt.get("outputs", []),
        owner_receipt.get("source_files", []),
    ]
    for records in collections:
        for registered in records:
            path = str(Path(registered["path"]).resolve())
            current = protected_before.get(path) or file_record(Path(path))
            comparisons += 1
            checks.require(
                name,
                current["exists"]
                and current["bytes"] == registered.get("bytes")
                and current["sha256"] == registered.get("sha256"),
                f"registered hash/bytes mismatch: {path}",
            )
    for identifier, expected_hash in protocol.get("expected_input_sha256", {}).items():
        registered = next(
            (row for row in manifest["inputs"] if row.get("id") == identifier), None
        )
        comparisons += 1
        checks.require(
            name,
            registered is not None and registered.get("sha256") == expected_hash,
            f"protocol input hash not matched: {identifier}",
        )
    checks.detail(name, registered_records_checked=comparisons)
    return {"registered_records_checked": comparisons}


def independent_counts(
    source: dict[str, Any],
    expected: dict[str, dict[tuple[str, str, str], dict[str, Any]]],
    target_report: dict[str, Any],
    ledger_report: dict[str, Any],
    exclusion_report: dict[str, Any],
    difference_report: dict[str, Any],
    identity_header: dict[str, Any],
) -> dict[str, Any]:
    measurements = source["measurements_by_row"]
    unresolved = identity_header.get(
        "unresolved_identity_conflicts",
        identity_header.get("counts", {}).get("unresolved_identity_conflicts"),
    )
    return {
        "source_records": source["source_row_count"],
        "identity_records": source["identity_count"],
        "primary_measurements": len(measurements),
        "primary_devices": len({row["device_key"] for row in measurements.values()}),
        "primary_structures": len({row["structure_key"] for row in measurements.values()}),
        "exact_duplicate_aliases": source["alias_rows"],
        "excluded_records": exclusion_report["rows"],
        "filename_fail_primary_records": sum(
            parse_bool(row["filename_has_fail"]) for row in measurements.values()
        ),
        "fl0_review_rows": len(source["review"]),
        "fl0_review_rows_retained": sum(
            row["audit_flag"] == FL0_FLAG for row in measurements.values()
        ),
        "fl0_sensitivity_masked_measurement_parameters": ledger_report["masked_qp_rows"],
        "device_ledger_rows": ledger_report["rows"],
        "primary_target_rows": target_report["views"]["primary"]["rows"],
        "sensitivity_target_rows": target_report["views"]["sensitivity"]["rows"],
        "primary_low_support_rows": sum(
            row["low_support"] for row in expected["primary"].values()
        ),
        "sensitivity_low_support_rows": sum(
            row["low_support"] for row in expected["sensitivity"].values()
        ),
        "old_coordinate_rows_compared_only": difference_report["summary"][
            "primary_vs_old_coordinate"
        ][next(iter(expected["primary"].values()))["parameter"]]["compared"],
        "identity_conflicts": unresolved,
    }


def future_artifacts(run: Path) -> list[str]:
    found = []
    for path in run.rglob("*"):
        relative = path.relative_to(run)
        lowered = [part.casefold() for part in relative.parts]
        if any(part.startswith("b03") for part in lowered) or any(
            part in {"models", "checkpoints", "research_bundle", "splits.json"}
            for part in lowered
        ):
            found.append(str(relative))
    return sorted(found)


def run_command(
    command_id: str,
    argv: list[str],
    cwd: Path,
    log_path: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    started = utc_now()
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_path.write_text(completed.stdout, encoding="utf-8")
    return {
        "command_id": command_id,
        "argv": argv,
        "cwd": str(cwd),
        "environment_differences": {
            key: environment[key]
            for key in ("PYTHONDONTWRITEBYTECODE", "UV_NO_CACHE", "GIT_OPTIONAL_LOCKS")
        },
        "started_at": started,
        "finished_at": utc_now(),
        "exit_code": completed.returncode,
        "status": "PASS" if completed.returncode == 0 else "FAILED",
        "stdout_stderr_combined": True,
        "log": file_record(log_path),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--records", type=Path, required=True)
    result.add_argument("--identity", type=Path, required=True)
    result.add_argument("--data", type=Path, required=True)
    result.add_argument("--out", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    started_at = utc_now()
    backend = Path(__file__).resolve().parents[1]
    records_path = args.records.resolve()
    identity_path = args.identity.resolve()
    data = args.data.resolve()
    out = args.out.resolve()
    run = data.parent
    b02 = run / "B02"
    review = b02 / "review"
    expected_out = review / "independent_check"
    if out != expected_out:
        raise ValueError(f"A4 output must be exactly {expected_out}")
    out.mkdir(parents=True, exist_ok=True)
    logs = review / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    self_log_path = logs / "independent_cli.log"

    def log(message: str) -> None:
        with self_log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{utc_now()} {message}\n")

    log("Independent B02 verification started; no A1 aggregation module imported")
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    owner_receipt = json.loads((b02 / "receipt.json").read_text(encoding="utf-8"))
    protocol = json.loads((b02 / "data_protocol.json").read_text(encoding="utf-8"))
    b01_review_path = identity_path.parent / "review" / "independent_review.json"
    b01_review = json.loads(b01_review_path.read_text(encoding="utf-8"))
    parameters = list(protocol["parameters"])
    threshold = int(protocol["aggregation"]["low_support_threshold"])
    audit_path = Path(
        next(row["path"] for row in manifest["inputs"] if row.get("id") == "audit")
    ).resolve()
    old_path = Path(
        next(
            row["path"]
            for row in manifest["inputs"]
            if row.get("id") == "old_coordinate_labels"
        )
    ).resolve()
    checks = Checks()
    checks.require(
        "registered_inputs_and_b01_gate",
        records_path == Path(
            next(row["path"] for row in manifest["inputs"] if row.get("id") == "records")
        ).resolve()
        and identity_path
        == Path(
            next(row["path"] for row in manifest["inputs"] if row.get("id") == "identity")
        ).resolve(),
        "CLI records/identity do not match registered B02 inputs",
    )
    checks.require(
        "registered_inputs_and_b01_gate",
        b01_review.get("acceptance_status") == "PASS"
        and b01_review.get("b02_authorized") is True,
        "B01 independent review does not authorize B02",
    )
    checks.require(
        "registered_inputs_and_b01_gate",
        protocol.get("fl0", {}).get("expected_review_rows") == 59
        and protocol.get("fl0", {}).get("sensitivity_masks_only") == ["qp"]
        and protocol.get("fl0", {}).get("training_overlay_used") is False,
        "FL0 protocol is not primary-retain/sensitivity-Qp-only without overlay",
    )

    protected_paths = {
        records_path,
        identity_path,
        audit_path,
        old_path,
        b01_review_path,
        b02 / "data_protocol.json",
        b02 / "receipt.json",
        identity_path.parent / "receipt.json",
        PLAN_PATH,
        backend / "app/ml/device_median_v1.py",
        backend / "scripts/run_device_median_v1.py",
        backend / "tests/ml/test_device_median_v1.py",
    }
    for collection in (
        manifest.get("inputs", []),
        manifest.get("input_hashes_after", []),
        manifest.get("outputs", []),
        owner_receipt.get("inputs", []),
        owner_receipt.get("outputs", []),
        owner_receipt.get("source_files", []),
    ):
        protected_paths.update(Path(row["path"]).resolve() for row in collection)
    protected_before = {
        str(path): file_record(path) for path in sorted(protected_paths, key=str)
    }
    for path, record in protected_before.items():
        checks.require(
            "registered_input_output_and_source_hashes",
            record["exists"],
            f"protected input/output missing before verification: {path}",
        )
    before_future = future_artifacts(run)
    checks.require(
        "no_training_or_b03_artifacts",
        not before_future,
        f"future artifacts: {before_future}",
    )
    checks.require(
        "no_training_or_b03_artifacts",
        manifest.get("training_performed") is False
        and manifest.get("b03_protocol_or_splits_created") is False
        and owner_receipt.get("training_performed") is False
        and owner_receipt.get("b03_protocol_or_splits_created") is False,
        "manifest/receipt declares training or B03 artifacts",
    )

    log("Streaming direct P3 rows and B01 identity")
    source = recompute_source(records_path, identity_path, audit_path, parameters, checks)
    expected = build_expected_targets(source, parameters, threshold)
    log("Recomputed both target views; comparing every target row and member set")
    target_report = verify_targets(data, expected, parameters, checks)
    ledger_report = verify_ledger(data, source, parameters, checks)
    unresolved = source["identity_header"].get(
        "unresolved_identity_conflicts",
        source["identity_header"].get("counts", {}).get("unresolved_identity_conflicts"),
    )
    exclusion_report = verify_exclusions(data, source, unresolved, checks)
    difference_report = verify_differences(
        data, old_path, expected, manifest, owner_receipt, checks
    )
    probe_report = run_rule_probes(checks)
    counts = independent_counts(
        source,
        expected,
        target_report,
        ledger_report,
        exclusion_report,
        difference_report,
        source["identity_header"],
    )
    counts.update(
        {
            "parameters": len(parameters),
            "structure_parameter_rows_verified": sum(
                counts[key] for key in ("primary_target_rows", "sensitivity_target_rows")
            ),
            "member_device_keys_verified": sum(
                len(row["source_device_keys"])
                for view in expected.values()
                for row in view.values()
            ),
            "primary_member_device_keys": sum(
                len(row["source_device_keys"]) for row in expected["primary"].values()
            ),
            "sensitivity_member_device_keys": sum(
                len(row["source_device_keys"]) for row in expected["sensitivity"].values()
            ),
            "source_zero_values": source["source_zero_values"],
            "source_valid_zero_values": source["source_valid_zero_values"],
            "sensitivity_changed_qp_structure_medians": len(
                difference_report["sensitivity_changed_qp"]
            ),
            "sensitivity_changed_non_qp_structure_medians": difference_report[
                "sensitivity_changed_non_qp_count"
            ],
            "old_coordinate_changed_structure_parameters": len(
                difference_report["old_coordinate_changed"]
            ),
        }
    )
    for identifier, wanted in protocol.get("expected_counts", {}).items():
        checks.require(
            "formal_manifest_receipt_and_protocol_census",
            counts.get(identifier) == wanted,
            f"protocol count {identifier}: independent={counts.get(identifier)} expected={wanted}",
        )
    for identifier, wanted in counts.items():
        if identifier in manifest.get("counts", {}):
            checks.require(
                "formal_manifest_receipt_and_protocol_census",
                manifest["counts"][identifier] == wanted,
                f"manifest count {identifier}: independent={wanted} "
                f"recorded={manifest['counts'][identifier]}",
            )
        if identifier in owner_receipt.get("counts", {}):
            checks.require(
                "formal_manifest_receipt_and_protocol_census",
                owner_receipt["counts"][identifier] == wanted,
                f"receipt count {identifier}: independent={wanted} "
                f"recorded={owner_receipt['counts'][identifier]}",
            )
    checks.detail(
        "formal_manifest_receipt_and_protocol_census",
        independent_counts=counts,
        expected_protocol_counts=protocol.get("expected_counts", {}),
    )
    checks.detail(
        "fl0_59_primary_retained_sensitivity_masks_only_qp",
        review_rows=counts["fl0_review_rows"],
        retained_primary=counts["fl0_review_rows_retained"],
        flagged_ledger_rows=ledger_report["flagged_rows"],
        masked_qp_rows=ledger_report["masked_qp_rows"],
        changed_qp_structure_medians=counts["sensitivity_changed_qp_structure_medians"],
        changed_non_qp_structure_medians=counts[
            "sensitivity_changed_non_qp_structure_medians"
        ],
        training_overlay_used=False,
    )
    checks.require(
        "fl0_59_primary_retained_sensitivity_masks_only_qp",
        counts["fl0_review_rows"] == 59
        and counts["fl0_review_rows_retained"] == 59
        and ledger_report["flagged_rows"] == 59 * len(parameters)
        and ledger_report["masked_qp_rows"] == 59,
        "59 FL0 rows are not retained/flagged with exactly 59 Qp masks",
    )
    hash_report = registered_hash_checks(
        manifest, owner_receipt, protocol, protected_before, checks
    )

    environment = dict(os.environ)
    environment.update(
        {"PYTHONDONTWRITEBYTECODE": "1", "UV_NO_CACHE": "1", "GIT_OPTIONAL_LOCKS": "0"}
    )
    log("Running all A1 targeted tests read-only")
    pytest_xml = review / "targeted_pytest.xml"
    pytest_command = run_command(
        "a1_targeted_pytest",
        [
            "uv",
            "run",
            "--no-sync",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(review / "pytest_tmp"),
            f"--junitxml={pytest_xml}",
            "tests/ml/test_device_median_v1.py",
        ],
        backend,
        logs / "targeted_pytest.log",
        environment,
    )
    pytest_log = (logs / "targeted_pytest.log").read_text(encoding="utf-8")
    passed_match = re.search(r"(\d+) passed", pytest_log)
    tests_run = int(passed_match.group(1)) if passed_match else 0
    tests_passed = (
        pytest_command["exit_code"] == 0
        and tests_run == 15
        and not re.search(r"\d+ (?:failed|skipped|error)", pytest_log)
    )
    checks.external(
        "all_a1_targeted_tests",
        tests_passed,
        tests_run=tests_run,
        exit_code=pytest_command["exit_code"],
        log=pytest_command["log"],
    )
    ruff_command = run_command(
        "independent_verifier_ruff",
        [
            "uv",
            "run",
            "--no-sync",
            "ruff",
            "check",
            "--no-cache",
            "scripts/verify_device_median_v1.py",
        ],
        backend,
        logs / "ruff_check.log",
        environment,
    )
    checks.external(
        "independent_verifier_ruff",
        ruff_command["exit_code"] == 0,
        exit_code=ruff_command["exit_code"],
        log=ruff_command["log"],
    )

    after_future = future_artifacts(run)
    checks.require(
        "no_training_or_b03_artifacts",
        not after_future,
        f"future artifacts after verification: {after_future}",
    )
    checks.detail(
        "no_training_or_b03_artifacts",
        future_paths_before=before_future,
        future_paths_after=after_future,
        training_performed=False,
        b03_protocol_or_splits_created=False,
    )
    protected_after = {
        str(path): file_record(path) for path in sorted(protected_paths, key=str)
    }
    protected_differences = [
        path
        for path in protected_before
        if protected_before[path]["bytes"] != protected_after[path]["bytes"]
        or protected_before[path]["sha256"] != protected_after[path]["sha256"]
        or protected_before[path]["exists"] != protected_after[path]["exists"]
    ]
    checks.require(
        "protected_inputs_outputs_and_a1_files_unchanged",
        not protected_differences,
        f"protected files changed: {protected_differences}",
    )
    checks.detail(
        "protected_inputs_outputs_and_a1_files_unchanged",
        protected_files=len(protected_before),
        differences=protected_differences,
    )

    status = "PASS" if checks.passed else "FAILED"
    next_task = "B03" if status == "PASS" else None
    reviewer_id = os.environ.get("PI_SESSION_ID")
    log(f"Independent B02 verification finished: {status}")
    self_command = {
        "command_id": "independent_cli_contract",
        "argv": [
            "uv",
            "run",
            "--no-sync",
            "python",
            str(Path(__file__).resolve()),
            "--records",
            str(records_path),
            "--identity",
            str(identity_path),
            "--data",
            str(data),
            "--out",
            str(out),
        ],
        "cwd": str(backend),
        "environment_differences": {
            key: environment[key]
            for key in ("PYTHONDONTWRITEBYTECODE", "UV_NO_CACHE", "GIT_OPTIONAL_LOCKS")
        },
        "started_at": started_at,
        "finished_at": utc_now(),
        "exit_code": 0 if status == "PASS" else 1,
        "status": status,
        "stdout_stderr_combined": True,
        "log": file_record(self_log_path),
    }
    commands = [self_command, pytest_command, ruff_command]
    check_list = list(checks.items.values())
    findings = checks.findings()
    source_report = {
        "identity_header": source["identity_header"],
        "counts": counts,
        "source_check": checks.items["source_identity_exact_import_repeat_and_scope"],
        "streaming_method": (
            "gzip csv.DictReader plus incremental JSONDecoder.raw_decode; "
            "no A1 aggregation import"
        ),
    }
    write_json(out / "source_recompute.json", source_report)
    write_json(out / "target_verification.json", target_report)
    write_json(out / "device_ledger_verification.json", ledger_report)
    write_json(out / "exclusion_verification.json", exclusion_report)
    write_json(out / "difference_verification.json", difference_report)
    write_json(out / "independent_rule_probes.json", probe_report)
    write_json(
        out / "hash_verification.json",
        {
            **hash_report,
            "protected_files": len(protected_before),
            "protected_differences": protected_differences,
            "inputs": list(protected_before.values()),
        },
    )
    write_json(
        out / "summary.json",
        {
            "schema": "aln_device_median_b02_independent_a4_check_v1",
            "task_id": "B02",
            "status": status,
            "acceptance_status": status,
            "counts": counts,
            "checks": check_list,
            "findings": findings,
            "commands": commands,
            "next_task": next_task,
        },
    )
    review_payload = {
        "schema": "aln_device_median_b02_independent_a4_review_v1",
        "task_id": "B02",
        "attempt_id": "attempt_001",
        "reviewer_role": "independent_A4",
        "reviewer_agent_id": reviewer_id,
        "reviewer_agent_id_source": "PI_SESSION_ID" if reviewer_id else None,
        "status": status,
        "execution_status": "PASS",
        "acceptance_status": status,
        "scientific_status": "REJECTED",
        "trusted": False,
        "b03_authorized": status == "PASS",
        "next_task": next_task,
        "counts": counts,
        "checks": check_list,
        "findings": findings,
        "difference_summary": difference_report["summary"],
        "sensitivity_changed_qp": difference_report["sensitivity_changed_qp"],
        "old_coordinate_comparison": {
            "role": "comparison_only",
            "changed_structure_parameters": len(difference_report["old_coordinate_changed"]),
            "used_as_device_source": False,
        },
        "aggregation_method": (
            "Direct P3 source rows and B01 identity; local sorted middle-element medians; "
            "per-device repeat median before equal-weight structure medians; no A1 functions"
        ),
        "commands": commands,
        "started_at": started_at,
        "finished_at": utc_now(),
        "limitations": [
            "Software/data acceptance does not change scientific_status=REJECTED or trusted=false.",
            "Observed formal data have no exact-import aliases or true repeat groups; "
            "independent nonzero probes and A1 synthetic tests cover those rules.",
            "Historical deembedding configuration remains unverified.",
        ],
    }
    write_json(review / "independent_review.json", review_payload)
    generated_paths = sorted(path for path in out.iterdir() if path.is_file())
    generated_paths.extend(
        [
            review / "independent_review.json",
            self_log_path,
            logs / "targeted_pytest.log",
            logs / "ruff_check.log",
            pytest_xml,
        ]
    )
    receipt = {
        "schema": "aln_device_median_b02_independent_a4_receipt_v1",
        "task_id": "B02",
        "attempt_id": "attempt_001",
        "status": status,
        "execution_status": "PASS",
        "acceptance_status": status,
        "scientific_status": "REJECTED",
        "trusted": False,
        "reviewer_role": "independent_A4",
        "reviewer_agent_id": reviewer_id,
        "b03_authorized": status == "PASS",
        "next_task": next_task,
        "counts": counts,
        "checks": check_list,
        "findings": findings,
        "recommendation_count": 0,
        "eligible_models": [],
        "model_selection_performed": False,
        "training_performed": False,
        "b03_protocol_or_splits_created": False,
        "fl0_training_overlay_used": False,
        "structures_csv_used_as_device_source": False,
        "structures_csv_used_for_old_label_comparison_only": True,
        "inputs": list(protected_after.values()),
        "outputs": [file_record(path) for path in generated_paths],
        "source_files": [
            file_record(backend / "scripts/verify_device_median_v1.py"),
            file_record(backend / "app/ml/device_median_v1.py"),
            file_record(backend / "scripts/run_device_median_v1.py"),
            file_record(backend / "tests/ml/test_device_median_v1.py"),
        ],
        "commands": commands,
        "protected_input_differences": protected_differences,
        "started_at": started_at,
        "finished_at": utc_now(),
        "receipt_path": str((review / "receipt.json").resolve()),
        "receipt_self_hash_excluded_to_avoid_recursion": True,
        "limitations": review_payload["limitations"],
    }
    write_json(review / "receipt.json", receipt)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
