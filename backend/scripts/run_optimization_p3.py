"""Reproducible P0-P3 commands; generated data stays in isolated output directories.

Run from backend with uv run python scripts/run_optimization_p3.py --help.
No command updates the platform database, HTML, or published surrogate.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.optimization_data import prepare_data, sha256, write_json


def file_manifest(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256(path),
    }


def new_directory(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite an existing output directory: {path}")
    path.mkdir(parents=True)


def git_state(root: Path) -> dict:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    return {
        "root": str(root),
        "head": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "status": git("status", "--short"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="P0-P2 audit, quarantine and empirical rankings")
    for name in ("de", "raw", "mapping", "protocol", "out"):
        prep.add_argument("--" + name, type=Path, required=True)
    prep.add_argument("--zip", type=Path)
    prep.add_argument("--integration-root", type=Path)
    evaluate = commands.add_parser("evaluate", help="P3 frozen grouped and boundary evaluation")
    evaluate.add_argument("--data-dir", type=Path, required=True)
    evaluate.add_argument("--out", type=Path, required=True)
    evaluate.add_argument("--legacy-root", type=Path)
    predict = commands.add_parser("predict", help="Batch scalar predictions; does not optimize")
    predict.add_argument("--bundle", type=Path, required=True)
    predict.add_argument("--input", type=Path, required=True)
    predict.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.command == "prepare":
        # Hash all fitted inputs before reading their values; preserve the exact protocol.
        inputs = {
            name: file_manifest(getattr(args, name))
            for name in ("de", "raw", "mapping", "protocol")
        }
        source_paths = [
            root / "backend/app/core/filename.py",
            root / "backend/app/core/mapping.py",
            root / "backend/app/ml/optimization_data.py",
            Path(__file__).resolve(),
        ]
        manifest = {
            "inputs": inputs,
            "source_code": [file_manifest(p) for p in source_paths],
            "worktrees": [git_state(root)],
            "versions": {
                name: importlib.metadata.version(name)
                for name in ("numpy", "pandas", "scipy", "scikit-learn", "openpyxl")
            },
        }
        if args.integration_root:
            manifest["worktrees"].append(git_state(args.integration_root))
        if args.zip:
            with zipfile.ZipFile(args.zip) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                manifest["raw_zip"] = {
                    "path": str(args.zip.resolve()),
                    "bytes": args.zip.stat().st_size,
                    "mtime_ns": args.zip.stat().st_mtime_ns,
                    "file_entries": len(infos),
                    "inspection": "central directory only; not fitted or fully checksummed",
                }
        new_directory(args.out)
        write_json(args.out / "input_manifest.json", manifest)
        (args.out / "frozen_protocol.md").write_bytes(args.protocol.read_bytes())
        summary = prepare_data(args.de, args.raw, args.mapping, args.out)
        # Detect replacement or editing of source workbooks during the run.
        for name in ("de", "raw", "mapping", "protocol"):
            if sha256(getattr(args, name)) != inputs[name]["sha256"]:
                raise RuntimeError(f"Source changed while preparing data: {name}")
        write_json(
            args.out / "outputs_manifest.json",
            {p.name: sha256(p) for p in sorted(args.out.iterdir()) if p.is_file()},
        )
        print(
            {
                "status": "P0-P2 complete",
                "n_structures": summary["n_structures"],
                "out": str(args.out),
            }
        )
    elif args.command == "evaluate":
        from app.ml.optimization_baselines import evaluate_baselines

        new_directory(args.out)
        summary = evaluate_baselines(args.data_dir, args.out, args.legacy_root)
        write_json(
            args.out / "implementation_manifest.json",
            {
                "code": [
                    file_manifest(root / "backend/app/ml/optimization_baselines.py"),
                    file_manifest(Path(__file__).resolve()),
                ],
                "sklearn_version": importlib.metadata.version("scikit-learn"),
                "joblib_version": importlib.metadata.version("joblib"),
                "note": "Only load trusted local joblib files; this is not a cross-runtime export.",
            },
        )
        write_json(
            args.out / "outputs_manifest.json",
            {p.name: sha256(p) for p in sorted(args.out.iterdir()) if p.is_file()},
        )
        print({"status": summary["status"], "out": str(args.out)})
    else:
        import joblib
        import pandas as pd

        from app.ml.optimization_baselines import predict_bundle

        if args.out.exists():
            raise FileExistsError(args.out)
        result = predict_bundle(joblib.load(args.bundle), pd.read_csv(args.input))
        with args.out.open("x") as f:
            result.to_csv(f, index=False)
        print({"rows": len(result), "out": str(args.out)})


if __name__ == "__main__":
    main()
