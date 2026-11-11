"""P5 viewer D2 reference-layer block: validated read-only import from frozen P10 products."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.ml.optimization_data import sha256

D2_SCHEMA = "aln_optimization_d2_reference_v1"
SUBSET_GEOMETRY = {"eg_um": 0.0, "area_um2": 700.0, "has_pf": 1}


def verify_p10_products(root: Path) -> dict:
    manifest = json.loads((root / "outputs_manifest.json").read_text())
    hashes = {}
    for relative, digest in manifest.items():
        path = root / relative
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"P10 artifact checksum mismatch: {relative}")
        hashes[relative] = digest
    return hashes


def make_d2_reference(p10_root: Path, measured: pd.DataFrame) -> dict:
    """Extract the eight subset structures' D2 reference intervals; fail closed on any doubt."""
    small = pd.read_csv(p10_root / "experiment1_intervals/small_area_pf_qp.csv")
    selection = json.loads((p10_root / "selection_e1.json").read_text())
    verification = json.loads((p10_root / "verification.json").read_text())
    if not verification.get("all_passed"):
        raise ValueError("P10 verification did not pass; refuse to import reference intervals")
    entries = []
    catalog = measured.set_index("structure_id")
    expected_arms = {"D0_control", "D1_subset_hetero_interval", "D2_subset_c1_switch"}
    for interval in ("joint_global", "joint_scaled"):
        for structure_id, group in small[small.interval.eq(interval)].groupby("structure_id"):
            arms = group.set_index("arm")
            if set(arms.index) != expected_arms:
                raise ValueError(f"Incomplete D2 arm rows: {structure_id}")
    for structure_id, group in small[small.interval.eq("joint_scaled")].groupby("structure_id"):
        row = group.iloc[0]
        if structure_id not in catalog.index:
            raise ValueError(f"D2 structure not in measured catalog: {structure_id}")
        catalog_row = catalog.loc[structure_id]
        for key, expected in SUBSET_GEOMETRY.items():
            if float(catalog_row[key]) != expected:
                raise ValueError(f"D2 subset geometry mismatch: {structure_id}")
        arms = group.set_index("arm")
        global_rows = small[
            small.interval.eq("joint_global") & small.structure_id.eq(structure_id)
        ].set_index("arm")
        entry = {
            "structure_id": structure_id,
            "fl_um": float(row.fl_um),
            "qp_actual": float(row.actual),
        }
        for prefix, arm in (("c0", "D0_control"), ("d2", "D2_subset_c1_switch")):
            for method, table in (("scaled", arms), ("global", global_rows)):
                entry[f"{prefix}_{method}"] = [
                    float(table.loc[arm, "lower"]),
                    float(table.loc[arm, "upper"]),
                ]
        entries.append(entry)
    if len(entries) != 8 or len({e["structure_id"] for e in entries}) != 8:
        raise ValueError("D2 reference must cover exactly the eight subset structures")
    for entry in entries:
        for key in ("c0_scaled", "d2_scaled", "c0_global", "d2_global"):
            lo, hi = entry[key]
            if not (0 < lo < hi):
                raise ValueError(f"Invalid interval order: {entry['structure_id']} {key}")
    return {
        "schema": D2_SCHEMA,
        "experimental": True,
        "frozen_config": False,
        "new_blind_test": False,
        "retrospective_evidence": True,
        "trusted_for_unmeasured_optimization": False,
        "target": "qp",
        "subset_rule": "has_pf=1 且 eg_um=0 且 area_um2=700（PF · EG0 · 700 μm² 子集）",
        "selected_arm_p10": selection["selected"],
        "inner_subset_qp_coverage": {"D0_control": 0.5263, "D2_subset_c1_switch": 0.7368},
        "outer_subset_qp_coverage": {"D0_control": "0/8", "D2_subset_c1_switch": "6/8"},
        "evidence_doc": "docs/superpowers/plans/2026-10-26-surrogate-p10-results.md",
        "p10_root": str(p10_root.resolve()),
        "entries": entries,
    }
