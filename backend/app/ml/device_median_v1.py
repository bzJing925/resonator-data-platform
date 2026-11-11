"""Pure all-device median aggregation for the Stage-B B02 data build.

The functions in this module perform no I/O and keep every parameter independent.
Exact-import aliases must be removed by the B01-aware caller before aggregation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

FL0_AUDIT_FLAG = "FL0_QP_REVIEW"
CORE_PARAMETERS = frozenset({"k2eff_pct", "qp", "qs", "bodeq_fitted"})
SENSITIVITY_MASKS = {FL0_AUDIT_FLAG: frozenset({"qp"})}
_METADATA_FIELDS = (
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
)


def median(values: Iterable[float]) -> float | None:
    """Return the ordinary median, averaging the two central values when even."""
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2)


def percentile(values: Sequence[float], probability: float) -> float | None:
    """Linearly interpolated percentile (the NumPy/Pandas default convention)."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def text_sha256(value: Any) -> str:
    """Match B01's canonical source-row fingerprint without importing owner code."""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"true", "1", "y", "yes"}:
        return True
    if text in {"false", "0", "n", "no", ""}:
        return False
    raise ValueError(f"Not a boolean value: {value!r}")


def classify_source_value(record: Mapping[str, Any], parameter: str) -> dict[str, Any]:
    """Apply P3's source-column validity rules and preserve valid zero values."""
    raw = record.get(parameter)
    text = "" if raw is None else str(raw).strip()
    if text.casefold() in {"", "nan", "none", "null", "na", "n/a", "#n/a"}:
        value = None
        derived_valid = False
        reasons = ["missing"]
    else:
        try:
            value = float(text)
        except ValueError:
            return {"value": None, "valid": False, "reason": "invalid_non_numeric"}
        reasons = []
        if not math.isfinite(value):
            reasons.append("non_finite")
        if not _boolean(record.get("frequency_valid")):
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
        derived_valid = not reasons
    if parameter in CORE_PARAMETERS:
        source_valid = _boolean(record.get("valid_" + parameter))
        if source_valid != derived_valid:
            raise ValueError(
                f"P3 validity mismatch for {record.get('row_id')} {parameter}: "
                f"source={source_valid}, derived={derived_valid}"
            )
    return {
        "value": value,
        "valid": derived_valid,
        "reason": ";".join(reasons),
    }


def _conflict(kind: str, row_ids: Iterable[str], details: Mapping[str, Any]) -> dict[str, Any]:
    ids = sorted(set(row_ids))
    return {"type": kind, "source_row_ids": ids, "details": dict(details)}


