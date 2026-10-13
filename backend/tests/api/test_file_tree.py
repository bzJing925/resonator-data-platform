"""虚拟文件树 API 测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def tree_client(request, tmp_path, monkeypatch):
    # HTTP/file tree verification is independent of upload and queue execution.
    with platform_database(request.param, tmp_path) as (db, batch):
        monkeypatch.setattr(app.state, "artifact_engine", db.bind, raising=False)
        directory = tmp_path / "persisted-results"
        directory.mkdir()
        for name in ("a.s1p", "b.s1p"):
            (directory / name).write_text("# Hz S RI R 50\n1000000000 0.1 0\n")
        batch.file_path = str(directory)
        batch.raw_zip_path = str(tmp_path / "original.zip")
        db.commit()
        batch_no = batch.batch_no

        def override_db():
            with Session(db.bind) as session:
                yield session

        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = override_db
        try:
            client = TestClient(app)
            try:
                yield client, batch_no
            finally:
                client.close()
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)


def test_file_tree_list_root_zip_node(tree_client) -> None:
    client, batch_no = tree_client

    r = client.get("/api/files/tree", params={"batch_no": batch_no})
    assert r.status_code == 200, r.text
    nodes = r.json()
    assert len(nodes) == 1
    assert nodes[0]["node_type"] == "zip"
    assert nodes[0]["name"].endswith(".zip")


def test_file_tree_navigate_and_mkdir(tree_client) -> None:
    client, batch_no = tree_client

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


def test_file_tree_reorder_and_delete(tree_client) -> None:
    client, batch_no = tree_client

    r = client.get("/api/files/tree", params={"batch_no": batch_no})
    zip_node = r.json()[0]
    r = client.get("/api/files/tree", params={"batch_no": batch_no, "parent_id": zip_node["id"]})
    children = r.json()
    assert len(children) == 2

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


def test_file_tree_download_nodes(tree_client) -> None:
    client, batch_no = tree_client

    r = client.get("/api/files/tree", params={"batch_no": batch_no})
    zip_node = r.json()[0]

    r = client.post(
        "/api/files/download-zip-nodes",
        json={"batch_no": batch_no, "node_ids": [zip_node["id"]]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert len(r.content) > 0
