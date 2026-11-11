"""P6G-style original/vz deembed re-verification for the 164 records of the 900 um2 PF group."""

import argparse
import re
import sys
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import skrf as rf
import zipfile_deflate64  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.deembed import DeembedMethod, deembed, match_calibration
from app.core.extract import extract_resonator_params
from app.core.touchstone import split_s2p_to_s1p
from app.ml.optimization_data import TARGETS, sha256, write_json

COMPARE = (*TARGETS, "fs_ghz", "fp_ghz")
EXACT = ("qp", "qs", "k2eff_pct", "fs_ghz", "fp_ghz")


def run(archive, p6e, p6h, out):
    out.mkdir(parents=True, exist_ok=False)
    labels_path = p6e / "final_audit/de_records.csv"
    shared_path = p6h / "pair_coordinate_comparison.csv"
    inputs = {
        str(labels_path.resolve()): sha256(labels_path),
        str(shared_path.resolve()): sha256(shared_path),
        str(archive.resolve()): sha256(archive),
    }
    records = pd.read_csv(labels_path)
    records = records.loc[records.area_um2.eq(900)].copy()
    assert len(records) == 164 and records.device_id.is_unique and records.port.eq("S11").all()
    shared = pd.read_csv(shared_path)
    shared_set = set(zip(shared.fl_um.astype(float), shared.coord, strict=True))
    records["on_p6h_shared_coord"] = [
        (float(fl), coord) in shared_set
        for fl, coord in zip(records.fl_um, records.coord, strict=True)
    ]

    rows = []
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        cal = [
            n
            for n in names
            if n.lower().endswith(".s2p") and ("OPEN" in n.upper() or "SHORT" in n.upper())
        ]
        calpaths = [Path(Path(n).stem + "_S11.s1p") for n in cal]
        rawdir = out / "raw_parents"
        dutdir = out / "dut"
        for d in [rawdir, dutdir / "S11", dutdir / "S22"]:
            d.mkdir(parents=True)
        pairs = {}
        for row in records.itertuples():
            parent = re.sub(r"^S(?:11|22)_", "", row.device_id)
            parent = re.sub(r"\.s1p$", ".s2p", parent)
            assert parent in names, parent
            path = rawdir / Path(parent).name
            path.write_bytes(z.read(parent))
            split = split_s2p_to_s1p(path, dutdir / "S11", dutdir / "S22")
            dut = split.s11_path if row.port == "S11" else split.s22_path
            found = {}
            for method in (DeembedMethod.ORIGINAL, DeembedMethod.VZ):
                op, sh = match_calibration(dut, calpaths, method)
                found[method.value] = (op.name, sh.name)
            pairs[row.device_id] = dict(
                dut=dut,
                original=found["original"],
                vz=found["vz"],
                same=found["original"] == found["vz"],
                raw_sha256=sha256(path),
            )
        same_all = all(p["same"] for p in pairs.values())
        calnames = sorted({n for p in pairs.values() for n in p["original"]})
        caldir = out / "calibration_parents"
        calsplit = out / "calibration"
        for d in [caldir, calsplit / "S11", calsplit / "S22"]:
            d.mkdir(parents=True)
        calrows = []
        for name in calnames:
            parent = name.rsplit("_S", 1)[0] + ".s2p"
            p = caldir / parent
            p.write_bytes(z.read(parent))
            split_s2p_to_s1p(p, calsplit / "S11", calsplit / "S22")
            calrows.append(dict(name=name, parent=parent, sha256=sha256(p)))
        pd.DataFrame(calrows).to_csv(out / "calibration_sources.csv", index=False)
        (out / "curves").mkdir()

        for row in records.itertuples():
            p = pairs[row.device_id]
            rec = dict(
                device_id=row.device_id,
                structure_id=row.structure_id,
                fl_um=row.fl_um,
                coord=row.coord,
                port=row.port,
                on_p6h_shared_coord=row.on_p6h_shared_coord,
                open=p["original"][0],
                short=p["original"][1],
                original_vz_same_pair=p["same"],
                raw_parent_sha256=p["raw_sha256"],
                dut_file=p["dut"].name,
                historical_recipe_verified=False,
            )
            op = calsplit / row.port / p["original"][0]
            sh = calsplit / row.port / p["original"][1]
            try:
                nets = [rf.Network(str(x)) for x in [p["dut"], op, sh]]
                rec["equal_frequency_grid"] = all(np.array_equal(nets[0].f, n.f) for n in nets)
                rec["finite_inputs"] = all(np.isfinite(n.s).all() for n in nets)
                rec["equal_z0"] = all(np.array_equal(nets[0].z0, n.z0) for n in nets)
                assert rec["equal_frequency_grid"] and rec["finite_inputs"] and rec["equal_z0"]
                target = out / "curves" / p["dut"].name
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    deembed(p["dut"], op, sh, target)
                    rec["finite_output"] = bool(np.isfinite(rf.Network(str(target)).s).all())
                    assert rec["finite_output"]
                    vals = extract_resonator_params(target, skip_validation=True)
                    rec["warnings"] = " | ".join(str(w.message) for w in caught)
                rec["status"] = "extracted"
                for metric in COMPARE:
                    old = getattr(row, metric)
                    rec[metric] = vals[metric]
                    rec["old_" + metric] = old
                    rec["delta_" + metric] = vals[metric] - old
            except Exception as exc:
                rec.update(status="failed", error=str(exc))
            rows.append(rec)

    df = pd.DataFrame(rows)
    df.to_csv(out / "verify_results.csv", index=False)
    summary = []
    for scope, g in [("all", df), ("shared_coord", df[df.on_p6h_shared_coord])]:
        ok = g.loc[g.status.eq("extracted")]
        for metric in COMPARE:
            delta = ok["delta_" + metric]
            rel = delta.abs() / ok["old_" + metric].abs()
            summary.append(
                dict(
                    scope=scope,
                    metric=metric,
                    n=len(g),
                    successful=len(ok),
                    numerical_matches=int(
                        np.isclose(ok[metric], ok["old_" + metric], rtol=1e-8, atol=1e-8).sum()
                    ),
                    mae=delta.abs().mean(),
                    max_abs=delta.abs().max(),
                    max_rel=rel.max(),
                )
            )
    pd.DataFrame(summary).to_csv(out / "summary.csv", index=False)
    mismatch = df.loc[
        ~np.isclose(df.qp, df.old_qp, rtol=1e-8, atol=1e-8)
        | ~np.isclose(df.qs, df.old_qs, rtol=1e-8, atol=1e-8)
        | ~np.isclose(df.k2eff_pct, df.old_k2eff_pct, rtol=1e-8, atol=1e-8)
        | ~np.isclose(df.fs_ghz, df.old_fs_ghz, rtol=1e-8, atol=1e-8)
        | ~np.isclose(df.fp_ghz, df.old_fp_ghz, rtol=1e-8, atol=1e-8)
    ]
    mismatch.to_csv(out / "mismatches.csv", index=False)
    verification = verify(df, out)
    write_json(out / "verification.json", verification)
    assert all(sha256(Path(p)) == h for p, h in inputs.items())
    write_json(
        out / "receipt.json",
        dict(
            inputs=inputs,
            original_vz_same_pairs_all=bool(same_all),
            new_fit=False,
            labels_changed=False,
            historical_recipe_verified=False,
            code={
                str(p): sha256(p)
                for p in [
                    Path(__file__),
                    Path("app/core/deembed.py"),
                    Path("app/core/extract.py"),
                    Path("app/core/touchstone.py"),
                ]
            },
            files={
                p.name: sha256(p)
                for p in sorted(out.glob("*.csv"))
            },
        ),
    )
    print(pd.DataFrame(summary).to_string(index=False))
    return df


def verify(df, out):
    """Re-extract metrics from two saved deembedded curves, independent of the deembed path."""
    picked = []
    for want_shared in [True, False]:
        g = df.loc[df.on_p6h_shared_coord.eq(want_shared) & df.status.eq("extracted")]
        if len(g):
            picked.append(g.iloc[0])
    checks = {}
    for row in picked:
        vals = extract_resonator_params(out / "curves" / row.dut_file, skip_validation=True)
        checks[str(row.device_id)] = bool(all(vals[m] == row[m] for m in COMPARE))
    return {
        "curve_reextract_exact": checks,
        "method": "re-extract from saved deembedded curves; guards against file/row mixups",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ["archive", "p6e", "p6h", "out"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.archive, args.p6e, args.p6h, args.out)
