"""虚拟文件树 API 测试。"""

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
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    import os

    if os.environ.get("ALN_PROTECT_DB") == "1":
        pytest.skip("ALN_PROTECT_DB=1，跳过会清数据的集成测试")
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE file_nodes, devices, batches, upload_tasks, "
                "mapping_entries, mappings RESTART IDENTITY CASCADE"
            )
        )


def _upload_mapping_and_batch(client: TestClient, sample_mapping: Path, sample_zip: Path) -> str:
    with sample_mapping.open("rb") as f:
        r = client.post(
            "/api/mappings",
            files={"file": ("ELB003.xlsx", f)},
            data={"name": "ELB003"},
        )
    assert r.status_code == 201, r.text
    mapping_id = r.json()["id"]

    with sample_zip.open("rb") as f:
        r = client.post(
            "/api/uploads",
            files={"file": ("T8901P.01.zip", f)},
            data={
                "mapping_id": str(mapping_id),
                "process_type": "S1P",
                "deembed": "false",
            },
        )
    assert r.status_code == 202, r.text
    return r.json()["batch_no"]


def test_file_tree_list_root_zip_node(
    client: TestClient, sample_mapping: Path, sample_zip: Path
) -> None:
    batch_no = _upload_mapping_and_batch(client, sample_mapping, sample_zip)

    r = client.get("/api/files/tree", params={"batch_no": batch_no})
    assert r.status_code == 200, r.text
    nodes = r.json()
    assert len(nodes) == 1
    assert nodes[0]["node_type"] == "zip"
    assert nodes[0]["name"].endswith(".zip")


def test_file_tree_navigate_and_mkdir(
    client: TestClient, sample_mapping: Path, sample_zip: Path
) -> None:
    batch_no = _upload_mapping_and_batch(client, sample_mapping, sample_zip)

    # 根节点下只有 zip 文件夹
    r = client.get("/api/files/tree", params={"batch_no": batch_no})
    zip_node = r.json()[0]

    # 进入 zip 文件夹，应看到真实文件
    r = client.get("/api/files/tree", params={"batch_no": batch_no, "parent_id": zip_node["id"]})
    assert r.status_code == 200, r.text
    children = r.json()
    assert len(children) > 0
    file_node = next(n for n in children if n["node_type"] == "file")
    assert file_node["size"] is not None

    # 新建文件夹
    r = client.post(
        "/api/files/tree/mkdir",
        json={"batch_no": batch_no, "parent_id": zip_node["id"], "name": "my-folder"},
    )
    assert r.status_code == 200, r.text
    folder = r.json()
    assert folder["node_type"] == "folder"

    # 把文件移进文件夹
    r = client.post(
        "/api/files/tree/move",
        json={"node_ids": [file_node["id"]], "target_folder_id": folder["id"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["moved"] == 1

    # 进入文件夹应能看到该文件
    r = client.get(
        "/api/files/tree",
        params={"batch_no": batch_no, "parent_id": folder["id"]},
    )
    assert r.json()[0]["id"] == file_node["id"]


def test_file_tree_reorder_and_delete(
    client: TestClient, sample_mapping: Path, sample_zip: Path
) -> None:
    batch_no = _upload_mapping_and_batch(client, sample_mapping, sample_zip)

    r = client.get("/api/files/tree", params={"batch_no": batch_no})
    zip_node = r.json()[0]
    r = client.get("/api/files/tree", params={"batch_no": batch_no, "parent_id": zip_node["id"]})
    children = r.json()
    if len(children) < 2:
        pytest.skip("样例文件不足 2 个，跳过排序测试")

    ids = [n["id"] for n in children]
    reversed_ids = list(reversed(ids))
    r = client.post(
        "/api/files/tree/reorder",
        json={"parent_id": zip_node["id"], "node_ids": reversed_ids},
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/files/tree", params={"batch_no": batch_no, "parent_id": zip_node["id"]})
    assert [n["id"] for n in r.json()] == reversed_ids

    # 软删除
    r = client.post("/api/files/tree/delete", json={"node_ids": [reversed_ids[0]]})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] >= 1

    r = client.get("/api/files/tree", params={"batch_no": batch_no, "parent_id": zip_node["id"]})
    assert reversed_ids[0] not in [n["id"] for n in r.json()]


def test_file_tree_download_nodes(
    client: TestClient, sample_mapping: Path, sample_zip: Path
) -> None:
    batch_no = _upload_mapping_and_batch(client, sample_mapping, sample_zip)

    r = client.get("/api/files/tree", params={"batch_no": batch_no})
    zip_node = r.json()[0]

    r = client.post(
        "/api/files/download-zip-nodes",
        json={"batch_no": batch_no, "node_ids": [zip_node["id"]]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert len(r.content) > 0
