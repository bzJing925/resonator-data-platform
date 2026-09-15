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
    _batch(db, "W-1", source="watch")              # watcher 入库同样要推送
    _files(tmp_path, "W-1")
    _batch(db, "WEB-1", source="web")             # 网页快照批次不推送
    _batch(db, "SRV-1", source="server")          # 服务器快照批次不推送
    _batch(db, "DONE-1", synced=datetime.now(UTC))  # 已同步不推送
    _batch(db, "EMPTY-1", count=0)                # 未完成入库不推送
    _batch(db, "NOFILES-1")                        # 文件目录不存在不推送
    # uploaded_at 是秒级 server_default，同秒插入顺序不定，按集合断言
    assert sorted(b.batch_no for b in svc.list_unsynced_batches(db)) == ["L-1", "W-1"]


def test_pack_batch_zip_decompresses_gz(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    batch = _batch(db, "P-1")
    _files(tmp_path, "P-1", gz=True)
    out = svc.pack_batch_zip(batch, tmp_path / "out.zip")
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["P-1/dut1.s1p"]
        assert zf.read("P-1/dut1.s1p").startswith(b"# GHz S MA")


def test_pack_batch_zip_skips_pipeline_temp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    batch = _batch(db, "DE-1")
    root = tmp_path / "files" / "DE-1"
    # 去嵌管线中间产物（批次目录顶层），服务器入库会自行重建，不得打包
    for temp_dir, name in [
        ("S11_raw", "dut1_S11.s1p"),
        ("S11_de", "dut1_S11_de.s1p"),
        ("cal_S11", "OPEN_S11.s1p"),
    ]:
        d = root / temp_dir
        d.mkdir(parents=True)
        (d / name).write_bytes(b"temp")
    # 原始上传布局：校准 s2p 与 DUT 同级，必须保留（服务器重跑去嵌需要）
    (root / "wafer1").mkdir(parents=True)
    (root / "wafer1" / "dut1.s2p").write_bytes(b"dut")
    (root / "wafer1" / "OPEN.s2p").write_bytes(b"open")
    (root / ".DS_Store").write_bytes(b"junk")

    out = svc.pack_batch_zip(batch, tmp_path / "out.zip")
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["DE-1/wafer1/OPEN.s2p", "DE-1/wafer1/dut1.s2p"]


def test_pack_batch_zip_bad_gzip_raises_server_error(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    batch = _batch(db, "G-1")
    root = _files(tmp_path, "G-1")
    (root / "broken.s1p.gz").write_bytes(b"not a gzip")

    out = tmp_path / "out.zip"
    with pytest.raises(ServerError, match="broken.s1p.gz"):
        svc.pack_batch_zip(batch, out)
    assert not out.exists()  # 半截 zip 已清理


def test_pack_batch_zip_truncated_gzip_raises_server_error(tmp_path, monkeypatch):
    """截断的 gzip 抛 EOFError，同样要转为带文件名的 ServerError。"""
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    batch = _batch(db, "T-1")
    root = _files(tmp_path, "T-1", gz=False)
    (root / "trunc.s1p.gz").write_bytes(gzip.compress(b"data")[:10])  # 截尾

    out = tmp_path / "out.zip"
    with pytest.raises(ServerError, match="trunc.s1p.gz"):
        svc.pack_batch_zip(batch, out)
    assert not out.exists()


def test_finalize_rmtree_failure_keeps_files_and_marks_synced(tmp_path, monkeypatch):
    """删除本地文件失败（OSError）：synced_at 已提交、文件保留、不抛异常只记日志。"""
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    _batch(db, "RM-1", count=2)
    files_dir = _files(tmp_path, "RM-1")
    client = _StubClient({"status": "already_synced", "device_count": 2}, batch_count=2)

    def _boom(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(svc.shutil, "rmtree", _boom)
    result = svc.finalize_synced_batch(db, client, "RM-1")
    assert result == {"status": "synced", "device_count": 2}
    assert files_dir.exists()
    assert db.scalar(select(Batch).where(Batch.batch_no == "RM-1")).synced_at is not None


class _StubClient:
    """预置推送结果、记录调用次数的任务成功桩。"""

    config = SimpleNamespace(username="tester")

    def __init__(self, push_result, batch_count):
        self._push = push_result
        self._count = batch_count
        self.ensure_calls = 0
        self.pushed_metas = []
        self.polled_task_ids = []

    def ensure_mapping(self, name, entries):
        self.ensure_calls += 1
        return {"mapping_id": 1, "created": True}

    def push_batch(self, zip_path, meta):
        self.pushed_metas.append(meta)
        return self._push

    def task_status(self, task_id):
        self.polled_task_ids.append(task_id)
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


def test_push_accepted_waits_task_then_syncs(tmp_path, monkeypatch):
    """accepted 主路径：ensure→pack→push→wait→finalize 全流程走通。"""
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    monkeypatch.setattr(svc, "POLL_INTERVAL_S", 0)
    db = _session()
    _batch(db, "A-1", count=4)
    files_dir = _files(tmp_path, "A-1")
    client = _StubClient({"status": "accepted", "task_id": 7}, batch_count=4)

    result = svc.push_batch_to_server(db, client, "A-1")
    assert result == {"status": "synced", "device_count": 4}
    assert client.ensure_calls == 1
    assert client.polled_task_ids == [7]
    assert not files_dir.exists()
    assert db.scalar(select(Batch).where(Batch.batch_no == "A-1")).synced_at is not None


def test_push_meta_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    db = _session()
    _batch(db, "M-1", count=2)
    _files(tmp_path, "M-1")
    ranged = _batch(db, "M-2", count=1)
    ranged.f_start_ghz = 3.5
    ranged.f_end_ghz = 4.5
    ranged.deembedded = True
    db.commit()
    _files(tmp_path, "M-2")

    client = _StubClient({"status": "already_synced", "device_count": 2}, batch_count=2)
    svc.push_batch_to_server(db, client, "M-1")
    meta = client.pushed_metas[0]
    assert meta["batch_no"] == "M-1"
    assert meta["mapping_name"] == "MAP-M-1"
    assert meta["deembed"] == "false"  # 字符串，非 bool
    assert meta["deembed_method"] == "default"
    assert meta["process_type"] == "AUTO"
    assert meta["uploaded_by"] == "tester"
    assert "f_start_ghz" not in meta  # None 时不传键（空串会让服务器 422）
    assert "f_end_ghz" not in meta

    client2 = _StubClient({"status": "already_synced", "device_count": 1}, batch_count=1)
    svc.push_batch_to_server(db, client2, "M-2")
    meta2 = client2.pushed_metas[0]
    assert meta2["deembed"] == "true"
    assert meta2["f_start_ghz"] == "3.5"
    assert meta2["f_end_ghz"] == "4.5"


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


def test_push_task_failure_keeps_files(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    monkeypatch.setattr(svc, "POLL_INTERVAL_S", 0)
    db = _session()
    _batch(db, "F-1", count=2)
    files_dir = _files(tmp_path, "F-1")

    class _FailClient(_StubClient):
        def task_status(self, task_id):
            return {"id": task_id, "status": "failed", "error_msg": "boom"}

    client = _FailClient({"status": "accepted", "task_id": 7}, batch_count=2)
    with pytest.raises(ServerError, match="boom"):
        svc.push_batch_to_server(db, client, "F-1")
    assert files_dir.exists()
    assert db.scalar(select(Batch).where(Batch.batch_no == "F-1")).synced_at is None


def test_push_already_synced_skips_ensure_and_push(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    db = _session()
    _batch(db, "S-1", count=2, synced=datetime.now(UTC))
    _files(tmp_path, "S-1")
    client = _StubClient({"status": "already_synced", "device_count": 2}, batch_count=2)

    result = svc.push_batch_to_server(db, client, "S-1")
    assert result["status"] == "synced"
    assert client.ensure_calls == 0
    assert client.pushed_metas == []


def test_wait_task_timeout_mentions_task_id(monkeypatch):
    monkeypatch.setattr(svc, "POLL_TIMEOUT_S", 0)
    with pytest.raises(ServerError, match="task_id=42"):
        svc.wait_task(_StubClient({}, 0), 42)


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
