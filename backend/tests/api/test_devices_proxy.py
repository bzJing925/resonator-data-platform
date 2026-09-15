"""本地无 snp 文件时，桌面模式透明代理到内网服务器。"""

import pytest
from fastapi import HTTPException

from app.services import server_proxy
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
        def __init__(self, config): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
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
        def __init__(self, config): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def fetch_sparam(self, *a):
            raise ServerError("无法连接服务器")

    monkeypatch.setattr(server_proxy, "ServerClient", DownClient)
    with pytest.raises(HTTPException) as excinfo:
        server_proxy.proxy_sparam(_Device(), "s11_db")
    assert excinfo.value.status_code == 503
