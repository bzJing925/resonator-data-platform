"""Offline repair preserves inputs and fences old checks; no live server is used."""

import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Mapping, UploadDraft
from app.services import upload_draft_service as service
from app.services.upload_precheck import validate_upload_options
from tests.fixtures.platform_cases import _algorithm_samples, _write_archive

SOURCE = Path(__file__).resolve().parents[1] / "recover_native_precheck.py"
SPEC = importlib.util.spec_from_file_location("precheck_recovery", SOURCE)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)
GROUP = repair.DEFAULT_GROUP


@pytest.fixture
def installed(tmp_path):
    root, data = tmp_path / "installed", tmp_path / "customer-data"
    root.mkdir()
    data.mkdir()
    (root / "server-config.json").write_text(json.dumps({"data_dir": str(data), "port": 8000}))
    (root / "native-bundle.json").write_text(json.dumps({"protocol": 2, "version": "0.3.5"}))
    engine = create_engine(f"sqlite:///{data / 'aln-data.db'}")
    Base.metadata.create_all(engine)
    mapping = data / "mapping.csv"
    mapping.write_text("E6-1,EG0 FL0 700&5500\n")
    with Session(engine) as db:
        item = Mapping(name="ELB003", file_path=str(mapping), entry_count=1)
        db.add(item)
        db.commit()
        mapping_id = item.id
    yield root, data, engine, mapping_id
    engine.dispose()


def add_draft(installed, *, group=GROUP, status="preparing", report=None, expired=False,
              consumed=None, payload=b"uploaded original"):
    _, data, engine, mapping_id = installed
    source = data / (str(uuid4()) + ".zip")
    source.write_bytes(payload)
    expiry = datetime.now(UTC) + timedelta(hours=-1 if expired else 24)
    with Session(engine) as db:
        draft = UploadDraft(
            group_id=group, request_key=str(uuid4()), status=status, storage_path=str(source),
            input_digest=repair.digest(source), original_name="#17.zip",
            upload_options=validate_upload_options({"mapping_id": mapping_id}),
            report=report or {"precheck_state": "running", "prepare_progress": 1,
                              "lease_token": "old-lease"},
            expires_at=expiry, consumed_task_id=consumed,
        )
        db.add(draft)
        db.commit()
        return draft.id, source


def rows(installed, group=GROUP):
    with repair.connection(installed[1] / "aln-data.db") as db:
        return repair.select_group(db, group)


def unchanged_files(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_inspect_readonly_and_missing_database_never_created(installed):
    root, data, _, _ = installed
    draft_id, _ = add_draft(installed)
    before = unchanged_files(root.parent)
    result = repair.inspect(root, GROUP)
    assert result["read_only"] and result["eligible_count"] == 1
    assert result["drafts"][0]["draft_id"] == draft_id
    assert result["drafts"][0]["input_present"]
    assert not any(result["maintenance_blockers"].values())
    assert unchanged_files(root.parent) == before
    (data / "aln-data.db").unlink()
    with pytest.raises(FileNotFoundError):
        repair.inspect(root, GROUP)
    assert not (data / "aln-data.db").exists()


def test_other_group_precheck_is_reported_before_automatic_stop(installed):
    add_draft(installed)
    add_draft(installed, group=str(uuid4()))
    result = repair.inspect(installed[0], GROUP)
    assert result["maintenance_blockers"]["other_preparing_drafts"] == 1


@pytest.mark.parametrize("state", ["running", "queued"])
def test_group_scoped_repair_backup_and_repeat_noop(installed, state):
    root, data, engine, _ = installed
    draft_id, source = add_draft(installed, report={"precheck_state": state, "lease_token": "old"})
    other_group = str(uuid4())
    add_draft(installed, group=other_group)
    add_draft(installed, status="ready")
    before, other_before = rows(installed), rows(installed, other_group)
    original = source.read_bytes()
    guard_calls = []
    def guard(*args):
        guard_calls.append(args)
    result = repair.recover(root, GROUP, stopped_guard=guard)
    assert result["changed"] and result["recovered"] == [draft_id] and len(guard_calls) == 3
    after = rows(installed)
    for old, new in zip(before, after, strict=True):
        assert {k: v for k, v in old.items() if k not in {"status", "report"}} == {
            k: v for k, v in new.items() if k not in {"status", "report"}}
        if old["id"] != draft_id:
            assert old == new
        else:
            assert new["status"] == "failed"
            report = json.loads(new["report"])
            assert "lease_token" not in report and report["check_digest"] is None
            assert report["issues"][0]["code"] == "PRECHECK_INTERRUPTED"
    assert source.read_bytes() == original and rows(installed, other_group) == other_before
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Batch)) == 0
    backup = Path(result["backup"])
    assert not backup.is_relative_to(data)
    with repair.connection(backup / "aln-data.db") as db:
        assert repair.select_group(db, GROUP) == before
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (backup / "server-config.json").read_bytes() == (
        root / "server-config.json").read_bytes()
    assert json.loads((backup / "recovery.json").read_text())["drafts"] == [draft_id]
    assert not repair.recover(root, GROUP, stopped_guard=guard)["changed"]
    assert len(guard_calls) == 3


