"""Read-only input and version audit for P13 T01."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

ALLOWED_STATUSES = {"NOT_STARTED", "RUNNING", "PASS", "REJECTED", "BLOCKED", "FAILED"}
P12_SCHEMA = "aln_optimization_p12_candidates_v1"


class AuditError(RuntimeError):
    pass


class HashMismatchError(AuditError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def verify_expected_hash(path: Path, expected: str) -> None:
    if not path.is_file():
        raise HashMismatchError(f"Required input is missing: {path}")
    actual = sha256(path)
    if actual != expected:
        raise HashMismatchError(f"Hash mismatch for {path}: expected {expected}, actual {actual}")


def manifest_expectation(manifest_path: Path, relative: str) -> dict[str, str]:
    manifest = read_json(manifest_path)
    entries = manifest.get("files", manifest)
    if relative not in entries:
        raise AuditError(f"{manifest_path} does not list required file {relative}")
    info = entries[relative]
    expected = info["sha256"] if isinstance(info, dict) else info
    return {
        "manifest_path": str(manifest_path),
        "manifest_key": relative,
        "expected_sha256": expected,
    }


class InputRegistry:
    def __init__(self, aliases: dict[str, Path]):
        self.aliases = aliases
        self.records: list[dict] = []
        self._ids: set[str] = set()
        self._paths: set[Path] = set()

    def add(
        self,
        file_id: str,
        path: Path,
        alias: str,
        category: str,
        consumers: list[str],
        expectations: list[dict[str, str]] | None = None,
    ) -> None:
        path = path.resolve()
        if file_id in self._ids or path in self._paths:
            raise AuditError(f"Duplicate input registration: {file_id} / {path}")
        if alias not in self.aliases:
            raise AuditError(f"Unknown path alias: {alias}")
        if not path.is_file():
            raise HashMismatchError(f"Required input is missing: {path}")
        verifications = []
        for expectation in expectations or []:
            verify_expected_hash(path, expectation["expected_sha256"])
            verifications.append({**expectation, "matches": True})
        try:
            relative = path.relative_to(self.aliases[alias]).as_posix()
        except ValueError as error:
            raise AuditError(f"{path} is outside alias {alias}") from error
        self._ids.add(file_id)
        self._paths.add(path)
        self.records.append(
            {
                "file_id": file_id,
                "absolute_path": str(path),
                "relative_to": alias,
                "relative_path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "category": category,
                "consumers": consumers,
                "upstream_verifications": verifications,
                "verification": "actual_file_read_sha256_verified",
            }
        )

    def require_ids(self, file_ids: list[str]) -> None:
        missing = set(file_ids) - self._ids
        if missing:
            raise AuditError(f"Downstream handoff references unregistered IDs: {sorted(missing)}")


def read_decimal_baseline(structures_path: Path, split_paths: dict[str, Path]) -> dict:
    matches = []
    with structures_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (
                "E1-1" in row["marks"].split("|")
                and Decimal(row["eg_um"]) == Decimal("2")
                and Decimal(row["fl_um"]) == Decimal("0")
                and Decimal(row["area_um2"]) == Decimal("5500")
                and row["has_pf"] == "0"
                and row["ports"] == "S22"
            ):
                matches.append((reader.line_num, row))
    if len(matches) != 1:
        raise AuditError(f"Expected exactly one E1-1 baseline structure, found {len(matches)}")
    line_number, row = matches[0]
    baseline = Decimal(row["k2eff_pct"])
    scope_roles = []
    for scope, path in split_paths.items():
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            split_matches = [
                (reader.line_num, split_row)
                for split_row in reader
                if split_row["structure_id"] == row["structure_id"]
            ]
        if len(split_matches) != 1:
            raise AuditError(f"Baseline scope lookup failed for {scope}: {len(split_matches)} rows")
        split_line, split_row = split_matches[0]
        if split_row["design_group_id"] != row["design_group_id"]:
            raise AuditError(f"Baseline design group differs in {scope}")
        scope_roles.append(
            {
                "scope": scope,
                "role": split_row["role"],
                "split_absolute_path": str(path.resolve()),
                "split_sha256": sha256(path),
                "csv_line_number": split_line,
            }
        )
    primary = next(item for item in scope_roles if item["scope"] == "structure_holdout")
    return {
        "structure_id": row["structure_id"],
        "design_group_id": row["design_group_id"],
        "branch_id": row["branch_id"],
        "cohort": row["cohort"],
        "marks": row["marks"],
        "ports": row["ports"],
        "scope": primary["scope"],
        "role": primary["role"],
        "scope_roles": scope_roles,
        "csv_line_number": line_number,
        "record_identity": {
            "n_rows": int(row["n_rows"]),
            "n_coords": int(row["n_coords"]),
            "k2eff_pct_n": int(row["k2eff_pct_n"]),
            "k2eff_pct_n_coords": int(row["k2eff_pct_n_coords"]),
            "k2eff_pct_max_row_id": row["k2eff_pct_max_row_id"],
            "max_row_id_note": "Maximum individual row only; not the source of the typical value",
        },
        "source_absolute_path": str(structures_path.resolve()),
        "source_sha256": sha256(structures_path),
        "source_column": "k2eff_pct",
        "source_mode": "deembedded",
        "typical_statistic": "median of coordinate-block medians; coordinates equally weighted",
        "baseline_k2eff_pct": str(baseline),
        "threshold_k2eff_pct": str(baseline + Decimal("1")),
        "increment_percentage_points": "1",
        "numeric_unit": "percent number; 14.674470284168734 means 14.674470284168734%",
        "decimal_read": True,
        "display_rounding_used_for_threshold": False,
    }


def extract_browser_package(html_path: Path) -> dict:
    html = html_path.read_text()
    blocks = re.findall(
        r'<script\s+type="application/json"\s+id="data">(.*?)</script>', html, flags=re.DOTALL
    )
    if len(blocks) != 1:
        raise AuditError(f"Expected exactly one HTML browser-data block in {html_path}")
    return json.loads(blocks[0])


def audit_candidates(
    candidates_path: Path,
    browser_data_path: Path,
    html_path: Path,
    negative_controls_path: Path | None,
) -> dict:
    with candidates_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    groups = {row["group"] for row in rows}
    if not groups <= {"P11", "P12", "C"}:
        raise AuditError(f"Unexpected P12 candidate groups: {sorted(groups)}")
    view_rows = [row for row in rows if row["group"] in {"P11", "P12"}]
    control_rows = [row for row in rows if row["group"] == "C"]
    package = read_json(browser_data_path)
    block = package.get("p12_candidates")
    if not block or block.get("schema") != P12_SCHEMA:
        raise AuditError("Current browser package lacks the P12 candidate block")
    entries = block.get("entries", [])
    if block.get("n_entries") != len(entries):
        raise AuditError("P12 browser block n_entries does not match its entries")
    csv_ids = [row["candidate_id"] for row in view_rows]
    html_ids = [entry["candidate_id"] for entry in entries]
    if len(csv_ids) != len(set(csv_ids)) or set(csv_ids) != set(html_ids):
        raise AuditError("P12 CSV and browser candidate IDs differ")
    html_package = extract_browser_package(html_path)
    if html_package != package:
        raise AuditError("HTML embedded package differs from browser_data.json")
    reasons = {}
    if negative_controls_path is not None:
        with negative_controls_path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                key = tuple(row[name] for name in ("eg_um", "fl_um", "area_um2", "has_pf"))
                reasons[key] = row["reason"]
    controls = []
    for row in control_rows:
        key = tuple(row[name] for name in ("eg_um", "fl_um", "area_um2", "has_pf"))
        controls.append(
            {
                "candidate_id": row["candidate_id"],
                "group": row["group"],
                "reason": reasons.get(key, row.get("risk_flags", "")),
                "recommendation": row["recommendation"],
                "displayed": False,
            }
        )
    return {
        "total_rows": len(rows),
        "non_negative_control_rows": len(view_rows),
        "csv_displayable_rows": len(view_rows),
        "displayable_rows": len(entries),
        "negative_control_rows": len(control_rows),
        "p12_group_rows": sum(row["group"] == "P12" for row in rows),
        "p11_group_rows": sum(row["group"] == "P11" for row in rows),
        "non_negative_candidates": [
            {
                "candidate_id": row["candidate_id"],
                "group": row["group"],
                "origin": row["origin"],
            }
            for row in view_rows
        ],
        "negative_controls": controls,
        "browser_schema": block["schema"],
        "browser_n_entries": block["n_entries"],
        "browser_p12_root": block["p12_root"],
        "p11_block_present": "p11_candidates" in package,
        "html_package_matches_browser_data": True,
        "csv_browser_id_sets_match": True,
    }


def validate_input_document(path: Path) -> dict:
    document = read_json(path)
    if document.get("task_id") != "T01" or document.get("status") not in ALLOWED_STATUSES:
        raise AuditError("Invalid T01 input manifest identity or status")
    records = document.get("inputs", [])
    ids = [record["file_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise AuditError("Duplicate file IDs in input manifest")
    for record in records:
        verify_expected_hash(Path(record["absolute_path"]), record["sha256"])
    baseline = document["baseline"]
    if Decimal(baseline["threshold_k2eff_pct"]) - Decimal(
        baseline["baseline_k2eff_pct"]
    ) != Decimal("1"):
        raise AuditError("Baseline threshold is not exactly one percentage point above baseline")
    for task, handoff in document["downstream_handoff"].items():
        missing = set(handoff["file_ids"]) - set(ids)
        if missing:
            raise AuditError(f"{task} handoff has unregistered file IDs: {sorted(missing)}")
    return {
        "input_count": len(records),
        "task_handoffs": sorted(document["downstream_handoff"]),
        "baseline_k2eff_pct": baseline["baseline_k2eff_pct"],
        "threshold_k2eff_pct": baseline["threshold_k2eff_pct"],
    }


def validate_conflicts(path: Path) -> dict:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise AuditError("Version conflict register is empty")
    ids = [row["conflict_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise AuditError("Duplicate conflict IDs")
    for row in rows:
        if row["status"] not in ALLOWED_STATUSES:
            raise AuditError(f"Invalid conflict status: {row['status']}")
        if not row["explanation"] or not row["disposition"]:
            raise AuditError(f"Unexplained version conflict: {row['conflict_id']}")
    return {"conflict_count": len(rows), "statuses": sorted({row["status"] for row in rows})}


def validate_artifact(run: Path) -> dict:
    manifest = read_json(run / "audit/artifact_manifest.json")
    if manifest.get("task_id") != "T01" or manifest.get("status") not in ALLOWED_STATUSES:
        raise AuditError("Invalid artifact manifest identity or status")
    for output in manifest["outputs"]:
        path = run / output["path"]
        verify_expected_hash(path, output["sha256"])
        if output.get("bytes") is not None and path.stat().st_size != output["bytes"]:
            raise AuditError(f"Output byte count mismatch: {path}")
    for source, expected in manifest["source_sha256"].items():
        verify_expected_hash(Path(source), expected)
    input_document = read_json(run / "audit/input_manifest.json")
    registered = {record["file_id"]: record["sha256"] for record in input_document["inputs"]}
    if manifest["inputs"] != registered:
        raise AuditError("Artifact manifest input IDs/hashes differ from input_manifest.json")
    return {
        "status": manifest["status"],
        "outputs_verified": len(manifest["outputs"]),
        "inputs_registered": len(registered),
        "sources_verified": len(manifest["source_sha256"]),
    }


class InferenceMismatchError(AuditError):
    pass


def verify_numeric_match(actual, expected, label: str, atol=1e-12, rtol=1e-12) -> dict:
    import numpy as np

    actual_array = np.asarray(actual, dtype=float)
    expected_array = np.asarray(expected, dtype=float)
    if actual_array.shape != expected_array.shape or actual_array.size == 0:
        raise InferenceMismatchError(f"inference/code mismatch: invalid shape for {label}")
    if not np.isfinite(actual_array).all() or not np.isfinite(expected_array).all():
        raise InferenceMismatchError(f"inference/code mismatch: non-finite values for {label}")
    differences = np.abs(actual_array - expected_array)
    relative = differences / np.maximum(np.abs(expected_array), np.finfo(float).tiny)
    result = {
        "label": label,
        "n": int(actual_array.size),
        "max_abs_difference": float(differences.max()),
        "max_relative_difference": float(relative.max()),
        "atol": atol,
        "rtol": rtol,
        "all_close": bool(np.allclose(actual_array, expected_array, atol=atol, rtol=rtol)),
    }
    if not result["all_close"]:
        raise InferenceMismatchError(f"inference/code mismatch: {label}: {result}")
    return result


def decompose_trend_gp(model, frame) -> dict:
    import numpy as np

    from app.ml.optimization_data import FEATURES

    transformed = model.transform(frame)
    residual_log, std = model.gp.predict_log(transformed)
    trend_log = np.zeros(len(frame), dtype=float)
    for branch, (low, scale, trend) in model.trends.items():
        where = np.flatnonzero(transformed.branch_id.eq(branch).to_numpy())
        if len(where):
            trend_log[where] = trend.predict(
                (transformed.iloc[where][list(FEATURES)].to_numpy() - low) / scale
            )
    total_log, total_std = model.predict_log(frame)
    verify_numeric_match(total_log, residual_log + trend_log, "GP trend/residual sum")
    verify_numeric_match(total_std, std, "GP residual standard deviation")
    return {
        "trend_log": trend_log,
        "residual_log": residual_log,
        "total_log": total_log,
        "std": std,
        "trend_only": np.exp(trend_log),
        "residual_factor": np.exp(residual_log),
        "prediction": np.exp(total_log),
    }


def make_geometry_frame(points):
    import numpy as np
    import pandas as pd

    from app.ml.optimization_data import branch_key

    frame = pd.DataFrame(points, columns=["eg_um", "fl_um", "area_um2", "has_pf"])
    if frame.empty:
        raise AuditError("Empty geometry profile")
    values = frame.to_numpy(float)
    if not np.isfinite(values).all():
        raise AuditError("Non-finite audit geometry")
    if (frame[["eg_um", "fl_um"]].to_numpy(float) < 0).any():
        raise AuditError("Negative EG/FL is not allowed")
    if (frame.area_um2 <= 0).any() or not frame.has_pf.isin([0, 1]).all():
        raise AuditError("Invalid area or PF in audit geometry")
    frame["branch_id"] = [
        branch_key(row.eg_um, row.fl_um, int(row.has_pf)) for row in frame.itertuples()
    ]
    return frame.reset_index(drop=True)


def nine_point_neighborhood(eg: float, fl: float) -> list[tuple[float, float]]:
    points = []
    for eg_delta in (Decimal("-0.025"), Decimal("0"), Decimal("0.025")):
        for fl_delta in (Decimal("-0.025"), Decimal("0"), Decimal("0.025")):
            next_eg = Decimal(str(eg)) + eg_delta
            next_fl = Decimal(str(fl)) + fl_delta
            if next_fl < 0:
                raise AuditError("P1203 audit neighborhood must not cross negative FL")
            points.append((float(next_eg), float(next_fl)))
    if len(points) != 9:
        raise AuditError("Neighborhood must contain exactly nine points")
    return points


def decimal_delta(value, reference) -> str:
    return str(Decimal(str(value)) - Decimal(str(reference)))
