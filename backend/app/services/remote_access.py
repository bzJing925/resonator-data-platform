"""Route file operations for server replicas without copying raw data locally.

The existing endpoints and plotting UI are reused. File-node IDs belong to the
server; device IDs in file listings are translated into local replica IDs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import SessionLocal
from app.models import Batch, Device, ReplicaBatch, SyncIdentity
from app.services.batch_scope import get_active_batch
from app.services.server_client import load_config


def route(path: str, method: str, params: dict, body: dict, db) -> tuple | None:
    batch = None
    device = None
    source_operation = re.fullmatch(
        r"/api/sync/v3/sources/([^/]+)/([^/]+)(/restore|/deletion-impact)?", path
    )
    if source_operation and method in {"DELETE", "POST", "GET"}:
        batch = db.scalar(select(Batch).where(
            Batch.origin_id == source_operation[1],
            Batch.source_batch_id == source_operation[2],
        ))
        replica = db.get(ReplicaBatch, batch.id) if batch else None
        if replica:
            return path, {}, None, replica.server_id
        return None
    task = re.fullmatch(r"/api/tasks/(-\d+)(/.*)?", path)
    if task:
        upstream = db.get(SyncIdentity, "upstream")
        return (f"/api/tasks/{-int(task[1])}{task[2] or ''}", {}, None,
                upstream.value if upstream else None)

    match = re.fullmatch(r"/api/devices/(\d+)/(sparam|bodeq|sparam-sparse|download-s1p)", path)
    if match:
        device = db.get(Device, int(match[1]))
        batch = device.batch if device else None
    elif path.startswith("/api/files"):
        name = params.get("batch_no") or body.get("batch_no")
        batch = db.scalar(select(Batch).where(Batch.batch_no == name)) if name else None
        raw_nodes = body.get("node_ids")
        nodes = (raw_nodes if isinstance(raw_nodes, list) else []) + [
            body.get("node_id", 0), body.get("target_folder_id", 0)]
        if batch is None and any(isinstance(n, int) and n < 0 for n in nodes):
            replica = db.scalar(select(ReplicaBatch).limit(1))
            batch = db.get(Batch, replica.batch_id) if replica else None
    elif path.startswith("/api/batches/"):
        tail = path[len("/api/batches/"):]
        if method != "GET" or tail.endswith("/download-zip"):
            name = tail.rsplit("/", 1)[0] if "/" in tail else tail
            batch = db.scalar(select(Batch).where(Batch.batch_no == name))
    if batch is None:
        return None
    if batch.deleted_at is not None:
        raise HTTPException(410, "批次已在回收站")
    get_active_batch(db, batch_id=batch.id)
    replica = db.get(ReplicaBatch, batch.id)
    if replica is None:
        return None
    if path.startswith("/api/batches/") and method == "DELETE":
        raise HTTPException(409, {"code": "EXPLICIT_REMOTE_OPERATION_REQUIRED",
                                 "message": "副本只能本地隐藏；远端删除须UUID/v3独立操作"})
    # Users can explicitly import local raw files; local reading remains available.
    if device and device.s_param_path:
        local = get_settings().files_dir / batch.batch_no / device.s_param_path
        gz = local.with_suffix(local.suffix + ".gz")
        if not Path(device.s_param_path).is_absolute() and (local.is_file() or gz.is_file()):
            return None
    ids = replica.device_ids or {}
    if device:
        remote_id = next((rid for rid, lid in ids.items() if lid == device.id), None)
        if remote_id is None:
            return None
        path = f"/api/devices/{remote_id}/{match[2]}"
    elif path.startswith("/api/batches/"):
        suffix = path[len("/api/batches/") + len(batch.batch_no):]
        path = "/api/batches/" + quote(batch.batch_no, safe="") + suffix
    return path, ids, device.id if device else None, replica.server_id


def _translate(value, ids: dict, path: str):
    """把服务器响应里的远程 ID 翻译回本地命名空间。

    - device_id：远程→本地映射；未映射（同步窗口期内服务器新增的器件）置 None，
      绝不原样透传——本地正数 ID 空间属于其他器件，透传会静默显示错误曲线。
    - 文件树节点 id/parent_id：取负，与本地节点区分。
    - task_id：取负（字符串数字先 int()，ReprocessResponse 的 task_id 是 str），
      使 /api/tasks/-N 代理路由生效；stream_url 同步改写。
    """
    if isinstance(value, list):
        return [_translate(v, ids, path) for v in value]
    if isinstance(value, dict):
        translated = {}
        for k, v in value.items():
            if k == "device_id" and isinstance(v, int):
                translated[k] = ids.get(str(v))
            else:
                translated[k] = _translate(v, ids, path)
        if path.startswith("/api/files/tree"):
            for key in ("id", "parent_id"):
                if isinstance(translated.get(key), int):
                    translated[key] = -abs(translated[key])
        if path.startswith("/api/tasks/"):
            if isinstance(translated.get("id"), int):
                translated["id"] = -abs(translated["id"])
            if isinstance(translated.get("retry_of_task_id"), int):
                translated["retry_of_task_id"] = -abs(translated["retry_of_task_id"])
        task_id = translated.get("task_id")
        if isinstance(task_id, int) or (isinstance(task_id, str) and task_id.isdigit()):
            translated["task_id"] = -abs(int(task_id))
        stream_url = translated.get("stream_url")
        if isinstance(stream_url, str):
            translated["stream_url"] = re.sub(r"/api/tasks/(\d+)", r"/api/tasks/-\1", stream_url)
        return translated
    return value


def resolve_route(path, method, params, body):
    with SessionLocal() as db:
        return route(path, method, params, body, db)


async def proxy_request(request: Request, call_next):
    settings = get_settings()
    if not settings.is_desktop or settings.ALN_SERVER_MODE:
        return await call_next(request)
    path = request.url.path
    if not path.startswith((
        "/api/files", "/api/devices/", "/api/batches/", "/api/tasks/-", "/api/sync/v3/sources/"
    )):
        return await call_next(request)
    raw_body = await request.body()
    try:
        body = json.loads(raw_body) if raw_body else {}
    except ValueError:
        return await call_next(request)
    params = dict(request.query_params)
    try:
        target = await run_in_threadpool(resolve_route, path, request.method, params, body)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if target is None:
        return await call_next(request)
    config = load_config()
    if config is None:
        return JSONResponse({"detail": "该批次在服务器上，请配置内网服务器地址"}, status_code=503)
    remote_path, ids, local_device_id, expected_server = target
    if path.startswith("/api/files"):
        raw_ids = body.get("node_ids")
        if raw_ids is not None and (
            not isinstance(raw_ids, list) or not all(isinstance(n, int) for n in raw_ids)
        ):
            return JSONResponse({"detail": "node_ids 必须是整数数组"}, status_code=400)
        for key in ("parent_id", "node_id", "target_folder_id"):
            if params.get(key):
                try:
                    param_id = int(params[key])
                except ValueError:
                    return JSONResponse({"detail": f"{key} 必须是整数"}, status_code=400)
                if param_id < 0:
                    params[key] = str(-param_id)
            body_id = body.get(key)
            if body_id is not None:
                if not isinstance(body_id, int):
                    return JSONResponse({"detail": f"{key} 必须是整数"}, status_code=400)
                if body_id < 0:
                    body[key] = -body_id
        if isinstance(raw_ids, list):
            body["node_ids"] = [abs(n) for n in raw_ids]
        raw_body = json.dumps(body).encode() if body else b""
    client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(300, connect=5))
    try:
        if request.method not in {"GET", "HEAD"} and expected_server:
            check = await client.get(config.server_url + "/api/health")
            if check.headers.get("X-ALN-Server-ID") != expected_server:
                await client.aclose()
                return JSONResponse({"detail": "服务器身份不匹配，操作未发送"}, status_code=409)
        req = client.build_request(request.method, config.server_url + remote_path,
                                   params=params, content=raw_body,
                                   headers={"content-type": "application/json"})
        response = await client.send(req, stream=True)
        if expected_server and response.headers.get("X-ALN-Server-ID") != expected_server:
            await response.aclose()
            await client.aclose()
            return JSONResponse(
                {"detail": "服务器身份不匹配，请检查同步配置，未显示其他服务器数据"},
                status_code=409,
            )
        if "application/json" in response.headers.get("content-type", ""):
            raw = await response.aread()
            await response.aclose()
            await client.aclose()
            data = _translate(json.loads(raw), ids, path)
            if local_device_id and isinstance(data, dict) and "device_id" in data:
                data["device_id"] = local_device_id
            return JSONResponse(data, status_code=response.status_code)
        async def cleanup():
            await response.aclose()
            await client.aclose()
        headers = {k: v for k, v in response.headers.items()
                   if k.lower() in {"content-type", "content-disposition"}}
        return StreamingResponse(response.aiter_bytes(), status_code=response.status_code,
                                 headers=headers, background=BackgroundTask(cleanup))
    except (httpx.HTTPError, ValueError):
        await client.aclose()
        return JSONResponse({"detail": "服务器不可达，请连接办公内网后重试；本地指标仍可使用"},
                            status_code=503)
