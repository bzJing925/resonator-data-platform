"""Build the Stage-B all-device median datasets without training or B03 artifacts."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.device_median_v1 import (  # noqa: E402
    aggregate_measurements,
    build_aggregation_differences,
    join_primary_measurements,
)

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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(identifier: str, path: Path) -> dict[str, Any]:
    return {
        "id": identifier,
        "path": str(path.resolve()),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "sha256": file_sha256(path) if path.is_file() else None,
    }


def snapshot_inputs(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {identifier: file_record(identifier, path) for identifier, path in paths.items()}


def recheck_input_hashes(before: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    after = {
        identifier: file_record(identifier, Path(record["path"]))
        for identifier, record in before.items()
    }
    changed = [
        identifier
        for identifier in before
        if before[identifier]["sha256"] != after[identifier]["sha256"]
        or before[identifier]["bytes"] != after[identifier]["bytes"]
    ]
    if changed:
        raise RuntimeError(f"Input hash changed during build: {sorted(changed)}")
    return after


def read_records(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_audit(path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    reviews = []
    decisions = Counter()
    total = 0
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            total += 1
            decision = row.get("decision", "")
            decisions[decision] += 1
            if decision == "review":
                reviews.append(row)
    return reviews, {
        "rows": total,
        "decision_counts": dict(sorted(decisions.items())),
        "review_rows": len(reviews),
        "training_overlay_columns_used": False,
    }


def _slim_identity(row: dict[str, Any]) -> dict[str, Any]:
    association = row["structure_association"]
    return {
        "source": {
            "row_id": row["source"]["row_id"],
            "source_row_sha256": row["source"]["source_row_sha256"],
        },
        "device_key": row["device_key"],
        "structure_key": row["structure_key"],
        "mode": row["mode"],
        "batch": row.get("batch", ""),
        "port": row.get("port", ""),
        "is_canonical_measurement": row["is_canonical_measurement"],
        "canonical_source_row_id": row.get("canonical_source_row_id"),
        "alias_of_source_row_id": row.get("alias_of_source_row_id"),
        "fl0_review": {"flagged": row.get("fl0_review", {}).get("flagged", False)},
        "structure_association": {
            "p3_structure_id": association.get("p3_structure_id"),
            "p3_branch_id": association.get("p3_branch_id"),
            "p3_design_group_id": association.get("p3_design_group_id"),
            "geometry": {
                key: association["geometry"].get(key)
                for key in ("eg_um", "fl_um", "area_um2", "has_pf")
            },
            "ag": {"status": association["ag"]["status"]},
            "topology": {"status": association["topology"]["status"]},
            "cohort": {
                "geometry_eligible": association["cohort"]["geometry_eligible"],
                "id": association["cohort"]["id"],
                "exclusion_reasons": association["cohort"].get("exclusion_reasons", []),
            },
        },
    }


def read_identity(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Stream the large B01 JSON array and retain only fields needed by B02."""
    decoder = json.JSONDecoder()
    records = []
    with path.open(encoding="utf-8") as handle:
        buffer = ""
        marker = None
        while marker is None:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                raise ValueError("B01 identity has no records array")
            buffer += chunk
            marker = re.search(r'"records"\s*:\s*\[', buffer)
        header = json.loads(buffer[: marker.start()] + '"records":[]}')
        buffer = buffer[marker.end() :]
        finished = False
        while not finished:
            buffer = buffer.lstrip()
            if buffer.startswith(","):
                buffer = buffer[1:].lstrip()
            if buffer.startswith("]"):
                finished = True
                break
            while True:
                try:
                    value, end = decoder.raw_decode(buffer)
                    break
                except json.JSONDecodeError:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        raise ValueError("Truncated B01 identity records array") from None
                    buffer += chunk
            records.append(_slim_identity(value))
            buffer = buffer[end:]
    if header.get("record_count") != len(records):
        raise ValueError("B01 identity record_count does not match its records array")
    return header, records


