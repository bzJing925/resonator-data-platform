"""桌面端同步编排：未同步批次识别、gz 重打包、推送状态机、删本地文件。"""

import gzip
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Mapping
from app.services import desktop_sync_service as svc
from app.services.server_client import ServerError


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _batch(db: Session, batch_no: str, *, source="http", count=1, synced=None) -> Batch:
    mapping = Mapping(name=f"MAP-{batch_no}", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no=batch_no, mapping_id=mapping.id, file_path="f",
                  source=source, device_count=count, synced_at=synced)
    db.add(batch)
    db.commit()
    return batch


def _files(settings_dir: Path, batch_no: str, gz: bool = True) -> Path:
    d = settings_dir / "files" / batch_no
    d.mkdir(parents=True, exist_ok=True)
    content = b"# GHz S MA R 50\n4.0 0.5 170\n"
    if gz:
        (d / "dut1.s1p.gz").write_bytes(gzip.compress(content))
    else:
        (d / "dut1.s1p").write_bytes(content)
    return d


def test_list_unsynced_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    _batch(db, "L-1")
    _files(tmp_path, "L-1")
    _batch(db, "WEB-1", source="server")          # 快照批次不推送
    _batch(db, "DONE-1", synced=datetime.now(UTC))  # 已同步不推送
    _batch(db, "EMPTY-1", count=0)                  # 未完成入库不推送
    _batch(db, "NOFILES-1")                          # 文件目录不存在不推送
    assert [b.batch_no for b in svc.list_unsynced_batches(db)] == ["L-1"]


def test_pack_batch_zip_decompresses_gz(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    batch = _batch(db, "P-1")
    _files(tmp_path, "P-1", gz=True)
    out = svc.pack_batch_zip(batch, tmp_path / "out.zip")
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["P-1/dut1.s1p"]
        assert zf.read("P-1/dut1.s1p").startswith(b"# GHz S MA")


class _StubClient:
    """预置推送结果的任务成功桩。"""

    config = SimpleNamespace(username="tester")

    def __init__(self, push_result, batch_count):
        self._push = push_result
        self._count = batch_count

    def ensure_mapping(self, name, entries):
        return {"mapping_id": 1, "created": True}

    def push_batch(self, zip_path, meta):
        return self._push

    def task_status(self, task_id):
        return {"id": task_id, "status": "success"}

    def get_batch(self, batch_no):
        return {"batch_no": batch_no, "device_count": self._count}


def test_push_success_deletes_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    db = _session()
    _batch(db, "OK-1", count=5)
    files_dir = _files(tmp_path, "OK-1")
    client = _StubClient({"status": "already_synced", "device_count": 5}, batch_count=5)

    result = svc.push_batch_to_server(db, client, "OK-1")
    assert result["status"] == "synced"
    assert not files_dir.exists()
    assert db.scalar(select(Batch).where(Batch.batch_no == "OK-1")).synced_at is not None


def test_push_count_mismatch_keeps_files(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    db = _session()
    _batch(db, "BAD-1", count=3)
    files_dir = _files(tmp_path, "BAD-1")
    client = _StubClient({"status": "already_synced", "device_count": 99}, batch_count=99)

    with pytest.raises(ServerError, match="同名不同数据"):
        svc.push_batch_to_server(db, client, "BAD-1")
    assert files_dir.exists()
    assert db.scalar(select(Batch).where(Batch.batch_no == "BAD-1")).synced_at is None


def test_pull_merges_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    _batch(db, "LOCAL-1")

    class PullClient:
        def fetch_snapshot(self, known):
            assert known == {"LOCAL-1"}
            return {
                "batches": [{"batch_no": "R-1", "mapping_id": 9, "device_count": 1,
                             "process_type": "S1P", "deembedded": False}],
                "mappings": [{"id": 9, "name": "R-MAP", "entries": [{"mark": "A1"}]}],
                "devices_arrow_gz_b64": "",
            }

    stats = svc.pull_from_server(db, PullClient())
    assert stats["batches_added"] == 1
    assert db.scalar(select(Batch).where(Batch.batch_no == "R-1")).source == "server"
