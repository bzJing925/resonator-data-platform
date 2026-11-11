"""Read-only paired 700 vs 900 um2 PF coordinate comparison; descriptive only, no causal claims."""

import argparse
import csv
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_data import sha256, write_json

COMPARE_TARGETS = ("qp", "qs", "k2eff_pct", "bodeq_fitted", "fs_ghz", "fp_ghz")
EXACT_TARGETS = ("qp", "qs", "k2eff_pct", "fs_ghz", "fp_ghz")
RATIO_TARGETS = ("qp", "qs", "bodeq_fitted")


def load(p6e, p6g):
    paths = [
        p6e / "final_audit/de_records.csv",
        p6e / "final_audit/structure_audit.csv",
        p6g / "audit/candidate_results.csv",
    ]
    hashes = {str(p.resolve()): sha256(p) for p in paths}
    return hashes, pd.read_csv(paths[0]), pd.read_csv(paths[1]), pd.read_csv(paths[2])


def cross_source_check(de, cand, out):
    """700 group: P6G original/archive recompute vs P6E labels; 900 group has labels only."""
    orig = cand.loc[cand.method.eq("original") & cand.order.eq("archive")].set_index("device_id")
    g700 = de.loc[de.area_um2.eq(700)].set_index("device_id")
    assert len(orig) == len(g700) == 125 and set(orig.index) == set(g700.index)
    rows = []
    for device_id, ref in g700.iterrows():
        new = orig.loc[device_id]
        row = dict(device_id=device_id, structure_id=ref.structure_id)
        for target in (*EXACT_TARGETS, "bodeq_fitted"):
            row[target + "_label"] = ref[target]
            row[target + "_p6g"] = new[target]
            row["delta_" + target] = new[target] - ref[target]
        rows.append(row)
    check = pd.DataFrame(rows)
    check.to_csv(out / "cross_source_check.csv", index=False)
    for target in EXACT_TARGETS:
        ok = np.isclose(check[target + "_p6g"], check[target + "_label"], rtol=1e-8, atol=1e-8)
        assert ok.all(), target
    stats = {
        "n_records": len(check),
        "exact_targets_matched": {t: 125 for t in EXACT_TARGETS},
        "qp_max_abs_delta": float(check.delta_qp.abs().max()),
        "bodeq_max_abs_delta": float(check.delta_bodeq_fitted.abs().max()),
        "bodeq_note": "bodeq_fitted not asserted; the ~1.2e-5 relative gap is tracked separately",
    }
    return stats


def build_inventory(de, audit):
    meta = audit.set_index("structure_id")
    rows = []
    for sid, g in de.groupby("structure_id"):
        assert g.coord.is_unique, sid
        first = g.iloc[0]
        m = meta.loc[sid]
        rows.append(
            dict(
                structure_id=sid,
                area_um2=first.area_um2,
                fl_um=first.fl_um,
                branch_id=first.branch_id,
                design_group_id=first.design_group_id,
                mark=";".join(sorted(g.mark.unique())),
                port=";".join(sorted(g.port.unique())),
                description=";".join(sorted(g.description.unique())),
                n_records=len(g),
                n_coords=g.coord.nunique(),
                coords=";".join(sorted(g.coord.unique())),
                qp_all_coord_median=g.qp.median(),
                prediction=m.prediction if "prediction" in m else None,
                lower=m.lower if "lower" in m else None,
                upper=m.upper if "upper" in m else None,
                original_covered=m.original_covered if "original_covered" in m else None,
            )
        )
    return pd.DataFrame(rows).sort_values(["area_um2", "fl_um"]).reset_index(drop=True)


def qp_median(values):
    return statistics.median(values) if values else None