def read_old_coordinate_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def prepare_output_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise FileExistsError(f"Refusing non-empty output directory: {path}")
        return
    path.mkdir(parents=True, exist_ok=False)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    list_fields = {"source_device_keys", "reasons"}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        for source in rows:
            row = dict(source)
            for field in list_fields & set(row):
                row[field] = json.dumps(row[field], ensure_ascii=False, separators=(",", ":"))
            writer.writerow(row)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _target_summary(rows: list[dict[str, Any]], parameters: list[str]) -> dict[str, Any]:
    result = {}
    for parameter in parameters:
        selected = [row for row in rows if row["parameter"] == parameter]
        result[parameter] = {
            "structures": len(selected),
            "n_total": sum(row["n_total"] for row in selected),
            "n_valid": sum(row["n_valid"] for row in selected),
            "n_missing": sum(row["n_missing"] for row in selected),
            "n_invalid": sum(row["n_invalid"] for row in selected),
            "all_missing_structures": sum(row["n_valid"] == 0 for row in selected),
            "low_support_structures": sum(row["low_support"] for row in selected),
        }
    return result


def _difference_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
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


def build(args: argparse.Namespace) -> None:
    paths = {
        "records": args.records.resolve(),
        "identity": args.identity.resolve(),
        "audit": args.audit.resolve(),
        "protocol": args.protocol.resolve(),
    }
    old_path = paths["records"].parent / "structures.csv"
    if old_path.is_file():
        paths["old_coordinate_labels"] = old_path
    review_path = paths["identity"].parent / "review" / "independent_review.json"
    if review_path.is_file():
        paths["b01_independent_review"] = review_path
    before = snapshot_inputs(paths)
    protocol = json.loads(paths["protocol"].read_text(encoding="utf-8"))
    parameters = list(protocol["parameters"])
    if not parameters or len(set(parameters)) != len(parameters):
        raise ValueError("data protocol parameters must be a nonempty unique list")
    for identifier, expected in protocol.get("expected_input_sha256", {}).items():
        if identifier not in before or before[identifier]["sha256"] != expected:
            raise RuntimeError(f"Registered input hash mismatch: {identifier}")
    if protocol.get("require_independent_b01_review"):
        if "b01_independent_review" not in before:
            raise RuntimeError("Required B01 independent review is missing")
        review = json.loads(review_path.read_text(encoding="utf-8"))
        if review.get("acceptance_status") != "PASS" or not review.get("b02_authorized"):
            raise RuntimeError("B01 independent review has not authorized B02")

    prepare_output_directory(args.out)
    identity_header, identity_rows = read_identity(paths["identity"])
    unresolved = identity_header.get(
        "unresolved_identity_conflicts",
        identity_header.get("counts", {}).get("unresolved_identity_conflicts"),
    )
    if identity_header.get("identity_status") != "RESOLVED" or unresolved != 0:
        raise RuntimeError("B01 identity is unresolved")
    records = read_records(paths["records"])
    audit_rows, audit_census = read_audit(paths["audit"])
    joined = join_primary_measurements(records, identity_rows, audit_rows, parameters)
    if joined["identity_conflicts"]:
        raise RuntimeError(
            f"Identity/source conflicts block B02: {len(joined['identity_conflicts'])}"
        )
    for identifier, expected in protocol.get("expected_counts", {}).items():
        if joined["counts"].get(identifier) != expected:
            raise RuntimeError(
                f"Registered count mismatch for {identifier}: "
                f"expected {expected}, got {joined['counts'].get(identifier)}"
            )
    aggregated = aggregate_measurements(
        joined["measurements"],
        parameters,
        low_support_threshold=int(protocol.get("low_support_threshold", 5)),
    )
    old_rows = read_old_coordinate_rows(old_path)
    differences = build_aggregation_differences(
        aggregated["primary"], aggregated["sensitivity"], old_rows
    )

    outputs = {
        "device_ledger": args.out / "device_ledger.csv",
        "structure_targets_primary": args.out / "structure_targets_primary.csv",
        "structure_targets_sensitivity": args.out / "structure_targets_sensitivity.csv",
        "aggregation_differences": args.out / "aggregation_differences.csv",
        "exclusions": args.out / "exclusions.csv",
        "identity_conflicts": args.out / "identity_conflicts.json",
    }
    write_csv(outputs["device_ledger"], aggregated["device_ledger"], LEDGER_FIELDS)
    write_csv(outputs["structure_targets_primary"], aggregated["primary"], TARGET_FIELDS)
    write_csv(
        outputs["structure_targets_sensitivity"], aggregated["sensitivity"], TARGET_FIELDS
    )
    write_csv(outputs["aggregation_differences"], differences, DIFFERENCE_FIELDS)
    write_csv(outputs["exclusions"], joined["exclusions"], EXCLUSION_FIELDS)
    write_json(
        outputs["identity_conflicts"],
        {
            "schema": "aln_device_median_b02_identity_conflicts_v1",
            "status": "PASS",
            "upstream_unresolved_count": unresolved,
            "conflicts": joined["identity_conflicts"],
            "count": 0,
        },
    )

    counts = dict(joined["counts"])
    counts.update(
        {
            "device_ledger_rows": len(aggregated["device_ledger"]),
            "primary_target_rows": len(aggregated["primary"]),
            "sensitivity_target_rows": len(aggregated["sensitivity"]),
            "primary_low_support_rows": sum(row["low_support"] for row in aggregated["primary"]),
            "sensitivity_low_support_rows": sum(
                row["low_support"] for row in aggregated["sensitivity"]
            ),
            "fl0_sensitivity_masked_measurement_parameters": sum(
                row["sensitivity_reason"] == "fl0_qp_review_masked"
                for row in aggregated["device_ledger"]
            ),
            "old_coordinate_rows_compared_only": len(old_rows),
            "identity_conflicts": 0,
        }
    )
    after = recheck_input_hashes(before)
    manifest = {
        "schema": "aln_device_median_b02_manifest_v1",
        "task_id": "B02",
        "status": "PASS",
        "scientific_status": "REJECTED",
        "trusted": False,
        "parameters": parameters,
        "core_parameters": ["k2eff_pct", "qp", "qs", "bodeq_fitted"],
        "low_support_threshold": int(protocol.get("low_support_threshold", 5)),
        "aggregation": {
            "repeat_rule": "per-device per-parameter median of valid canonical repeats first",
            "structure_rule": "equal-weight all-device median per structure and parameter",
            "quartile_rule": "linear interpolation at (n-1)*p",
            "source_device_keys_rule": "valid contributing device keys, sorted",
            "missing_invalid_rule": "device-level invalid precedence only when no repeat is valid",
            "sensitivity_rule": "mask Qp only for FL0 review source rows",
        },
        "counts": counts,
        "target_statistics": {
            "primary": _target_summary(aggregated["primary"], parameters),
            "sensitivity": _target_summary(aggregated["sensitivity"], parameters),
        },
        "difference_summary": _difference_summary(differences),
        "audit_census": audit_census,
        "inputs": list(before.values()),
        "input_hashes_after": list(after.values()),
        "inputs_unchanged_before_after": True,
        "outputs": [file_record(identifier, path) for identifier, path in outputs.items()],
        "structures_csv_used_as_device_source": False,
        "old_coordinate_labels_role": "comparison_only_in_aggregation_differences",
        "fl0_overlay_role": "review_flags_only_not_training_values",
        "training_performed": False,
        "b03_protocol_or_splits_created": False,
    }
    write_json(args.out / "manifest.json", manifest)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--records", type=Path, required=True)
    build_parser.add_argument("--identity", type=Path, required=True)
    build_parser.add_argument("--audit", type=Path, required=True)
    build_parser.add_argument("--protocol", type=Path, required=True)
    build_parser.add_argument("--out", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build":
        args.out = args.out.resolve()
        build(args)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
