"""桌面端服务器配置存取 + httpx 客户端封装。"""

import json

import httpx
import pytest

from app.services import server_client


def test_config_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(server_client, "_config_dir", lambda: tmp_path)
    assert server_client.load_config() is None
    cfg = server_client.ServerConfig(server_url="http://192.168.1.10:8080/", username="bozuo")
    server_client.save_config(cfg)
    loaded = server_client.load_config()
    assert loaded.server_url == "http://192.168.1.10:8080"  # 尾斜杠归一化
    assert loaded.username == "bozuo"


def test_client_calls(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/sync/snapshot":
            return httpx.Response(200, json={"batches": [], "mappings": [], "devices_arrow_gz_b64": ""})
        if request.url.path == "/api/tasks/7":
            return httpx.Response(200, json={"id": 7, "status": "success"})
        return httpx.Response(404, json={"detail": "nope"})

    cfg = server_client.ServerConfig(server_url="http://server", username="u")
    client = server_client.ServerClient(cfg, transport=httpx.MockTransport(handler))
    assert client.reachable() is True
    assert client.fetch_snapshot({"A"}) == {"batches": [], "mappings": [], "devices_arrow_gz_b64": ""}
    assert json.loads(seen["/api/sync/snapshot"].content) == {"known_batch_nos": ["A"]}
    assert client.task_status(7)["status"] == "success"
    with pytest.raises(server_client.ServerError):
        client.task_status(999)
