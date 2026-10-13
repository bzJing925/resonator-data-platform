"""Regression for offline servers without zipfile-deflate64 or 7-Zip on PATH."""

import base64
import os
import shutil
import zipfile

import pytest

from app.workers import extract_batch

# Produced by 7-Zip with -tzip -mm=Deflate64; compression method is genuinely 9.
DEFLATE64_ZIP = (
    'UEsDBBUDAAAJAFtTTV3K3M8+nwAAAMBdAAAGAAAAcmYuczFw5cihEcAgFAUwzxTvrr73K5ihrYVtmJ4lcInMlfdb'
    'mRl/Rnq1J3X3VOOff/75559//vnnn3/++eeff/75559//vnnn3/++eeff/75559//vnnn3/++eeff/75559//vnn'
    'n3/++eeff/75559//vnnn3/++eeff/75559//vnnn3/++eeff/75559//vnnn3/++eeff/75559//vnn/+BvUEsB'
    'Aj8DFQMAAAkAW1NNXcrczz6fAAAAwF0AAAYAJAAAAAAAAAAggKSBAAAAAHJmLnMxcAoAIAAAAAAAAQAYAABryU+6'
    'Wt0BAGvJT7pa3QEAa8lPulrdAVBLBQYAAAAAAQABAFgAAADDAAAAAAA='
)


def test_windows_bundled_decoder_works_without_path(tmp_path, monkeypatch):
    module = tmp_path / "backend/app/workers/extract_batch.py"
    bundled = tmp_path / "tools/7zip/7za.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ")
    monkeypatch.setattr(extract_batch, "__file__", str(module))
    monkeypatch.setattr(extract_batch.sys, "platform", "win32")
    monkeypatch.setattr(extract_batch.shutil, "which", lambda _: None)
    assert extract_batch._find_7z() == str(bundled)


def test_windows_installed_decoder_does_not_require_path(tmp_path, monkeypatch):
    installed = tmp_path / "Program Files/7-Zip/7z.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"MZ")
    monkeypatch.setattr(extract_batch, "__file__", str(tmp_path / "backend/app/workers/module.py"))
    monkeypatch.setattr(extract_batch.sys, "platform", "win32")
    monkeypatch.setattr(extract_batch.shutil, "which", lambda _: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    assert extract_batch._find_7z() == str(installed)


def test_non_windows_uses_native_decoder(tmp_path, monkeypatch):
    bundled = tmp_path / "tools/7zip/7za.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ")
    monkeypatch.setattr(extract_batch, "__file__", str(tmp_path / "backend/app/workers/module.py"))
    monkeypatch.setattr(extract_batch.sys, "platform", "darwin")
    monkeypatch.setattr(
        extract_batch.shutil, "which", lambda name: "/native/7zz" if name == "7zz" else None
    )
    assert extract_batch._find_7z() == "/native/7zz"


def test_real_deflate64_extracts_through_external_decoder(tmp_path, monkeypatch):
    executable = os.environ.get("ALN_TEST_7ZIP") or shutil.which("7zz") or shutil.which("7z")
    if not executable:
        pytest.skip("Real 7-Zip is required; decoder integration has not been verified")
    archive = tmp_path / "Deflate64.zip"
    archive.write_bytes(base64.b64decode(DEFLATE64_ZIP))
    with zipfile.ZipFile(archive) as source:
        assert source.infolist()[0].compress_type == 9
    monkeypatch.setattr(extract_batch, "_find_7z", lambda: executable)
    output = tmp_path / "中文 output"
    progress = []
    decoder, _, files, size = extract_batch._extract_zip(
        archive, output, progress_callback=lambda current, total: progress.append((current, total)),
        cancel_check=lambda: False,
    )
    assert decoder == "7z"
    expected = b"# GHz S RI R 50\n1 0.5 0\n" * 1000
    assert (output / "rf.s1p").read_bytes() == expected
    assert (files, size) == (1, len(expected))
    assert progress[0] == (0, 100)
    assert progress[-1] == (100, 100)


def test_streaming_extractor_uses_the_same_decoder(monkeypatch):
    from app.workers.pipeline.extractor import _find_7z

    monkeypatch.setattr(extract_batch, "_find_7z", lambda: "/bundled/7za.exe")
    assert _find_7z() == "/bundled/7za.exe"
