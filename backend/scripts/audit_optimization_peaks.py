"""P2 descriptive peak and position checks, separate from model selection.

Selectively restore original RAW curves. This cannot verify the de-embedded Excel
peaks without its historical calibration/extraction recipe. No labels are changed.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import zipfile_deflate64  # noqa: F401  Register the project's existing Deflate64 support.

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.extract import extract_resonator_params
from app.core.touchstone import split_s2p_to_s1p
from app.ml.optimization_data import TARGETS, sha256, write_json


def audit(data_dir: Path, archive: Path, out: Path) -> dict:
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    files = out / "selective_raw_curves"
    files.mkdir()
    records = pd.read_csv(data_dir / "records.csv.gz")
    structures = pd.read_csv(data_dir / "structures.csv")
    measured = records[records["mode"].eq("deembedded") & records.geometry_eligible]
    raw = records[records["mode"].eq("raw")].set_index("device_id")
    selected = {}
    comparisons = []
    for target in TARGETS:
        ranked = structures[structures[target + "_n_coords"] >= 10].sort_values(
            [target, "structure_id"], ascending=[False, True]
        )
        leader = ranked.iloc[0]
        group = measured[measured.structure_id.eq(leader.structure_id)]
        representative = group.loc[(group[target] - leader[target]).abs().idxmin()]
        selected.setdefault(representative.row_id, []).append(target + ":best_structure_typical")
        maximum = measured.loc[measured[target].idxmax()]
        selected.setdefault(maximum.row_id, []).append(target + ":individual_maximum")
        a = group.groupby("coord")[target].median()
        for other in ranked.iloc[1:5].itertuples():
            b = (
                measured[measured.structure_id.eq(other.structure_id)]
                .groupby("coord")[target]
                .median()
            )
            pairs = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
            differences = (pairs.a - pairs.b).to_numpy()
            seed = int(hashlib.sha256((target + other.structure_id).encode()).hexdigest()[:8], 16)
            if len(pairs):
                draws = np.median(
                    np.random.default_rng(seed).choice(differences, (1000, len(differences))),
                    axis=1,
                )
                lo, hi = np.quantile(draws, [0.025, 0.975])
                delta = float(np.median(differences))
            else:
                lo = hi = delta = np.nan
            comparisons.append(
                {
                    "target": target,
                    "leader_structure_id": leader.structure_id,
                    "other_structure_id": other.structure_id,
                    "n_common_positions": len(pairs),
                    "median_paired_difference": delta,
                    "ci_low": lo,
                    "ci_high": hi,
                    "interpretation": "post_selection_descriptive_single_batch_only",
                }
            )
    pd.DataFrame(comparisons).to_csv(out / "top_rank_position_sensitivity.csv", index=False)
    curve_rows = []
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        for row in measured[measured.row_id.isin(selected)].itertuples():
            parent = re.sub(r"^S(?:11|22)_", "", row.device_id, flags=re.IGNORECASE)
            parent = re.sub(r"\.s1p$", ".s2p", parent, flags=re.IGNORECASE)
            info = {
                "de_row_id": row.row_id,
                "device_id": row.device_id,
                "structure_id": row.structure_id,
                "selection": "|".join(selected[row.row_id]),
                "archive_member": parent,
                "deembedded_curve_verified": False,
            }
            if parent not in names:
                info["status"] = "parent_not_found"
                curve_rows.append(info)
                continue
            path = files / Path(parent).name
            if not path.exists():
                path.write_bytes(z.read(parent))
            split = split_s2p_to_s1p(path, files / "S11", files / "S22")
            s1p = split.s11_path if row.port == "S11" else split.s22_path
            try:
                extracted = extract_resonator_params(s1p, skip_validation=True)
            except Exception as exc:
                info.update(status="raw_reextraction_failed", error=str(exc))
                curve_rows.append(info)
                continue
            info.update(status="raw_parent_restored_and_reextracted", raw_curve_sha256=sha256(path))
            counterpart = raw.loc[row.device_id] if row.device_id in raw.index else None
            for target in (*TARGETS, "fs_ghz", "fp_ghz"):
                current = extracted[target]
                info["reextracted_raw_" + target] = current
                info["source_de_" + target] = getattr(row, target)
                if counterpart is not None:
                    reference = counterpart[target]
                    info["source_raw_" + target] = reference
                    info["delta_reextracted_minus_source_raw_" + target] = current - reference
            curve_rows.append(info)
    curves = pd.DataFrame(curve_rows)
    curves.to_csv(out / "peak_curve_review.csv", index=False)
    summary = {
        "selected_devices": len(selected),
        "raw_parents_reextracted": int(
            curves.status.eq("raw_parent_restored_and_reextracted").sum()
        ),
        "deembedded_curve_verification": (
            "not performed; historical deembedding recipe not recovered"
        ),
        "ranking_scope": "observed scalar summaries; not a confirmed unique design optimum",
        "paired_position_intervals": "post-selection descriptive; no multiple-testing correction",
        "source_data_sha256": sha256(data_dir / "structures.csv"),
        "extraction_code_sha256": sha256(
            Path(__file__).resolve().parents[1] / "app/core/extract.py"
        ),
        "script_sha256": sha256(Path(__file__).resolve()),
    }
    write_json(out / "peak_audit_summary.json", summary)
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--zip", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    print(audit(args.data_dir, args.zip, args.out))


if __name__ == "__main__":
    main()
