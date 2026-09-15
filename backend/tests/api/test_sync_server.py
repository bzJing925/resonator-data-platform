"""服务器角色同步端点：快照/mapping 确保/推送入库/自然键曲线。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.models import Batch, Mapping, UploadTask


def _make_zip(path: Path) -> Path:
    rows = (f"{4.0 + i * 0.001:.3f} {0.5 - i * 1e-4:.6f} {170 + i * 0.01:.2f}" for i in range(801))
    s1p = "# GHz S MA R 50\n" + "\n".join(rows) + "\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("dut1.s1p", s1p)
    return path


def test_snapshot_endpoint_excludes_known(client, db):
    mapping = Mapping(name="M", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    db.add(Batch(batch_no="S-1", mapping_id=mapping.id, file_path="f", device_count=0))
    db.add(Batch(batch_no="S-2", mapping_id=mapping.id, file_path="f", device_count=0))
    db.commit()

    resp = client.post("/api/sync/snapshot", json={"known_batch_nos": ["S-1"]})
    assert resp.status_code == 200
    payload = resp.json()
    assert [b["batch_no"] for b in payload["batches"]] == ["S-2"]


def test_mapping_ensure_create_and_conflict(client, db):
    entries = [{"mark": "A1", "description": "d", "has_pf": False}]
    resp = client.post("/api/sync/mapping", json={"name": "NEW-MAP", "entries": entries})
    assert resp.status_code == 200 and resp.json()["created"] is True

    again = client.post("/api/sync/mapping", json={"name": "NEW-MAP", "entries": entries})
    assert again.status_code == 200 and again.json()["created"] is False

    conflict = client.post(
        "/api/sync/mapping",
        json={"name": "NEW-MAP", "entries": [{"mark": "B2", "description": "x", "has_pf": True}]},
    )
    assert conflict.status_code == 409


def test_mapping_ensure_numeric_entries_idempotent(client, db):
    """带数值列的对照表重复 ensure 不应误判 409。

    DB 里 area_s11/area_s22 是 Integer、eg/fl/ag 是 Float；请求 JSON 一律按
    float 解析。指纹比较必须归一 int/float，否则同一份数据第二次推送时
    3 (DB int) vs 3.0 (请求 float) 会被误判为内容不同。
    """
    entries = [
        {"mark": "A1", "eg": 1.5, "fl": 0, "ag": 2, "area_s11": 3, "area_s22": 4, "has_pf": True}
    ]
    first = client.post("/api/sync/mapping", json={"name": "NUM-MAP", "entries": entries})
    assert first.status_code == 200 and first.json()["created"] is True

    again = client.post("/api/sync/mapping", json={"name": "NUM-MAP", "entries": entries})
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False


def test_push_batch_idempotent(client, db, tmp_path, monkeypatch, settings):
    # sync.py 在收集期就可能被 import（其他测试模块顶层 import app.main），
    # 此时 from app.config import get_settings 绑定的是真实函数；这里显式钉住
    # 模块命名空间，保证端点写到 tmp DATA_ROOT，与测试执行顺序无关。
    monkeypatch.setattr("app.api.sync.get_settings", lambda: settings)

    # Celery 任务体内用 app.db.SessionLocal（导入期绑定真实 PostgreSQL），
    # eager 内联执行也够不到本测试的内存 SQLite（本环境无 PG/Redis，已实测
    # 抛 TaskDispatchError: connection refused）。worker 链路本身由
    # test_integration.py 在真实环境覆盖；这里在 worker 边界打桩，把任务
    # 置为 success，专注验证端点自身的幂等分支。
    def _fake_dispatch_batch_task(*, task_id, **_kwargs):
        task = db.get(UploadTask, task_id)
        task.status = "success"
        task.progress_pct = 100
        db.commit()
        return f"test-{task_id}"

    monkeypatch.setattr(
        "app.services.upload_service.dispatch_batch_task", _fake_dispatch_batch_task
    )

    client.post("/api/sync/mapping", json={"name": "PUSH-MAP", "entries": [{"mark": "A1"}]})
    zip_path = _make_zip(tmp_path / "PUSH-1.zip")

    with zip_path.open("rb") as fh:
        resp = client.post(
            "/api/sync/push-batch",
            files={"file": ("PUSH-1.zip", fh, "application/zip")},
            data={"batch_no": "PUSH-1", "mapping_name": "PUSH-MAP", "uploaded_by": "tester"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in {"accepted", "already_synced"}

    with zip_path.open("rb") as fh:
        again = client.post(
            "/api/sync/push-batch",
            files={"file": ("PUSH-1.zip", fh, "application/zip")},
            data={"batch_no": "PUSH-1", "mapping_name": "PUSH-MAP", "uploaded_by": "tester"},
        )
    assert again.status_code == 200
    assert again.json()["status"] == "already_synced"


def test_push_batch_unknown_mapping_422(client):
    resp = client.post(
        "/api/sync/push-batch",
        files={"file": ("X.zip", io.BytesIO(b"PK\x05\x06" + b"\0" * 18), "application/zip")},
        data={"batch_no": "X-1", "mapping_name": "NOPE"},
    )
    assert resp.status_code == 422


def test_sparam_by_natural_key_404(client, db):
    resp = client.get(
        "/api/sync/sparam",
        params={"batch_no": "NONE", "filename": "x.s1p", "port": "S11"},
    )
    assert resp.status_code == 404
