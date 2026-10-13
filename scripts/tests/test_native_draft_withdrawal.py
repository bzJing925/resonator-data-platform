"""One-draft withdrawal retains its files in quarantine and preserves all other data."""

import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from test_native_precheck_recovery import GROUP, add_draft
from test_native_precheck_recovery import installed as installed

from app.models import Batch, UploadDraft
from app.services import upload_draft_service as service
from tests.fixtures.platform_cases import _algorithm_samples, _write_archive

SOURCE = Path(__file__).resolve().parents[1] / "discard_native_draft.py"
sys.path.insert(0, str(SOURCE.parent))
try:
    SPEC = importlib.util.spec_from_file_location("draft_withdrawal", SOURCE)
    withdrawal = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(withdrawal)
finally:
    sys.path.pop(0)


def create(installed, **kwargs):
    draft_id, source = add_draft(installed, **kwargs)
    directory = installed[1] / "uploads" / "drafts" / str(uuid4())
    directory.mkdir(parents=True)
    saved = directory / "#2.zip"
    source.rename(saved)
    (directory / "inspection-old" / "nested").mkdir(parents=True)
    (directory / "inspection-old" / "nested" / "sample.s1p").write_bytes(b"partial sample")
    with Session(installed[2]) as db:
        draft = db.get(UploadDraft, draft_id)
        draft.storage_path, draft.original_name = str(saved), saved.name
        db.commit()
    return draft_id, directory


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_withdrawal_moves_whole_owned_directory_and_backups_database(installed):
    root, data, engine, _ = installed
    draft_id, directory = create(installed)
    other_id, other = create(installed, status="ready", group=str(uuid4()))
    original, protected = snapshot(directory), snapshot(other)
    with Session(engine) as db:
        previous_options = db.get(UploadDraft, draft_id).upload_options
        previous_request = db.get(UploadDraft, draft_id).request_key
    calls = []
    def guard(*args):
        calls.append(True)
    result = withdrawal.discard(root, GROUP, draft_id, stopped_guard=guard)
    assert result["changed"] and result["quarantined"] and len(calls) == 2
    assert not directory.exists() and snapshot(other) == protected
    backup = Path(result["backup"])
    assert snapshot(backup / "upload-directory") == original
    assert not backup.is_relative_to(data)
    assert "groupId=" in result["next_url"] and GROUP not in result["next_url"]
    with Session(engine) as db:
        draft = db.get(UploadDraft, draft_id)
        assert draft.status == "expired" and draft.report["precheck_state"] == "stopped"
        assert "lease_token" not in draft.report and draft.report["check_digest"] is None
        assert draft.upload_options == previous_options and draft.request_key == previous_request
        assert db.get(UploadDraft, other_id).status == "ready"
    with withdrawal.connection(backup / "aln-data.db") as db:
        previous = withdrawal.target(db, GROUP, draft_id)
        assert previous["status"] == "preparing"
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not withdrawal.discard(root, GROUP, draft_id, stopped_guard=guard)["changed"]
    assert len(calls) == 2


@pytest.mark.parametrize("consumed", [False, True])
def test_consumed_status_or_link_refused_before_stop(installed, consumed):
    draft_id, _ = create(installed, **({"consumed": 123} if consumed else {"status": "consumed"}))
    before = snapshot(installed[0].parent)
    with pytest.raises(ValueError, match="DRAFT_CONSUMED"):
        withdrawal.discard(installed[0], GROUP, draft_id)
    assert snapshot(installed[0].parent) == before


def test_other_preparing_draft_even_in_same_group_prevents_stop(installed):
    draft_id, _ = create(installed)
    create(installed)
    before = snapshot(installed[0].parent)
    with pytest.raises(ValueError, match="OTHER_UNFINISHED"):
        withdrawal.discard(installed[0], GROUP, draft_id)
    assert snapshot(installed[0].parent) == before


def test_unconfirmed_stop_has_no_database_or_file_changes(installed):
    draft_id, _ = create(installed)
    before = snapshot(installed[0].parent)
    def running(*args):
        raise ValueError("STILL_RUNNING")
    with pytest.raises(ValueError, match="STILL_RUNNING"):
        withdrawal.discard(installed[0], GROUP, draft_id, stopped_guard=running)
    assert snapshot(installed[0].parent) == before


def test_directory_rename_failure_rolls_back_database(installed, monkeypatch):
    draft_id, directory = create(installed)
    before = snapshot(directory)
    def fail(*args):
        raise OSError("rename test failure")
    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError, match="rename test failure"):
        withdrawal.discard(installed[0], GROUP, draft_id, stopped_guard=lambda *args: None)
    assert snapshot(directory) == before
    with Session(installed[2]) as db:
        assert db.get(UploadDraft, draft_id).status == "preparing"


def test_missing_input_still_quarantines_partial_inspection(installed):
    draft_id, directory = create(installed, expired=True)
    (directory / "#2.zip").unlink()
    before = snapshot(directory)
    result = withdrawal.discard(installed[0], GROUP, draft_id, stopped_guard=lambda *args: None)
    assert snapshot(Path(result["backup"]) / "upload-directory") == before


def test_shared_draft_directory_and_batch_reference_are_refused(installed):
    draft_id, directory = create(installed)
    other_id, _ = create(installed, status="ready")
    with Session(installed[2]) as db:
        db.get(UploadDraft, other_id).storage_path = str(directory / "other.zip")
        db.commit()
    with pytest.raises(ValueError, match="REFERENCED_BY_OTHER_DRAFT"):
        withdrawal.inspect(installed[0], GROUP, draft_id)
    with Session(installed[2]) as db:
        db.delete(db.get(UploadDraft, other_id))
        db.add(Batch(batch_no="protected", mapping_id=installed[3], source="http",
                     file_path=str(directory)))
        db.commit()
    with pytest.raises(ValueError, match="REFERENCED_BY_BATCH"):
        withdrawal.inspect(installed[0], GROUP, draft_id)


