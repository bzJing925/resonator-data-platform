"""快照导出→导入 round-trip：空库导入后数据逐行一致；known 差集过滤。"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Device, Mapping, MappingEntry
from app.services import snapshot_service


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> None:
    mapping = Mapping(name="MAP-A", file_path="a.csv", entry_count=1)
    db.add(mapping)
    db.flush()
    db.add(MappingEntry(mapping_id=mapping.id, mark="A1", description="d",
                        eg=1.0, fl=0.5, ag=None, area_s11=700, area_s22=5500,
                        has_pf=True, raw_tokens=None))
    batch = Batch(batch_no="B-1", mapping_id=mapping.id, f_start_ghz=4.0,
                  f_end_ghz=5.0, process_type="S1P", file_path="f", source="http",
                  device_count=2, uploaded_by="tester")
    db.add(batch)
    db.flush()
    for i, port in enumerate(("S11", "S22")):
        db.add(Device(batch_id=batch.id, original_filename=f"d{i}.s1p", mark="A1",
                      s_param_port=port, pf="Y", fs_ghz=4.8, deembedded=False))
    db.commit()


def test_snapshot_round_trip() -> None:
    src = _session()
    _seed(src)
    snap = snapshot_service.build_snapshot(src, known_batch_nos=set())
    assert [b["batch_no"] for b in snap["batches"]] == ["B-1"]
    assert snap["batches"][0]["uploaded_by"] == "tester"
    assert [m["name"] for m in snap["mappings"]] == ["MAP-A"]
    devices = snapshot_service.decode_devices(snap["devices_arrow_gz_b64"])
    assert len(devices) == 2

    dst = _session()
    stats = snapshot_service.import_snapshot(
        dst, snap["batches"], snap["mappings"], devices, source="server"
    )
    assert stats == {"mappings_added": 1, "batches_added": 1, "devices_added": 2}
    batch = dst.scalar(select(Batch).where(Batch.batch_no == "B-1"))
    assert batch.source == "server"
    assert batch.file_path == "server://B-1"
    assert dst.scalar(select(Mapping).where(Mapping.name == "MAP-A")).file_path == "server://MAP-A"
    imported = dst.scalars(select(Device).order_by(Device.id)).all()
    assert [d.original_filename for d in imported] == ["d0.s1p", "d1.s1p"]
    assert [d.s_param_port for d in imported] == ["S11", "S22"]
    assert all(d.pf == "Y" for d in imported)
    assert all(d.fs_ghz == 4.8 for d in imported)
    assert all(d.deembedded is False for d in imported)
    # 幂等：重复导入不再新增
    again = snapshot_service.import_snapshot(
        dst, snap["batches"], snap["mappings"], devices, source="server"
    )
    assert again == {"mappings_added": 0, "batches_added": 0, "devices_added": 0}


def test_snapshot_multi_chunk_writer_reuse(monkeypatch) -> None:
    """_DEVICE_CHUNK=1 时每行都触发 flush，钉住 Arrow writer 复用分支。

    2 条 device 会写 2 个 chunk：第二次 _write_chunk 必须复用首个 writer
    （否则 IPC 流出现多个 header，解码会丢行或报错）。
    """
    monkeypatch.setattr(snapshot_service, "_DEVICE_CHUNK", 1)
    src = _session()
    _seed(src)
    snap = snapshot_service.build_snapshot(src, known_batch_nos=set())
    devices = snapshot_service.decode_devices(snap["devices_arrow_gz_b64"])
    assert len(devices) == 2
    assert [d["original_filename"] for d in devices] == ["d0.s1p", "d1.s1p"]
    assert [d["s_param_port"] for d in devices] == ["S11", "S22"]
    assert all(d["batch_no"] == "B-1" for d in devices)
    assert all(d["pf"] == "Y" for d in devices)
    assert all(d["fs_ghz"] == 4.8 for d in devices)
    assert all(d["deembedded"] is False for d in devices)


def test_snapshot_known_batches_excluded() -> None:
    src = _session()
    _seed(src)
    snap = snapshot_service.build_snapshot(src, known_batch_nos={"B-1"})
    assert snap["batches"] == []
    assert snap["mappings"] == []
    assert snap["devices_arrow_gz_b64"] == ""
    assert snapshot_service.decode_devices("") == []
