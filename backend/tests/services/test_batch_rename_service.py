"""批次号重命名服务测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import Batch, FileNode, Mapping, UploadTask
from app.services.batch_rename_service import (
    BatchNotFoundError,
    BatchRenameActiveTaskError,
    BatchRenameDuplicateError,
    BatchRenameInvalidNameError,
    rename_batch,
)


@pytest.fixture
def rename_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """创建临时数据目录；重命名只改显示名称，不再依赖服务配置。"""
    settings = Settings(DATA_ROOT=tmp_path / "data")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    return settings


def _make_mapping_and_batch(db, settings: Settings, batch_no: str = "B.01") -> Batch:
    mapping = Mapping(name="M1", file_path="/tmp/mapping.xlsx")
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
        raw_zip_path=str(settings.data_root / "uploads" / f"{batch_no}.zip"),
        task_id=task.id,
    )
    db.add(batch)
    db.commit()

    root = FileNode(batch_id=batch.id, node_type="root", name=batch_no, sort_order=0)
    db.add(root)
    db.commit()

    return batch


def test_rename_batch_success(db, rename_settings):
    batch = _make_mapping_and_batch(db, rename_settings, "B.01")
    old_dir = Path(batch.file_path)
    new_dir = rename_settings.files_dir / "B.02"

    new_name = rename_batch(db, "B.01", "B.02")

    assert new_name == "B.02"
    db.refresh(batch)
    assert batch.batch_no == "B.02"
    assert batch.file_path == str(old_dir)
    assert batch.raw_zip_path.endswith("B.01.zip")
    assert old_dir.exists()
    assert not new_dir.exists()
    assert (old_dir / "a.s1p").exists()

    task = db.get(UploadTask, batch.task_id)
    assert task.batch_no == "B.02"

    root = db.scalar(
        select(FileNode).where(FileNode.batch_id == batch.id, FileNode.node_type == "root")
    )
    assert root is not None
    assert root.name == "B.02"


def test_rename_batch_not_found(db, rename_settings):
    with pytest.raises(BatchNotFoundError):
        rename_batch(db, "NONEXISTENT", "B.02")


def test_rename_batch_duplicate(db, rename_settings):
    _make_mapping_and_batch(db, rename_settings, "B.01")

    mapping = Mapping(name="M2", file_path="/tmp/mapping2.xlsx")
    db.add(mapping)
    db.commit()
    other = Batch(
        batch_no="B.02",
        mapping_id=mapping.id,
        file_path=str(rename_settings.files_dir / "B.02"),
    )
    db.add(other)
    db.commit()

    with pytest.raises(BatchRenameDuplicateError):
        rename_batch(db, "B.01", "B.02")


def test_rename_batch_active_task_blocked(db, rename_settings):
    mapping = Mapping(name="M1", file_path="/tmp/mapping.xlsx")
    db.add(mapping)
    db.commit()

    files_dir = rename_settings.files_dir / "B.01"
    files_dir.mkdir(parents=True)

    task = UploadTask(batch_no="B.01", status="running")
    db.add(task)
    db.commit()

    batch = Batch(batch_no="B.01", mapping_id=mapping.id, file_path=str(files_dir), task_id=task.id)
    db.add(batch)
    db.commit()

    with pytest.raises(BatchRenameActiveTaskError):
        rename_batch(db, "B.01", "B.02")


@pytest.mark.parametrize(
    "new_name",
    ["", "   ", "B/02", "B\\02", "B\x0002", "..", "."],
)
def test_rename_batch_invalid_name(db, rename_settings, new_name):
    with pytest.raises(BatchRenameInvalidNameError):
        rename_batch(db, "B.01", new_name)