@pytest.mark.parametrize("kwargs,reason", [
    ({"status": "ready"}, "ready"), ({"status": "failed"}, "failed"),
    ({"status": "consumed"}, "consumed"), ({"consumed": 123}, "consumed"),
    ({"expired": True}, "expired"), ({"status": "expired"}, "expired"),
    ({"report": {"precheck_state": "running", "cancel_requested": True}},
     "cancellation_requested"),
    ({"report": {"precheck_state": "finished"}}, "unknown_precheck_state"),
])
def test_ineligible_drafts_never_changed_or_backed_up(installed, kwargs, reason):
    root, _, _, _ = installed
    add_draft(installed, **kwargs)
    before = unchanged_files(root.parent)
    def forbidden(*args):
        pytest.fail("A no-op must not stop/check processes")
    assert repair.inspect(root, GROUP)["drafts"][0]["reason"] == reason
    assert not repair.recover(root, GROUP, stopped_guard=forbidden)["changed"]
    assert unchanged_files(root.parent) == before


@pytest.mark.parametrize("damage", ["changed", "missing", "outside", "link"])
def test_invalid_original_aborts_before_backup_or_database_change(installed, damage):
    root, data, engine, _ = installed
    draft_id, source = add_draft(installed)
    if damage == "changed":
        source.write_bytes(b"modified")
    elif damage == "missing":
        source.unlink()
    elif damage == "outside":
        outside = root / "outside.zip"
        outside.write_bytes(source.read_bytes())
        with Session(engine) as db:
            db.get(UploadDraft, draft_id).storage_path = str(outside)
            db.commit()
    else:
        actual = data / "original.zip"
        source.rename(actual)
        source.symlink_to(actual)
    before = unchanged_files(root.parent)
    with pytest.raises((ValueError, FileNotFoundError)):
        repair.recover(root, GROUP, stopped_guard=lambda *args: None)
    assert unchanged_files(root.parent) == before


def test_unconfirmed_stop_aborts_before_backup(installed):
    root, _, _, _ = installed
    add_draft(installed)
    before = unchanged_files(root.parent)
    def unknown(*args):
        raise ValueError("INSTALLATION_PROCESS_STILL_RUNNING")
    with pytest.raises(ValueError, match="STILL_RUNNING"):
        repair.recover(root, GROUP, stopped_guard=unknown)
    assert unchanged_files(root.parent) == before


def test_concurrent_change_is_preserved_and_refused(installed):
    root, _, engine, _ = installed
    draft_id, _ = add_draft(installed)
    calls = []
    def changed(*args):
        calls.append(True)
        if len(calls) == 2:
            with Session(engine) as db:
                db.get(UploadDraft, draft_id).report = {"precheck_state": "running", "new": True}
                db.commit()
    with pytest.raises(ValueError, match="DRAFT_CHANGED"):
        repair.recover(root, GROUP, stopped_guard=changed)
    assert json.loads(rows(installed)[0]["report"]) == {"precheck_state": "running", "new": True}


