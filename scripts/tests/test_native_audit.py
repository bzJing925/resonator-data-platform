"""Audit reports all conflicts without changing installation or payload files."""

import hashlib
import importlib.util
import json
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "audit_native_installation.py"
SPEC = importlib.util.spec_from_file_location("native_audit", SOURCE)
auditor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auditor)


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def test_all_conflicts_returned_and_no_files_changed(tmp_path):
    root, bundle = tmp_path / "installed", tmp_path / "patch"
    files = {"backend/app/main.py": "user edit", "backend/app/config.py": "other edit",
             "native-bundle.json": json.dumps({"version": "0.3.0", "built_at": "other time"}),
             "DEPLOYMENT.md": "local prose", "SHA256SUMS.json": "{}",
             "runtime/python.exe": "different runtime", "data/customer.db": "protected"}
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    manifest = {"format": "aln-native-replacements-v1", "files": {},
                "unchanged_required": {"runtime/python.exe": [_hash("original runtime")]}}
    for relative in ("backend/app/main.py", "backend/app/config.py", "native-bundle.json",
                     "DEPLOYMENT.md"):
        path = bundle / "files" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("target")
        manifest["files"][relative] = {"sha256": _hash("target"),
                                       "allowed_before": [_hash("original")]}
    (bundle / "patch-manifest.json").write_text(json.dumps(manifest))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = auditor.audit(root, bundle)
    assert {i["path"] for i in result["conflicts"]} == {
        "backend/app/main.py", "backend/app/config.py", "native-bundle.json", "runtime/python.exe"}
    assert result["summary"]["program_or_dependency_conflicts"] == 3
    assert result["metadata"]["built_at"] == "other time"
    assert result["local_documents"][0]["path"] == "DEPLOYMENT.md"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_bad_paths_and_missing_files_are_reported_without_following_links(tmp_path):
    root, bundle = tmp_path / "installed", tmp_path / "patch"
    root.mkdir()
    bundle.mkdir()
    (bundle / "patch-manifest.json").write_text(json.dumps({
        "format": "aln-native-replacements-v1", "files": {
            "../outside": {"sha256": "bad", "allowed_before": ["bad"]},
            "backend/app/missing.py": {"sha256": "bad", "allowed_before": ["bad"]}}}))
    result = auditor.audit(root, bundle)
    assert len(result["errors"]) == 4
    assert len(result["conflicts"]) == 2
    assert result["read_only"] is True
