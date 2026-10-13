"""桌面端 → 内网服务器 的配置存取与 HTTP 客户端。

配置存在 ``<desktop_dir>/server.json``；仅桌面模式使用，非桌面环境
load_config 返回 None 的路径不会被触达（上层先判 is_desktop）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings


class ServerError(RuntimeError):
    """服务器不可达或返回错误。"""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ServerConfig:
    server_url: str
    username: str = "anonymous"

    def __post_init__(self) -> None:
        self.server_url = self.server_url.strip().rstrip("/")
        self.username = self.username.strip() or "anonymous"


def _config_dir() -> Path:
    return get_settings().desktop_dir


def _config_path() -> Path:
    return _config_dir() / "server.json"


def load_config() -> ServerConfig | None:
    path = _config_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ServerConfig(
            server_url=str(data["server_url"]),
            username=str(data.get("username") or "anonymous"),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def save_config(config: ServerConfig) -> None:
    _config_dir().mkdir(parents=True, exist_ok=True)
    _config_path().write_text(
        json.dumps(
            {"server_url": config.server_url, "username": config.username},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


class ServerClient:
    def __init__(
        self, config: ServerConfig, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.config = config
        self._http = httpx.Client(
            base_url=f"{config.server_url}/api",
            timeout=httpx.Timeout(60.0, read=300.0),
            transport=transport,
            trust_env=False,
        )

    def request_json(self, method: str, path: str, **kwargs) -> dict:
        try:
            return self._check(self._http.request(method, path, **kwargs)).json()
        except httpx.HTTPError as exc:
            raise ServerError(f"无法连接服务器: {exc}") from exc

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ServerClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            raise ServerError(f"服务器返回 {response.status_code}: {response.text[:300]}",
                              status_code=response.status_code)
        return response

    def reachable(self) -> bool:
        try:
            # 状态端点轮询用：60s 默认连接超时太长，探测要快速失败
            return self._http.get("/health", timeout=5.0).status_code == 200
        except httpx.HTTPError:
            return False

    def fetch_snapshot(self, known_batch_nos: set[str]) -> dict[str, Any]:
        try:
            return self._check(
                self._http.post("/sync/snapshot", json={"known_batch_nos": sorted(known_batch_nos)})
            ).json()
        except httpx.HTTPError as exc:
            raise ServerError(f"无法连接服务器: {exc}") from exc

    def ensure_mapping(self, name: str, entries: list[dict]) -> dict[str, Any]:
        try:
            return self._check(
                self._http.post("/sync/mapping", json={"name": name, "entries": entries})
            ).json()
        except httpx.HTTPError as exc:
            raise ServerError(f"无法连接服务器: {exc}") from exc

    def push_batch(self, zip_path: Path, meta: dict[str, str]) -> dict[str, Any]:
        try:
            with zip_path.open("rb") as fh:
                data = dict(meta)
                files = {"file": (zip_path.name, fh, "application/zip")}
                if "source_inputs" in data:
                    files["source_inputs"] = ("source-inputs.json",
                                              data.pop("source_inputs").encode(),
                                              "application/json")
                return self._check(
                    self._http.post(
                        "/sync/push-batch",
                        files=files,
                        data=data,
                        timeout=httpx.Timeout(60.0, write=3600.0, read=600.0),
                    )
                ).json()
        except httpx.HTTPError as exc:
            raise ServerError(f"推送失败: {exc}") from exc

    def task_status(self, task_id: int) -> dict[str, Any]:
        try:
            return self._check(self._http.get(f"/tasks/{task_id}")).json()
        except httpx.HTTPError as exc:
            raise ServerError(f"无法连接服务器: {exc}") from exc

    def get_batch(self, batch_no: str) -> dict[str, Any]:
        try:
            return self._check(self._http.get(f"/batches/{batch_no}")).json()
        except httpx.HTTPError as exc:
            raise ServerError(f"无法连接服务器: {exc}") from exc

    def fetch_sparam(self, batch_no: str, filename: str, port: str, param: str) -> dict[str, Any]:
        try:
            return self._check(
                self._http.get(
                    "/sync/sparam",
                    params={
                        "batch_no": batch_no,
                        "filename": filename,
                        "port": port,
                        "param": param,
                    },
                )
            ).json()
        except httpx.HTTPError as exc:
            raise ServerError(f"无法从服务器获取曲线: {exc}") from exc

    def download_s1p(self, batch_no: str, filename: str, port: str, dest: Path) -> Path:
        try:
            with self._http.stream(
                "GET", "/sync/download",
                params={"batch_no": batch_no, "filename": filename, "port": port},
            ) as response:
                # 仅错误路径读入 body（错误页小，开销可忽略），否则 _check
                # 访问 response.text 会抛 ResponseNotRead；成功路径保持
                # 真流式写盘，不把大文件整个读进内存。
                if response.status_code >= 400:
                    response.read()
                self._check(response)
                with dest.open("wb") as out:
                    for chunk in response.iter_bytes():
                        out.write(chunk)
            return dest
        except httpx.HTTPError as exc:
            raise ServerError(f"无法从服务器下载文件: {exc}") from exc
