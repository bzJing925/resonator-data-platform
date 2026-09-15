"""batches.synced_at：桌面端标记原始文件已回传服务器。"""

from datetime import UTC, datetime

from app.models import Batch, Mapping


def test_batch_synced_at_defaults_null_and_settable(db) -> None:
    mapping = Mapping(name="M1", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no="B-1", mapping_id=mapping.id, file_path="f.zip")
    db.add(batch)
    db.commit()

    loaded = db.get(Batch, batch.id)
    assert loaded is not None
    assert loaded.synced_at is None

    now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
    loaded.synced_at = now
    db.commit()
    db.refresh(loaded)
    assert loaded.synced_at is not None
    # SQLite 不保留时区信息，去掉 tzinfo 后应与写入值相等（验证 DB round-trip）
    assert loaded.synced_at == now.replace(tzinfo=None)
