"""本地无 snp 文件时，桌面模式透明代理到内网服务器。"""

import pytest
from fastapi import HTTPException

from app.models import Batch, Device, Mapping
from app.services import server_client, server_proxy
from app.services.server_client import ServerConfig, ServerError


class _Device:
    s_param_path = "missing.s1p"
    original_filename = "missing.s1p"
    s_param_port = "S11"

    class batch:  # noqa: N801 模拟 ORM 关系属性名
        batch_no = "B-1"


def test_proxy_skipped_without_desktop(monkeypatch):
    monkeypatch.setattr(server_proxy, "_is_desktop", lambda: False)
    assert server_proxy.proxy_sparam(_Device(), "s11_db") is None


def test_proxy_skipped_without_config(monkeypatch, tmp_path):
    monkeypatch.setattr(server_proxy, "_is_desktop", lambda: True)
    monkeypatch.setattr(server_proxy.server_client, "_config_dir", lambda: tmp_path)
    assert server_proxy.proxy_sparam(_Device(), "s11_db") is None


def test_proxy_returns_server_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(server_proxy, "_is_desktop", lambda: True)
    monkeypatch.setattr(server_proxy.server_client, "_config_dir", lambda: tmp_path)
    server_proxy.server_client.save_config(ServerConfig(server_url="http://s"))

    class FakeClient:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def fetch_sparam(self, batch_no, filename, port, param):
            assert (batch_no, filename, port, param) == ("B-1", "missing.s1p", "S11", "s11_db")
            return {"freq_ghz": [4.0], "values": [-1.0], "param": param}

    monkeypatch.setattr(server_proxy, "ServerClient", FakeClient)
    result = server_proxy.proxy_sparam(_Device(), "s11_db")
    assert result["source"] == "server-proxy"
    assert result["values"] == [-1.0]


def test_proxy_unreachable_raises_503(monkeypatch, tmp_path):
    monkeypatch.setattr(server_proxy, "_is_desktop", lambda: True)
    monkeypatch.setattr(server_proxy.server_client, "_config_dir", lambda: tmp_path)
    server_proxy.server_client.save_config(ServerConfig(server_url="http://s"))

    class DownClient:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def fetch_sparam(self, *a):
            raise ServerError("无法连接服务器")

    monkeypatch.setattr(server_proxy, "ServerClient", DownClient)
    with pytest.raises(HTTPException) as excinfo:
        server_proxy.proxy_sparam(_Device(), "s11_db")
    assert excinfo.value.status_code == 503


def _seed_missing_file_device(db, batch_no="EP-1", filename="gone.s1p") -> Device:
    """造一个 s_param_path 指向不存在文件的 device（文件已被同步清理）。"""
    mapping = Mapping(name=f"MAP-{batch_no}", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no=batch_no, mapping_id=mapping.id, file_path="f", device_count=1)
    db.add(batch)
    db.flush()
    device = Device(
        batch_id=batch.id,
        original_filename=filename,
        s_param_path=filename,
        s_param_port="S11",
    )
    db.add(device)
    db.commit()
    return device


def test_sparam_endpoint_404_without_desktop(client, db):
    """非桌面模式（默认 fixture）：本地无文件仍是原 404，证明只拦 404 不误代理。"""
    device = _seed_missing_file_device(db)
    resp = client.get(f"/api/devices/{device.id}/sparam")
    assert resp.status_code == 404


def test_sparam_endpoint_proxies_when_desktop(client, db, monkeypatch):
    """桌面模式 + 已配置服务器：同请求返回代理 payload，file_path 用本机 DB 值。"""
    device = _seed_missing_file_device(db)
    monkeypatch.setattr(server_proxy, "_is_desktop", lambda: True)
    # settings fixture 已钉住 app.config.get_settings，配置落在测试 desktop_dir
    server_client.save_config(ServerConfig(server_url="http://s"))

    class FakeClient:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def fetch_sparam(self, batch_no, filename, port, param):
            assert (batch_no, filename, port) == ("EP-1", "gone.s1p", "S11")
            # 服务器侧 file_path 是服务器本地路径，端点必须覆盖回本机值
            return {
                "freq_ghz": [4.0],
                "values": [-1.0],
                "param": param,
                "file_path": "/srv/data3/files/EP-1/gone.s1p",
            }

    monkeypatch.setattr(server_proxy, "ServerClient", FakeClient)
    resp = client.get(f"/api/devices/{device.id}/sparam")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["source"] == "server-proxy"
    assert payload["device_id"] == device.id
    assert payload["file_path"] == "gone.s1p"
    assert payload["values"] == [-1.0]
