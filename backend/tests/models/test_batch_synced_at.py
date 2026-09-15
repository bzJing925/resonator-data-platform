"""batches.synced_at：桌面端标记原始文件已回传服务器。"""

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Mapping


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_batch_synced_at_defaults_null_and_settable() -> None:
    db = _session()
    mapping = Mapping(name="M1", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no="B-1", mapping_id=mapping.id, file_path="f.zip")
    db.add(batch)
    db.commit()

    loaded = db.scalar(select(Batch).where(Batch.batch_no == "B-1"))
    assert loaded.synced_at is None

    now = datetime.now(UTC)
    loaded.synced_at = now
    db.commit()
    assert db.scalar(select(Batch).where(Batch.batch_no == "B-1")).synced_at is not None
