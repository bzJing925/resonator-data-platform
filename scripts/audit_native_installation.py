"""Read-only audit of every replacement prerequisite; never start the application."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

METADATA_FIELDS = ("version", "protocol", "built_at", "python", "electron", "git_commit",
                   "includes_working_tree_changes", "wheel_count", "patch", "base_bundle")
DOCUMENTS = frozenset({"README.txt", "DEPLOYMENT.md"})
PROVENANCE = frozenset({"native-bundle.json", "SHA256SUMS.json"})


def safe_path(root: Path, relative: str) -> Path:
    if (not relative or relative.startswith("/") or "\\" in relative or ":" in relative
            or any(p in {"", ".", ".."} for p in relative.split("/"))):
        raise ValueError("Unsafe audit path")
    result = root.joinpath(*PurePosixPath(relative).parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Audit path escapes root")
    for path in (result, *result.parents):
        if path == root:
            break
        if path.is_symlink():
            raise ValueError("Audit path contains a link")
    return result


def digest(path: Path) -> str | None:
    if not path.exists():
        return None
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def audit(root: Path, bundle: Path) -> dict:
    manifest = json.loads((bundle / "patch-manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("format") != "aln-native-replacements-v1":
        raise ValueError("Unsupported replacement manifest")
    report = {"read_only": True, "installation": str(root), "bundle": str(bundle),
              "metadata": {}, "metadata_sha256": None, "conflicts": [],
              "local_documents": [], "errors": [], "checked": {}}
    try:
        path = safe_path(root, "native-bundle.json")
        report["metadata_sha256"] = digest(path)
        metadata = json.loads(path.read_text(encoding="utf-8-sig"))
        report["metadata"] = {k: metadata[k] for k in METADATA_FIELDS if k in metadata}
    except (OSError, ValueError) as exc:
        report["errors"].append({"path": "native-bundle.json", "reason": str(exc)})
    installed_checksums = {}
    try:
        path = safe_path(root, "SHA256SUMS.json")
        installed_checksums = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(installed_checksums, dict):
            raise ValueError("Installed checksum list is not an object")
    except (OSError, ValueError) as exc:
        report["errors"].append({"path": "SHA256SUMS.json", "reason": str(exc)})
        installed_checksums = {}

    def check(phase, relative, allowed, *, payload=False):
        try:
            actual = digest(safe_path(bundle / "files" if payload else root, relative))
        except (OSError, ValueError) as exc:
            report["errors"].append({"phase": phase, "path": relative, "reason": str(exc)})
            return
        report["checked"][phase] = report["checked"].get(phase, 0) + 1
        if actual in allowed:
            return
        item = {"phase": phase, "path": relative, "actual_sha256": actual,
                "allowed_sha256": allowed}
        if not payload:
            item["installed_manifest_sha256"] = installed_checksums.get(relative)
        if relative in DOCUMENTS and not payload:
            report["local_documents"].append(item)
        else:
            item["kind"] = "provenance" if relative in PROVENANCE else "program_or_dependency"
            report["conflicts"].append(item)

    for relative, item in manifest["files"].items():
        check("payload", relative, [item["sha256"]], payload=True)
        check("replacement_baseline", relative, item["allowed_before"])
    for relative, allowed in manifest.get("unchanged_required", {}).items():
        check("unchanged_dependency", relative, allowed)
    for relative, expected in manifest.get("removed", {}).items():
        check("obsolete_file", relative, [None, expected])
    report["summary"] = {
        "conflicts": len(report["conflicts"]), "errors": len(report["errors"]),
        "local_documents": len(report["local_documents"]),
        "program_or_dependency_conflicts": sum(i["kind"] == "program_or_dependency"
                                              for i in report["conflicts"]),
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    # Stdout only: no files, configuration, databases or processes are modified.
    print(json.dumps(audit(args.root.resolve(), args.bundle.resolve()),
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
