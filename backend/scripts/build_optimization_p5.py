"""Build a standalone offline P4 result viewer, without fitting or predicting."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.optimization_d2_view import make_d2_reference, verify_p10_products
from app.ml.optimization_data import sha256, write_json
from app.ml.optimization_p11_view import make_p11_candidates, verify_p11_products
from app.ml.optimization_p12_view import make_p12_candidates, verify_p12_products


def verify_delivery(root: Path) -> dict:
    manifest = json.loads((root / "receipts/delivery_manifest.json").read_text())
    hashes = {}
    for relative, info in manifest["files"].items():
        path = root / relative
        if sha256(path) != info["sha256"]:
            raise ValueError(f"P4 artifact checksum mismatch: {relative}")
        hashes[relative] = info["sha256"]
    return hashes


def make_package(
    root: Path,
    p10_root: Path | None = None,
    p11_root: Path | None = None,
    p12_root: Path | None = None,
) -> dict:
    datasets = {}
    for mode in ("measured", "exploratory"):
        directory = root / mode
        frame = pd.read_csv(directory / "candidates.csv")
        summary = json.loads((directory / "summary.json").read_text())
        if len(frame) != summary["n_candidates"] or summary["mode"] != mode:
            raise ValueError("P4 catalog/summary mismatch")
        # pandas CSV parsing keeps the P4 numerical representation used by its verifier.
        # stdlib JSON float repr round-trips doubles; do not round to display precision.
        rows = frame.astype(object).where(frame.notna(), None).values.tolist()
        datasets[mode] = {
            "columns": frame.columns.tolist(),
            "rows": rows,
            "summary": summary,
            "source_sha256": sha256(directory / "candidates.csv"),
        }
    package = {
        "schema": "aln_optimization_browser_v1",
        "version": root.name,
        "model_version": "optimization_scalar_p3_v1",
        "source": "#2 去嵌 · ELB002 · 标准 EG/FL 拓扑 · AG 未记录",
        "new_blind_test": False,
        "datasets": datasets,
        "joint_coverage": json.loads(
            pd.read_csv(root / "audit/joint_four_target_coverage.csv").to_json(orient="records")
        ),
    }
    if p10_root is not None:
        measured_frame = pd.read_csv(root / "measured/candidates.csv")
        package["d2_reference"] = make_d2_reference(p10_root, measured_frame)
    if p11_root is not None:
        measured_frame = pd.read_csv(root / "measured/candidates.csv")
        exploratory_frame = pd.read_csv(root / "exploratory/candidates.csv")
        catalog_ids = set(measured_frame.structure_id) | set(exploratory_frame.structure_id)
        package["p11_candidates"] = make_p11_candidates(p11_root, catalog_ids)
    if p12_root is not None:
        measured_frame = pd.read_csv(root / "measured/candidates.csv")
        exploratory_frame = pd.read_csv(root / "exploratory/candidates.csv")
        catalog_ids = set(measured_frame.structure_id) | set(exploratory_frame.structure_id)
        package["p12_candidates"] = make_p12_candidates(p12_root, catalog_ids)
    return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p4-root", type=Path, required=True)
    parser.add_argument(
        "--p10-root",
        type=Path,
        default=None,
        help="Optional P10 products root: embed the experimental D2 reference interval layer",
    )
    parser.add_argument(
        "--p11-root",
        type=Path,
        default=None,
        help="Optional P11 products root: embed extrapolation candidates as red plot points",
    )
    parser.add_argument(
        "--p12-root",
        type=Path,
        default=None,
        help="Optional P12 products root: embed refined candidates (supersedes p11 view)",
    )
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"Refusing to overwrite {args.out}")
    repo = Path(__file__).resolve().parents[2]
    assets = repo / "frontend/optimization"
    compiler = repo / "frontend/node_modules/typescript/bin/tsc"
    before = verify_delivery(args.p4_root)
    p10_hashes = verify_p10_products(args.p10_root) if args.p10_root is not None else None
    p11_hashes = verify_p11_products(args.p11_root) if args.p11_root is not None else None
    p12_hashes = verify_p12_products(args.p12_root) if args.p12_root is not None else None
    package = make_package(args.p4_root, args.p10_root, args.p11_root, args.p12_root)
    with tempfile.TemporaryDirectory() as temporary:
        js_path = Path(temporary) / "app.js"
        subprocess.run(
            [
                "node",
                str(compiler),
                str(assets / "app.ts"),
                "--strict",
                "--target",
                "ES2020",
                "--lib",
                "ES2020,DOM",
                "--module",
                "none",
                "--outFile",
                str(js_path),
            ],
            check=True,
        )
        html = (assets / "index.html").read_text()
        encoded = json.dumps(package, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        for token, contents in {
            "%%STYLE%%": (assets / "style.css").read_text(),
            "%%DATA%%": encoded,
            "%%APP%%": js_path.read_text().replace("</script", "<\\/script"),
        }.items():
            if html.count(token) != 1:
                raise ValueError(f"Invalid template token: {token}")
            html = html.replace(token, contents)
    if verify_delivery(args.p4_root) != before:
        raise RuntimeError("P4 source changed during build")
    if p10_hashes is not None and verify_p10_products(args.p10_root) != p10_hashes:
        raise RuntimeError("P10 source changed during build")
    if p11_hashes is not None and verify_p11_products(args.p11_root) != p11_hashes:
        raise RuntimeError("P11 source changed during build")
    if p12_hashes is not None and verify_p12_products(args.p12_root) != p12_hashes:
        raise RuntimeError("P12 source changed during build")
    args.out.mkdir(parents=True)
    output = args.out / "AlN结构寻优.html"
    output.write_text(html)
    write_json(args.out / "browser_data.json", package)
    sources = [
        Path(__file__).resolve(),
        *[assets / n for n in ("app.ts", "index.html", "style.css")],
    ]
    write_json(
        args.out / "build_manifest.json",
        {
            "status": "built_functional_verification_separate",
            "p4_root": str(args.p4_root.resolve()),
            "p4_files_verified": len(before),
            "p4_delivery_manifest_sha256": sha256(args.p4_root / "receipts/delivery_manifest.json"),
            "p10_root": str(args.p10_root.resolve()) if args.p10_root else None,
            "p10_files_verified": len(p10_hashes) if p10_hashes is not None else 0,
            "d2_reference_embedded": p10_hashes is not None,
            "p11_root": str(args.p11_root.resolve()) if args.p11_root else None,
            "p11_files_verified": len(p11_hashes) if p11_hashes is not None else 0,
            "p11_candidates_embedded": p11_hashes is not None,
            "p12_root": str(args.p12_root.resolve()) if args.p12_root else None,
            "p12_files_verified": len(p12_hashes) if p12_hashes is not None else 0,
            "p12_candidates_embedded": p12_hashes is not None,
            "source_sha256": {str(p): sha256(p) for p in sources},
            "output_sha256": {p.name: sha256(p) for p in (output, args.out / "browser_data.json")},
            "typescript_version": subprocess.check_output(
                ["node", str(compiler), "--version"], text=True
            ).strip(),
            "python": sys.version,
            "pandas": pd.__version__,
            "new_blind_test": False,
        },
    )
    print(
        json.dumps(
            {"html": str(output.resolve()), "bytes": output.stat().st_size}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
