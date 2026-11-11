"""Offline, provenance-preserving scalar dataset for structure optimization (P1/P2).

The original workbooks and personal database are never modified. Unresolved geometry
is quarantined, not imputed. Raw measurements are a paired diagnostic only.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from app.core.filename import parse_filename
from app.core.mapping import MappingEntry, get_area_for_port, load_mapping

TARGETS = ("k2eff_pct", "qs", "qp", "bodeq_fitted")
METRICS = {
    "k2eff_pct": "k2eff(%)",
    "qs": "Qs",
    "qp": "Qp",
    "bodeq_fitted": "BodeQ_fitted",
    "qs_bodeq": "Qs_BodeQ",
    "qp_bodeq": "Qp_BodeQ",
    "bodeq_smooth": "BodeQ_smooth",
    "fs_ghz": "fs(GHz)",
    "fp_ghz": "fp(GHz)",
}
FEATURES = ("eg_um", "fl_um", "area_um2")
GEOMETRY = ("eg_um", "fl_um", "area_um2", "has_pf")
STANDARD_COHORT = "standard_eg_fl_unrecorded_ag"
_NUM = r"\d+(?:\.\d+)?"
_STANDARD_RE = re.compile(rf"EG\s*{_NUM}\s+FL\s*{_NUM}\s+\d+\s*&\s*\d+", re.IGNORECASE)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def structure_key(values: tuple | list) -> str:
    """Stable key; absent AG is a cohort condition, never a numeric feature."""
    token = STANDARD_COHORT + "|" + "|".join(format(float(v), ".12g") for v in values)
    return "s_" + hashlib.sha256(token.encode()).hexdigest()[:16]


def branch_key(eg: float, fl: float, pf: int) -> str:
    return f"pf{pf}_eg{int(eg > 0)}_fl{int(fl > 0)}"


def cohort(description: str) -> str:
    stripped = re.sub(r"(?:\+PF\b|\bPF\b)", "", description, flags=re.IGNORECASE).strip()
    if _STANDARD_RE.fullmatch(stripped):
        return STANDARD_COHORT
    for pattern, name in (
        (r"\bNo\s+AG\b", "no_ag_unresolved"),
        (r"C\s*>\s*B", "c_gt_b_unresolved"),
        (r"\bBASIC\b", "basic_unresolved"),
        (r"\b(?:AG|Airgap)\b", "ag_variant_unresolved"),
    ):
        if re.search(pattern, description, re.IGNORECASE):
            return name
    return "incomplete_or_single_area_unresolved"


def canonical_device(name: str) -> str:
    return re.sub(r"_de(?=\.s1p$)", "", name, flags=re.IGNORECASE)


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def normalize_workbook(path: Path, mode: str, mapping: dict[str, MappingEntry]) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Sheet1")
    required = {"original_filename", "EG", "FL", "PF", "Area(um2)", *METRICS.values()}
    if missing := required - set(df.columns):
        raise ValueError(f"Missing workbook columns: {sorted(missing)}")
    records = []
    for offset, row in enumerate(df.to_dict("records"), 2):
        name = str(row["original_filename"])
        parsed = parse_filename(name)
        entry = mapping.get(parsed.mark)
        reasons = []
        if parsed.is_calibration:
            reasons.append("calibration_not_target")
        if entry is None:
            reasons.append("mapping_not_found")
        if parsed.port not in ("S11", "S22"):
            reasons.append("port_unknown")
        if parsed.coord is None:
            reasons.append("coordinate_unknown")
        expected_area = get_area_for_port(entry, parsed.port) if entry and parsed.port else None
        kind = cohort(entry.description) if entry else "unmapped"
        if kind != STANDARD_COHORT:
            reasons.append("unresolved_cohort")
        if expected_area is None:
            reasons.append("port_area_unknown")
        if entry and (entry.eg is None or entry.fl is None):
            reasons.append("eg_or_fl_unknown")
        if entry:
            comparisons = [("EG", entry.eg), ("FL", entry.fl), ("Area(um2)", expected_area)]
            for source, expected in comparisons:
                actual = _number(row[source])
                if expected is not None and np.isfinite(actual):
                    if not np.isclose(actual, expected, atol=1e-6, rtol=0):
                        reasons.append(
                            "area_conflict" if source == "Area(um2)" else source + "_conflict"
                        )
            pf_source = str(row["PF"]).strip().upper()
            if pf_source in ("Y", "N") and (pf_source == "Y") != entry.has_pf:
                reasons.append("geometry_pf_conflict")
        for col, expected in (("X", parsed.x), ("Y", parsed.y)):
            actual = _number(row.get(col))
            if expected is not None and np.isfinite(actual) and actual != expected:
                reasons.append("coordinate_conflict")
        canonical = canonical_device(name)
        rec = {
            "row_id": f"{mode}:{offset}",
            "source_path": str(path.resolve()),
            "source_sheet": "Sheet1",
            "excel_row": offset,
            "mode": mode,
            "original_filename": name,
            "display_name": row.get("display_name"),
            "device_id": canonical,
            "physical_id": re.sub(r"^S(?:11|22)_", "", canonical, flags=re.IGNORECASE),
            "mark": parsed.mark,
            "port": parsed.port,
            "coord": parsed.coord,
            "description": entry.description if entry else None,
            "cohort": kind,
            "eg_um": entry.eg if entry else None,
            "fl_um": entry.fl if entry else None,
            "ag_um": entry.ag if entry else None,
            "ag_status": "unrecorded" if entry and entry.ag is None else "mapping_explicit",
            "area_um2": expected_area,
            "has_pf": int(entry.has_pf) if entry else None,
            "source_eg": row["EG"],
            "source_fl": row["FL"],
            "source_ag": row.get("AG"),
            "source_area_um2": row["Area(um2)"],
            "source_geometry_pf": row["PF"],
            "filename_has_fail": parsed.pf == "Y",
            "geometry_eligible": not reasons,
            "exclusion_reasons": ";".join(sorted(set(reasons))),
        }
        for dest, source in METRICS.items():
            rec[dest] = _number(row[source])
        fs, fp = rec["fs_ghz"], rec["fp_ghz"]
        freq_ok = np.isfinite(fs) and np.isfinite(fp) and 0 < fs < fp
        rec["frequency_valid"] = bool(freq_ok)
        k_formula = 100 * np.pi**2 / 4 * (fs / fp) * ((fp - fs) / fp) if freq_ok else np.nan
        rec["k2_formula_delta_pct"] = rec["k2eff_pct"] - k_formula
        for target in TARGETS:
            value = rec[target]
            ok = freq_ok and np.isfinite(value) and value > 0
            if target == "k2eff_pct":
                ok = ok and value < 100 and abs(rec["k2_formula_delta_pct"]) <= 0.01
            rec["valid_" + target] = bool(ok)
        if rec["geometry_eligible"]:
            rec["structure_id"] = structure_key([rec[c] for c in GEOMETRY])
            rec["branch_id"] = branch_key(rec["eg_um"], rec["fl_um"], rec["has_pf"])
        else:
            rec["structure_id"] = None
            rec["branch_id"] = None
        records.append(rec)
    result = pd.DataFrame(records)
    if result["device_id"].duplicated().any():
        raise ValueError(f"Duplicate canonical devices in {path}")
    return result


def assign_design_groups(records: pd.DataFrame) -> pd.DataFrame:
    """Connected components of structure, mark and physical DUT, across both modes."""
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    for row in records.loc[records.geometry_eligible].itertuples():
        sid = find(row.structure_id)
        for other in ("mark:" + row.mark, "dut:" + row.physical_id):
            root = find(other)
            if sid != root:
                parent[root] = sid
    components: dict[str, list[str]] = {}
    for sid in records.structure_id.dropna().unique():
        components.setdefault(find(sid), []).append(sid)
    groups = {}
    for members in components.values():
        token = "|".join(sorted(members))
        gid = "d_" + hashlib.sha256(token.encode()).hexdigest()[:16]
        groups.update(dict.fromkeys(members, gid))
    result = records.copy()
    result["design_group_id"] = result.structure_id.map(groups)
    return result


def aggregate_structures(records: pd.DataFrame, n_bootstrap: int = 400) -> pd.DataFrame:
    valid = records[(records["mode"] == "deembedded") & records.geometry_eligible]
    rows = []
    for sid, group in valid.groupby("structure_id", sort=True):
        head = group.iloc[0]
        rec = {c: head[c] for c in (*GEOMETRY, "cohort", "branch_id", "design_group_id")}
        rec.update(structure_id=sid, n_rows=len(group), n_coords=group.coord.nunique())
        rec["marks"] = "|".join(sorted(group.mark.unique()))
        rec["ports"] = "|".join(sorted(group.port.unique()))
        for target in TARGETS:
            usable = group[group["valid_" + target]]
            vals = usable[target].to_numpy(float)
            blocks = usable.groupby("coord")[target].median().to_numpy(float)
            rec[target + "_n"] = len(vals)
            rec[target + "_n_coords"] = len(blocks)
            for suffix in ("", "_row_median", "_p10", "_p90", "_maximum", "_ci_low", "_ci_high"):
                rec[target + suffix] = np.nan
            rec[target + "_max_row_id"] = ""
            rec[target + "_position_mad"] = np.nan
            if len(blocks):
                median = float(np.median(blocks))
                seed = int(hashlib.sha256((sid + target).encode()).hexdigest()[:8], 16)
                rng = np.random.default_rng(seed)
                draws = np.median(rng.choice(blocks, (n_bootstrap, len(blocks))), axis=1)
                lo, hi = np.quantile(draws, [0.025, 0.975])
                rec.update(
                    {
                        target: median,
                        target + "_row_median": float(np.median(vals)),
                        target + "_p10": float(np.quantile(vals, 0.1)),
                        target + "_p90": float(np.quantile(vals, 0.9)),
                        target + "_maximum": float(np.max(vals)),
                        target + "_ci_low": float(lo),
                        target + "_ci_high": float(hi),
                        target + "_position_mad": float(np.median(np.abs(blocks - median))),
                        target + "_max_row_id": usable.loc[usable[target].idxmax(), "row_id"],
                    }
                )
        for target in ("fs_ghz", "fp_ghz", "qs_bodeq", "qp_bodeq", "bodeq_smooth"):
            good = group[group.frequency_valid & np.isfinite(group[target]) & (group[target] > 0)]
            rec[target] = float(good.groupby("coord")[target].median().median())
        rows.append(rec)
    if not rows:
        raise ValueError("No unambiguous structures remain")
    return pd.DataFrame(rows).sort_values("structure_id").reset_index(drop=True)


def coverage_grid(structures: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for branch, group in structures.groupby("branch_id"):
        counts = group.set_index("structure_id").n_rows.to_dict()
        pf = int(group.has_pf.iloc[0])
        for dims in itertools.product(*(sorted(group[c].unique()) for c in FEATURES)):
            sid = structure_key([*dims, pf])
            rows.append(
                {
                    "branch_id": branch,
                    **dict(zip(FEATURES, dims, strict=True)),
                    "has_pf": pf,
                    "structure_id": sid,
                    "n_rows": int(counts.get(sid, 0)),
                }
            )
    return pd.DataFrame(rows)


def write_empirical_outputs(records: pd.DataFrame, structures: pd.DataFrame, out: Path) -> dict:
    summary = {}
    for target in TARGETS:
        ranked = structures[structures[target + "_n_coords"] >= 10].sort_values(
            [target, "structure_id"], ascending=[False, True]
        )
        ranked.to_csv(out / f"rank_{target}.csv", index=False)
        individuals = (
            records[
                (records["mode"] == "deembedded")
                & records.geometry_eligible
                & records["valid_" + target]
            ]
            .nlargest(20, target)
            .copy()
        )
        individuals["curve_review_status"] = "not_reviewed_not_a_verified_design_optimum"
        individuals.to_csv(out / f"individual_maxima_{target}.csv", index=False)
        best = ranked.iloc[0]
        summary[target] = {
            c: best[c].item() if isinstance(best[c], np.generic) else best[c]
            for c in (
                "structure_id",
                *GEOMETRY,
                target,
                target + "_n_coords",
                target + "_ci_low",
                target + "_ci_high",
                "marks",
            )
        }
    write_json(out / "empirical_best.json", summary)
    return summary


def prepare_data(de_path: Path, raw_path: Path, mapping_path: Path, out: Path) -> dict:
    mapping = load_mapping(mapping_path)
    if not mapping:
        raise ValueError("Empty mapping")
    frames = [
        normalize_workbook(de_path, "deembedded", mapping),
        normalize_workbook(raw_path, "raw", mapping),
    ]
    records = assign_design_groups(pd.concat(frames, ignore_index=True))
    structures = aggregate_structures(records)
    records.to_csv(out / "records.csv.gz", index=False, compression="gzip")
    records[~records.geometry_eligible].to_csv(out / "quarantine.csv.gz", index=False)
    structures.to_csv(out / "structures.csv", index=False)
    coverage_grid(structures).to_csv(out / "coverage.csv", index=False)
    paired = frames[0].merge(
        frames[1],
        on="device_id",
        suffixes=("_de", "_raw"),
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    pair_rows = paired[["device_id", "row_id_de", "row_id_raw", "_merge"]].copy()
    pairs = {}
    for target in TARGETS:
        mask = (
            paired.geometry_eligible_de.eq(True)
            & paired.geometry_eligible_raw.eq(True)
            & paired["valid_" + target + "_de"].eq(True)
            & paired["valid_" + target + "_raw"].eq(True)
        )
        delta = paired[target + "_de"] - paired[target + "_raw"]
        pair_rows[target + "_delta_de_minus_raw"] = delta.where(mask)
        vals = delta[mask]
        pairs[target] = {
            "n_valid_pairs": len(vals),
            "median_delta": float(vals.median()),
            "p10_delta": float(vals.quantile(0.1)),
            "p90_delta": float(vals.quantile(0.9)),
        }
    pair_rows.to_csv(out / "paired_comparison.csv.gz", index=False)
    summary = {
        "mapping_entries": len(mapping),
        "sources": {},
        "pairing": {
            "common": int(paired._merge.eq("both").sum()),
            "de_only": int(paired._merge.eq("left_only").sum()),
            "raw_only": int(paired._merge.eq("right_only").sum()),
            "metrics": pairs,
        },
        "n_structures": len(structures),
        "n_design_groups": structures.design_group_id.nunique(),
        "branch_structure_counts": structures.branch_id.value_counts().to_dict(),
        "ag_policy": "unrecorded; omitted from features; no AG optimization",
        "typical_statistic": "median of coordinate-block medians; all valid repeats retained",
        "ranking_minimum_coordinates": 10,
        "bootstrap_replicates": 400,
    }
    for mode, group in records.groupby("mode"):
        reasons = Counter(r for text in group.exclusion_reasons for r in text.split(";") if r)
        summary["sources"][mode] = {
            "n_rows": len(group),
            "n_geometry_eligible": int(group.geometry_eligible.sum()),
            "n_quarantined": int((~group.geometry_eligible).sum()),
            "reasons": dict(reasons),
            "cohort_counts": group.cohort.value_counts().to_dict(),
            "target_valid_in_eligible": {
                t: int((group.geometry_eligible & group["valid_" + t]).sum()) for t in TARGETS
            },
        }
    write_json(out / "data_audit.json", summary)
    write_empirical_outputs(records, structures, out)
    return summary
