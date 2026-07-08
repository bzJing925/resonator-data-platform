from __future__ import annotations

from sqlalchemy import select

from app.models import Batch, Device, FileNode, Mapping
from app.services.cleanup_service import delete_batch_and_files


def test_delete_batch_and_files_removes_batch_and_files(db, cleanup_settings, tmp_path):
    mapping = Mapping(name="M1", file_path=str(tmp_path / "mapping.xlsx"))
    db.add(mapping)
    db.commit()

    raw_zip = tmp_path / "raw.zip"
    raw_zip.write_text("zip")
    files_dir = cleanup_settings.files_dir / "B.01"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "a.s1p").write_text("s1p")

    batch = Batch(
        batch_no="B.01",
        mapping_id=mapping.id,
        file_path=str(files_dir),
        raw_zip_path=str(raw_zip),
    )
    db.add(batch)
    db.commit()

    device = Device(
        batch_id=batch.id,
        original_filename="a.s1p",
    )
    file_node = FileNode(
        batch_id=batch.id,
        node_type="file",
        name="a.s1p",
    )
    db.add(device)
    db.add(file_node)
    db.commit()

    assert delete_batch_and_files(db, "B.01") is True
    assert db.scalar(select(Batch).where(Batch.batch_no == "B.01")) is None
    assert db.scalar(select(Device).where(Device.batch_id == batch.id)) is None
    assert db.scalar(select(FileNode).where(FileNode.batch_id == batch.id)) is None
    assert not files_dir.exists()
    assert not raw_zip.exists()


def test_delete_batch_and_files_returns_false_when_missing(db, cleanup_settings):
    assert delete_batch_and_files(db, "NONEXISTENT") is False
