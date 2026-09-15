"""网页版快照同步：仅新增，且桌面端推送写入 main/uploads。"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Device, Mapping, MappingEntry
from app.services import web_sync_service


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_import_web_snapshot_is_incremental() -> None:
    db = _session()
    mappings = [{
        "id": 9,
        "name": "WEB-MAP",
        "entries": [{"mark": "A1", "description": "web", "eg": 1.0, "fl": 0.5,
                     "ag": None, "area_s11": 700, "area_s22": 5500, "has_pf": False}],
    }]
    batches = [{
        "batch_no": "WEB-1", "mapping_id": 9, "deembedded": False,
        "process_type": "S1P", "device_count": 1,
    }]
    devices = [{
        "batch_no": "WEB-1", "original_filename": "one.s1p", "mark": "A1",
        "pf": "N", "fs_ghz": 4.8, "s_param_port": "S11", "deembedded": False,
    }]

    result = web_sync_service.import_web_snapshot(db, batches, mappings, devices)
    assert result == {"mappings_added": 1, "batches_added": 1, "devices_added": 1}
    assert db.scalar(select(Mapping).where(Mapping.name == "WEB-MAP")).file_path == "web://WEB-MAP"
    assert db.scalar(select(Batch).where(Batch.batch_no == "WEB-1")).source == "web"
    assert db.scalar(select(Device)).original_filename == "one.s1p"

    again = web_sync_service.import_web_snapshot(db, batches, mappings, devices)
    assert again == {"mappings_added": 0, "batches_added": 0, "devices_added": 0}


def test_push_batch_writes_share_pack_to_main(monkeypatch) -> None:
    db = _session()
    mapping = Mapping(name="LOCAL-MAP", file_path="local.csv", entry_count=1)
    db.add(mapping)
    db.flush()
    db.add(MappingEntry(mapping_id=mapping.id, mark="A1", description="local", has_pf=False))
    batch = Batch(
        batch_no="LOCAL-1", mapping_id=mapping.id, process_type="S1P", file_path="local.zip",
        source="table", device_count=1,
    )
    db.add(batch)
    db.flush()
    db.add(Device(batch_id=batch.id, original_filename="one.s1p", s_param_port="S11", pf="N"))
    db.commit()

    seen = {}

    def fake_request(url, *, method="GET", token=None, body=None):
        seen.update(url=url, method=method, token=token, body=body)
        return {"commit": {"html_url": "https://example.test/commit/1"}}

    monkeypatch.setattr(web_sync_service, "_request_json", fake_request)
    result = web_sync_service.push_batch_to_web(db, "LOCAL-1", "token-value")

    assert result["device_count"] == 1
    assert result["commit_url"] == "https://example.test/commit/1"
    assert seen["method"] == "PUT" and seen["token"] == "token-value"
    assert "/contents/uploads/" in seen["url"]
    assert seen["body"]["branch"] == "main"
