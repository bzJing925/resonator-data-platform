"""端到端集成测试：API → Celery (EAGER) → DB → API。

覆盖：
- POST /api/mappings 上传对照表
- POST /api/uploads 上传 zip + 触发 worker（同步跑）
- GET /api/tasks/{id} 任务状态
- GET /api/batches 列表
- POST /api/query/devices 查询
- POST /api/query/aggregate 聚合
- GET /api/query/fields 字段元数据
- GET /api/health 健康检查
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import engine
from app.main import app
from app.workers import celery_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def celery_eager() -> None:
    """让 process_batch_task.delay() 在当前线程同步跑，无需起 worker。"""
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    """每个测试前清掉所有业务表。

    注意：默认会清掉本地开发数据。设 ALN_PROTECT_DB=1 跳过 truncate（保护生产/staging 数据）。
    CI 应当用独立的临时数据库，无需此变量。
    """
    import os

    if os.environ.get("ALN_PROTECT_DB") == "1":
        pytest.skip("ALN_PROTECT_DB=1，跳过会清数据的集成测试")
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE devices, batches, upload_tasks, "
                "mapping_entries, mappings RESTART IDENTITY CASCADE"
            )
        )


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["db"] == "ok"


def test_fields_metadata(client: TestClient) -> None:
    r = client.get("/api/query/fields")
    assert r.status_code == 200
    body = r.json()
    assert {"categorical", "geometric", "numeric", "process"} <= set(body.keys())
    numeric_names = {f["name"] for f in body["numeric"]}
    assert {"fs_ghz", "fp_ghz", "qs", "qp", "k2eff_pct"} <= numeric_names


def test_full_upload_query_flow(client: TestClient, sample_mapping: Path, sample_zip: Path) -> None:
    # 1. 上传对照表
    with sample_mapping.open("rb") as f:
        r = client.post(
            "/api/mappings",
            files={"file": ("ELB003.xlsx", f)},
            data={"name": "ELB003"},
        )
    assert r.status_code == 201, r.text
    mapping_id = r.json()["id"]
    assert r.json()["entry_count"] > 0

    # 重名应该 409
    with sample_mapping.open("rb") as f:
        r2 = client.post(
            "/api/mappings",
            files={"file": ("ELB003.xlsx", f)},
            data={"name": "ELB003"},
        )
    assert r2.status_code == 409

    # 2. 上传 zip（EAGER 模式下任务同步跑完）
    with sample_zip.open("rb") as f:
        r = client.post(
            "/api/uploads",
            files={"file": ("T8901P.01.zip", f)},
            data={
                "mapping_id": str(mapping_id),
                "process_type": "S2P",
                "deembed": "false",
            },
        )
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    assert r.json()["batch_no"] == "T8901P.01"

    # 3. 任务应该已经成功（EAGER 模式下立即完成）
    r = client.get(f"/api/tasks/{task_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body
    assert body["progress_pct"] == 100

    # 重名上传应该 409
    with sample_zip.open("rb") as f:
        r_dup = client.post(
            "/api/uploads",
            files={"file": ("T8901P.01.zip", f)},
            data={"mapping_id": str(mapping_id)},
        )
    assert r_dup.status_code == 409

    # 4. 批次列表应该有 1 个
    r = client.get("/api/batches")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["batch_no"] == "T8901P.01"
    assert body["items"][0]["device_count"] >= 20

    # 5. 跨批次查询（验证 23 行成功入库）
    r = client.post(
        "/api/query/devices",
        json={
            "filters": {"batch_no": ["T8901P.01"]},
            "fields": ["batch_no", "mark", "folder_name", "fs_ghz", "fp_ghz", "qs", "k2eff_pct"],
            "limit": 100,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 20, f"应至少 20 行，实际 {body['total']}"
    assert body["returned"] == body["total"]
    row = body["rows"][0]
    assert {"batch_no", "mark", "folder_name", "fs_ghz", "qs"} <= set(row.keys())
    assert 1 < row["fs_ghz"] < 30  # 物理范围

    # 6. 聚合：按 folder_name 分组统计 qs
    r = client.post(
        "/api/query/aggregate",
        json={
            "filters": {},
            "group_by": ["folder_name"],
            "metrics": [{"field": "qs", "agg": ["min", "max", "p50", "count"]}],
        },
    )
    assert r.status_code == 200, r.text
    groups = r.json()["groups"]
    assert len(groups) == 2  # S11 + S22
    for g in groups:
        assert g["folder_name"] in ("S11", "S22")
        assert g["qs"]["count"] > 0

    # 7. distinct
    r = client.get("/api/query/distinct?field=batch_no")
    assert r.status_code == 200
    assert r.json()["values"] == ["T8901P.01"]


def test_upload_validation(client: TestClient, sample_zip: Path) -> None:
    # 不存在的 mapping_id → 422
    with sample_zip.open("rb") as f:
        r = client.post(
            "/api/uploads",
            files={"file": ("T8901P.01.zip", f)},
            data={"mapping_id": "9999"},
        )
    assert r.status_code == 422

    # 非 zip → 400
    r = client.post(
        "/api/uploads",
        files={"file": ("foo.txt", b"hello")},
        data={"mapping_id": "1"},
    )
    assert r.status_code == 400


def test_query_field_whitelist(client: TestClient) -> None:
    """非白名单字段必须 400，防 SQL 注入。"""
    r = client.post(
        "/api/query/devices",
        json={"filters": {}, "fields": ["DROP TABLE devices"]},
    )
    assert r.status_code == 400


def test_upload_cleans_orphan_zip_when_db_commit_fails(
    client: TestClient, sample_mapping: Path, sample_zip: Path, monkeypatch
) -> None:
    """db.commit() 抛 IntegrityError 时（模拟 batch_no unique 冲突的 TOCTOU race），
    我们的 except 必须 unlink 已写盘的 zip，不能孤儿占盘。"""
    with sample_mapping.open("rb") as f:
        r = client.post(
            "/api/mappings",
            files={"file": ("orphan_test.xlsx", f)},
            data={"name": "orphan_test"},
        )
    assert r.status_code == 201
    mapping_id = r.json()["id"]

    from app.config import get_settings

    uploads_dir = get_settings().uploads_dir
    before_zips = sorted(p for p in uploads_dir.rglob("*.zip") if p.is_file())

    # patch Session.commit 在第一次调用就抛 IntegrityError
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session as _Session

    orig_commit = _Session.commit
    state = {"calls": 0}

    def boom_commit(self, *a, **kw):
        state["calls"] += 1
        if state["calls"] == 1:
            raise IntegrityError("simulated dup", None, Exception("simulated"))
        return orig_commit(self, *a, **kw)

    monkeypatch.setattr(_Session, "commit", boom_commit, raising=True)

    # TestClient 默认会 re-raise 未捕获的异常；commit fail 后我们的 except
    # 会 unlink 文件再 raise，TestClient 把 IntegrityError 抛回这里。
    with pytest.raises(IntegrityError), sample_zip.open("rb") as f:
        client.post(
            "/api/uploads",
            files={"file": ("orphan_new.zip", f)},
            data={"mapping_id": str(mapping_id), "process_type": "S1P"},
        )

    monkeypatch.undo()

    # 关键断言：uploads_dir 的 zip 数不应增加（孤儿 zip 被清掉）
    after_zips = sorted(p for p in uploads_dir.rglob("*.zip") if p.is_file())
    new_zips = set(after_zips) - set(before_zips)
    assert not new_zips, f"DB 失败后留下孤儿 zip: {new_zips}"


def test_upload_marks_task_failed_when_broker_unreachable(
    client: TestClient, sample_mapping: Path, sample_zip: Path, monkeypatch
) -> None:
    """broker（Redis/kombu）不可达时 upload_tasks 必须立刻标 failed 而不是停在 pending。

    历史 bug：upload.py 只 catch ImportError，broker 抛 ConnectionError 时直接
    冒泡 500，但 batch+task 已 commit、zip 已写盘 → upload_task 永远 pending。
    """
    # 先建一个 mapping
    with sample_mapping.open("rb") as f:
        r = client.post(
            "/api/mappings",
            files={"file": ("broker_test.xlsx", f)},
            data={"name": "broker_test"},
        )
    assert r.status_code == 201
    mapping_id = r.json()["id"]

    # monkeypatch chain.apply_async 抛 ConnectionError 模拟 broker 不可达
    from app.services import upload_service as upload_service_mod

    class _ExplodingChain:
        def __init__(self, *args, **kwargs):
            pass

        def apply_async(self, **kwargs):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(upload_service_mod, "chain", _ExplodingChain)

    with sample_zip.open("rb") as f:
        r = client.post(
            "/api/uploads",
            files={"file": ("broker_fail.zip", f)},
            data={"mapping_id": str(mapping_id), "process_type": "S1P"},
        )
    # 应该返回 503 而不是 500
    assert r.status_code == 503, r.text

    # 看一眼 upload_tasks：必须是 failed
    r = client.get("/api/tasks", params={"limit": 50})
    assert r.status_code == 200
    tasks = r.json()
    matching = [t for t in tasks if t["batch_no"] == "broker_fail"]
    assert matching, "找不到对应 upload_task"
    assert matching[0]["status"] == "failed", (
        f"broker 失败后 task 应为 failed，实际 {matching[0]['status']}"
    )
