"""批次号重命名 API 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.models import Batch, Mapping, UploadTask


@pytest.fixture
def rename_api_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """将重命名服务使用的 files_dir 指向临时目录。"""
    settings = Settings(DATA_ROOT=tmp_path / "data")
    monkeypatch.setattr("app.services.batch_rename_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.tasks.get_settings", lambda: settings)
    return settings


def _seed_batch(db, settings: Settings, batch_no: str) -> Batch:
    mapping = Mapping(name=f"M-{batch_no}", file_path="/tmp/mapping.xlsx")
    db.add(mapping)
    db.commit()

    files_dir = settings.files_dir / batch_no
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "a.s1p").write_text("s1p")

    task = UploadTask(batch_no=batch_no, status="success")
    db.add(task)
    db.commit()

    batch = Batch(
        batch_no=batch_no,
        mapping_id=mapping.id,
        file_path=str(files_dir),
        task_id=task.id,
    )
    db.add(batch)
    db.commit()
    return batch


def test_rename_batch_success(client: TestClient, db, rename_api_settings):
    _seed_batch(db, rename_api_settings, "B.01")

    resp = client.post("/api/batches/B.01/rename", json={"new_batch_no": "B.02"})
    assert resp.status_code == 200
    assert resp.json()["batch_no"] == "B.02"

    old_dir = rename_api_settings.files_dir / "B.01"
    new_dir = rename_api_settings.files_dir / "B.02"
    assert not old_dir.exists()
    assert new_dir.exists()


def test_rename_batch_missing(client: TestClient, db, rename_api_settings):
    resp = client.post("/api/batches/NONEXISTENT/rename", json={"new_batch_no": "B.02"})
    assert resp.status_code == 404


def test_rename_batch_duplicate(client: TestClient, db, rename_api_settings):
    _seed_batch(db, rename_api_settings, "B.01")
    _seed_batch(db, rename_api_settings, "B.02")

    resp = client.post("/api/batches/B.01/rename", json={"new_batch_no": "B.02"})
    assert resp.status_code == 409


def test_rename_batch_active_task(client: TestClient, db, rename_api_settings):
    mapping = Mapping(name="M1", file_path="/tmp/mapping.xlsx")
    db.add(mapping)
    db.commit()

    files_dir = rename_api_settings.files_dir / "B.01"
    files_dir.mkdir(parents=True)

    task = UploadTask(batch_no="B.01", status="pending")
    db.add(task)
    db.commit()

    batch = Batch(batch_no="B.01", mapping_id=mapping.id, file_path=str(files_dir), task_id=task.id)
    db.add(batch)
    db.commit()

    resp = client.post("/api/batches/B.01/rename", json={"new_batch_no": "B.02"})
    assert resp.status_code == 409


def test_rename_batch_invalid_name(client: TestClient, db, rename_api_settings):
    _seed_batch(db, rename_api_settings, "B.01")
    resp = client.post("/api/batches/B.01/rename", json={"new_batch_no": ""})
    assert resp.status_code == 422
