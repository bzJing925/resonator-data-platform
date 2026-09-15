"""桌面角色端点：配置存取、状态、拉取、推送；非桌面模式 enabled=false。"""

from unittest.mock import patch


def test_status_disabled_when_not_desktop(client):
    resp = client.get("/api/desktop-sync/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_config_round_trip(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.desktop_sync.server_client._config_dir", lambda: tmp_path)
    resp = client.put(
        "/api/desktop-sync/config",
        json={"server_url": "http://192.168.1.10:8080", "username": "u1"},
    )
    assert resp.status_code == 200
    loaded = client.get("/api/desktop-sync/config").json()
    assert loaded == {"server_url": "http://192.168.1.10:8080", "username": "u1"}


def test_pull_requires_config(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.desktop_sync.server_client._config_dir", lambda: tmp_path)
    resp = client.post("/api/desktop-sync/pull")
    assert resp.status_code == 400


def test_pull_calls_service(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.desktop_sync.server_client._config_dir", lambda: tmp_path)
    client.put("/api/desktop-sync/config", json={"server_url": "http://s", "username": "u"})
    with patch("app.api.desktop_sync.desktop_sync_service.pull_from_server") as pull:
        pull.return_value = {"mappings_added": 0, "batches_added": 2, "devices_added": 10}
        resp = client.post("/api/desktop-sync/pull")
    assert resp.status_code == 200
    assert resp.json()["batches_added"] == 2