def test_readonly_inspection_and_nonwindows_apply_refusal(installed):
    draft_id, _ = create(installed)
    before = snapshot(installed[0].parent)
    result = withdrawal.inspect(installed[0], GROUP, draft_id)
    assert result["read_only"] and result["file"] == "#2.zip"
    if sys.platform != "win32":
        with pytest.raises(ValueError, match="APPLY_REQUIRES_THE_WINDOWS_SERVER"):
            withdrawal.discard(installed[0], GROUP, draft_id)
    assert snapshot(installed[0].parent) == before


def test_change_before_baseline_is_backed_up(installed):
    draft_id, directory = create(installed)
    calls = []
    def change(*args):
        calls.append(True)
        if len(calls) == 1:
            with Session(installed[2]) as db:
                db.get(UploadDraft, draft_id).report = {"changed": True}
                db.commit()
    # The first guard precedes the baseline read: this permitted change becomes the baseline.
    result = withdrawal.discard(installed[0], GROUP, draft_id, stopped_guard=change)
    assert result["quarantined"] and not directory.exists()
    assert json.loads((Path(result["backup"]) / "draft-before.json").read_text())["report"] == (
        '{"changed": true}')


def test_change_after_baseline_is_refused(installed, monkeypatch):
    draft_id, directory = create(installed)
    original = withdrawal.connection
    calls = []
    @contextmanager
    def raced(path, **kwargs):
        calls.append(True)
        if len(calls) == 3:
            with Session(installed[2]) as db:
                db.get(UploadDraft, draft_id).report = {"concurrent_change": True}
                db.commit()
        with original(path, **kwargs) as db:
            yield db
    monkeypatch.setattr(withdrawal, "connection", raced)
    with pytest.raises(ValueError, match="DRAFT_CHANGED"):
        withdrawal.discard(installed[0], GROUP, draft_id, stopped_guard=lambda *args: None)
    assert directory.exists()
    with Session(installed[2]) as db:
        draft = db.get(UploadDraft, draft_id)
        assert draft.status == "preparing" and draft.report == {"concurrent_change": True}


def test_failure_after_move_before_commit_restores_directory(installed, monkeypatch):
    draft_id, directory = create(installed)
    before = snapshot(directory)
    original = withdrawal.connection
    @contextmanager
    def fail_commit(path, **kwargs):
        with original(path, **kwargs) as db:
            yield db
            if kwargs.get("write"):
                raise OSError("commit test failure")
    monkeypatch.setattr(withdrawal, "connection", fail_commit)
    with pytest.raises(OSError, match="commit test failure"):
        withdrawal.discard(installed[0], GROUP, draft_id, stopped_guard=lambda *args: None)
    assert snapshot(directory) == before
    with Session(installed[2]) as db:
        assert db.get(UploadDraft, draft_id).status == "preparing"


@pytest.mark.parametrize("damage", ["outside", "symlink", "prepared_outside"])
def test_invalid_owned_path_is_refused_before_stop(installed, damage):
    draft_id, directory = create(installed)
    with Session(installed[2]) as db:
        draft = db.get(UploadDraft, draft_id)
        if damage == "outside":
            draft.storage_path = str(installed[0] / "outside.zip")
        elif damage == "prepared_outside":
            draft.report = {"prepared_dir": str(installed[0])}
        else:
            actual = directory.with_name(str(uuid4()))
            directory.rename(actual)
            directory.symlink_to(actual)
        db.commit()
    before = snapshot(installed[0].parent)
    with pytest.raises(ValueError):
        withdrawal.discard(installed[0], GROUP, draft_id)
    assert snapshot(installed[0].parent) == before


def test_fresh_group_can_prepare_and_check_after_withdrawal(installed, monkeypatch):
    root, data, engine, mapping_id = installed
    draft_id, directory = create(installed)
    samples = _algorithm_samples(data)
    local = _write_archive(data / "local.zip", [("17_E6-1_X0Y0.s1p", samples["dut_s1p"])])
    (directory / "#2.zip").write_bytes(local.read_bytes())
    result = withdrawal.discard(root, GROUP, draft_id, stopped_guard=lambda *args: None)
    new_group = result["next_url"].split("groupId=")[1]
    fresh_dir = data / "uploads" / "drafts" / str(uuid4())
    fresh_dir.mkdir()
    fresh_source = fresh_dir / "#2.zip"
    fresh_source.write_bytes(local.read_bytes())
    fresh_id, created = service.prepare_draft(
        engine, fresh_source, "#2.zip", {"mapping_id": mapping_id}, str(uuid4()), new_group)
    assert created and fresh_id != draft_id
    monkeypatch.setattr("app.workers.extract_batch._find_7z", lambda: None)
    service.run_precheck(engine, fresh_id)
    with Session(engine) as db:
        assert db.get(UploadDraft, draft_id).status == "expired"
        fresh = db.get(UploadDraft, fresh_id)
        assert fresh.status == "ready" and fresh.report["fatal_count"] == 0
        assert fresh.report["prepare_progress"] == 100 and fresh.group_id == new_group
    with pytest.raises(service.DraftError) as error:
        service.recheck_draft(engine, draft_id, {})
    assert error.value.code == "DRAFT_EXPIRED"