def run(p6e, p6g, out):
    out.mkdir(parents=True, exist_ok=False)
    hashes, de, audit, cand = load(p6e, p6g)
    assert de.port.eq("S11").all() and set(de.area_um2.unique()) == {700.0, 900.0}
    cross = cross_source_check(de, cand, out)
    inv = build_inventory(de, audit)
    inv.to_csv(out / "structure_inventory.csv", index=False)
    by_sid = {sid: g.set_index("coord") for sid, g in de.groupby("structure_id")}
    s700 = inv.loc[inv.area_um2.eq(700)].set_index("fl_um")
    s900 = inv.loc[inv.area_um2.eq(900)].set_index("fl_um")
    fls = sorted(set(s700.index) & set(s900.index))
    assert len(fls) == len(s700) == len(s900) == 8

    group_rows, pair_rows, detail_rows, missing_rows = [], [], [], []
    unions = {}
    for area, frame in ((700.0, s700), (900.0, s900)):
        sets = [set(by_sid[sid].index) for sid in frame.structure_id]
        union = sorted(set().union(*sets))
        inter = sorted(set.intersection(*sets))
        unions[area] = set(union)
        group_rows.append(
            dict(
                area_um2=area,
                n_structures=len(frame),
                n_records=int(frame.n_records.sum()),
                fl_list=";".join(format(f, ".12g") for f in sorted(frame.index)),
                marks=";".join(sorted(frame.mark)),
                ports=";".join(sorted(frame.port.unique())),
                n_design_groups=frame.design_group_id.nunique(),
                design_group_ids=";".join(sorted(frame.design_group_id)),
                coord_union_n=len(union),
                coord_union=";".join(union),
                coord_intersection_n=len(inter),
                coord_intersection=";".join(inter),
            )
        )
    global_union = sorted(unions[700.0] | unions[900.0])

    for fl in fls:
        a, b = s700.loc[fl], s900.loc[fl]
        ga, gb = by_sid[a.structure_id], by_sid[b.structure_id]
        ca, cb = set(ga.index), set(gb.index)
        shared = sorted(ca & cb)
        only_a, only_b = sorted(ca - cb), sorted(cb - ca)
        row = dict(
            fl_um=fl,
            structure_700=a.structure_id,
            structure_900=b.structure_id,
            mark_700=a.mark,
            mark_900=b.mark,
            design_group_700=a.design_group_id,
            design_group_900=b.design_group_id,
            n_coords_700=len(ca),
            n_coords_900=len(cb),
            n_shared=len(shared),
            only_700=";".join(only_a),
            only_900=";".join(only_b),
            qp_med_700_all_coords=qp_median(list(ga.qp)),
            qp_med_900_all_coords=qp_median(list(gb.qp)),
            qp_med_700_only_coords=qp_median([ga.loc[c, "qp"] for c in only_a]),
            qp_med_900_only_coords=qp_median([gb.loc[c, "qp"] for c in only_b]),
        )
        deltas = {t: [] for t in COMPARE_TARGETS}
        for coord in shared:
            ra, rb = ga.loc[coord], gb.loc[coord]
            detail = dict(fl_um=fl, coord=coord)
            for t in COMPARE_TARGETS:
                detail[t + "_700"], detail[t + "_900"] = ra[t], rb[t]
                detail["delta_" + t] = rb[t] - ra[t]
                if t in RATIO_TARGETS:
                    detail["ratio_" + t] = rb[t] / ra[t]
                deltas[t].append(rb[t] - ra[t])
            detail_rows.append(detail)
        for t in COMPARE_TARGETS:
            row["shared_" + t + "_med_700"] = float(ga.loc[shared, t].median())
            row["shared_" + t + "_med_900"] = float(gb.loc[shared, t].median())
            row["shared_delta_" + t + "_median"] = float(np.median(deltas[t]))
            row["shared_delta_" + t + "_n_pos"] = int(sum(x > 0 for x in deltas[t]))
            row["shared_delta_" + t + "_n_neg"] = int(sum(x < 0 for x in deltas[t]))
        row["shared_qp_ratio_median"] = float(
            np.median([gb.loc[c, "qp"] / ga.loc[c, "qp"] for c in shared])
        )
        pair_rows.append(row)
        for sid, frame, partner_coords in (
            (a.structure_id, a, cb),
            (b.structure_id, b, ca),
        ):
            own = set(by_sid[sid].index)
            for reference, refset in (
                ("pair_partner", partner_coords),
                ("group_union", unions[frame.area_um2]),
                ("global_union", set(global_union)),
            ):
                for coord in sorted(refset - own):
                    missing_rows.append(
                        dict(
                            structure_id=sid,
                            area_um2=frame.area_um2,
                            fl_um=fl,
                            reference=reference,
                            missing_coord=coord,
                        )
                    )

    pd.DataFrame(group_rows).to_csv(out / "group_summary.csv", index=False)
    pairs = pd.DataFrame(pair_rows)
    pairs.to_csv(out / "pair_summary.csv", index=False)
    pd.DataFrame(detail_rows).to_csv(out / "pair_coordinate_comparison.csv", index=False)
    pd.DataFrame(missing_rows).to_csv(out / "missing_coordinates.csv", index=False)

    strict = sorted(set.intersection(*[set(by_sid[s].index) for s in inv.structure_id]))
    strict_rows = []
    for coord in strict:
        row = dict(coord=coord)
        for t in COMPARE_TARGETS:
            v7 = [by_sid[s].loc[coord, t] for s in s700.structure_id]
            v9 = [by_sid[s].loc[coord, t] for s in s900.structure_id]
            row[t + "_700_median"] = qp_median(v7)
            row[t + "_900_median"] = qp_median(v9)
            row["delta_" + t + "_median"] = row[t + "_900_median"] - row[t + "_700_median"]
        strict_rows.append(row)
    pd.DataFrame(strict_rows).to_csv(out / "group_intersection_comparison.csv", index=False)

    pooled_rows = []
    for t in COMPARE_TARGETS:
        d = np.concatenate(
            [
                np.array(
                    [
                        by_sid[s900.loc[fl].structure_id].loc[c, t]
                        - by_sid[s700.loc[fl].structure_id].loc[c, t]
                        for c in sorted(
                            set(by_sid[s700.loc[fl].structure_id].index)
                            & set(by_sid[s900.loc[fl].structure_id].index)
                        )
                    ]
                )
                for fl in fls
            ]
        )
        pooled_rows.append(
            dict(
                target=t,
                n=len(d),
                delta_median=float(np.median(d)),
                delta_mean=float(d.mean()),
                delta_min=float(d.min()),
                delta_max=float(d.max()),
                n_pos=int((d > 0).sum()),
                n_neg=int((d < 0).sum()),
            )
        )
    pd.DataFrame(pooled_rows).to_csv(out / "pooled_shared_summary.csv", index=False)

    verification = verify(p6e, p6g, out, pairs, strict, cross)
    write_json(out / "verification.json", verification)
    assert all(sha256(Path(p)) == h for p, h in hashes.items())
    write_json(
        out / "receipt.json",
        dict(
            inputs=hashes,
            source_sha256=sha256(Path(__file__)),
            new_fit=False,
            labels_modified=False,
            independent_test=False,
            paired_descriptive_only=True,
            causal_area_claim=False,
            files={p.name: sha256(p) for p in sorted(out.glob("*.csv"))},
        ),
    )
    return pairs


