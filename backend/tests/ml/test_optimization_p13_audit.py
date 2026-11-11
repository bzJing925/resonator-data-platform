import csv
import json
from pathlib import Path

import pytest

from app.ml.optimization_p13.audit import (
    AuditError,
    HashMismatchError,
    audit_candidates,
    extract_browser_package,
    read_decimal_baseline,
    verify_expected_hash,
)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_decimal_baseline_preserves_csv_precision_and_scope(tmp_path: Path) -> None:
    structures = tmp_path / "structures.csv"
    fields = [
        "structure_id",
        "design_group_id",
        "branch_id",
        "cohort",
        "marks",
        "ports",
        "eg_um",
        "fl_um",
        "area_um2",
        "has_pf",
        "n_rows",
        "n_coords",
        "k2eff_pct_n",
        "k2eff_pct_n_coords",
        "k2eff_pct",
        "k2eff_pct_max_row_id",
    ]
    row = {
        "structure_id": "s_baseline",
        "design_group_id": "d_baseline",
        "branch_id": "pf0_eg1_fl0",
        "cohort": "standard_eg_fl_unrecorded_ag",
        "marks": "E1-1",
        "ports": "S22",
        "eg_um": "2.0",
        "fl_um": "0.0",
        "area_um2": "5500.0",
        "has_pf": "0",
        "n_rows": "50",
        "n_coords": "50",
        "k2eff_pct_n": "50",
        "k2eff_pct_n_coords": "50",
        "k2eff_pct": "14.674470284168734",
        "k2eff_pct_max_row_id": "deembedded:28197",
    }
    write_csv(structures, fields, [row])
    split = tmp_path / "split_manifest.csv"
    write_csv(
        split,
        ["structure_id", "design_group_id", "role"],
        [{"structure_id": "s_baseline", "design_group_id": "d_baseline", "role": "train"}],
    )

    result = read_decimal_baseline(structures, {"structure_holdout": split})

    assert result["baseline_k2eff_pct"] == "14.674470284168734"
    assert result["threshold_k2eff_pct"] == "15.674470284168734"
    assert result["scope"] == "structure_holdout"
    assert result["role"] == "train"
    assert result["csv_line_number"] == 2
    assert result["scope_roles"][0]["csv_line_number"] == 2


def test_candidate_audit_counts_controls_and_html_entries(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.csv"
    rows = [
        {
            "candidate_id": "P1200_example",
            "group": "P12",
            "origin": "peak",
            "eg_um": "3.0",
            "fl_um": "0.025",
            "area_um2": "5500.0",
            "has_pf": "0",
            "recommendation": "待实测验证",
        },
        {
            "candidate_id": "P1101_example",
            "group": "P11",
            "origin": "P11",
            "eg_um": "2.5",
            "fl_um": "0.0",
            "area_um2": "5500.0",
            "has_pf": "0",
            "recommendation": "保留对照",
        },
        {
            "candidate_id": "C02_example",
            "group": "C",
            "origin": "negative_control",
            "eg_um": "1.0",
            "fl_um": "2.5",
            "area_um2": "5500.0",
            "has_pf": "0",
            "recommendation": "不推荐（负面对照）",
        },
    ]
    fields = list(rows[0])
    write_csv(candidates, fields, rows)
    controls = tmp_path / "negative_controls.csv"
    write_csv(
        controls,
        ["eg_um", "fl_um", "area_um2", "has_pf", "reason", "note"],
        [
            {
                "eg_um": "1.0",
                "fl_um": "2.5",
                "area_um2": "5500.0",
                "has_pf": "0",
                "reason": "FL 上边界失效史",
                "note": "不进图",
            }
        ],
    )
    entries = [{"candidate_id": row["candidate_id"]} for row in rows[:2]]
    package = {
        "p12_candidates": {
            "schema": "aln_optimization_p12_candidates_v1",
            "n_entries": 2,
            "p12_root": "/frozen/p12",
            "entries": entries,
        }
    }
    browser_data = tmp_path / "browser_data.json"
    browser_data.write_text(json.dumps(package))
    html = tmp_path / "viewer.html"
    html.write_text(
        '<script type="application/json" id="data">'
        + json.dumps(package).replace("<", "\\u003c")
        + "</script>"
    )

    result = audit_candidates(candidates, browser_data, html, controls)

    assert result["total_rows"] == 3
    assert result["non_negative_control_rows"] == 2
    assert result["displayable_rows"] == 2
    assert result["negative_control_rows"] == 1
    assert result["negative_controls"][0]["reason"] == "FL 上边界失效史"
    assert result["html_package_matches_browser_data"] is True


def test_candidate_audit_rejects_html_package_mismatch(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.csv"
    write_csv(candidates, ["candidate_id", "group"], [])
    package = {
        "p12_candidates": {
            "schema": "aln_optimization_p12_candidates_v1",
            "n_entries": 0,
            "p12_root": "/frozen/p12",
            "entries": [],
        }
    }
    browser_data = tmp_path / "browser_data.json"
    browser_data.write_text(json.dumps(package))
    html = tmp_path / "viewer.html"
    html.write_text('<script type="application/json" id="data">{}</script>')

    with pytest.raises(AuditError, match="HTML embedded package"):
        audit_candidates(candidates, browser_data, html, None)


def test_extract_browser_package_requires_unique_data_block(tmp_path: Path) -> None:
    html = tmp_path / "viewer.html"
    html.write_text("<html>No data block</html>")
    with pytest.raises(AuditError, match="exactly one"):
        extract_browser_package(html)


def test_expected_hash_mismatch_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("actual")
    with pytest.raises(HashMismatchError, match="input.txt"):
        verify_expected_hash(source, "0" * 64)
