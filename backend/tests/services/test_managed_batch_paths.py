"""Persisted result locations survive display renaming and remain readable."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.services.batch_rename_service import rename_batch
from app.services.file_tree_service import batch_files_dir
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize("kind", DATABASES)
@pytest.mark.parametrize("managed", [False, True])
def test_rename_keeps_actual_files_raw_input_and_other_run_directory(kind, managed, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        old_name = batch.batch_no
        directory = tmp_path / (str(uuid4()) if managed else old_name)
        other_run = tmp_path / str(uuid4())
        directory.mkdir()
        other_run.mkdir()
        (directory / "dut.s1p").write_text("# Hz S RI R 50\n1000000000 0.1 0\n")
        (other_run / "retained.s1p").write_text("other run")
        raw = tmp_path / f"{old_name}.zip"
        raw.write_bytes(b"raw input")
        batch.file_path, batch.raw_zip_path = str(directory), str(raw)
        db.commit()
        rename_batch(db, old_name, "renamed-display")
        assert batch_files_dir(batch) == directory
        assert batch.raw_zip_path == str(raw) and raw.exists()
        assert (other_run / "retained.s1p").read_text() == "other run"

        def override_db():
            with Session(db.bind) as session:
                yield session

        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        try:
            response = client.get("/api/files", params={"batch_no": "renamed-display"})
            assert response.status_code == 200 and response.json()[0]["relpath"] == "dut.s1p"
            response = client.post(
                "/api/files/download-zip",
                json={"batch_no": "renamed-display", "relpaths": ["dut.s1p"]},
            )
            assert response.status_code == 200 and response.content.startswith(b"PK")
        finally:
            client.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)


@pytest.mark.parametrize("kind", DATABASES)
def test_actual_extract_uses_uuid_directory_and_preserves_previous_files(
    kind, tmp_path, monkeypatch
):
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.orm import sessionmaker

    from app.config import Settings
    from app.models import ProcessingRun
    from app.services.processing_run import create_run
    from app.workers.extract_batch import extract_batch_task
    from tests.fixtures.platform_cases import _algorithm_samples, _write_archive

    samples = _algorithm_samples(tmp_path)
    archive = _write_archive(tmp_path / "input.zip", [("17_E6-1_X0Y0.s1p", samples["dut_s1p"])])
    settings = Settings(
        DATA_ROOT=tmp_path / "data",
        ALN_DESKTOP_MODE=True,
        ALN_DESKTOP_DIR=tmp_path / "desktop",
        KEEP_RAW_ZIP=True,
    )
    with platform_database(kind, tmp_path) as (db, batch):
        old = tmp_path / "legacy-results"
        old.mkdir()
        (old / "retained.s1p").write_text("previous result")
        batch.file_path = str(old)
        batch.raw_zip_path = str(archive)
        db.commit()
        batch_id, batch_no, mapping_id = batch.id, batch.batch_no, batch.mapping_id
        engine = db.bind
        db.rollback()
        factory = sessionmaker(engine)
        for module in ("app.db", "app.workers.extract_batch", "app.workers.cancel"):
            monkeypatch.setattr(f"{module}.SessionLocal", factory)
        for module in ("app.workers.extract_batch", "app.workers.cancel", "app.workers.progress",
                       "app.services.result_publish"):
            monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
        monkeypatch.setattr("app.db.engine", engine)
        monkeypatch.setattr("app.workers.extract_batch._find_7z", lambda: None)
        ticket = create_run(engine, batch_id, "reextract")
        result = extract_batch_task.apply(
            kwargs={
                "upload_task_id": ticket.task_id,
                "run_id": ticket.run_id,
                "lease_token": ticket.lease_token,
                "zip_path": str(archive),
                "batch_no": batch_no,
                "mapping_id": mapping_id,
                "deembed_enabled": False,
            }
        ).get(propagate=True)
        assert len(result["all_files"]) == 1
        db.expire_all()
        run = db.get(ProcessingRun, ticket.run_id)
        actual = Path(run.stage_dir)
        assert UUID(actual.name).version == 4
        assert actual.parent.name == ticket.run_id
        assert actual.parent.parent.name == batch.source_batch_id
        assert actual.parent.parent.parent == settings.files_dir / '.runs'
        assert Path(batch.file_path) == old  # Extraction has not atomically published yet.
        assert (actual / "17_E6-1_X0Y0.s1p").exists()
        assert (old / "retained.s1p").read_text() == "previous result"
        assert archive.exists()
        assert run.dispatch_phase == "extract_handoff"
