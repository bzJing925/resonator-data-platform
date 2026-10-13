"""Offline, group-scoped recovery; default inspection never starts the application."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

DEFAULT_GROUP = "3fa60c64-38a4-45a0-8123-1852c06dc184"
RECOVERY_CODE = "PRECHECK_INTERRUPTED"


def checked_path(path: Path, root: Path) -> Path:
    root, resolved = root.resolve(), path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("PATH_OUTSIDE_DATA_DIRECTORY")
    for parent in (path, *path.parents):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("LINKED_DATA_PATH_NOT_SUPPORTED")
        if parent == root:
            break
    return resolved


@contextmanager
def connection(path: Path, *, write=False):
    db = sqlite3.connect(path.resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"),
                         uri=True, timeout=5)
    try:
        with db:
            yield db
    finally:
        db.close()


def installation(root: Path):
    root = root.resolve(strict=True)
    config = root / "server-config.json"
    cfg = json.loads(config.read_text(encoding="utf-8-sig"))
    data = Path(cfg["data_dir"])
    if not data.is_absolute():
        raise ValueError("DATA_DIRECTORY_MUST_BE_ABSOLUTE")
    data = checked_path(data, data)
    database = checked_path(data / "aln-data.db", data)
    metadata = json.loads((root / "native-bundle.json").read_text(encoding="utf-8-sig"))
    if metadata.get("protocol") != 2:
        raise ValueError("ONLY_NATIVE_V2_SQLITE_IS_SUPPORTED")
    port = int(cfg.get("port", 8000))
    if not 1 <= port <= 65535:
        raise ValueError("INVALID_SERVER_PORT")
    return root, config, data, database, port


def select_group(db, group):
    db.row_factory = sqlite3.Row
    return [dict(row) for row in db.execute(
        "SELECT * FROM upload_drafts WHERE group_id = ? ORDER BY created_at, id", (group,)
    )]


def eligibility(row):
    report = json.loads(row["report"])
    expiry = datetime.fromisoformat(row["expires_at"])
    expiry = expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry.astimezone(UTC)
    if row["consumed_task_id"] is not None or row["status"] == "consumed":
        return False, "consumed"
    if row["status"] != "preparing":
        return False, row["status"]
    if expiry <= datetime.now(UTC):
        return False, "expired"
    if report.get("cancel_requested"):
        return False, "cancellation_requested"
    if report.get("precheck_state") not in {"queued", "running"}:
        return False, "unknown_precheck_state"
    return True, "interrupted_candidate"


def inspect(root: Path, group: str):
    _, _, data, database, _ = installation(root)
    with connection(database) as db:
        rows = select_group(db, group)
        blockers = {"other_preparing_drafts": db.execute(
            "SELECT COUNT(*) FROM upload_drafts WHERE group_id != ? AND status = 'preparing'",
            (group,),
        ).fetchone()[0]}
        for table in ("upload_tasks", "export_jobs"):
            blockers[table] = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE status NOT IN "
                "('success', 'failed', 'cancelled')"
            ).fetchone()[0]
    if not rows:
        raise ValueError("UPLOAD_GROUP_NOT_FOUND")
    drafts = []
    for row in rows:
        eligible, reason = eligibility(row)
        try:
            present = checked_path(Path(row["storage_path"]), data).is_file()
        except (ValueError, OSError):
            present = False
        report = json.loads(row["report"])
        drafts.append({"draft_id": row["id"], "file": row["original_name"],
                       "status": row["status"], "precheck_state": report.get("precheck_state"),
                       "progress": report.get("prepare_progress"), "eligible": eligible,
                       "reason": reason, "input_present": present,
                       "consumed_task_id": row["consumed_task_id"]})
    return {"read_only": True, "group_id": group, "drafts": drafts,
            "maintenance_blockers": blockers,
            "eligible_count": sum(row["eligible"] for row in drafts)}


def validate_process_snapshot(snapshot, root: Path, data: Path, own_pid: int):
    if snapshot["task_state"] not in {"Absent", "Disabled"}:
        raise ValueError("AUTOSTART_TASK_MUST_BE_DISABLED")
    needles = [str(path).lower().replace("\\", "/").rstrip("/") for path in (root, data)]
    for process in snapshot["processes"]:
        if int(process["ProcessId"]) == own_pid:
            continue
        executable = str(process.get("ExecutablePath") or "").lower().replace("\\", "/")
        command = str(process.get("CommandLine") or "").lower().replace("\\", "/")
        name = str(process.get("Name") or "").lower()
        if name in {"python.exe", "pythonw.exe"} and (not executable or not command):
            raise ValueError("PYTHON_PROCESS_OWNERSHIP_UNKNOWN")
        writer = name in {"python.exe", "pythonw.exe", "7za.exe", "7z.exe", "unzip.exe",
                          "robocopy.exe"}
        if writer and (not executable or not command):
            raise ValueError("WRITER_PROCESS_OWNERSHIP_UNKNOWN")
        launching = name in {"powershell.exe", "pwsh.exe", "cmd.exe"} and any(
            token in command for token in ("run_server.py", "start.ps1", "start-server.cmd")
        )
        if ((writer or launching) and any(needle in command for needle in needles)) or any(
            executable.startswith(needle + "/") for needle in needles
        ):
            raise ValueError(f"INSTALLATION_PROCESS_STILL_RUNNING_PID_{process['ProcessId']}")
        if name in {"python.exe", "pythonw.exe"} and any(
            token in command for token in ("run_server.py", "app.main", "celery")
        ):
            raise ValueError("OTHER_BACKEND_PROCESS_MUST_BE_CHECKED")


def assert_native_stopped(root: Path, data: Path, port: int):
    if sys.platform != "win32":
        raise ValueError("APPLY_REQUIRES_THE_WINDOWS_SERVER")
    code = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$Tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq 'ALN-Data-Server' })
$State = 'Absent'
if ($Tasks.Count -gt 1) { throw 'Ambiguous scheduled task' }
if ($Tasks.Count -eq 1) { $State = [string]$Tasks[0].State }
$Processes = @(Get-CimInstance Win32_Process -ErrorAction Stop |
    Select-Object ProcessId, Name, ExecutablePath, CommandLine)
@{ task_state=$State; processes=$Processes } | ConvertTo-Json -Depth 4 -Compress
"""
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", code],
                            capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError("CANNOT_CONFIRM_STOPPED_PROCESSES_RUN_AS_ADMINISTRATOR")
    snapshot = json.loads(result.stdout.decode("utf-8-sig"))
    validate_process_snapshot(snapshot, root, data, os.getpid())
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family) as probe:
                probe.settimeout(1)
                if probe.connect_ex((host, port)) == 0:
                    raise ValueError("SERVER_PORT_STILL_IN_USE")
        except OSError:
            if family == socket.AF_INET:
                raise


