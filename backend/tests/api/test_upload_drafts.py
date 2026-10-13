"""Real SQLite/PG draft lifecycle over HTTP; dispatch is an explicit test boundary."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.main import app
from app.models import ProcessingRun, UploadDraft, UploadTask
from app.services.upload_draft_service import run_precheck
from app.workers.dispatch import ConfirmedDispatchError
from tests.fixtures.platform_cases import _algorithm_samples, _write_archive
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def draft_api(request, tmp_path, monkeypatch):
    with platform_database(request.param, tmp_path) as (db, batch):
        mapping = tmp_path / "mapping.csv"
        mapping.write_text("E6-1,EG0 FL0 700&5500\n")
        batch.mapping.file_path = str(mapping)
        db.commit()
        mapping_id = batch.mapping_id
        engine = db.bind
        settings = Settings(
            DATA_ROOT=tmp_path / "data", WATCH_ENABLED=False, ALN_DESKTOP_MODE=False
        )
        monkeypatch.setattr("app.api.upload.get_settings", lambda: settings)
        monkeypatch.setattr("app.workers.extract_batch._find_7z", lambda: None)
        delivered = []
        monkeypatch.setattr(
            "app.workers.dispatch.dispatch_precheck_job",
            lambda draft_id: run_precheck(engine, draft_id) or "prechecked",
        )
        monkeypatch.setattr(
            "app.services.upload_draft_service.dispatch_batch_task",
            lambda *args, **kwargs: delivered.append((args, kwargs)) or "accepted",
        )

        def override_db():
            with Session(engine) as session:
                yield session

        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        samples = _algorithm_samples(tmp_path)
        archive = _write_archive(tmp_path / "valid.zip", [("17_E6-1_X0Y0.s1p", samples["dut_s1p"])])
        try:
            yield client, db, mapping_id, archive, mapping, delivered
        finally:
            client.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)


def prepare(fixture, *, payload=None, key=None, group=None):
    client, _db, mapping_id, archive, _mapping, _delivered = fixture
    key, group = key or str(uuid4()), group or str(uuid4())
    response = client.post(
        "/api/uploads/prepare",
        files={"file": ("valid.zip", payload or archive.read_bytes())},
        data={"mapping_id": mapping_id, "request_key": key, "group_id": group},
    )
    assert response.status_code == 202, response.text
    draft_id = response.json()["draft_id"]
    report = client.get(f"/api/uploads/drafts/{draft_id}").json()
    return draft_id, key, group, report


def commit(client, draft_id, key, report, warnings=None):
    return client.post(
        f"/api/uploads/drafts/{draft_id}/commit",
        json={
            "request_key": key,
            "check_digest": report["check_digest"],
            "acknowledged_warning_codes": warnings or [],
        },
    )


def test_once_prepare_commit_and_group_restore(draft_api):
    client, db, _mapping_id, archive, _mapping, delivered = draft_api
    draft_id, key, group, report = prepare(draft_api)
    assert report["status"] == "ready"
    repeated, _, _, _ = prepare(draft_api, key=key, group=group)
    assert repeated == draft_id
    first = commit(client, draft_id, key, report)
    second = commit(client, draft_id, key, report)
    assert first.status_code == second.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"] and len(delivered) == 1
    db.expire_all()
    assert db.scalar(select(func.count(ProcessingRun.id))) == 1
    restored = client.get(f"/api/uploads/groups/{group}").json()["drafts"]
    assert restored[0]["consumed_task_id"] == int(first.json()["task_id"])
    assert "prepared_dir" not in restored[0]["report"]
    assert archive.exists()


def test_request_key_conflict_and_stale_mapping(draft_api):
    client, _db, mapping_id, archive, mapping, _delivered = draft_api
    draft_id, key, group, report = prepare(draft_api)
    response = client.post(
        "/api/uploads/prepare",
        files={"file": ("valid.zip", archive.read_bytes() + b"changed")},
        data={"mapping_id": mapping_id, "request_key": key, "group_id": group},
    )
    assert response.status_code == 409
    mapping.write_text(mapping.read_text() + "E6-2,EG1 FL0 1&2\n")
    response = commit(client, draft_id, key, report)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "PRECHECK_STALE"
    response = client.post(f"/api/uploads/drafts/{draft_id}/recheck", json={})
    assert response.status_code == 202
    refreshed = client.get(f"/api/uploads/drafts/{draft_id}").json()
    assert refreshed["check_digest"] != report["check_digest"]
    assert commit(client, draft_id, key, report).status_code == 409
    assert commit(client, draft_id, key, refreshed).status_code == 200


def test_warning_ack_fatal_and_expiry(draft_api, tmp_path):
    client, db, _mapping_id, archive, _mapping, _delivered = draft_api
    with __import__("zipfile").ZipFile(archive, "a") as output:
        output.writestr("note.txt", "unrelated")
    draft_id, key, _, report = prepare(draft_api)
    assert commit(client, draft_id, key, report).status_code == 422
    assert (
        commit(client, draft_id, key, report, report["report"]["warning_codes"]).status_code == 200
    )
    bad = _write_archive(tmp_path / "empty.zip", [])
    fatal_id, fatal_key, _, fatal = prepare(draft_api, payload=bad.read_bytes())
    assert (
        fatal["status"] == "failed"
        and commit(client, fatal_id, fatal_key, fatal).status_code == 422
    )
    expiry_id, expiry_key, _, expiry_report = prepare(draft_api)
    db.expire_all()
    db.get(UploadDraft, expiry_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert commit(client, expiry_id, expiry_key, expiry_report).status_code == 410


def test_dispatch_failure_consumes_one_attempt_and_preserves_input(draft_api, monkeypatch):
    client, db, _mapping_id, _archive, _mapping, _delivered = draft_api
    draft_id, key, _, report = prepare(draft_api)

    def rejected(*args, **kwargs):
        raise ConfirmedDispatchError("queue rejected")

    monkeypatch.setattr("app.services.upload_draft_service.dispatch_batch_task", rejected)
    response = commit(client, draft_id, key, report)
    assert response.status_code == 503
    accepted = response.json()["detail"]["context"]
    assert accepted["status"] == "failed"
    assert commit(client, draft_id, key, report).json()["task_id"] == accepted["task_id"]
    db.expire_all()
    draft = db.get(UploadDraft, draft_id)
    assert draft.status == "consumed"
    assert db.get(UploadTask, draft.consumed_task_id).status == "failed"
    assert db.scalar(select(func.count(ProcessingRun.id))) == 1
    from pathlib import Path

    assert Path(draft.storage_path).exists()


def test_prechecked_table_has_independent_run_and_retains_failure_report(
    draft_api, tmp_path, monkeypatch
):
    from sqlalchemy.orm import sessionmaker

    from app.models import Device
    from app.workers.precheck import import_prechecked_table_task
    from tests.services.test_upload_precheck import table_frame

    client, db, mapping_id, _archive, _mapping, _delivered = draft_api
    table = tmp_path / "parameters.xlsx"
    table_frame().to_excel(table, index=False)
    monkeypatch.setattr("app.db.engine", db.bind)
    monkeypatch.setattr("app.db.SessionLocal", sessionmaker(db.bind))
    monkeypatch.setattr(
        "app.workers.dispatch.dispatch_prechecked_table",
        lambda *args: import_prechecked_table_task.apply(args=args).get(propagate=True) or "table",
    )
    key, group = str(uuid4()), str(uuid4())
    response = client.post(
        "/api/uploads/prepare",
        files={"file": (table.name, table.read_bytes())},
        data={"mapping_id": mapping_id, "request_key": key, "group_id": group},
    )
    assert response.status_code == 202
    draft_id = response.json()["draft_id"]
    report = client.get(f"/api/uploads/drafts/{draft_id}").json()
    assert report["status"] == "ready" and report["report"]["row_failures"]
    response = commit(client, draft_id, key, report, report["report"]["warning_codes"])
    assert response.status_code == 200 and response.json()["status"] == "success"
    db.expire_all()
    run = db.get(ProcessingRun, response.json()["run_id"])
    assert run.status == "success" and run.task_id == int(response.json()["task_id"])
    assert db.scalar(select(func.count(Device.id))) == 1


def test_running_draft_delete_waits_for_confirmed_writer_exit(draft_api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Event

    from app.services.upload_draft_service import delete_draft, prepare_draft

    _client, db, mapping_id, archive, _mapping, _delivered = draft_api
    draft_id, _ = prepare_draft(
        db.bind, archive, archive.name, {"mapping_id": mapping_id}, str(uuid4()), str(uuid4())
    )
    entered, release = Event(), Event()

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        kwargs["checkpoint"]()
        return {"fatal_count": 0}

    monkeypatch.setattr("app.services.upload_draft_service.build_precheck_report", blocked)
    engine = db.bind
    db.rollback()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_precheck, engine, draft_id)
        assert entered.wait(timeout=5)
        result = delete_draft(engine, draft_id)
        assert result["stop_confirmed"] is False and archive.exists()
        release.set()
        future.result(timeout=5)
    db.expire_all()
    draft = db.get(UploadDraft, draft_id)
    assert draft.status == "expired" and not Path(draft.storage_path).exists()


def test_two_simultaneous_commits_consume_one_task(draft_api):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.services.upload_draft_service import commit_draft

    _client, db, _mapping_id, _archive, _mapping, delivered = draft_api
    draft_id, key, _, report = prepare(draft_api)
    engine = db.bind
    db.rollback()
    barrier = Barrier(2)

    def contender():
        barrier.wait(timeout=5)
        return commit_draft(engine, draft_id, key, report["check_digest"], [])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0]["task_id"] == results[1]["task_id"] and len(delivered) == 1
    db.expire_all()
    assert db.scalar(select(func.count(ProcessingRun.id))) == 1


def test_actual_extract_reuses_prechecked_directory(draft_api, monkeypatch):
    from pathlib import Path

    from sqlalchemy.orm import sessionmaker

    from app.workers.extract_batch import extract_batch_task

    client, db, _mapping_id, _archive, _mapping, delivered = draft_api
    draft_id, key, _, report = prepare(draft_api)
    accepted = commit(client, draft_id, key, report).json()
    db.expire_all()
    draft = db.get(UploadDraft, draft_id)
    original_directory = Path(draft.report["prepared_dir"])
    for module in ("app.db", "app.workers.extract_batch", "app.workers.cancel"):
        monkeypatch.setattr(f"{module}.SessionLocal", sessionmaker(db.bind))
    monkeypatch.setattr("app.db.engine", db.bind)
    monkeypatch.setattr(
        "app.workers.progress.get_settings", lambda: SimpleNamespace(is_desktop=True)
    )
    monkeypatch.setattr(
        "app.workers.extract_batch._extract_zip",
        lambda *args, **kwargs: pytest.fail("Input was decompressed twice"),
    )
    args, dispatch_options = delivered[0]
    db.rollback()
    result = extract_batch_task.apply(
        kwargs={
            "upload_task_id": args[0],
            "zip_path": str(args[1]),
            "batch_no": args[2],
            "mapping_id": args[3],
            "run_id": dispatch_options["run_id"],
            "lease_token": dispatch_options["lease_token"],
            "deembed_enabled": False,
        }
    ).get(propagate=True)
    assert len(result["all_files"]) == 1
    stage_directory = Path(result["all_files"][0]["path"]).parent
    assert ".runs" in stage_directory.parts and stage_directory != original_directory
    assert original_directory.exists()
    db.expire_all()
    assert db.get(UploadDraft, draft_id).consumed_task_id == int(accepted["task_id"])


def test_two_simultaneous_prepare_requests_return_one_draft(draft_api):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.services.upload_draft_service import prepare_draft

    _client, db, mapping_id, archive, _mapping, _delivered = draft_api
    key, group, barrier = str(uuid4()), str(uuid4()), Barrier(2)
    engine = db.bind
    db.rollback()

    def contender():
        barrier.wait(timeout=5)
        return prepare_draft(engine, archive, archive.name, {"mapping_id": mapping_id}, key, group)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0][0] == results[1][0]
    assert sum(created for _, created in results) == 1
    assert db.scalar(select(func.count(UploadDraft.id))) == 1


@pytest.mark.parametrize("native", [False, True])
def test_precheck_dispatch_uses_typed_native_job_or_celery(native, monkeypatch):
    from app.workers import dispatch
    from app.workers.local_queue import LocalPrecheckTask, LocalTaskQueue
    from app.workers.precheck import precheck_upload_task

    queue, seen = LocalTaskQueue(), []
    monkeypatch.setattr(dispatch, "get_settings", lambda: SimpleNamespace(is_desktop=native))
    monkeypatch.setattr("app.workers.local_queue.get_local_queue", lambda: queue)
    monkeypatch.setattr(
        precheck_upload_task,
        "apply_async",
        lambda **kwargs: seen.append(kwargs) or SimpleNamespace(id="precheck-id"),
    )
    draft_id = str(uuid4())
    dispatch.dispatch_precheck_job(draft_id)
    if native:
        task = queue.get(timeout=0)
        assert isinstance(task, LocalPrecheckTask) and task.draft_id == draft_id
    else:
        assert seen == [{"args": [draft_id]}]


@pytest.mark.parametrize("confirmed", [False, True])
def test_precheck_dispatch_failure_is_persistent_and_retains_input(
    draft_api, confirmed, monkeypatch
):
    from pathlib import Path

    client, db, mapping_id, archive, _mapping, _delivered = draft_api

    def reject(draft_id):
        if confirmed:
            raise ConfirmedDispatchError("rejected")
        raise TimeoutError("outcome unknown")

    monkeypatch.setattr("app.workers.dispatch.dispatch_precheck_job", reject)
    response = client.post(
        "/api/uploads/prepare",
        files={"file": (archive.name, archive.read_bytes())},
        data={"mapping_id": mapping_id, "request_key": str(uuid4()), "group_id": str(uuid4())},
    )
    assert response.status_code == 503
    draft_id = response.json()["detail"]["context"]["draft_id"]
    state = client.get(f"/api/uploads/drafts/{draft_id}").json()
    assert state["status"] == ("failed" if confirmed else "preparing")
    assert state["report"]["dispatch_error"]["code"] == (
        "PRECHECK_DISPATCH_FAILED" if confirmed else "DISPATCH_OUTCOME_UNKNOWN"
    )
    db.expire_all()
    assert Path(db.get(UploadDraft, draft_id).storage_path).exists()


def test_live_progress_is_public_monotonic_and_does_not_publish_data(draft_api, monkeypatch):
    from app.config import get_settings
    from app.models import DataEpoch, Device
    from app.services.upload_precheck import build_precheck_report

    client, db, _mapping_id, _archive, _mapping, _delivered = draft_api
    monkeypatch.setattr(get_settings(), "PRECHECK_PROGRESS_INTERVAL_SECONDS", 0.000001)
    snapshots = []
    epoch = db.get(DataEpoch, 1).epoch
    db.rollback()

    def observe(*args, **kwargs):
        original = kwargs["progress_callback"]

        def progress(*event):
            original(*event)
            # Independent HTTP reader sees progress before precheck returns.
            with Session(db.bind) as reader:
                group = reader.scalar(select(UploadDraft.group_id))
            snapshot = client.get(f"/api/uploads/groups/{group}").json()["drafts"][0]
            assert snapshot["status"] == "preparing"
            assert "lease_token" not in snapshot["report"]
            snapshots.append(snapshot)

        kwargs["progress_callback"] = progress
        return build_precheck_report(*args, **kwargs)

    monkeypatch.setattr("app.services.upload_draft_service.build_precheck_report", observe)
    _id, _key, _group, report = prepare(draft_api)
    assert report["status"] == "ready" and report["prepare_progress"] == 100
    values = [s["prepare_progress"] for s in snapshots]
    assert values == sorted(values) and min(values) == 1 and max(values) == 99
    assert len(set(values)) > 5
    assert {s["report"]["prepare_unit"] for s in snapshots} >= {"bytes", "files", "percent"}
    db.expire_all()
    assert db.get(DataEpoch, 1).epoch == epoch
    assert db.scalar(select(func.count(Device.id))) == 0


@pytest.mark.parametrize("action", ["cancel", "new_lease"])
def test_progress_cannot_overwrite_stop_or_new_lease(draft_api, monkeypatch, action):
    from app.services.upload_draft_service import delete_draft, prepare_draft

    _client, db, mapping_id, archive, _mapping, _delivered = draft_api
    engine = db.bind
    draft_id, _ = prepare_draft(
        engine, archive, archive.name, {"mapping_id": mapping_id}, str(uuid4()), str(uuid4())
    )

    def interrupted(*args, **kwargs):
        kwargs["progress_callback"]("hash_input", 1, 100, "bytes")
        if action == "cancel":
            delete_draft(engine, draft_id)
        else:
            with Session(engine) as session:
                draft = session.get(UploadDraft, draft_id)
                draft.report = {"lease_token": "new-owner", "prepare_progress": 42}
                session.commit()
        # A phase change bypasses throttling and checks the lease under the lock.
        with pytest.raises(InterruptedError):
            kwargs["progress_callback"]("extract", 50, 100, "percent")
        raise InterruptedError("writer stopped")

    monkeypatch.setattr("app.services.upload_draft_service.build_precheck_report", interrupted)
    db.rollback()
    run_precheck(engine, draft_id)
    db.expire_all()
    draft = db.get(UploadDraft, draft_id)
    if action == "cancel":
        assert draft.status == "expired" and draft.report["precheck_state"] == "stopped"
        assert not archive.exists()
    else:
        assert draft.status == "preparing" and draft.report == {
            "lease_token": "new-owner", "prepare_progress": 42
        }
        assert archive.exists()


def test_progress_is_throttled_but_phase_changes_are_immediate(draft_api, monkeypatch):
    from app.config import get_settings

    client, db, _mapping_id, _archive, _mapping, _delivered = draft_api
    monkeypatch.setattr(get_settings(), "PRECHECK_PROGRESS_INTERVAL_SECONDS", 60)

    def public():
        with Session(db.bind) as reader:
            draft_id = reader.scalar(select(UploadDraft.id))
        return client.get(f"/api/uploads/drafts/{draft_id}").json()

    def many_updates(*args, **kwargs):
        progress = kwargs["progress_callback"]
        progress("inspect_files", 0, 10000, "files")
        first = public()
        for done in range(1, 1000):
            progress("inspect_files", done, 10000, "files")
        assert public()["report"] == first["report"]
        progress("verify_input", 0, 100, "bytes")
        assert public()["report"]["prepare_stage"] == "verify_input"
        assert public()["prepare_progress"] == 90
        return {"fatal_count": 0}

    monkeypatch.setattr("app.services.upload_draft_service.build_precheck_report", many_updates)
    _id, _key, _group, report = prepare(draft_api)
    assert report["status"] == "ready" and report["prepare_progress"] == 100
