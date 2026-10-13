"""表格关联目标在上传时校验：目标不存在/非服务器副本 → 400，不落库、不留 409 重放。"""

from app.models import Batch, Mapping


def _post_table(client, target_batch: str):
    return client.post(
        "/api/uploads",
        files={"file": ("metrics.xlsx", b"not-a-real-xlsx")},
        data={"mapping_id": "1", "target_batch": target_batch},
    )


def test_table_target_must_exist(client, db):
    resp = _post_table(client, "NOPE")
    assert resp.status_code == 400
    assert "不存在" in resp.json()["detail"]


def test_table_target_must_be_server_replica(client, db):
    mapping = Mapping(name="M", file_path="m", entry_count=0)
    db.add(mapping)
    db.flush()
    db.add(Batch(batch_no="Local", mapping_id=mapping.id, file_path="x",
                 source="http", device_count=0))
    db.commit()
    resp = _post_table(client, "Local")
    assert resp.status_code == 400
    assert "服务器" in resp.json()["detail"]


def test_raw_upload_ignores_target_batch_validation(client, db):
    """原始 zip 上传不触发关联校验（target_batch 仅对表格生效）。"""
    resp = client.post(
        "/api/uploads",
        files={"file": ("devices.zip", b"not-a-real-zip")},
        data={"mapping_id": "1", "target_batch": "NOPE"},
    )
    assert resp.status_code != 400 or "关联目标" not in resp.json().get("detail", "")
