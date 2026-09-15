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


def test_client_calls():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/sync/snapshot":
            return httpx.Response(
                200, json={"batches": [], "mappings": [], "devices_arrow_gz_b64": ""}
            )
        if request.url.path == "/api/tasks/7":
            return httpx.Response(200, json={"id": 7, "status": "success"})
        return httpx.Response(404, json={"detail": "nope"})

    cfg = server_client.ServerConfig(server_url="http://server", username="u")
    client = server_client.ServerClient(cfg, transport=httpx.MockTransport(handler))
    assert client.reachable() is True
    assert client.fetch_snapshot({"A"}) == {
        "batches": [],
        "mappings": [],
        "devices_arrow_gz_b64": "",
    }
    assert json.loads(seen["/api/sync/snapshot"].content) == {"known_batch_nos": ["A"]}
    assert client.task_status(7)["status"] == "success"
    with pytest.raises(server_client.ServerError):
        client.task_status(999)


class _SyncStream(httpx.SyncByteStream):
    """自定义同步字节流：让 MockTransport 返回 content 未预读的 Response。

    不用私有 API httpx._content.IteratorByteStream（版本间不稳）。
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self):
        yield from self._chunks

    def close(self) -> None:
        pass


def test_download_s1p_not_found_raises_server_error(tmp_path):
    """流式 404 必须抛 ServerError 且消息含状态码（钉住契约防 ResponseNotRead 回归）。

    handler 返回带真实 stream 的 404 Response（content 未预读）：
    直接访问 .text 会抛 ResponseNotRead，证明错误 body 确由
    download_s1p 的"仅错误时 read()"分支载入，而非测试假数据预读。
    """
    body = b'{"detail": "not found"}'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sync/download":
            return httpx.Response(404, stream=_SyncStream([body]))
        return httpx.Response(404, stream=_SyncStream([b'{"detail": "nope"}']))

    cfg = server_client.ServerConfig(server_url="http://server", username="u")
    client = server_client.ServerClient(cfg, transport=httpx.MockTransport(handler))
    dest = tmp_path / "down.s1p"
    with pytest.raises(server_client.ServerError, match="404"):
        client.download_s1p("B1", "dev.s2p", "S11", dest)
    assert not dest.exists()

    # 旁证：同一 stream 形态下未 read() 时 .text 确实抛 ResponseNotRead，
    # 即上面的 ServerError 走的是条件 read() 分支而非依赖预读 content。
    with client._http.stream("GET", "/sync/download") as raw:
        assert raw.status_code == 404
        with pytest.raises(httpx.ResponseNotRead):
            _ = raw.text
    client.close()
