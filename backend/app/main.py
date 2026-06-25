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
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.api import batches, devices, export, files, mappings, query, system, tasks, upload
from app.config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.LOG_LEVEL)
log = logging.getLogger("aln")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    watcher_task = None
    if settings.WATCH_ENABLED:
        from app.watch.watcher import watch_uploads

        watcher_task = asyncio.create_task(watch_uploads())
    yield
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


app.add_middleware(GZipMiddleware, minimum_size=1024)

# 静态文件目录：优先使用项目根目录 frontend/dist；打包后回退到可执行文件目录
ROOT_CANDIDATES = [
    Path(__file__).resolve().parent.parent.parent,
    Path(sys.executable).parent,
    Path(getattr(sys, '_MEIPASS', Path.cwd())),
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
app.include_router(mappings.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(devices.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(export.router, prefix="/api")

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
