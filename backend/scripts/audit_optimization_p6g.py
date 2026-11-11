"""Frozen candidate deembedding sensitivity, never historical attribution."""

import argparse
import sys
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import skrf as rf
import zipfile_deflate64  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.deembed import deembed
from app.core.extract import extract_resonator_params
from app.core.touchstone import split_s2p_to_s1p
from app.ml.optimization_data import TARGETS, sha256, write_json


def run(archive, p6f, p6e, out):
    out.mkdir(parents=True, exist_ok=False)
    paths = [
        p6f / "audit/candidate_calibration_matches.csv",
        p6f / "audit/raw_curve_audit.csv",
        p6e / "final_audit/paired_records.csv",
    ]
    hashes = {str(p.resolve()): sha256(p) for p in paths}
    matches, raw, records = [pd.read_csv(p) for p in paths]
    a = matches[(matches.method == "original") & (matches.order == "archive")].set_index(
        "device_id"
    )
    b = matches[(matches.method == "vz") & (matches.order == "archive")].set_index("device_id")
    assert a[["open", "short"]].equals(b[["open", "short"]])
    chosen = matches[
        ((matches.method == "original") & (matches.order == "archive"))
        | (matches.method == "default")
    ].copy()
    assert len(chosen) == 375 and chosen.status.eq("matched").all()
    records = records.set_index("device_id")
    raw = raw.set_index("device_id")
    cals = sorted(set(chosen.open) | set(chosen["short"]))
    parentdir = out / "calibration_parents"
    parentdir.mkdir()
    calrows = []
    with zipfile.ZipFile(archive) as z:
        for name in cals:
            parent = name.rsplit("_S", 1)[0] + ".s2p"
            p = parentdir / parent
            p.write_bytes(z.read(parent))
            split_s2p_to_s1p(p, out / "S11", out / "S22")
            calrows.append(dict(name=name, parent=parent, sha256=sha256(p)))
    pd.DataFrame(calrows).to_csv(out / "calibration_sources.csv", index=False)
    results = []
    for row in chosen.itertuples():
        ref = records.loc[row.device_id]
        source = raw.loc[row.device_id]
        port = ref.port
        dut = p6f / "audit" / port / (Path(source.parent).stem + "_" + port + ".s1p")
        op = out / port / row.open
        sh = out / port / row.short
        rec = dict(
            device_id=row.device_id,
            structure_id=ref.structure_id_de,
            method=row.method,
            order=row.order,
            open=row.open,
            short=row.short,
            historical_recipe_verified=False,
        )
        try:
            nets = [rf.Network(str(p)) for p in [dut, op, sh]]
            rec["equal_frequency_grid"] = all(np.array_equal(nets[0].f, n.f) for n in nets)
            rec["finite_inputs"] = all(np.isfinite(n.s).all() for n in nets)
            rec["equal_z0"] = all(np.array_equal(nets[0].z0, n.z0) for n in nets)
            assert rec["equal_frequency_grid"] and rec["finite_inputs"] and rec["equal_z0"]
            target = out / "curves" / f"{row.method}_{row.order}" / dut.name
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                deembed(dut, op, sh, target)
                rec["finite_output"] = bool(np.isfinite(rf.Network(str(target)).s).all())
                assert rec["finite_output"]
                vals = extract_resonator_params(target, skip_validation=True)
                rec["warnings"] = " | ".join(str(w.message) for w in caught)
            rec["status"] = "extracted"
            for metric in (*TARGETS, "fs_ghz", "fp_ghz"):
                old = ref.qp_de if metric == "qp" else ref[metric]
                rec[metric] = vals[metric]
                rec["old_" + metric] = old
                rec["delta_" + metric] = vals[metric] - old
        except Exception as exc:
            rec.update(status="failed", error=str(exc))
        results.append(rec)
    df = pd.DataFrame(results)
    df.to_csv(out / "candidate_results.csv", index=False)
    summary = []
    for (method, order), g in df.groupby(["method", "order"]):
        for metric in (*TARGETS, "fs_ghz", "fp_ghz"):
            delta = g["delta_" + metric]
            summary.append(
                dict(
                    method=method,
                    order=order,
                    metric=metric,
                    n=len(g),
                    successful=int(g.status.eq("extracted").sum()),
                    mae=delta.abs().mean(),
                    max_abs=delta.abs().max(),
                    numerical_matches=int(
                        np.isclose(g[metric], g["old_" + metric], rtol=1e-8, atol=1e-8).sum()
                    ),
                )
            )
    pd.DataFrame(summary).to_csv(out / "summary.csv", index=False)
    assert all(sha256(Path(p)) == v for p, v in hashes.items())
    write_json(
        out / "receipt.json",
        dict(
            inputs=hashes,
            original_vz_same_pairs=True,
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
        ),
    )
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in ["archive", "p6f", "p6e", "out"]:
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    run(a.archive, a.p6f, a.p6e, a.out)