def join_primary_measurements(
    records: Iterable[Mapping[str, Any]],
    identity_records: Iterable[Mapping[str, Any]],
    audit_rows: Iterable[Mapping[str, Any]],
    parameters: Sequence[str],
) -> dict[str, Any]:
    """Join B01 identity to P3 values; never use the audit overlay as labels."""
    conflicts = []
    source_rows: dict[str, dict[str, Any]] = {}
    for source in records:
        row = dict(source)
        row_id = str(row["row_id"])
        if row_id in source_rows:
            conflicts.append(_conflict("duplicate_source_row_id", [row_id], {}))
            continue
        source_rows[row_id] = row

    identities: dict[str, dict[str, Any]] = {}
    for identity in identity_records:
        row = dict(identity)
        row_id = str(row["source"]["row_id"])
        if row_id in identities:
            conflicts.append(_conflict("duplicate_identity_source_row_id", [row_id], {}))
            continue
        identities[row_id] = row

    for row_id in sorted(set(source_rows) - set(identities)):
        conflicts.append(_conflict("source_row_missing_from_identity", [row_id], {}))
    for row_id in sorted(set(identities) - set(source_rows)):
        conflicts.append(_conflict("identity_row_missing_from_source", [row_id], {}))

    review_rows: dict[str, Mapping[str, Any]] = {}
    audit_identity_fields = (
        "source_path",
        "source_sheet",
        "excel_row",
        "mode",
        "original_filename",
        "device_id",
        "port",
    )
    for audit in audit_rows:
        if audit.get("decision") != "review":
            continue
        row_id = str(audit["row_id"])
        if row_id in review_rows:
            conflicts.append(_conflict("fl0_duplicate_review_row", [row_id], {}))
            continue
        review_rows[row_id] = audit
        source = source_rows.get(row_id)
        if source is None:
            conflicts.append(_conflict("fl0_review_source_missing", [row_id], {}))
        elif mismatched := [
            field
            for field in audit_identity_fields
            if str(audit.get(field, "")) != str(source.get(field, ""))
        ]:
            conflicts.append(
                _conflict("fl0_review_source_identity_mismatch", [row_id], {"fields": mismatched})
            )

    measurements = []
    exclusions = []
    for row_id, identity in sorted(identities.items()):
        source = source_rows.get(row_id)
        if source is None:
            continue
        expected_hash = identity["source"].get("source_row_sha256")
        if expected_hash != text_sha256(source):
            conflicts.append(_conflict("identity_source_payload_mismatch", [row_id], {}))
        review = review_rows.get(row_id)
        identity_flagged = bool(identity.get("fl0_review", {}).get("flagged", False))
        if identity_flagged != (review is not None):
            conflicts.append(_conflict("fl0_identity_audit_flag_mismatch", [row_id], {}))

        association = identity["structure_association"]
        eligible = bool(association["cohort"]["geometry_eligible"])
        canonical = bool(identity["is_canonical_measurement"])
        primary = canonical and identity["mode"] == "deembedded" and eligible
        if review is not None and not primary:
            conflicts.append(_conflict("fl0_review_not_retained_in_primary", [row_id], {}))
        if not primary:
            reasons = []
            if not canonical:
                reasons.append("exact_duplicate_import")
            if identity["mode"] != "deembedded":
                reasons.append("raw_control_only")
            if not eligible:
                reasons.append("geometry_not_eligible")
                reasons.extend(association["cohort"].get("exclusion_reasons", []))
            exclusions.append(
                {
                    "source_row_id": row_id,
                    "device_key": identity["device_key"],
                    "structure_key": identity["structure_key"],
                    "mode": identity["mode"],
                    "reasons": sorted(set(reasons)),
                    "alias_of_source_row_id": identity.get("alias_of_source_row_id"),
                }
            )
            continue

        classified = {
            parameter: classify_source_value(source, parameter) for parameter in parameters
        }
        geometry = association["geometry"]
        measurements.append(
            {
                "device_key": identity["device_key"],
                "structure_key": identity["structure_key"],
                "source_row_id": row_id,
                "canonical_source_row_id": identity.get("canonical_source_row_id", row_id),
                "mode": identity["mode"],
                "batch": identity.get("batch", ""),
                "port": identity.get("port", source.get("port", "")),
                "coord": source.get("coord", ""),
                "filename_has_fail": source.get("filename_has_fail", ""),
                "audit_flag": FL0_AUDIT_FLAG if review is not None else "",
                "audit_reasons": review.get("reasons", "") if review is not None else "",
                "values": {parameter: item["value"] for parameter, item in classified.items()},
                "validity": {
                    parameter: {"valid": item["valid"], "reason": item["reason"]}
                    for parameter, item in classified.items()
                },
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
        )
    measurements.sort(key=lambda row: row["source_row_id"])
    exclusions.sort(key=lambda row: row["source_row_id"])
    return {
        "measurements": measurements,
        "exclusions": exclusions,
        "identity_conflicts": sorted(
            conflicts, key=lambda row: (row["type"], row["source_row_ids"])
        ),
        "counts": {
            "source_records": len(source_rows),
            "identity_records": len(identities),
            "primary_measurements": len(measurements),
            "primary_devices": len({row["device_key"] for row in measurements}),
            "primary_structures": len({row["structure_key"] for row in measurements}),
            "exact_duplicate_aliases": sum(
                "exact_duplicate_import" in row["reasons"] for row in exclusions
            ),
            "excluded_records": len(exclusions),
            "fl0_review_rows": len(review_rows),
            "fl0_review_rows_retained": sum(
                row["audit_flag"] == FL0_AUDIT_FLAG for row in measurements
            ),
            "filename_fail_primary_records": sum(
                _boolean(row["filename_has_fail"]) for row in measurements
            ),
        },
    }


def _masked_reason(measurement: Mapping[str, Any], parameter: str) -> str:
    masked = SENSITIVITY_MASKS.get(str(measurement.get("audit_flag", "")), frozenset())
    return "fl0_qp_review_masked" if parameter in masked else ""


def _device_parameter_summary(
    measurements: Sequence[Mapping[str, Any]], parameter: str, *, sensitivity: bool
) -> dict[str, Any]:
    values = []
    missing = 0
    invalid = 0
    for measurement in measurements:
        mask_reason = _masked_reason(measurement, parameter) if sensitivity else ""
        validity = measurement["validity"][parameter]
        if mask_reason:
            missing += 1
            continue
        if validity["valid"]:
            value = measurement["values"][parameter]
            if value is None:
                raise ValueError("A valid measurement must have a numeric value, including zero")
            values.append(float(value))
        elif validity["reason"] == "missing":
            missing += 1
        else:
            invalid += 1
    value = median(values)
    if value is not None:
        return {"value": value, "valid": True, "reason": ""}
    reason = f"no_valid_repeat_values;missing={missing};invalid={invalid}"
    return {
        "value": None,
        "valid": False,
        "reason": reason,
        "missing": int(invalid == 0),
        "invalid": int(invalid > 0),
    }


def _structure_statistics(
    device_summaries: Mapping[tuple[tuple[str, str, str, str], str], dict[str, Any]],
    structure_devices: Iterable[tuple[tuple[str, str, str, str], str]],
    *,
    low_support_threshold: int,
) -> dict[str, Any]:
    structure_devices = tuple(structure_devices)
    summaries = [device_summaries[key] for key in structure_devices]
    valid = [item for item in summaries if item["valid"]]
    values = sorted(float(item["value"]) for item in valid)
    n_valid = len(values)
    n_missing = sum(not item["valid"] and item.get("missing", 0) for item in summaries)
    n_invalid = sum(not item["valid"] and item.get("invalid", 0) for item in summaries)
    n_total = len(summaries)
    if n_total != n_valid + n_missing + n_invalid:
        raise AssertionError("Device validity counts do not reconcile")
    return {
        "median": median(values),
        "q25": percentile(values, 0.25),
        "q75": percentile(values, 0.75),
        "min": float(values[0]) if values else None,
        "max": float(values[-1]) if values else None,
        "n_total": n_total,
        "n_devices_total": n_total,
        "n_valid": n_valid,
        "n_missing": int(n_missing),
        "n_invalid": int(n_invalid),
        "source_device_keys": sorted(
            key[0][3]
            for key, item in zip(structure_devices, summaries, strict=True)
            if item["valid"]
        ),
        "low_support": n_valid < low_support_threshold,
    }


def aggregate_measurements(
    measurements: Iterable[Mapping[str, Any]],
    parameters: Sequence[str],
    *,
    low_support_threshold: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """Aggregate canonical measurements with equal weight per physical device.

    Each input row is one canonical measurement. True repeats share a device key;
    their valid values are reduced to a per-parameter device median before the
    structure median. The sensitivity view masks Qp only on FL0 review rows.
    """
    if low_support_threshold < 0:
        raise ValueError("low_support_threshold must be nonnegative")
    if len(set(parameters)) != len(parameters):
        raise ValueError("parameters must be unique")
    rows = sorted(
        (dict(row) for row in measurements),
        key=lambda row: (
            str(row.get("batch", "")),
            str(row["mode"]),
            str(row["structure_key"]),
            str(row["device_key"]),
            str(row["source_row_id"]),
        ),
    )
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("batch", "")),
            str(row["mode"]),
            str(row["structure_key"]),
            str(row["device_key"]),
        )
        groups[key].append(row)

    device_values: dict[str, dict[tuple[str, str, str, str], dict[str, Any]]] = {
        "primary": {},
        "sensitivity": {},
    }
    for parameter in parameters:
        for view in device_values:
            device_values[view].update(
                {
                    (key, parameter): _device_parameter_summary(
                        group, parameter, sensitivity=view == "sensitivity"
                    )
                    for key, group in groups.items()
                }
            )

    targets: dict[str, list[dict[str, Any]]] = {"primary": [], "sensitivity": []}
    structure_keys = sorted({key[:3] for key in groups})
    parameter_order = {parameter: index for index, parameter in enumerate(parameters)}
    for view in targets:
        for batch, mode, structure_key in structure_keys:
            members = sorted(key for key in groups if key[:3] == (batch, mode, structure_key))
            first = groups[members[0]][0]
            metadata = {field: first.get(field) for field in _METADATA_FIELDS}
            for parameter in parameters:
                keys = [(key, parameter) for key in members]
                stats = _structure_statistics(
                    device_values[view],
                    keys,
                    low_support_threshold=low_support_threshold,
                )
                targets[view].append(
                    {
                        "structure_key": structure_key,
                        "mode": mode,
                        "parameter": parameter,
                        **metadata,
                        **stats,
                    }
                )
        targets[view].sort(
            key=lambda row: (
                row["mode"],
                row["structure_key"],
                parameter_order[row["parameter"]],
            )
        )

    ledger = []
    for key, group in sorted(groups.items()):
        for row in sorted(group, key=lambda item: str(item["source_row_id"])):
            for parameter in parameters:
                validity = row["validity"][parameter]
                mask_reason = _masked_reason(row, parameter)
                primary = device_values["primary"][(key, parameter)]
                sensitivity = device_values["sensitivity"][(key, parameter)]
                ledger.append(
                    {
                        "device_key": row["device_key"],
                        "structure_key": row["structure_key"],
                        "source_row_id": row["source_row_id"],
                        "canonical_source_row_id": row.get(
                            "canonical_source_row_id", row["source_row_id"]
                        ),
                        "mode": row["mode"],
                        "port": row.get("port", ""),
                        "coord": row.get("coord", ""),
                        "filename_has_fail": row.get("filename_has_fail", ""),
                        "parameter": parameter,
                        "value": row["values"][parameter],
                        "valid": validity["valid"],
                        "reason": validity["reason"],
                        "audit_flag": row.get("audit_flag", ""),
                        "audit_reasons": row.get("audit_reasons", ""),
                        "n_measurements": len(group),
                        "device_value": primary["value"],
                        "device_valid": primary["valid"],
                        "device_reason": primary["reason"],
                        "sensitivity_value": (
                            None if mask_reason else row["values"][parameter]
                        ),
                        "sensitivity_valid": False if mask_reason else validity["valid"],
                        "sensitivity_reason": mask_reason or validity["reason"],
                        "sensitivity_device_value": sensitivity["value"],
                        "sensitivity_device_valid": sensitivity["valid"],
                        "sensitivity_device_reason": sensitivity["reason"],
                    }
                )
    ledger.sort(
        key=lambda row: (
            row["mode"],
            row["structure_key"],
            row["device_key"],
            row["source_row_id"],
            parameter_order[row["parameter"]],
        )
    )
    return {"device_ledger": ledger, **targets}


