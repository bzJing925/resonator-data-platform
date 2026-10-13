"""本地无 snp 文件时，桌面模式透明代理到内网服务器。"""

import asyncio

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


def test_proxy_download_writes_tmp_and_cleans_up(monkeypatch, tmp_path):
    """命中：桩客户端往 dest 写字节，返回 (tmp, BackgroundTask)；清理任务删 tmp。"""
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

        def download_s1p(self, batch_no, filename, port, dest):
            assert (batch_no, filename, port) == ("B-1", "missing.s1p", "S11")
            dest.write_bytes(b"# fake s1p from server")

    monkeypatch.setattr(server_proxy, "ServerClient", FakeClient)
    result = server_proxy.proxy_download(_Device())
    assert result is not None
    tmp, cleanup = result
    assert tmp.read_bytes() == b"# fake s1p from server"
    asyncio.run(cleanup())
    assert not tmp.exists()


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


def _seed_snapshot_device(db, batch_no="EP-2", filename="remote.s1p") -> Device:
    """造一个 s_param_path=None 的 device（模拟从服务器快照导入的器件）。"""
    mapping = Mapping(name=f"MAP-{batch_no}", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no=batch_no, mapping_id=mapping.id, file_path="f", device_count=1)
    db.add(batch)
    db.flush()
    device = Device(
        batch_id=batch.id,
        original_filename=filename,
        s_param_path=None,
        s_param_port="S11",
    )
    db.add(device)
    db.commit()
    return device


def test_download_endpoint_404_without_desktop(client, db):
    """快照导入器件（无 s_param_path）：非桌面模式（默认 fixture）仍是原 404。"""
    device = _seed_snapshot_device(db)
    resp = client.get(f"/api/devices/{device.id}/download-s1p")
    assert resp.status_code == 404
    assert "没有 S 参数文件路径" in resp.json()["detail"]


def test_download_endpoint_proxies_when_desktop(client, db, monkeypatch, tmp_path):
    """桌面模式 + 已配置服务器：同请求返回 200，文件内容来自"服务器"。"""
    device = _seed_snapshot_device(db)
    monkeypatch.setattr(server_proxy, "_is_desktop", lambda: True)
    monkeypatch.setattr(server_client, "_config_dir", lambda: tmp_path)
    server_client.save_config(ServerConfig(server_url="http://s"))

    class FakeClient:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def download_s1p(self, batch_no, filename, port, dest):
            assert (batch_no, filename, port) == ("EP-2", "remote.s1p", "S11")
            dest.write_bytes(b"# server side s1p")

    monkeypatch.setattr(server_proxy, "ServerClient", FakeClient)
    resp = client.get(f"/api/devices/{device.id}/download-s1p")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"# server side s1p"
    assert "remote.s1p" in resp.headers["content-disposition"]


@pytest.mark.parametrize("disabled", [False, True])
def test_pinn_fast_path_falls_back_when_torch_missing(client, db, monkeypatch, tmp_path, disabled):
    """Torch 导入失败或安装自检失败时，fast 请求仍使用完整 skrf 曲线。"""
    import shutil
    from pathlib import Path

    import skrf.data

    from app.models import Batch, Device, Mapping

    src = tmp_path / "dut.s1p"
    shutil.copy(Path(skrf.data.__file__).parent / "ro,1.s1p", src)

    mapping = Mapping(name="M", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no="FB-1", mapping_id=mapping.id, file_path="f", device_count=1)
    db.add(batch)
    db.flush()
    device = Device(batch_id=batch.id, original_filename="dut.s1p", s_param_port="S11",
                    s_param_path=str(src), deembedded=False)
    db.add(device)
    db.commit()

    def boom(_device):
        if disabled:
            raise AssertionError("PyTorch must not be imported after a failed native probe")
        raise ImportError("DLL init failed")

    monkeypatch.setattr("app.ml.inference.predict_s11_db", boom)
    if disabled:
        monkeypatch.setenv("ALN_DISABLE_TORCH", "1")
    resp = client.get(f"/api/devices/{device.id}/sparam?param=s11_db&fast=true")
    assert resp.status_code == 200
    assert resp.json()["source"] == "skrf"