def digest(path: Path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def recover(root: Path, group: str, *, stopped_guard=assert_native_stopped,
            backup_root: Path | None = None):
    root, config, data, database, port = installation(root)
    with connection(database) as db:
        rows = select_group(db, group)
    if not rows:
        raise ValueError("UPLOAD_GROUP_NOT_FOUND")
    candidates = [row for row in rows if eligibility(row)[0]]
    if not candidates:
        return {"group_id": group, "recovered": [], "changed": False,
                "inspection": inspect(root, group)}
    stopped_guard(root, data, port)
    for row in candidates:
        path = checked_path(Path(row["storage_path"]), data)
        if not path.is_file() or digest(path) != row["input_digest"]:
            raise ValueError("INPUT_DIGEST_MISMATCH_NO_CHANGES")
    stopped_guard(root, data, port)
    parent = (backup_root or root.parent / "aln-precheck-recovery-backups").resolve()
    if parent.is_relative_to(data):
        raise ValueError("BACKUP_MUST_BE_OUTSIDE_DATA_DIRECTORY")
    backup = parent / (datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
    backup.mkdir(parents=True, exist_ok=False)
    with connection(database) as source:
        target = sqlite3.connect(backup / "aln-data.db")
        try:
            with target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("BACKUP_INTEGRITY_CHECK_FAILED")
        finally:
            target.close()
    shutil.copy2(config, backup / "server-config.json")
    (backup / "drafts-before.json").write_text(json.dumps(candidates, indent=2), encoding="utf-8")
    recovered = []
    with connection(database, write=True) as db:
        db.execute("BEGIN IMMEDIATE")
        current = select_group(db, group)
        if current != rows:
            raise ValueError("DRAFT_CHANGED_DURING_RECOVERY_NO_CHANGES")
        if not all(eligibility(row)[0] for row in candidates):
            raise ValueError("DRAFT_EXPIRED_DURING_RECOVERY_NO_CHANGES")
        stopped_guard(root, data, port)
        for row in candidates:
            # Removing the old lease fences a late old worker's final write/checkpoint.
            report = {"precheck_state": "interrupted", "prepare_progress": 1,
                      "quality_status": "unknown", "fatal_count": 1, "warning_codes": [],
                      "check_digest": None, "issues": [{"code": RECOVERY_CODE,
                          "severity": "fatal", "message": "升级中断预检查；原文件已保留，请复查",
                          "relpath": None}],
                      "recovered_at": datetime.now(UTC).isoformat()}
            cursor = db.execute(
                "UPDATE upload_drafts SET status = 'failed', report = ? "
                "WHERE id = ? AND group_id = ? AND status = 'preparing' "
                "AND consumed_task_id IS NULL AND report = ?",
                (json.dumps(report), row["id"], group, row["report"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("DRAFT_CAS_FAILED_NO_CHANGES")
            recovered.append(row["id"])
        (backup / "recovery.json").write_text(json.dumps({
            "group_id": group, "drafts": recovered,
            "verified_input_digests": {row["id"]: row["input_digest"] for row in candidates},
            "operation": "preparing_to_failed_for_manual_recheck", "backup": str(backup),
        }, indent=2), encoding="utf-8")
    # Only upload_drafts.status/report were updated. Original inputs/options/expiry stay intact.
    return {"group_id": group, "recovered": recovered, "changed": True,
            "backup": str(backup), "next_action": "Refresh the original upload page; click recheck"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--group-id", default=DEFAULT_GROUP)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    group = args.group_id
    try:
        group = str(UUID(group))
        result = recover(args.root, group) if args.apply else inspect(args.root, group)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error,
            subprocess.TimeoutExpired) as error:
        print(json.dumps({"error": str(error), "group_id": group}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
