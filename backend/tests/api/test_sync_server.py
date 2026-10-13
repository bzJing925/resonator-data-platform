"""服务器角色同步端点：快照/mapping 确保/推送入库/自然键曲线。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Batch, Device, Mapping, UploadTask


def _make_zip(path: Path) -> Path:
    rows = (f"{4.0 + i * 0.001:.3f} {0.5 - i * 1e-4:.6f} {170 + i * 0.01:.2f}" for i in range(801))
    s1p = "# GHz S MA R 50\n" + "\n".join(rows) + "\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("17_A1-1_X0Y0.s1p", s1p)
    return path


def _empty_zip() -> io.BytesIO:
    return io.BytesIO(b"PK\x05\x06" + b"\0" * 18)


@pytest.fixture
def dispatch_success(db, monkeypatch):
    """打桩 worker 边界：直接把任务置 success。

    Celery 任务体内用 app.db.SessionLocal（导入期绑定真实 PostgreSQL），
    eager 内联执行也够不到本测试的内存 SQLite（本环境无 PG/Redis，已实测
    抛 TaskDispatchError: connection refused）。worker 链路本身由
    test_integration.py 在真实环境覆盖；这里专注验证端点自身的分支。
    """

    def _fake_dispatch_batch_task(*, task_id, **_kwargs):
        from app.services.processing_run import claim_run, finish_run

        claimed = claim_run(db.bind, _kwargs["run_id"], _kwargs["lease_token"], "compute")
        finish_run(db.bind, claimed.run_id, claimed.lease_token, "success")
        return f"test-{task_id}"

    monkeypatch.setattr(
        "app.services.upload_service.dispatch_batch_task", _fake_dispatch_batch_task
    )


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
    from app.core.mapping import load_mapping

    mapping = db.get(Mapping, first.json()["mapping_id"])
    projected = load_mapping(mapping.file_path)["A1"]
    assert projected.eg == 1.5 and projected.area_s11 == 3 and projected.area_s22 == 4
    assert projected.has_pf is True

    again = client.post("/api/sync/mapping", json={"name": "NUM-MAP", "entries": entries})
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False


def test_push_batch_idempotent(client, db, tmp_path, dispatch_success):
    client.post("/api/sync/mapping", json={
        "name": "PUSH-MAP",
        "entries": [{"mark": "A1-1", "description": "EG0 FL0 700&5500"}],
    })
    zip_path = _make_zip(tmp_path / "PUSH-1.zip")

    with zip_path.open("rb") as fh:
        resp = client.post(
            "/api/sync/push-batch",
            files={"file": ("PUSH-1.zip", fh, "application/zip")},
            data={"batch_no": "PUSH-1", "mapping_name": "PUSH-MAP", "uploaded_by": "tester"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"

    with zip_path.open("rb") as fh:
        again = client.post(
            "/api/sync/push-batch",
            files={"file": ("PUSH-1.zip", fh, "application/zip")},
            data={"batch_no": "PUSH-1", "mapping_name": "PUSH-MAP", "uploaded_by": "tester"},
        )
    assert again.status_code == 200
    assert again.json()["status"] == "already_synced"


@pytest.mark.parametrize("batch_no", [".", "..", "../../x", "a/b", "a\\b", "C:", ". ", " ."])
def test_push_batch_rejects_path_traversal(client, batch_no):
    # batch_no 会拼进 files_dir 路径并用于目录清理，必须挡住路径穿越：
    # "." 经 rmtree(files_dir / ".") 会删整个 files_dir；"a\\b"/"C:" 是
    # Windows 语义下的子目录/盘符相对路径，". "/" ." 是 Windows 会归一掉的
    # 尾点空格名，双语义都需拒绝。
    resp = client.post(
        "/api/sync/push-batch",
        files={"file": ("X.zip", _empty_zip(), "application/zip")},
        data={"batch_no": batch_no, "mapping_name": "NOPE"},
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("batch_no", ["T8901P.01", "批 次.01", "batch 01"])
def test_push_batch_accepts_normal_batch_no(client, batch_no):
    """正常批次名（含点号、中文、空格）不应被路径校验误伤。

    用不存在的 mapping 名：请求若穿过 batch_no 校验，会落在 mapping 查询的 422。
    """
    resp = client.post(
        "/api/sync/push-batch",
        files={"file": ("X.zip", _empty_zip(), "application/zip")},
        data={"batch_no": batch_no, "mapping_name": "NOPE"},
    )
    assert resp.status_code == 422


def test_push_batch_invalid_form_values_400(client, db):
    client.post("/api/sync/mapping", json={"name": "FV-MAP", "entries": [{"mark": "A1"}]})

    bad_proc = client.post(
        "/api/sync/push-batch",
        files={"file": ("X.zip", _empty_zip(), "application/zip")},
        data={"batch_no": "FV-1", "mapping_name": "FV-MAP", "process_type": "BOGUS"},
    )
    assert bad_proc.status_code == 400

    bad_method = client.post(
        "/api/sync/push-batch",
        files={"file": ("X.zip", _empty_zip(), "application/zip")},
        data={
            "batch_no": "FV-1",
            "mapping_name": "FV-MAP",
            "deembed": "true",
            "deembed_method": "bogus",
        },
    )
    assert bad_method.status_code == 400


@pytest.mark.parametrize("status", ["pending", "running", "cancel_requested"])
def test_push_batch_in_progress_409(client, db, status):
    mapping_id = client.post(
        "/api/sync/mapping", json={"name": "RUN-MAP", "entries": [{"mark": "A1"}]}
    ).json()["mapping_id"]
    task = UploadTask(batch_no="RUN-1", status=status, kind="upload")
    db.add(task)
    db.flush()
    db.add(Batch(batch_no="RUN-1", mapping_id=mapping_id, file_path="f.zip", task_id=task.id))
    db.commit()

    resp = client.post(
        "/api/sync/push-batch",
        files={"file": ("RUN-1.zip", _empty_zip(), "application/zip")},
        data={"batch_no": "RUN-1", "mapping_name": "RUN-MAP"},
    )
    assert resp.status_code == 409


def test_push_batch_failed_previous_preserved(
    client, db, tmp_path, settings, dispatch_success
):
    """失败输入与处理历史保留，由显式retry创建下一次尝试。"""
    mapping_id = client.post(
        "/api/sync/mapping", json={"name": "FAIL-MAP", "entries": [{"mark": "A1"}]}
    ).json()["mapping_id"]

    old_task = UploadTask(batch_no="FAIL-1", status="failed", kind="upload")
    db.add(old_task)
    db.flush()
    old_task_id = old_task.id
    old_raw_zip = tmp_path / "old-raw.zip"
    old_raw_zip.write_bytes(b"old")
    old_batch = Batch(
        batch_no="FAIL-1",
        mapping_id=mapping_id,
        file_path=str(old_raw_zip),
        raw_zip_path=str(old_raw_zip),
        task_id=old_task.id,
    )
    db.add(old_batch)
    db.commit()

    old_files_dir = settings.files_dir / "FAIL-1"
    old_files_dir.mkdir(parents=True)
    (old_files_dir / "dut1.s1p").write_text("dummy")

    zip_path = _make_zip(tmp_path / "FAIL-1.zip")
    with zip_path.open("rb") as fh:
        resp = client.post(
            "/api/sync/push-batch",
            files={"file": ("FAIL-1.zip", fh, "application/zip")},
            data={"batch_no": "FAIL-1", "mapping_name": "FAIL-MAP"},
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "SYNC_BATCH_CONFLICT"
    assert old_files_dir.exists() and old_raw_zip.exists()
    batches = db.scalars(select(Batch).where(Batch.batch_no == "FAIL-1")).all()
    assert len(batches) == 1 and batches[0].task_id == old_task_id
    assert batches[0].raw_zip_path == str(old_raw_zip)
    assert db.get(UploadTask, old_task_id).status == "failed"


def test_push_batch_unknown_mapping_422(client):
    resp = client.post(
        "/api/sync/push-batch",
        files={"file": ("X.zip", _empty_zip(), "application/zip")},
        data={"batch_no": "X-1", "mapping_name": "NOPE"},
    )
    assert resp.status_code == 422


def test_sparam_by_natural_key_404(client, db):
    resp = client.get(
        "/api/sync/sparam",
        params={"batch_no": "NONE", "filename": "x.s1p", "port": "S11"},
    )
    assert resp.status_code == 404


def test_sparam_duplicate_natural_key_409(client, db):
    """同批次 zip 内不同子目录的同名文件会产生多条 device，自然键不唯一 → 409。"""
    mapping = Mapping(name="DUP-MAP", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no="DUP-1", mapping_id=mapping.id, file_path="f", device_count=2)
    db.add(batch)
    db.flush()
    db.add_all(
        [
            Device(
                batch_id=batch.id,
                original_filename="dup.s1p",
                s_param_path="sub1/dup.s1p",
                s_param_port="S11",
            ),
            Device(
                batch_id=batch.id,
                original_filename="dup.s1p",
                s_param_path="sub2/dup.s1p",
                s_param_port="S11",
            ),
        ]
    )
    db.commit()

    resp = client.get(
        "/api/sync/sparam",
        params={"batch_no": "DUP-1", "filename": "dup.s1p", "port": "S11"},
    )
    assert resp.status_code == 409