def _optional_float(value: Any) -> float | None:
    if value is None or str(value).strip().casefold() in {"", "nan", "none", "null"}:
        return None
    return float(value)


def _difference_row(
    comparison: str,
    candidate: Mapping[str, Any],
    reference_value: float | None,
    reference_n_valid: Any = None,
    reference_n_coords: Any = None,
) -> dict[str, Any]:
    candidate_value = _optional_float(candidate["median"])
    if reference_value is None or candidate_value is None:
        changed = reference_value != candidate_value
        delta = None
    else:
        changed = not math.isclose(reference_value, candidate_value, rel_tol=1e-12, abs_tol=1e-12)
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


def build_aggregation_differences(
    primary: Sequence[Mapping[str, Any]],
    sensitivity: Sequence[Mapping[str, Any]],
    old_coordinate_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Describe old/new and primary/sensitivity differences without changing labels."""
    old_by_structure = {str(row["structure_id"]): row for row in old_coordinate_rows}
    primary_by_key = {
        (row["mode"], row["structure_key"], row["parameter"]): row for row in primary
    }
    differences = []
    for row in primary:
        old = old_by_structure.get(str(row.get("p3_structure_id")))
        parameter = row["parameter"]
        differences.append(
            _difference_row(
                "primary_vs_old_coordinate",
                row,
                _optional_float(old.get(parameter)) if old is not None else None,
                old.get(parameter + "_n") if old is not None else None,
                old.get(parameter + "_n_coords") if old is not None else None,
            )
        )
    for row in sensitivity:
        reference = primary_by_key[(row["mode"], row["structure_key"], row["parameter"])]
        differences.append(
            _difference_row(
                "sensitivity_vs_primary",
                row,
                _optional_float(reference["median"]),
                reference["n_valid"],
            )
        )
    return differences