def test_transaction_rolls_back_every_draft_on_second_update_failure(installed):
    root, data, _, _ = installed
    add_draft(installed)
    second_id, _ = add_draft(installed)
    with sqlite3.connect(data / "aln-data.db") as db:
        db.execute("CREATE TRIGGER reject_second BEFORE UPDATE ON upload_drafts "
                   f"WHEN NEW.id = '{second_id}' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    before = rows(installed)
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        repair.recover(root, GROUP, stopped_guard=lambda *args: None)
    assert rows(installed) == before


@pytest.mark.parametrize("state,process,error", [
    ("Ready", None, "AUTOSTART_TASK"),
    ("Disabled", {"Name": "python.exe", "ExecutablePath": None}, "OWNERSHIP_UNKNOWN"),
    ("Disabled", {"Name": "python.exe", "ExecutablePath": r"C:\python.exe"},
     "OWNERSHIP_UNKNOWN"),
    ("Disabled", {"Name": "7za.exe", "ExecutablePath": None}, "OWNERSHIP_UNKNOWN"),
    ("Disabled", {"Name": "python.exe", "ExecutablePath": r"E:\ALN\runtime\python.exe",
                  "CommandLine": "python run_server.py"}, "STILL_RUNNING"),
    ("Disabled", {"Name": "7za.exe", "ExecutablePath": r"C:\7za.exe",
                  "CommandLine": r"7za x E:\DATA\input.zip"}, "STILL_RUNNING"),
    ("Disabled", {"Name": "python.exe", "ExecutablePath": r"C:\python.exe",
                  "CommandLine": "python -m app.main"}, "OTHER_BACKEND"),
    ("Disabled", {"Name": "powershell.exe", "ExecutablePath": r"C:\powershell.exe",
                  "CommandLine": r"powershell -File E:\ALN\start.ps1"}, "STILL_RUNNING"),
])
def test_windows_writer_guards(state, process, error):
    snapshot = {"task_state": state, "processes": [{"ProcessId": 2, **process}] if process else []}
    with pytest.raises(ValueError, match=error):
        repair.validate_process_snapshot(snapshot, Path("E:/ALN"), Path("E:/DATA"), 1)


def test_own_helper_and_unrelated_processes_allowed():
    snapshot = {"task_state": "Absent", "processes": [
        {"ProcessId": 1, "Name": "python.exe", "ExecutablePath": r"E:\ALN\runtime\python.exe"},
        {"ProcessId": 2, "Name": "powershell.exe", "ExecutablePath": r"C:\powershell.exe",
         "CommandLine": r"powershell -File E:\repair\recover-native-precheck.ps1 "
                        r"-InstallRoot E:\ALN"},
        {"ProcessId": 3, "Name": "python.exe", "ExecutablePath": r"C:\python.exe",
         "CommandLine": "python unrelated.py"},
    ]}
    repair.validate_process_snapshot(snapshot, Path("E:/ALN"), Path("E:/DATA"), 1)


def test_actual_busy_recovery_recheck_valid_zip_and_commit_gate(installed, monkeypatch):
    root, data, engine, _ = installed
    samples = _algorithm_samples(data)
    archive = _write_archive(data / "valid.zip", [("17_E6-1_X0Y0.s1p", samples["dut_s1p"])])
    draft_id, source = add_draft(installed, payload=archive.read_bytes())
    before = rows(installed)[0]
    with pytest.raises(service.DraftError, match="草稿正在处理或已消费"):
        service.recheck_draft(engine, draft_id, {})
    repair.recover(root, GROUP, stopped_guard=lambda *args: None)
    with pytest.raises(service.DraftError) as error:
        service.commit_draft(engine, draft_id, before["request_key"], "old-digest", [])
    assert error.value.code == "DRAFT_NOT_READY"
    service.recheck_draft(engine, draft_id, {})
    monkeypatch.setattr("app.workers.extract_batch._find_7z", lambda: None)
    service.run_precheck(engine, draft_id)
    with Session(engine) as db:
        draft = db.get(UploadDraft, draft_id)
        assert draft.status == "ready" and draft.report["prepare_progress"] == 100
        assert draft.report["fatal_count"] == 0 and draft.report["check_digest"]
        assert draft.report["lease_token"] != "old-lease"
        assert db.scalar(select(func.count()).select_from(Batch)) == 0
        assert draft.request_key == before["request_key"]
        assert draft.upload_options == json.loads(before["upload_options"])
        assert source.read_bytes() == archive.read_bytes()


def test_late_old_worker_cannot_overwrite_recovered_draft(installed, monkeypatch):
    root, _, engine, _ = installed
    draft_id, _ = add_draft(installed, report={"precheck_state": "queued", "prepare_progress": 0})
    def interrupted(*args, **kwargs):
        repair.recover(root, GROUP, stopped_guard=lambda *args: None)
        with pytest.raises(InterruptedError):
            kwargs["checkpoint"]()
        return {"fatal_count": 0, "check_digest": "stale-result"}
    monkeypatch.setattr(service, "build_precheck_report", interrupted)
    service.run_precheck(engine, draft_id)
    report = json.loads(rows(installed)[0]["report"])
    assert rows(installed)[0]["status"] == "failed" and report["check_digest"] is None
    assert report["issues"][0]["code"] == repair.RECOVERY_CODE


def test_expiry_timezone_and_nonwindows_cli_apply_guard(installed):
    root, _, _, _ = installed
    add_draft(installed)
    row = rows(installed)[0]
    row["expires_at"] = "2000-01-01T12:00:00+08:00"
    assert repair.eligibility(row) == (False, "expired")
    before = unchanged_files(root.parent)
    import sys
    if sys.platform != "win32":
        with pytest.raises(ValueError, match="APPLY_REQUIRES_THE_WINDOWS_SERVER"):
            repair.recover(root, GROUP)
        assert unchanged_files(root.parent) == before
