"""Trace raw parents and enumerate current calibration rules without choosing history."""

import argparse
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd
import zipfile_deflate64  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.deembed import DeembedMethod, match_calibration
from app.core.extract import extract_resonator_params
from app.core.touchstone import split_s2p_to_s1p
from app.ml.optimization_data import TARGETS, sha256, write_json


def run(archive, p6e, out):
    out.mkdir(parents=True, exist_ok=False)
    source = p6e / "final_audit/paired_records.csv"
    rows = pd.read_csv(source)
    rows = rows[rows.area_um2.eq(700)]
    assert len(rows) == 125 and rows.device_id.is_unique
    inputs = {str(source.resolve()): sha256(source), str(archive.resolve()): sha256(archive)}
    curves, matches = [], []
    files = out / "raw_parents"
    files.mkdir()
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        assert len(names) == len(set(names))
        cal = [
            n
            for n in names
            if n.lower().endswith(".s2p") and ("OPEN" in n.upper() or "SHORT" in n.upper())
        ]
        pd.DataFrame(
            [dict(member=n, crc=z.getinfo(n).CRC, bytes=z.getinfo(n).file_size) for n in cal]
        ).to_csv(out / "calibration_inventory.csv", index=False)
        for row in rows.itertuples():
            parent = re.sub(r"^S(?:11|22)_", "", row.device_id)
            parent = re.sub(r"\.s1p$", ".s2p", parent)
            item = dict(
                device_id=row.device_id,
                structure_id=row.structure_id_de,
                parent=parent,
                parent_found=parent in names,
                de_verified=False,
            )
            if parent not in names:
                curves.append(item)
                continue
            path = files / Path(parent).name
            path.write_bytes(z.read(parent))
            item["sha256"] = sha256(path)
            split = split_s2p_to_s1p(path, out / "S11", out / "S22")
            dut = split.s11_path if row.port == "S11" else split.s22_path
            try:
                extracted = extract_resonator_params(dut, skip_validation=True)
                item["status"] = "raw_reextracted"
                for target in (*TARGETS, "fs_ghz", "fp_ghz"):
                    item["reextracted_" + target] = extracted[target]
                item["source_raw_qp"] = row.qp_raw
                item["qp_delta_raw"] = extracted["qp"] - row.qp_raw
            except Exception as exc:
                item.update(status="raw_extraction_failed", error=str(exc))
            curves.append(item)
            calpaths = [Path(Path(n).stem + "_" + row.port + ".s1p") for n in cal]
            for method in DeembedMethod:
                for order, candidates in [("archive", calpaths), ("reverse", calpaths[::-1])]:
                    m = dict(
                        device_id=row.device_id,
                        method=method.value,
                        order=order,
                        historical_method_verified=False,
                    )
                    try:
                        op, sh = match_calibration(dut, candidates, method)
                        m.update(status="matched", open=op.name, short=sh.name)
                    except Exception as exc:
                        m.update(status="unmatched", error=str(exc))
                    matches.append(m)
    pd.DataFrame(curves).to_csv(out / "raw_curve_audit.csv", index=False)
    pd.DataFrame(matches).to_csv(out / "candidate_calibration_matches.csv", index=False)
    assert all(sha256(Path(p)) == h for p, h in inputs.items())
    write_json(
        out / "receipt.json",
        dict(
            inputs=inputs,
            code={
                str(p): sha256(p)
                for p in [
                    Path(__file__),
                    Path("app/core/deembed.py"),
                    Path("app/core/extract.py"),
                    Path("app/core/touchstone.py"),
                ]
            },
            historical_recipe_recovered=False,
            de_curves_verified=False,
            labels_changed=False,
            new_fit=False,
        ),
    )
    print(pd.DataFrame(curves).status.value_counts().to_dict())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--p6e-root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    run(a.archive, a.p6e_root, a.out)