def verify(p6e, p6g, out, pairs, strict, cross):
    """Independent csv-module recompute of key numbers; never shares pandas code paths."""
    with (p6e / "final_audit/de_records.csv").open() as f:
        de = list(csv.DictReader(f))
    with (p6g / "audit/candidate_results.csv").open() as f:
        cand = list(csv.DictReader(f))
    rec = {(r["structure_id"], r["coord"]): r for r in de}
    meta = {}
    for r in de:
        meta[r["structure_id"]] = (float(r["area_um2"]), float(r["fl_um"]))
    s700 = {m[1]: s for s, m in meta.items() if m[0] == 700.0}
    s900 = {m[1]: s for s, m in meta.items() if m[0] == 900.0}
    pair_ok, pooled = True, []
    for fl, row in pairs.set_index("fl_um").iterrows():
        a, b = s700[fl], s900[fl]
        ca = {c for (s, c) in rec if s == a}
        cb = {c for (s, c) in rec if s == b}
        shared = sorted(ca & cb)
        d = [float(rec[(b, c)]["qp"]) - float(rec[(a, c)]["qp"]) for c in shared]
        pooled += d
        pair_ok &= len(shared) == int(row.n_shared)
        pair_ok &= np.isclose(statistics.median(d), row.shared_delta_qp_median, rtol=1e-12)
    sets = [{c for (s, c) in rec if s == sid} for sid in meta]
    strict_ok = sorted(set.intersection(*sets)) == strict
    orig = {
        r["device_id"]: float(r["qp"])
        for r in cand
        if r["method"] == "original" and r["order"] == "archive"
    }
    label = {r["device_id"]: float(r["qp"]) for r in de if float(r["area_um2"]) == 700.0}
    qp_max = max(abs(orig[k] - label[k]) for k in orig)
    cross_ok = np.isclose(qp_max, cross["qp_max_abs_delta"], rtol=1e-12)
    return {
        "pair_shared_counts_and_qp_delta_medians_match": bool(pair_ok),
        "pooled_qp_delta_median": statistics.median(pooled),
        "strict_intersection_matches": bool(strict_ok),
        "cross_source_qp_max_abs_delta_matches": bool(cross_ok),
        "method": "csv module + statistics, independent of the pandas primary path",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ["p6e", "p6g", "out"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(run(args.p6e, args.p6g, args.out).to_string(index=False))
