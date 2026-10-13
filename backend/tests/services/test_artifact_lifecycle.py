"""Two-database purge recovery, leases, source ownership and path containment."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ArtifactLease, CleanupJob, Device, SourceInput, Tombstone
from app.services import artifact_lifecycle as lifecycle
from app.services.batch_lifecycle import restore, soft_delete
from app.services.source_identity import assign_batch_identity
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def artifacts(request, tmp_path, monkeypatch):
    with platform_database(request.param, tmp_path) as (db, batch):
        settings = Settings(DATA_ROOT=tmp_path / 'managed', ALN_DESKTOP_MODE=False)
        monkeypatch.setattr(lifecycle, 'get_settings', lambda: settings)
        directory = settings.files_dir / 'batch'
        directory.mkdir(parents=True)
        (directory / 'a.s1p').write_bytes(b'original')
        raw = settings.uploads_dir / 'batch.zip'
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b'original-zip')
        batch.file_path, batch.raw_zip_path = str(directory), str(raw)
        assign_batch_identity(db, batch)
        db.add(Device(batch_id=batch.id, original_filename='a.s1p',
                      source_device_key=str(uuid4())))
        db.add(SourceInput(batch_id=batch.id, member_key="a.s1p",
                           source_device_key=str(uuid4()), input_digest="a" * 64,
                           original_filename="a.s1p", current_relpath="a.s1p"))
        db.commit()
        yield db, batch, directory, raw


def delete_and_age(db, batch):
    name, bid, engine = batch.batch_no, batch.id, db.bind
    db.rollback()
    soft_delete(engine, name)
    db.expire_all()
    batch.purge_after = datetime.now(UTC) - timedelta(days=1)
    db.commit()
    db.rollback()
    return name, bid, engine


def test_leases_expiry_and_purge_retains_identity(artifacts):
    db, batch, directory, raw = artifacts
    bid, engine, name = batch.id, db.bind, batch.batch_no
    lease = lifecycle.acquire_lease(engine, bid, '*', 'download', 'reader')
    assert not lifecycle.renew_lease(engine, lease, 'other')
    assert lifecycle.renew_lease(engine, lease, 'reader')
    name, bid, engine = delete_and_age(db, batch)
    assert lifecycle.purge_due(engine)['deferred'] == 1
    assert directory.exists() and raw.exists()
    lifecycle.release_lease(engine, lease, 'other')
    assert lifecycle.purge_due(engine)['deferred'] == 1
    lifecycle.release_lease(engine, lease, 'reader')
    assert lifecycle.purge_due(engine)['success'] == 1
    assert not directory.exists() and not raw.exists()
    assert lifecycle.purge_due(engine)['success'] == 0
    db.expire_all()
    assert db.get(Tombstone, (batch.origin_id, batch.source_batch_id)).purged_at is not None
    assert db.scalar(select(Device.id)) is None
    assert batch.batch_no == name and batch.origin_id is not None
    assert db.scalar(select(SourceInput.input_digest)) == "a" * 64
    db.rollback()
    with pytest.raises(HTTPException) as exc:
        restore(engine, name)
    assert exc.value.status_code == 410


def test_io_failure_and_crash_intent_resume(artifacts, monkeypatch):
    db, batch, directory, raw = artifacts
    name, bid, engine = delete_and_age(db, batch)
    original = lifecycle._remove
    def interrupt(path):
        if path == raw:
            raise PermissionError('permission-denied')
        original(path)
    monkeypatch.setattr(lifecycle, '_remove', interrupt)
    assert lifecycle.purge_due(engine)['failed'] == 1
    assert not directory.exists() and raw.exists()
    db.expire_all()
    job = db.scalar(select(CleanupJob))
    assert job.status == 'failed' and job.attempts == 1
    assert db.get(Tombstone, (batch.origin_id, batch.source_batch_id)).purged_at is None
    db.rollback()
    with pytest.raises(HTTPException) as exc:
        restore(engine, name)
    assert exc.value.status_code == 409
    # Simulate loss of the process after a committed intent, before its final DB update.
    job.status = 'running'
    db.commit()
    db.rollback()
    monkeypatch.setattr(lifecycle, '_remove', original)
    assert lifecycle.purge_due(engine)['success'] == 1
    assert not raw.exists()


def test_restore_before_purge_and_outside_root_rejected(artifacts, tmp_path):
    db, batch, directory, raw = artifacts
    name, bid, engine = delete_and_age(db, batch)
    restore(engine, name)
    assert lifecycle.purge_due(engine)['success'] == 0
    assert directory.exists()
    db.expire_all()
    external = tmp_path / 'personal-files'
    external.mkdir()
    (external / 'keep').write_bytes(b'personal')
    batch.file_path = str(external)
    db.commit()
    delete_and_age(db, batch)
    outcome = lifecycle.purge_due(engine)
    assert outcome['failed'] == 1 and (external / 'keep').exists() and raw.exists()
    db.expire_all()
    assert 'PURGE_PATH_ESCAPE' in db.scalar(select(CleanupJob.error_code))


def test_expired_lease_does_not_revive(artifacts):
    db, batch, _directory, _raw = artifacts
    engine = db.bind
    lease_id = lifecycle.acquire_lease(engine, batch.id, '*', 'export', 'owner')
    lease = db.get(ArtifactLease, lease_id)
    lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    db.rollback()
    assert not lifecycle.renew_lease(engine, lease_id, 'owner')
    delete_and_age(db, batch)
    assert lifecycle.purge_due(engine)['success'] == 1


def test_purge_restore_race_uses_same_batch_lock(artifacts, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    db, batch, _directory, _raw = artifacts
    name, _bid, engine = delete_and_age(db, batch)
    started, release = Event(), Event()
    original = lifecycle._remove
    def blocking_remove(path):
        started.set()
        assert release.wait(5)
        original(path)
    monkeypatch.setattr(lifecycle, '_remove', blocking_remove)
    with ThreadPoolExecutor(2) as pool:
        purging = pool.submit(lifecycle.purge_due, engine)
        assert started.wait(5)
        restoring = pool.submit(restore, engine, name)
        try:
            with pytest.raises(TimeoutError):
                restoring.result(timeout=0.05)
        finally:
            release.set()
        assert purging.result(timeout=5)['success'] == 1
        with pytest.raises(HTTPException) as exc:
            restoring.result(timeout=5)
        assert exc.value.status_code == 410


@pytest.mark.parametrize('disconnect', [False, True])
def test_stream_body_holds_lease_and_disconnect_releases(artifacts, disconnect):
    import asyncio
    from types import SimpleNamespace

    from app.services.artifact_readers import ArtifactReaderMiddleware

    db, batch, _directory, _raw = artifacts
    engine, name = db.bind, batch.batch_no
    db.rollback()
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def body(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'first', 'more_body': True})
            started.set()
            await release.wait()
            if disconnect:
                raise RuntimeError('client-disconnected')
            await send({'type': 'http.response.body', 'body': b'last', 'more_body': False})
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        messages = []
        async def send(message):
            messages.append(message)
        scope = {'type': 'http', 'method': 'GET',
                 'path': f'/api/batches/{name}/download-zip', 'query_string': b'',
                 'app': SimpleNamespace(state=SimpleNamespace(artifact_engine=engine))}
        request = asyncio.create_task(ArtifactReaderMiddleware(body)(scope, receive, send))
        await started.wait()
        with Session(engine) as session:
            assert session.scalar(select(ArtifactLease.id)) is not None
        delete_and_age(db, batch)
        assert lifecycle.purge_due(engine)['deferred'] == 1
        release.set()
        if disconnect:
            with pytest.raises(RuntimeError, match='client-disconnected'):
                await request
        else:
            await request
            assert messages[-1]['more_body'] is False
        with Session(engine) as session:
            assert session.scalar(select(ArtifactLease.id)) is None
        assert lifecycle.purge_due(engine)['success'] == 1
    asyncio.run(scenario())


def test_old_result_retention_and_failed_raw_input_protection(artifacts):
    from app.models import UploadTask

    db, batch, directory, raw = artifacts
    engine, bid = db.bind, batch.id
    old = directory.parent / 'old-result'
    old.mkdir()
    (old / 'old.s1p').write_bytes(b'old')
    now = datetime.now(UTC)
    db.add(CleanupJob(batch_id=bid, resource_kind='old_result', resource_path=str(old),
                      not_before=now + timedelta(hours=24)))
    db.add(CleanupJob(batch_id=bid, resource_kind='raw_zip', resource_path=str(raw),
                      not_before=now - timedelta(seconds=1)))
    db.add(UploadTask(batch_no=batch.batch_no, status='failed'))
    db.commit()
    db.rollback()
    assert lifecycle.purge_due(engine, now=now)['success'] == 0
    assert old.exists() and raw.exists()
    assert lifecycle.purge_due(engine, now=now + timedelta(hours=25))['success'] == 1
    assert not old.exists() and directory.exists() and raw.exists()


def test_wrong_origin_never_removes_files(artifacts):
    db, batch, directory, raw = artifacts
    lease = lifecycle.acquire_lease(db.bind, batch.id, "*", "download", "origin-check")
    _name, _bid, engine = delete_and_age(db, batch)
    assert lifecycle.purge_due(engine)["deferred"] == 1
    lifecycle.release_lease(engine, lease, "origin-check")
    db.expire_all()
    batch.origin_id = str(uuid4())
    db.commit()
    db.rollback()
    assert lifecycle.purge_due(engine)['failed'] == 1
    assert directory.exists() and raw.exists()


def test_symlink_escape_preserves_target(artifacts, tmp_path):
    db, batch, directory, raw = artifacts
    external = tmp_path / 'personal'
    external.mkdir()
    (external / 'keep').write_bytes(b'keep')
    linked = directory.parent / 'linked'
    linked.symlink_to(external, target_is_directory=True)
    batch.file_path = str(linked)
    db.commit()
    _name, _bid, engine = delete_and_age(db, batch)
    assert lifecycle.purge_due(engine)['failed'] == 1
    assert (external / 'keep').exists() and raw.exists() and directory.exists()
