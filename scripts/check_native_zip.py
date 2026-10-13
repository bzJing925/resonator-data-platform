"""Check the installed decoder with a genuine method-9 ZIP without opening the DB."""

from __future__ import annotations

import argparse
import ast
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any


def check(root: Path, fixture: Path) -> None:
    source = root / "backend/app/workers/extract_batch.py"
    names = {"_find_7z", "_extract_zip", "_extract_with_7z", "_extract_with_unzip",
             "_stop_extractor",
             "_monitor_extracted_size", "_read_stream_into_queue", "_zip_uncompressed_size",
             "_count_extracted"}
    parsed = ast.parse(source.read_text(encoding="utf-8"))
    functions = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
                 and node.name in names]
    if {node.name for node in functions} != names:
        raise ValueError("Installed extraction functions do not match the supported package")
    # Only extraction functions are loaded: no application startup, tasks or DB connection.
    namespace = {
        "__file__": str(source), "sys": sys, "os": os, "shutil": shutil, "Path": Path,
        "queue": queue, "re": re, "subprocess": subprocess, "threading": threading,
        "time": time, "zipfile": zipfile, "Callable": Callable, "Any": Any,
        "logger": logging.getLogger("zip-support-check"), "TaskCancelledError": RuntimeError,
        "_PROGRESS_RE": re.compile(r"\(\s*(\d+)%\)"),
    }
    sys.path.insert(0, str(root / "backend"))
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    decoder = namespace["_find_7z"]()
    bundled = root / "tools/7zip/7za.exe"
    if sys.platform == "win32" and (not decoder or Path(decoder).resolve() != bundled.resolve()):
        raise ValueError(f"Bundled decoder not discovered: {decoder}")
    with zipfile.ZipFile(fixture) as archive:
        if [member.compress_type for member in archive.infolist()] != [9]:
            raise ValueError("Smoke fixture is not a Deflate64 ZIP")
    with tempfile.TemporaryDirectory(prefix="aln-zip-smoke-") as temporary:
        output = Path(temporary) / "decoded"
        extractor, _, count, total = namespace["_extract_zip"](fixture, output)
        expected = b"# GHz S RI R 50\n1 0.5 0\n" * 1000
        if extractor != "7z" or count != 1 or total != len(expected):
            raise ValueError(f"Extraction result mismatch: {extractor}, {count}, {total}")
        if (output / "rf.s1p").read_bytes() != expected:
            raise ValueError("Decoded bytes differ from the original payload")
    print(f"ZIP method 9: OK; decoder={decoder}; payload bytes={len(expected)}; database untouched")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--fixture", type=Path,
                        default=Path(__file__).resolve().parent / "support/deflate64-smoke.zip")
    args = parser.parse_args()
    check(args.root.resolve(), args.fixture.resolve())


if __name__ == "__main__":
    main()
