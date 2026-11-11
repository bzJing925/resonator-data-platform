"""Read-only coordinate and paired-workbook audit; no label edits or model fits."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(p3, p6c, out):
    out.mkdir(parents=True, exist_ok=False)
    paths = [
        p3 / "data/records.csv.gz",
        p3 / "data/structures.csv",
        p3 / "peak_review/peak_curve_review.csv",
        p6c / "experiment/predictions.csv",
    ]
    hashes = {str(p.resolve()): digest(p) for p in paths}
    records, structures, review, predictions = [pd.read_csv(p) for p in paths]
    selected = predictions.loc[
        predictions.scope.eq("area_edges")
        & predictions.recipe.eq("trend_gp_log_area")
        & predictions.interval.eq("joint_scaled")
        & predictions.target.eq("qp")
        & predictions.area_um2.eq(700)
        & predictions.has_pf.eq(1)
    ]
    assert len(selected) == 8 and selected.structure_id.is_unique
    # All PF 700/900 structures provide explicit comparison cohorts, never training labels.
    cohort = structures.loc[structures.has_pf.eq(1) & structures.area_um2.isin([700, 900])]
    de = records.loc[
        records["mode"].eq("deembedded")
        & records.geometry_eligible
        & records.structure_id.isin(cohort.structure_id)
    ].copy()
    raw = records.loc[records["mode"].eq("raw")]
    pairs = de.merge(
        raw[["device_id", "structure_id", "qp", "valid_qp", "geometry_eligible"]],
        on="device_id",
        how="left",
        suffixes=("_de", "_raw"),
        validate="one_to_one",
        indicator=True,
    )
    pairs["pair_valid"] = (
        pairs._merge.eq("both")
        & pairs.geometry_eligible_raw.eq(True)
        & pairs.valid_qp_de
        & pairs.valid_qp_raw.eq(True)
        & pairs.structure_id_de.eq(pairs.structure_id_raw)
    )
    pairs["qp_delta_de_minus_raw"] = (pairs.qp_de - pairs.qp_raw).where(pairs.pair_valid)
    pairs["qp_ratio_de_over_raw"] = (pairs.qp_de / pairs.qp_raw).where(pairs.pair_valid)
    pairs.to_csv(out / "paired_records.csv", index=False)
    de.to_csv(out / "de_records.csv", index=False)
    summary, coordinates, deletions = [], [], []
    for row in cohort.itertuples():
        g = de.loc[de.structure_id.eq(row.structure_id)]
        valid = g.loc[g.valid_qp]
        block = valid.groupby("coord").qp.agg(["median", "size", "min", "max"])
        observed = block["median"].median()
        assert np.isclose(observed, row.qp, rtol=1e-12)
        leave = []
        for coord, item in block.iterrows():
            value = block.drop(index=coord)["median"].median()
            leave.append(value)
            coordinates.append(dict(structure_id=row.structure_id, coord=coord, **item.to_dict()))
            deletions.append(
                dict(structure_id=row.structure_id, removed_coord=coord, qp_without_coord=value)
            )
        nonfail = valid.loc[~valid.filename_has_fail].groupby("coord").qp.median()
        p = pairs.loc[pairs.structure_id_de.eq(row.structure_id) & pairs.pair_valid]
        pred = selected.loc[selected.structure_id.eq(row.structure_id)]
        result = dict(
            structure_id=row.structure_id,
            design_group_id=row.design_group_id,
            area_um2=row.area_um2,
            fl_um=row.fl_um,
            branch_id=row.branch_id,
            n_rows=len(g),
            n_valid=len(valid),
            n_coords=len(block),
            n_coords_multiple_records=int(block["size"].gt(1).sum()),
            n_fail=int(g.filename_has_fail.sum()),
            qp=observed,
            qp_coordinate_min=block["median"].min(),
            qp_coordinate_max=block["median"].max(),
            qp_leave_one_min=min(leave),
            qp_leave_one_max=max(leave),
            nonfail_coords=len(nonfail),
            qp_nonfail=nonfail.median(),
            n_valid_pairs=len(p),
            paired_delta_median=p.qp_delta_de_minus_raw.median(),
            paired_ratio_median=p.qp_ratio_de_over_raw.median(),
            paired_raw_coordinate_median=p.groupby("coord").qp_raw.median().median(),
            prior_raw_curve_reviews=int(review.structure_id.eq(row.structure_id).sum()),
            de_curve_verified=False,
            selected_failure=len(pred) == 1,
        )
        if len(pred):
            pr = pred.iloc[0]
            result.update(
                prediction=pr.prediction,
                lower=pr.lower,
                upper=pr.upper,
                original_covered=pr.lower <= observed <= pr.upper,
                deletion_can_restore_coverage=any(pr.lower <= x <= pr.upper for x in leave),
                all_deletions_above_upper=min(leave) > pr.upper,
                nonfail_covered=(pr.lower <= nonfail.median() <= pr.upper)
                if len(nonfail)
                else None,
            )
        summary.append(result)
    result = pd.DataFrame(summary)
    result.to_csv(out / "structure_audit.csv", index=False)
    pd.DataFrame(coordinates).to_csv(out / "coordinate_blocks.csv", index=False)
    pd.DataFrame(deletions).to_csv(out / "leave_one_coordinate.csv", index=False)
    result.loc[result.selected_failure].to_csv(out / "eight_failures.csv", index=False)
    assert all(digest(Path(p)) == h for p, h in hashes.items())
    receipt = dict(
        inputs=hashes,
        source_sha256=digest(Path(__file__)),
        new_fit=False,
        labels_modified=False,
        independent_test=False,
        de_curves_reextracted=False,
        files={p.name: digest(p) for p in sorted(out.glob("*.csv"))},
    )
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--p3-root", type=Path, required=True)
    parser.add_argument("--p6c-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(audit(args.p3_root, args.p6c_root, args.out).to_string(index=False))
