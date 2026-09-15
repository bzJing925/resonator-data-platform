"""本地无 snp 文件时的服务器代理降级（仅桌面模式生效）。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from starlette.background import BackgroundTask

from app.config import get_settings
from app.services import server_client
from app.services.server_client import ServerClient, ServerError


def _is_desktop() -> bool:
    return get_settings().is_desktop


def _natural_key(device) -> tuple[str, str, str]:
    batch_no = device.batch.batch_no if device.batch else ""
    return batch_no, device.original_filename or "", device.s_param_port or "S11"


def proxy_sparam(device, param: str) -> dict[str, Any] | None:
    """返回 None 表示不适用（非桌面/未配置）；结果打上 server-proxy 标记。"""
    if not _is_desktop():
        return None
    config = server_client.load_config()
    if config is None:
        return None
    batch_no, filename, port = _natural_key(device)
    try:
        with ServerClient(config) as client:
            payload = client.fetch_sparam(batch_no, filename, port, param)
    except ServerError as exc:
        raise HTTPException(
            status_code=503, detail=f"本地无文件且服务器不可用（需连接内网）: {exc}"
        ) from exc
    payload["source"] = "server-proxy"
    return payload


def proxy_download(device) -> tuple[Path, BackgroundTask] | None:
    """下载到临时文件并返回 (路径, 清理任务)；不适用返回 None。"""
    if not _is_desktop():
        return None
    config = server_client.load_config()
    if config is None:
        return None
    batch_no, filename, port = _natural_key(device)
    fd, tmp_name = tempfile.mkstemp(prefix="aln-proxy-", suffix=Path(filename).suffix)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with ServerClient(config) as client:
            client.download_s1p(batch_no, filename, port, tmp)
    except ServerError as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503, detail=f"本地无文件且服务器不可用（需连接内网）: {exc}"
        ) from exc
    return tmp, BackgroundTask(tmp.unlink, missing_ok=True)
