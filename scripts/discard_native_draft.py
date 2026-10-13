"""Withdraw one unconsumed upload draft offline; quarantine its dedicated directory."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid4

# Embedded Windows Python can omit the script directory from sys.path.
_SPEC = spec_from_file_location("withdrawal_helpers", Path(__file__).with_name(
    "recover_native_precheck.py"))
_helpers = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_helpers)
DEFAULT_GROUP = _helpers.DEFAULT_GROUP
assert_native_stopped = _helpers.assert_native_stopped
checked_path = _helpers.checked_path
connection = _helpers.connection
installation = _helpers.installation
select_group = _helpers.select_group

DEFAULT_DRAFT = "c8a1c190-5a47-4844-acd8-7fc5d3c90986"


def target(db, group, draft_id):
    found = [row for row in select_group(db, group) if row["id"] == draft_id]
    if len(found) != 1:
        raise ValueError("TARGET_DRAFT_NOT_FOUND")
    row = found[0]
    if row["consumed_task_id"] is not None or row["status"] == "consumed":
        raise ValueError("DRAFT_CONSUMED_CANNOT_WITHDRAW")
    return row


def owned_directory(db, row, data):
    path = Path(row["storage_path"])
    parent = path.parent
    if not parent.is_absolute() or path.name != row["original_name"]:
        raise ValueError("UNEXPECTED_INPUT_PATH")
    UUID(parent.name)
    expected = data / "uploads" / "drafts" / parent.name
    if parent != expected:
        raise ValueError("ONLY_DEDICATED_DRAFT_DIRECTORY_IS_SUPPORTED")
    if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
        raise ValueError("LINKED_DRAFT_DIRECTORY_NOT_SUPPORTED")
    if parent.exists():
        checked_path(parent, data)
    else:
        checked_path(parent.parent, data)
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("LINKED_INPUT_NOT_SUPPORTED")
    report = json.loads(row["report"])
    prepared = report.get("prepared_dir")
    if prepared and not Path(prepared).resolve().is_relative_to(parent.resolve()):
        raise ValueError("PREPARED_DIRECTORY_IS_NOT_OWNED_BY_DRAFT")
    prefix = str(parent).replace("\\", "/").casefold().rstrip("/") + "/"
    for other in db.execute("SELECT id, storage_path FROM upload_drafts WHERE id != ?",
                            (row["id"],)):
        value = other["storage_path"].replace("\\", "/").casefold()
        if value == prefix.rstrip("/") or value.startswith(prefix):
            raise ValueError("DIRECTORY_REFERENCED_BY_OTHER_DRAFT")
    for batch in db.execute("SELECT file_path, raw_zip_path FROM batches"):
        for value in batch:
            normalized = str(value or "").replace("\\", "/").casefold()
            if normalized == prefix.rstrip("/") or normalized.startswith(prefix):
                raise ValueError("DIRECTORY_REFERENCED_BY_BATCH")
    return parent


def inspect(root, group, draft_id):
    _, _, data, database, _ = installation(root)
    with connection(database) as db:
        row = target(db, group, draft_id)
        directory = owned_directory(db, row, data)
        blockers = {"other_preparing_drafts": db.execute(
            "SELECT COUNT(*) FROM upload_drafts WHERE id != ? AND status = 'preparing'",
            (draft_id,),
        ).fetchone()[0]}
        for table in ("upload_tasks", "export_jobs"):
            blockers[table] = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE status NOT IN "
                "('success', 'failed', 'cancelled')"
            ).fetchone()[0]
    return {"read_only": True, "group_id": group, "draft_id": draft_id,
            "file": row["original_name"], "status": row["status"],
            "eligible_count": int(directory.exists() or row["status"] != "expired"),
            "directory_present": directory.exists(), "maintenance_blockers": blockers}


def discard(root, group, draft_id, *, stopped_guard=assert_native_stopped):
    root, config, data, database, port = installation(root)
    info = inspect(root, group, draft_id)
    if not info["eligible_count"]:
        return {"changed": False, "inspection": info}
    if any(info["maintenance_blockers"].values()):
        raise ValueError("OTHER_UNFINISHED_WORK_NO_CHANGES")
    stopped_guard(root, data, port)
    with connection(database) as db:
        before = target(db, group, draft_id)
        directory = owned_directory(db, before, data)
    # The same data volume allows rename, without rereading/copying a large ZIP.
    backup = data.parent / "aln-withdrawn-upload-backups" / (
        datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
    backup.mkdir(parents=True)
    with connection(database) as source, closing(sqlite3.connect(backup / "aln-data.db")) as sink:
        source.backup(sink)
        if sink.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("BACKUP_INTEGRITY_CHECK_FAILED")
    (backup / "server-config.json").write_bytes(config.read_bytes())
    (backup / "draft-before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    quarantined = backup / "upload-directory"
    moved = False
    try:
        with connection(database, write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            if target(db, group, draft_id) != before:
                raise ValueError("DRAFT_CHANGED_NO_CHANGES")
            owned_directory(db, before, data)
            stopped_guard(root, data, port)
            report = {"precheck_state": "stopped", "prepare_progress": 100,
                      "check_digest": None, "withdrawn_at": datetime.now(UTC).isoformat(),
                      "quarantine": str(quarantined)}
            db.execute("UPDATE upload_drafts SET status = 'expired', report = ? WHERE id = ?",
                       (json.dumps(report), draft_id))
            if directory.exists():
                directory.rename(quarantined)
                moved = True
        result = {"changed": True, "draft_id": draft_id, "status": "expired",
                  "backup": str(backup), "quarantined": moved,
                  "next_url": f"http://localhost:{port}/upload?groupId={uuid4()}"}
    except BaseException:
        if moved:
            quarantined.rename(directory)
        raise
    # Database is already committed: failure writing this receipt must not undo the rename.
    (backup / "withdrawal.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--group-id", default=DEFAULT_GROUP)
    parser.add_argument("--draft-id", default=DEFAULT_DRAFT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        group, draft_id = str(UUID(args.group_id)), str(UUID(args.draft_id))
        result = (discard(args.root, group, draft_id) if args.apply
                  else inspect(args.root, group, draft_id))
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error,
            subprocess.TimeoutExpired) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
