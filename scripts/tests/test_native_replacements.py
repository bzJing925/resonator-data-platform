"""Program replacement rejects conflicts and restores files after write failures."""

import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "apply_native_replacements.py"
SPEC = importlib.util.spec_from_file_location("native_replacements", SOURCE)
patcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patcher)


@pytest.fixture
def installation(tmp_path):
    root, bundle = tmp_path / "installed", tmp_path / "patch"
    program = "backend/app/main.py"
    installed, payload = root / program, bundle / "files" / program
    installed.parent.mkdir(parents=True)
    payload.parent.mkdir(parents=True)
    installed.write_text("old")
    payload.write_text("new")
    (root / "data").mkdir()
    (root / "data/aln-data.db").write_bytes(b"customer-data")
    (root / "server-config.json").write_text('{"port":8000}')
    manifest = {"format": "aln-native-replacements-v1", "files": {program: {
        "sha256": patcher.digest(payload), "allowed_before": [patcher.digest(installed)]}}}
    return root, bundle, manifest, tmp_path / "backup"


def test_verified_replacement_preserves_data_and_config(installation):
    root, bundle, manifest, backup = installation
    saved = patcher.apply(root, bundle, manifest, backup)
    assert (root / "backend/app/main.py").read_text() == "new"
    assert (saved / "files/backend/app/main.py").read_text() == "old"
    assert json.loads((saved / "restore-manifest.json").read_text())
    assert (root / "data/aln-data.db").read_bytes() == b"customer-data"
    assert (root / "server-config.json").read_text() == '{"port":8000}'


def test_conflicting_installation_stops_before_backup_or_mutation(installation):
    root, bundle, manifest, backup = installation
    (root / "backend/app/main.py").write_text("user-local-change")
    with pytest.raises(ValueError, match="unexpected version"):
        patcher.apply(root, bundle, manifest, backup)
    assert not backup.exists()
    assert (root / "backend/app/main.py").read_text() == "user-local-change"


def test_failed_write_restores_program_files(installation, monkeypatch):
    root, bundle, manifest, backup = installation
    real_replace = patcher.replace_file
    calls = 0

    def fail_after_write(source, target):
        nonlocal calls
        calls += 1
        real_replace(source, target)
        if calls == 1:
            raise OSError("interrupted write")

    monkeypatch.setattr(patcher, "replace_file", fail_after_write)
    with pytest.raises(OSError, match="interrupted write"):
        patcher.apply(root, bundle, manifest, backup)
    assert (root / "backend/app/main.py").read_text() == "old"
    assert (root / "data/aln-data.db").read_bytes() == b"customer-data"


@pytest.mark.parametrize("relative", ["../data/db", "backend/app/../../data/db",
                                      "server-config.json", "runtime/python.exe"])
def test_data_traversal_and_runtime_mutation_rejected(tmp_path, relative):
    with pytest.raises(ValueError):
        patcher.destination(tmp_path, relative)


def _local_document(installation):
    root, bundle, manifest, _ = installation
    document = "DEPLOYMENT.md"
    (root / document).write_text("local server deployment notes")
    (bundle / "files" / document).write_text("new release instructions")
    manifest["files"][document] = {
        "sha256": patcher.digest(bundle / "files" / document),
        "allowed_before": ["original-release-document-hash"],
    }
    return document


def test_modified_prose_preserved_and_checksums_match_actual_installation(installation):
    root, bundle, manifest, backup = installation
    document = _local_document(installation)
    original_list = root / "SHA256SUMS.json"
    original_list.write_text('{"old": "checksums"}')
    payload_list = bundle / "files/SHA256SUMS.json"
    payload_list.write_text(json.dumps({relative: item["sha256"]
                                       for relative, item in manifest["files"].items()}))
    manifest["files"]["SHA256SUMS.json"] = {
        "sha256": patcher.digest(payload_list), "allowed_before": [patcher.digest(original_list)]}
    saved = patcher.apply(root, bundle, manifest, backup)
    assert (root / document).read_text() == "local server deployment notes"
    checksums = json.loads(original_list.read_text())
    assert checksums[document] == patcher.digest(root / document)
    assert checksums["backend/app/main.py"] == patcher.digest(root / "backend/app/main.py")
    assert document not in json.loads((saved / "restore-manifest.json").read_text())
    manifest["files"]["backend/app/main.py"]["allowed_before"].append(
        manifest["files"]["backend/app/main.py"]["sha256"])
    patcher.preflight(root, bundle, manifest)  # Repeated verification accepts adjusted checksums.


def test_preserved_document_payload_still_requires_valid_checksum(installation):
    _, bundle, _, backup = installation
    _local_document(installation)
    (bundle / "files/DEPLOYMENT.md").write_text("corrupt replacement document")
    root, bundle, manifest, _ = installation
    with pytest.raises(ValueError, match="payload checksum mismatch"):
        patcher.apply(root, bundle, manifest, backup)
    assert not backup.exists()


def test_modified_prose_does_not_allow_unknown_program_files(installation):
    root, bundle, manifest, backup = installation
    _local_document(installation)
    (root / "backend/app/main.py").write_text("different installed program")
    with pytest.raises(ValueError, match="unexpected version"):
        patcher.apply(root, bundle, manifest, backup)
    assert not backup.exists()


def test_next_upgrade_accepts_known_previous_checksums_with_preserved_prose(installation):
    root, bundle, manifest, backup = installation
    document = _local_document(installation)
    baseline = {"backend/app/main.py": patcher.digest(root / "backend/app/main.py"),
                document: "original-release-document-hash"}
    previous = {**baseline, document: patcher.digest(root / document)}
    installed = root / "SHA256SUMS.json"
    installed.write_text(json.dumps(previous, indent=2) + "\n")
    payload = bundle / "files/SHA256SUMS.json"
    payload.write_text(json.dumps({key: item["sha256"] for key, item in manifest["files"].items()}))
    manifest["files"]["SHA256SUMS.json"] = {
        "sha256": patcher.digest(payload), "allowed_before": ["known-original-list-hash"]}
    manifest["checksum_baselines"] = [baseline]
    patcher.preflight(root, bundle, manifest)
    # Only prose digests may differ. Any other checksum-list content still blocks before writes.
    changed = {**previous, "backend/app/main.py": "unknown-hash"}
    installed.write_text(json.dumps(changed, indent=2) + "\n")
    with pytest.raises(ValueError, match="unexpected version: SHA256SUMS.json"):
        patcher.apply(root, bundle, manifest, backup)
    assert not backup.exists()
    installed.write_text(json.dumps(previous, indent=2) + "\n")
    patcher.apply(root, bundle, manifest, backup)
    assert (root / document).read_text() == "local server deployment notes"
    checksums = json.loads(installed.read_text())
    assert checksums["backend/app/main.py"] == patcher.digest(root / "backend/app/main.py")
