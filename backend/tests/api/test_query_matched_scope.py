"""matched=FALSE（未命中对照表）的器件不参与分析：查询/聚合/distinct/导出均排除。

设备列表与曲线接口不在此过滤范围内（批次详情与阻抗曲线仍可见）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def matched_db(db: Session) -> Session:
    from app.models import Batch, Device, Mapping

    mapping = Mapping(name="M", file_path="m", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no="T_MATCHED.01", mapping_id=mapping.id, file_path="x.zip")
    db.add(batch)
    db.flush()
    base = dict(
        batch_id=batch.id, s_param_path="a.s1p", s_param_port="S11",
        fs_ghz=1.0, qs=10.0, k2eff_pct=5.0,
    )
    db.add(Device(original_filename="hit.s1p", mark="E6-1", matched=True, **base))
    db.add(Device(
        original_filename="miss.s1p", mark="E9-9", matched=False,
        **{**base, "s_param_path": "b.s1p", "fs_ghz": 9.9},
    ))
    db.commit()
    return db


def test_query_devices_excludes_unmatched(client: TestClient, matched_db: Session) -> None:
    r = client.post("/api/query/devices", json={"limit": 100, "fields": ["id", "mark", "fs_ghz"]})
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert [row["mark"] for row in rows] == ["E6-1"]


def test_aggregate_excludes_unmatched(client: TestClient, matched_db: Session) -> None:
    r = client.post(
        "/api/query/aggregate",
        json={"group_by": ["batch_no"], "metrics": [{"field": "fs_ghz", "agg": ["avg"]}]},
    )
    assert r.status_code == 200, r.text
    groups = r.json()["groups"]
    assert len(groups) == 1 and groups[0]["fs_ghz"]["avg"] == pytest.approx(1.0)


def test_distinct_values_excludes_unmatched(client: TestClient, matched_db: Session) -> None:
    r = client.get("/api/query/distinct", params={"field": "mark"})
    assert r.status_code == 200, r.text
    assert r.json()["values"] == ["E6-1"]


def test_export_excludes_unmatched(matched_db: Session) -> None:
    from app.schemas.query import QueryRequest
    from app.services.export_query import select_export_rows

    fields, rows = select_export_rows(QueryRequest(limit=100, fields=["mark"]), matched_db)
    assert fields == ["mark"] and [row["mark"] for row in rows] == ["E6-1"]
