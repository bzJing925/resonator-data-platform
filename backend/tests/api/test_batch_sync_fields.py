"""batches API：synced_at / source 字段从 DB 透传到响应 JSON。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Batch, Mapping


def _make_batch(
    db: Session,
    batch_no: str,
    source: str = "http",
    synced_at: datetime | None = None,
) -> Batch:
    mapping = Mapping(name=f"M-{batch_no}", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(
        batch_no=batch_no,
        mapping_id=mapping.id,
        file_path="f.zip",
        source=source,
        synced_at=synced_at,
    )
    db.add(batch)
    db.commit()
    return batch


def test_list_batches_returns_synced_at_and_source(client, db: Session) -> None:
    _make_batch(
        db,
        "B-SYNC",
        source="server",
        synced_at=datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC),
    )
    _make_batch(db, "B-DEF")  # source 默认 "http"，synced_at 默认 NULL

    resp = client.get("/api/batches")
    assert resp.status_code == 200
    items = {i["batch_no"]: i for i in resp.json()["items"]}

    assert items["B-SYNC"]["source"] == "server"
    assert items["B-SYNC"]["synced_at"].startswith("2026-09-15T12:00:00")
    assert items["B-DEF"]["source"] == "http"
    assert items["B-DEF"]["synced_at"] is None


def test_get_batch_returns_synced_at_and_source(client, db: Session) -> None:
    _make_batch(
        db,
        "B-SYNC",
        source="server",
        synced_at=datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC),
    )

    resp = client.get("/api/batches/B-SYNC")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "server"
    assert data["synced_at"].startswith("2026-09-15T12:00:00")
