"""FastAPI 入口。

路由分模块挂载：
- /api/uploads        → api/upload.py
- /api/batches        → api/batches.py
- /api/mappings       → api/mappings.py
- /api/query/*        → api/query.py
- /api/devices/*      → api/devices.py
- /api/export/*       → api/export.py
- /api/tasks/*        → api/tasks.py
- /api/health, /api/stats → api/system.py
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    batches,
    comparison,
    desktop_sync,
    devices,
    export,
    export_jobs,
    files,
    mappings,
    process,
    query,
    replication,
    replication_v3,
    surrogate,
    sync,
    system,
    tasks,
    trash,
    upload,
    web_sync,
)
from app.config import get_settings
from app.services.artifact_readers import ArtifactReaderMiddleware
from app.services.remote_access import proxy_request

settings = get_settings()
logging.basicConfig(level=settings.LOG_LEVEL)
log = logging.getLogger("aln")

# 代理模型预热延迟：让开启动后的首批 UI 请求再开始加载 torch/检查点
_PREWARM_DELAY_S = 2.0


async def _prewarm_surrogate() -> None:
    """服务就绪后后台预热代理模型（torch/ckpt 加载约 30s，避免首次访问页面卡顿）。"""
    await asyncio.sleep(_PREWARM_DELAY_S)
    try:
        await asyncio.to_thread(surrogate.prewarm)
    except Exception:  # noqa: BLE001 — 预热失败不影响平台其余功能
        log.exception("代理模型预热失败")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    worker_thread = None
    if settings.is_desktop:
        from app.desktop_setup import init_desktop_environment
        from app.workers.local_queue import start_local_worker, stop_local_worker

        init_desktop_environment()
        if settings.ALN_SERVER_MODE:
            from app.db import SessionLocal
            from app.services.replica_service import identity
            with SessionLocal() as db:
                app.state.replication_server_id = identity(db)
        from app.workers.local_worker import recover_interrupted_tasks

        recover_interrupted_tasks()

    from app.db import SessionLocal, engine
    from app.services.replica_service import init_replication
    from app.services.source_identity import backfill_source_refs

    if not settings.is_desktop:
        init_replication(engine)
    with SessionLocal() as db:
        backfill_source_refs(db)
        db.commit()
    from app.services.export_job_service import recover_exports
    recover_exports(engine)
    if settings.is_desktop:
        worker_thread = start_local_worker()

    watcher_task = None
    if settings.WATCH_ENABLED and not settings.is_desktop:
        from app.watch.watcher import watch_uploads

        watcher_task = asyncio.create_task(watch_uploads())
    cleanup_task = None
    if settings.is_desktop:
        from app.services.artifact_lifecycle import purge_due

        async def maintain_artifacts():
            while True:
                try:
                    await asyncio.to_thread(purge_due, engine)
                except Exception:
                    log.exception("文件维护失败，将在下一周期重试")
                await asyncio.sleep(settings.CLEANUP_INTERVAL_SECONDS)

        cleanup_task = asyncio.create_task(maintain_artifacts())
    prewarm_task = asyncio.create_task(_prewarm_surrogate())
    yield
    if cleanup_task is not None:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
    prewarm_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await prewarm_task
    if worker_thread is not None:
        stop_local_worker()
    if watcher_task is not None:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="谐振器测试数据平台",
    description="多用户在线上传、入库、可视化分析谐振器测试数据",
    version="0.1.1",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录请求耗时与状态码；>500ms 标 warn。"""
    start = time.perf_counter()
    response = await call_next(request)
    server_id = getattr(app.state, "replication_server_id", None)
    if server_id:
        response.headers["X-ALN-Server-ID"] = server_id
    elapsed = (time.perf_counter() - start) * 1000
    level = logging.WARNING if elapsed > 500 else logging.INFO
    log.log(
        level,
        "%s %s → %d  %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response



app.middleware("http")(proxy_request)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(ArtifactReaderMiddleware)

# 静态文件目录：优先使用项目根目录 frontend/dist；打包后回退到可执行文件目录
ROOT_CANDIDATES = [
    Path(__file__).resolve().parent.parent.parent,
    Path(sys.executable).parent,
    Path(getattr(sys, "_MEIPASS", Path.cwd())),
]
STATIC_DIR = None
for root in ROOT_CANDIDATES:
    candidate = root / "frontend" / "dist"
    if candidate.exists() and (candidate / "index.html").exists():
        STATIC_DIR = candidate
        break

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]
if os.environ.get("ALN_DESKTOP") == "1":
    origins.append(f"http://127.0.0.1:{os.environ.get('ALN_BACKEND_PORT', '8000')}")
    # Electron 从 file:// 加载时 Origin 为 null，必须允许才能访问本地后端
    origins.append("null")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 路由
app.include_router(system.router, prefix="/api")
app.include_router(upload.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(batches.router, prefix="/api")
app.include_router(trash.router, prefix="/api")
app.include_router(mappings.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(comparison.router, prefix="/api")
app.include_router(devices.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(export_jobs.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(process.router, prefix="/api")
app.include_router(surrogate.router, prefix="/api")
app.include_router(web_sync.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(replication.router, prefix="/api")
app.include_router(replication_v3.router, prefix="/api")
app.include_router(desktop_sync.router, prefix="/api")

# 静态文件服务 + SPA fallback
if STATIC_DIR:
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # API 不应走到这里（前面已挂载）
        target = STATIC_DIR / full_path
        if target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(STATIC_DIR / "index.html")
