"""P11 viewer block: validated read-only import of extrapolation candidates from P11 products."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.optimization_data import sha256

P11_SCHEMA = "aln_optimization_p11_candidates_v1"


def verify_p11_products(root: Path) -> dict:
    manifest = json.loads((root / "outputs_manifest.json").read_text())
    hashes = {}
    for relative, digest in manifest.items():
        path = root / relative
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"P11 artifact checksum mismatch: {relative}")
        hashes[relative] = digest
    return hashes


def make_p11_candidates(p11_root: Path, catalog_ids: set[str]) -> dict:
    """Extract A/B candidate display entries; C-group rows stay out of the view by contract."""
    verification = json.loads((p11_root / "verification.json").read_text())
    if not verification.get("all_passed"):
        raise ValueError("P11 verification did not pass; refuse to import candidates")
    frame = pd.read_csv(p11_root / "candidates.csv")
    excluded = frame[frame.group.eq("C")]
    if len(excluded) != 1:
        raise ValueError("P11 candidates file must keep exactly the one C demo point")
    frame = frame[frame.group.isin(["A", "B"])].reset_index(drop=True)
    if len(frame) != 10:
        raise ValueError("P11 A/B candidate count mismatch")
    entries = []
    for row in frame.itertuples():
        if row.candidate_id in catalog_ids:
            raise ValueError(f"Candidate collides with measured catalog: {row.candidate_id}")
        values = [
            row.qp_prediction, row.qp_lower, row.qp_upper,
            row.k2eff_pct_prediction, row.k2eff_pct_lower, row.k2eff_pct_upper,
            row.p6c_qp, row.p6c_k2eff_pct,
        ]
        if not np.isfinite(np.array(values, dtype=float)).all():
            raise ValueError(f"Nonfinite candidate values: {row.candidate_id}")
        if not (0 < row.qp_lower < row.qp_upper and 0 < row.k2eff_pct_lower < row.k2eff_pct_upper):
            raise ValueError(f"Invalid interval order: {row.candidate_id}")
        entries.append(
            dict(
                candidate_id=row.candidate_id,
                group=row.group,
                eg_um=float(row.eg_um),
                fl_um=float(row.fl_um),
                area_um2=float(row.area_um2),
                has_pf=int(row.has_pf),
                branch_id=row.branch_id,
                qp=dict(
                    prediction=float(row.qp_prediction),
                    lower=float(row.qp_lower),
                    upper=float(row.qp_upper),
                ),
                k2eff_pct=dict(
                    prediction=float(row.k2eff_pct_prediction),
                    lower=float(row.k2eff_pct_lower),
                    upper=float(row.k2eff_pct_upper),
                ),
                p6c_qp=float(row.p6c_qp),
                p6c_k2eff_pct=float(row.p6c_k2eff_pct),
                p3_support=row.p3_support,
                extrapolation_axis=row.extrapolation_axis,
                extrapolation_distance=float(row.extrapolation_distance),
                qp_delta_vs_qp_baseline=float(row.qp_delta_vs_qp_baseline),
                k2_delta_vs_k2_baseline=float(row.k2_delta_vs_k2_baseline),
                risk_flags=row.risk_flags,
                recommendation=row.recommendation,
            )
        )
    return {
        "schema": P11_SCHEMA,
        "experimental": True,
        "frozen_config": False,
        "new_blind_test": False,
        "retrospective_evidence": True,
        "trusted_for_unmeasured_optimization": False,
        "verified_hypotheses_only": True,
        "targets": ["qp", "k2eff_pct"],
        "baselines": {
            "qp_champion": {"marks": "C6-1", "qp": 1395.957083, "k2eff_pct": 14.014150},
            "k2_champion": {"marks": "E1-1", "qp": 1179.625929, "k2eff_pct": 14.674470},
        },
        "evidence_doc": "docs/superpowers/plans/2026-10-26-surrogate-p11-results.md",
        "p11_root": str(p11_root.resolve()),
        "entries": entries,
    }
