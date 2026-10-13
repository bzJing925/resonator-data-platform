"""Apply a hash-checked native replacement bundle after the server is stopped.

Only program files listed in patch-manifest.json are changed. Data/config backup
and stopping/restarting Windows services are the responsibility of the wrapper.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

LOCAL_DOCUMENTS = frozenset({"README.txt", "DEPLOYMENT.md"})


def digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"Not a regular program file: {path}")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def destination(root: Path, relative: str, *, dependency: bool = False) -> Path:
    parts = PurePosixPath(relative).parts
    allowed = (
        relative.startswith(("backend/app/", "frontend/dist/", "desktop/resources/app/",
                             "tools/7zip/"))
        or relative in {"README.txt", "DEPLOYMENT.md", "native-bundle.json", "SHA256SUMS.json"}
    )
    if dependency:
        allowed = allowed or relative.startswith(("runtime/", "wheelhouse/")) or relative in {
            "requirements-server.txt", "run_server.py", "start.ps1", "stop.ps1", "backup.ps1"
        }
    if not allowed or not parts or "\\" in relative or ":" in relative:
        raise ValueError(f"Disallowed replacement path: {relative}")
    if relative.startswith("/") or any(part in {".", ".."} for part in relative.split("/")):
        raise ValueError(f"Unsafe replacement path: {relative}")
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Replacement escapes installation: {relative}")
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"Replacement path contains a link: {relative}")
    return path


def preserved_documents(root: Path, manifest: dict) -> dict[str, str]:
    """Only keep locally edited prose; executable files always require a known hash."""
    result = {}
    for relative in LOCAL_DOCUMENTS & manifest["files"].keys():
        current = digest(destination(root, relative))
        if current is not None and current not in manifest["files"][relative]["allowed_before"]:
            result[relative] = current
    return result


def installed_checksums(bundle: Path, manifest: dict, preserved: dict[str, str]) -> bytes | None:
    """Keep the installed checksum list accurate when local prose is retained."""
    if not preserved or "SHA256SUMS.json" not in manifest["files"]:
        return None
    source = destination(bundle / "files", "SHA256SUMS.json")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest["files"]["SHA256SUMS.json"]["sha256"]:
        raise ValueError("Replacement payload checksum mismatch: SHA256SUMS.json")
    checksums = json.loads(data)
    for relative, value in preserved.items():
        checksums[relative] = value
    return (json.dumps(checksums, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def matches_preserved_checksum_baseline(
    current: str | None, manifest: dict, preserved: dict[str, str],
) -> bool:
    """Accept a known prior release checksum list with only preserved prose changed."""
    if not preserved:
        return False
    for baseline in manifest.get("checksum_baselines", []):
        checksums = {**baseline, **preserved}
        data = (json.dumps(checksums, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if hashlib.sha256(data).hexdigest() == current:
            return True
    return False


def preflight(root: Path, bundle: Path, manifest: dict) -> None:
    if manifest.get("format") != "aln-native-replacements-v1":
        raise ValueError("Unknown replacement manifest format")
    preserved = preserved_documents(root, manifest)
    checksum_data = installed_checksums(bundle, manifest, preserved)
    for relative, item in manifest["files"].items():
        payload = destination(bundle / "files", relative)
        if digest(payload) != item["sha256"]:
            raise ValueError(f"Replacement payload checksum mismatch: {relative}")
        current = digest(destination(root, relative))
        if relative in preserved:
            continue
        if relative == "SHA256SUMS.json" and checksum_data is not None:
            if current == hashlib.sha256(checksum_data).hexdigest():
                continue
            if matches_preserved_checksum_baseline(current, manifest, preserved):
                continue
        if current not in item["allowed_before"]:
            raise ValueError(f"Installed file has an unexpected version: {relative} ({current})")
    for relative, allowed in manifest.get("unchanged_required", {}).items():
        if digest(destination(root, relative, dependency=True)) not in allowed:
            raise ValueError(f"Unchanged dependency has an unexpected version: {relative}")
    for relative, expected in manifest.get("removed", {}).items():
        current = digest(destination(root, relative))
        if current not in (None, expected):
            raise ValueError(f"Obsolete file has an unexpected version: {relative}")


def replace_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".aln-replacement-", dir=target.parent)
    os.close(fd)
    staging = Path(name)
    try:
        shutil.copy2(source, staging)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)


def apply(root: Path, bundle: Path, manifest: dict, backup_directory: Path) -> Path:
    root, bundle, backup_directory = root.resolve(), bundle.resolve(), backup_directory.resolve()
    if backup_directory.is_relative_to(root) or root.is_relative_to(backup_directory):
        raise ValueError("Program backup must be in a separate sibling directory")
    preflight(root, bundle, manifest)
    preserved = preserved_documents(root, manifest)
    checksum_data = installed_checksums(bundle, manifest, preserved)
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="program-", dir=backup_directory))
    relatives = sorted((set(manifest["files"]) - set(preserved))
                       | set(manifest.get("removed", {})))
    previous = {}
    for relative in relatives:
        installed = destination(root, relative)
        previous[relative] = digest(installed)
        if previous[relative] is not None:
            saved = backup / "files" / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(installed, saved)
    (backup / "restore-manifest.json").write_text(json.dumps(previous, indent=2))
    checksum_source = None
    if checksum_data is not None:
        checksum_source = backup / "installed-SHA256SUMS.json"
        checksum_source.write_bytes(checksum_data)
    try:
        for relative in manifest["files"]:
            if relative in preserved:
                continue
            source = (checksum_source if relative == "SHA256SUMS.json" and checksum_source
                      else bundle / "files" / relative)
            replace_file(source, destination(root, relative))
        for relative in manifest.get("removed", {}):
            destination(root, relative).unlink(missing_ok=True)
        for relative, item in manifest["files"].items():
            expected = preserved.get(relative, item["sha256"])
            if relative == "SHA256SUMS.json" and checksum_data is not None:
                expected = hashlib.sha256(checksum_data).hexdigest()
            if digest(destination(root, relative)) != expected:
                raise ValueError(f"Installed checksum mismatch: {relative}")
    except Exception:
        for relative, original in previous.items():
            installed = destination(root, relative)
            if original is None:
                installed.unlink(missing_ok=True)
            else:
                replace_file(backup / "files" / relative, installed)
        raise
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((args.bundle / "patch-manifest.json").read_text(encoding="utf-8"))
    preflight(args.root, args.bundle, manifest)
    for relative in sorted(preserved_documents(args.root, manifest)):
        print(f"Preserving locally changed document: {relative}")
    if args.verify_only:
        print("Replacement payload and installed baseline checksums OK")
        return
    if args.backup_dir is None:
        parser.error("--backup-dir is required when applying a patch")
    backup = apply(args.root, args.bundle, manifest, args.backup_dir)
    print(f"Program replacement verified. Original program files: {backup}")


if __name__ == "__main__":
    main()
