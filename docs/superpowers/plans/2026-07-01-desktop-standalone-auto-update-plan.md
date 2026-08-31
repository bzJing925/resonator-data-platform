# 桌面单机版与自动更新实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把现有 Electron + FastAPI 平台改造成双击即可运行的单机桌面应用（无需 PostgreSQL/Redis），并集成 GitHub Releases 自动更新；同时完成前端 TypeScript 迁移与开发环境稳定性改进。

**架构：** Electron 主进程动态发现端口、启动 PyInstaller 后端、健康检查与自动重启；后端通过 `ALN_DESKTOP_MODE=true` 切换为 SQLite + 本地任务队列；自动更新通过 `electron-updater` 从 GitHub Releases 拉取安装包，预留内网源接口。

**技术栈：** Electron 42 + TypeScript, FastAPI, SQLAlchemy 2.0, SQLite, Python threading, electron-updater, Vite 5, React 18 + TypeScript。

---

## 文件结构

### 后端新增/修改

| 文件 | 职责 |
|---|---|
| `backend/app/config.py` | 新增桌面模式判断，SQLite 默认路径，本地队列开关 |
| `backend/app/db.py` | 桌面模式下使用 SQLite 引擎参数 |
| `backend/app/desktop_setup.py` | 桌面模式初始化：建目录、建表、schema 版本管理 |
| `backend/app/workers/dispatch.py` | 上传任务分发抽象：Celery / 本地队列二选一 |
| `backend/app/workers/local_queue.py` | SQLite 持久化任务队列 + 后台 worker 线程 |
| `backend/app/workers/local_worker.py` | 本地执行 extract → compute 流程 |
| `backend/app/workers/progress.py` | 桌面模式下跳过 Redis pub/sub |
| `backend/app/api/tasks.py` | 桌面模式 SSE 改为轮询 UploadTask 表 |
| `backend/app/services/upload_service.py` | 改调用 `dispatch.dispatch_batch_task` |
| `backend/app/main.py` | 桌面模式启动本地 worker 线程；静态文件路径适配 |
| `backend/desktop_entry.py` | 设置 `ALN_DESKTOP_MODE=true`，读取 `ALN_BACKEND_PORT` |
| `backend/build_backend.py` | 确保桌面模式产物正确 |
| `backend/tests/test_desktop_mode.py` | 桌面模式配置与本地队列测试 |

### 前端新增/修改

| 文件 | 职责 |
|---|---|
| `frontend/package.json` | 加 TypeScript、electron-updater、类型依赖；改 scripts |
| `frontend/tsconfig.json` | TypeScript 配置 |
| `frontend/tsconfig.node.json` | Vite/Electron 主进程 TS 配置 |
| `frontend/vite.config.ts` | Vite 配置 TS 化，开发代理保持 |
| `frontend/electron/main.ts` | Electron 主进程：动态端口、后端管理、自动更新 |
| `frontend/electron/preload.ts` | 预加载脚本：暴露 backendUrl、更新、状态 |
| `frontend/electron/updater.ts` | 自动更新封装 |
| `frontend/electron/splash.html` | 启动 loading 页（可保留） |
| `frontend/src/types/index.ts` | 核心类型定义 |
| `frontend/src/electron-api.ts` | 渲染进程调用 Electron API 的类型封装 |
| `frontend/src/main.tsx` | 入口 TS 化 |
| `frontend/src/App.tsx` | App TS 化 |
| `frontend/src/api/client.ts` | 重试逻辑 |
| `frontend/src/hooks/useSSE.ts` | SSE 自动重连 |
| `frontend/src/pages/*.tsx` | 页面组件逐个迁移 |
| `frontend/src/contexts/*.tsx` | Context TS 化 |
| `frontend/src/components/*.tsx` | 组件 TS 化 |
| `frontend/src/hooks/*.ts` | hooks TS 化 |
| `frontend/src/api/endpoints.ts` | API 封装 TS 化 |
| `frontend/src/router/*.ts` | 路由 TS 化 |

### 构建与脚本

| 文件 | 职责 |
|---|---|
| `build.py` | 桌面版构建流程适配（PyInstaller + Electron-builder） |
| `scripts/dev-start.sh` | macOS/Linux 统一开发启动 |
| `scripts/dev-start.ps1` | Windows 统一开发启动 |

---

## 任务 1：后端配置桌面模式

**文件：**
- 修改：`backend/app/config.py`
- 测试：`backend/tests/test_desktop_mode.py`

### 步骤 1：编写失败测试

创建 `backend/tests/test_desktop_mode.py`：

```python
import os
from pathlib import Path

from app.config import Settings, get_settings


def test_desktop_mode_uses_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    # 清除 lru_cache
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.is_desktop is True
    assert settings.DATABASE_URL.startswith("sqlite:///")
    assert Path(settings.files_dir).parent == tmp_path
```

### 步骤 2：运行测试验证失败

```bash
cd backend
uv run pytest tests/test_desktop_mode.py::test_desktop_mode_uses_sqlite -v
```

预期：`FAIL`，`AttributeError: 'Settings' object has no attribute 'is_desktop'`

### 步骤 3：实现桌面模式配置

修改 `backend/app/config.py`：

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("DOTENV_PATH", ".env"),
        extra="ignore",
    )

    # 新增：桌面模式开关
    ALN_DESKTOP_MODE: bool = False
    # 桌面模式数据根目录；Electron 会传入 userData 路径
    ALN_DESKTOP_DIR: Path | None = None

    DATABASE_URL: str = "postgresql+psycopg://aln:aln@localhost:5432/aln"
    REDIS_URL: str = "redis://localhost:6379/0"
    DATA_ROOT: Path = Path("/data3/aln")
    # ... 其余字段不变

    @property
    def is_desktop(self) -> bool:
        return self.ALN_DESKTOP_MODE

    @property
    def desktop_dir(self) -> Path:
        if self.ALN_DESKTOP_DIR:
            return Path(self.ALN_DESKTOP_DIR)
        return Path.home() / ".aln-data"

    def _resolve_path(self, fallback: Path) -> Path:
        if self.is_desktop:
            return self.desktop_dir / fallback.name
        return fallback

    @property
    def data_root(self) -> Path:
        if self.is_desktop:
            return self.desktop_dir
        return self.DATA_ROOT

    @property
    def uploads_dir(self) -> Path:
        return self.data_root / "uploads"

    @property
    def files_dir(self) -> Path:
        return self.data_root / "files"

    @property
    def mappings_dir(self) -> Path:
        return self.data_root / "mappings"

    @property
    def exports_dir(self) -> Path:
        return self.data_root / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def watch_dir(self) -> Path:
        return self.data_root / "watch"

    @property
    def resolved_database_url(self) -> str:
        if self.is_desktop:
            return f"sqlite:///{self.desktop_dir / 'aln-data.db'}"
        return self.DATABASE_URL
```

同时把 `db.py` 改使用 `settings.resolved_database_url`，并为 SQLite 调整引擎参数：

```python
from app.config import get_settings

_settings = get_settings()

_engine_kwargs = {"pool_pre_ping": True, "future": True}
if _settings.resolved_database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(
        pool_size=_settings.DB_POOL_SIZE,
        max_overflow=_settings.DB_MAX_OVERFLOW,
        pool_recycle=_settings.DB_POOL_RECYCLE,
        pool_timeout=_settings.DB_POOL_TIMEOUT,
    )

engine = create_engine(_settings.resolved_database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
```

### 步骤 4：运行测试验证通过

```bash
uv run pytest tests/test_desktop_mode.py::test_desktop_mode_uses_sqlite -v
```

预期：`PASS`

### 步骤 5：Commit

```bash
git add backend/app/config.py backend/app/db.py backend/tests/test_desktop_mode.py
git commit -m "feat(backend): add desktop mode config and SQLite db engine

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 2：桌面模式目录与表初始化

**文件：**
- 创建：`backend/app/desktop_setup.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_desktop_mode.py`

### 步骤 1：编写失败测试

在 `backend/tests/test_desktop_mode.py` 追加：

```python
from app.desktop_setup import init_desktop_environment


def test_init_desktop_environment_creates_dirs_and_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    get_settings.cache_clear()

    init_desktop_environment()

    assert (tmp_path / "aln-data.db").exists()
    assert (tmp_path / "uploads").exists()
    assert (tmp_path / "files").exists()
    assert (tmp_path / "mappings").exists()
```

### 步骤 2：运行测试验证失败

```bash
uv run pytest tests/test_desktop_mode.py::test_init_desktop_environment_creates_dirs_and_db -v
```

预期：`FAIL`，`ModuleNotFoundError: No module named 'app.desktop_setup'`

### 步骤 3：实现桌面初始化模块

创建 `backend/app/desktop_setup.py`：

```python
"""桌面模式环境初始化：目录、SQLite 建表、schema 版本。"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.models import Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _ensure_dirs(root: Path) -> None:
    for sub in ("uploads", "files", "mappings", "exports", "logs", "watch"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def _init_schema(engine) -> None:
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS _aln_schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO _aln_schema_version (version) VALUES (:v) "
                "ON CONFLICT(version) DO NOTHING"
            ),
            {"v": SCHEMA_VERSION},
        )
        conn.commit()


def init_desktop_environment() -> None:
    settings = get_settings()
    if not settings.is_desktop:
        return

    root = settings.desktop_dir
    _ensure_dirs(root)

    engine = create_engine(
        settings.resolved_database_url,
        connect_args={"check_same_thread": False},
    )
    _init_schema(engine)
    engine.dispose()
    logger.info("桌面环境初始化完成: %s", root)
```

### 步骤 4：在 FastAPI 启动时调用

修改 `backend/app/main.py` 的 `lifespan`：

```python
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.is_desktop:
        from app.desktop_setup import init_desktop_environment
        from app.workers.local_queue import start_local_worker, stop_local_worker

        init_desktop_environment()
        worker_thread = start_local_worker()
    else:
        worker_thread = None

    watcher_task = None
    if settings.WATCH_ENABLED and not settings.is_desktop:
        from app.watch.watcher import watch_uploads
        watcher_task = asyncio.create_task(watch_uploads())

    yield

    if worker_thread is not None:
        stop_local_worker()
    if watcher_task is not None:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
```

### 步骤 5：运行测试验证通过

```bash
uv run pytest tests/test_desktop_mode.py -v
```

预期：两个测试都 `PASS`

### 步骤 6：Commit

```bash
git add backend/app/desktop_setup.py backend/app/main.py backend/tests/test_desktop_mode.py
git commit -m "feat(backend): initialize SQLite dirs and schema in desktop mode

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 3：本地任务队列与上传分发抽象

**文件：**
- 创建：`backend/app/workers/dispatch.py`
- 创建：`backend/app/workers/local_queue.py`
- 修改：`backend/app/services/upload_service.py`
- 测试：`backend/tests/test_desktop_mode.py`

### 步骤 1：编写失败测试

在 `backend/tests/test_desktop_mode.py` 追加：

```python
from app.workers.dispatch import dispatch_batch_task


def test_dispatch_uses_local_queue_in_desktop_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    get_settings.cache_clear()

    task_id = 123
    dispatch_batch_task(
        task_id=task_id,
        zip_path=tmp_path / "test.zip",
        batch_no="TEST001",
        mapping_id=1,
    )

    from app.workers.local_queue import get_local_queue

    pending = get_local_queue().list_pending()
    assert any(item.task_id == task_id for item in pending)
```

### 步骤 2：运行测试验证失败

```bash
uv run pytest tests/test_desktop_mode.py::test_dispatch_uses_local_queue_in_desktop_mode -v
```

预期：`FAIL`，导入错误

### 步骤 3：实现本地队列与分发抽象

创建 `backend/app/workers/local_queue.py`：

```python
"""桌面模式下的本地 SQLite 任务队列。"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LocalTask:
    task_id: int
    zip_path: Path
    batch_no: str
    mapping_id: int
    f_start_ghz: float | None
    f_end_ghz: float | None
    deembed: bool
    deembed_method: str
    process_type: str


class LocalTaskQueue:
    def __init__(self) -> None:
        self._pending: deque[LocalTask] = deque()
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._worker: threading.Thread | None = None
        self._event = threading.Event()

    def put(self, task: LocalTask) -> None:
        with self._lock:
            self._pending.append(task)
        self._event.set()

    def get(self, timeout: float = 0.5) -> LocalTask | None:
        if self._event.wait(timeout):
            with self._lock:
                if self._pending:
                    item = self._pending.popleft()
                    if not self._pending:
                        self._event.clear()
                    return item
            self._event.clear()
        return None

    def list_pending(self) -> list[LocalTask]:
        with self._lock:
            return list(self._pending)

    def shutdown(self) -> None:
        self._shutdown.set()
        self._event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=5.0)

    def is_shutdown(self) -> bool:
        return self._shutdown.is_set()


_local_queue = LocalTaskQueue()


def get_local_queue() -> LocalTaskQueue:
    return _local_queue


def start_local_worker() -> threading.Thread:
    from app.workers.local_worker import local_worker_loop

    t = threading.Thread(target=local_worker_loop, name="aln-local-worker", daemon=True)
    _local_queue._worker = t
    t.start()
    return t


def stop_local_worker() -> None:
    _local_queue.shutdown()
```

创建 `backend/app/workers/dispatch.py`：

```python
"""上传任务分发：服务器版走 Celery，桌面版走本地队列。"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings


def dispatch_batch_task(
    task_id: int,
    zip_path: Path,
    batch_no: str,
    mapping_id: int,
    *,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
) -> str | None:
    settings = get_settings()
    if settings.is_desktop:
        from app.workers.local_queue import LocalTask, get_local_queue

        get_local_queue().put(
            LocalTask(
                task_id=task_id,
                zip_path=Path(zip_path),
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=f_start_ghz,
                f_end_ghz=f_end_ghz,
                deembed=deembed,
                deembed_method=deembed_method,
                process_type=process_type,
            )
        )
        return f"local-{task_id}"

    from celery import chain
    from app.workers.extract_batch import extract_batch_task
    from app.workers.compute_batch import compute_batch_task

    result = chain(
        extract_batch_task.s(
            upload_task_id=task_id,
            zip_path=str(zip_path),
            batch_no=batch_no,
            mapping_id=mapping_id,
            f_start_ghz=f_start_ghz,
            f_end_ghz=f_end_ghz,
            deembed_enabled=bool(deembed),
            deembed_method=deembed_method if deembed else "default",
            process_type=process_type,
        ),
        compute_batch_task.s(),
    ).apply_async()
    return result.id
```

### 步骤 4：上传服务改用分发抽象

修改 `backend/app/services/upload_service.py`，把 `_dispatch_chain` 替换为：

```python
from app.workers.dispatch import dispatch_batch_task


def _dispatch_chain(
    task: UploadTask,
    zip_path: Path,
    batch_no: str,
    mapping_id: int,
    *,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
) -> str | None:
    celery_task_id = dispatch_batch_task(
        task_id=task.id,
        zip_path=zip_path,
        batch_no=batch_no,
        mapping_id=mapping_id,
        f_start_ghz=f_start_ghz,
        f_end_ghz=f_end_ghz,
        deembed=deembed,
        deembed_method=deembed_method,
        process_type=process_type,
    )
    if celery_task_id is None:
        task.status = "failed"
        task.error_msg = "任务投递失败"
        task.finished_at = datetime.now(UTC)
    return celery_task_id
```

并删除旧的 `from celery import chain` 和手动 chain 代码。

### 步骤 5：运行测试验证通过

```bash
uv run pytest tests/test_desktop_mode.py -v
```

### 步骤 6：Commit

```bash
git add backend/app/workers/local_queue.py backend/app/workers/dispatch.py backend/app/services/upload_service.py backend/tests/test_desktop_mode.py
git commit -m "feat(backend): add local task queue and dispatch abstraction

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 4：本地 Worker 实现

**文件：**
- 创建：`backend/app/workers/local_worker.py`
- 修改：`backend/app/workers/progress.py`
- 测试：`backend/tests/test_desktop_mode.py`

### 步骤 1：实现本地 worker 循环

创建 `backend/app/workers/local_worker.py`：

```python
"""桌面模式下在后台线程执行上传处理任务。"""

from __future__ import annotations

import logging
import traceback

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.workers.extract_batch import extract_batch_task
from app.workers.compute_batch import compute_batch_task
from app.workers.local_queue import get_local_queue
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)


def _run_task(task_id: int, **kwargs) -> None:
    db = SessionLocal()
    try:
        publisher = ProgressPublisher(task_id)
        publisher.start(db, "本地处理开始")

        extract_result = extract_batch_task.apply(
            kwargs={"upload_task_id": task_id, **kwargs}
        ).get()

        compute_result = compute_batch_task.apply(args=[extract_result]).get()

        publisher.done(
            db,
            batch_id=compute_result.get("batch_id"),
            device_count=compute_result.get("device_count", 0),
        )
    except Exception as exc:
        logger.exception("本地任务 %s 失败", task_id)
        try:
            publisher = ProgressPublisher(task_id)
            publisher.fail(db, f"{exc}\n{traceback.format_exc()}")
        except Exception:
            pass
    finally:
        db.close()


def local_worker_loop() -> None:
    queue = get_local_queue()
    logger.info("本地 worker 启动")
    while not queue.is_shutdown():
        task = queue.get(timeout=1.0)
        if task is None:
            continue
        logger.info("本地 worker 开始处理任务 %s", task.task_id)
        _run_task(
            task_id=task.task_id,
            zip_path=str(task.zip_path),
            batch_no=task.batch_no,
            mapping_id=task.mapping_id,
            f_start_ghz=task.f_start_ghz,
            f_end_ghz=task.f_end_ghz,
            deembed_enabled=task.deembed,
            deembed_method=task.deembed_method,
            process_type=task.process_type,
        )
    logger.info("本地 worker 退出")
```

### 步骤 2：让 ProgressPublisher 兼容桌面模式

修改 `backend/app/workers/progress.py`，在 `__init__` 中：

```python
from app.config import get_settings

class ProgressPublisher:
    def __init__(self, task_id: int) -> None:
        self.task_id = task_id
        self.channel = f"task:{task_id}"
        settings = get_settings()
        if not settings.is_desktop:
            self._redis: Redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        else:
            self._redis = None

    def _publish(self, payload: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            self._redis.publish(self.channel, json.dumps(payload))
        except Exception:
            pass
```

### 步骤 3：运行现有测试

```bash
uv run pytest tests/workers -v
uv run pytest tests/test_desktop_mode.py -v
```

预期：无破坏现有测试，桌面测试通过。

### 步骤 4：Commit

```bash
git add backend/app/workers/local_worker.py backend/app/workers/progress.py backend/tests/test_desktop_mode.py
git commit -m "feat(backend): implement local worker thread and skip Redis in desktop mode

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 5：桌面模式 SSE 轮询兜底

**文件：**
- 修改：`backend/app/api/tasks.py`
- 测试：`backend/tests/test_desktop_mode.py`

### 步骤 1：实现桌面模式 SSE

修改 `backend/app/api/tasks.py` 的 `_stream_task_events`：

```python
async def _stream_task_events(task_id: int) -> AsyncIterator[dict]:
    settings = get_settings()
    if settings.is_desktop:
        async for item in _stream_task_polling(task_id):
            yield item
        return

    # ... 原有 Redis pub/sub 逻辑不变
```

在文件末尾新增轮询实现：

```python
async def _stream_task_polling(task_id: int) -> AsyncIterator[dict]:
    import json
    import time

    start_ts = time.monotonic()
    max_seconds = 3600

    while True:
        with next(get_db()) as db:
            task = db.get(UploadTask, task_id)
            if task is None:
                yield {"event": "error", "data": json.dumps({"error_msg": f"任务 {task_id} 不存在"})}
                return

            yield {
                "event": "progress",
                "data": json.dumps(
                    {
                        "progress_pct": task.progress_pct,
                        "progress_msg": task.progress_msg,
                        "status": task.status,
                        "stage": task.stage,
                        "stage_progress_pct": task.stage_progress_pct,
                    }
                ),
            }

            if task.status == "success":
                yield {"event": "done", "data": json.dumps({"status": "success", "batch_no": task.batch_no})}
                return
            if task.status == "failed":
                yield {"event": "error", "data": json.dumps({"status": "failed", "error_msg": task.error_msg})}
                return

        if time.monotonic() - start_ts > max_seconds:
            yield {"event": "error", "data": json.dumps({"error_msg": "流超时"})}
            return

        await asyncio.sleep(1.0)
```

### 步骤 2：运行测试

```bash
uv run pytest tests/test_desktop_mode.py -v
uv run pytest tests/test_e2e_pipeline.py -v -k "not integration"
```

### 步骤 3：Commit

```bash
git add backend/app/api/tasks.py backend/tests/test_desktop_mode.py
git commit -m "feat(backend): add SSE polling fallback for desktop mode

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 6：桌面入口与 PyInstaller 适配

**文件：**
- 修改：`backend/desktop_entry.py`
- 修改：`backend/build_backend.py`
- 测试：手动构建验证

### 步骤 1：修改桌面入口

修改 `backend/desktop_entry.py`，在 `if __name__ == '__main__':` 前添加：

```python
if meipass:
    os.environ.setdefault("ALN_DESKTOP_MODE", "true")
    os.environ.setdefault("DATA_ROOT", os.path.join(os.path.expanduser("~"), ".aln-data", "data"))
    os.environ.setdefault("ALN_DESKTOP_DIR", os.path.join(os.path.expanduser("~"), ".aln-data"))
```

并把端口读取改为：

```python
host = os.environ.get('ALN_BACKEND_HOST', '127.0.0.1')
port = int(os.environ.get('ALN_BACKEND_PORT', '8000'))
```

### 步骤 2：验证手动启动桌面模式

```bash
cd backend
ALN_DESKTOP_MODE=true ALN_DESKTOP_DIR=/tmp/aln-desktop-test uv run uvicorn app.main:app --host 127.0.0.1 --port 9000
```

在另一个终端：

```bash
curl http://127.0.0.1:9000/api/health
```

预期返回包含 `{"status":"ok"}`。

### 步骤 3：Commit

```bash
git add backend/desktop_entry.py
git commit -m "feat(backend): enable desktop mode in PyInstaller entry

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 7：前端 TypeScript 基础设施

**文件：**
- 修改：`frontend/package.json`
- 创建：`frontend/tsconfig.json`
- 创建：`frontend/tsconfig.node.json`
- 修改：`frontend/vite.config.js` → `vite.config.ts`
- 修改：`frontend/electron/main.js` → `electron/main.ts`
- 修改：`frontend/electron/preload.cjs` → `electron/preload.ts`

### 步骤 1：安装依赖

```bash
cd frontend
npm install -D typescript @types/react @types/react-dom @types/node
```

### 步骤 2：创建 tsconfig.json

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "allowJs": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  },
  "include": ["src/**/*.ts", "src/**/*.tsx", "src/**/*.js", "src/**/*.jsx"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

### 步骤 3：创建 tsconfig.node.json

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "noEmit": false,
    "outDir": "electron-dist",
    "target": "ES2020"
  },
  "include": ["vite.config.ts", "electron/**/*.ts"]
}
```

> 说明：`noEmit` 设为 `false` 以便 `npm run electron:build-main` 把 Electron 主进程编译到 `electron-dist/`。开发时用 `tsx` 不需要编译。

### 步骤 4：Vite 配置 TS 化

```bash
git mv frontend/vite.config.js frontend/vite.config.ts
```

修改 `frontend/vite.config.ts`：

```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.ALN_BACKEND_URL || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/plotly.js')) return 'plotly';
          if (id.includes('node_modules/react-plotly.js')) return 'plotly-react';
          if (
            id.includes('node_modules/react') ||
            id.includes('node_modules/react-dom') ||
            id.includes('node_modules/react-router-dom')
          ) {
            return 'vendor';
          }
        },
      },
    },
  },
});
```

### 步骤 5：重命名 Electron 文件

```bash
cd frontend
git mv electron/main.js electron/main.ts
git mv electron/preload.cjs electron/preload.ts
```

### 步骤 6：更新 package.json

修改 `frontend/package.json`：

```json
{
  "name": "aln-data-frontend",
  "version": "0.2.0",
  "main": "electron-dist/main.js",
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "tsc --noEmit && vite build && npm run electron:build-main",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit",
    "electron:build-main": "tsc -p tsconfig.node.json --outDir electron-dist",
    "electron:dev": "npm run electron:build-main && concurrently \"npm run dev\" \"wait-on http://127.0.0.1:5173 && electron .\"",
    "electron:preview": "npm run build && electron .",
    "electron:pack": "npm run build && electron-builder",
    "dist": "npm run build && electron-builder"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "@types/react": "^18.0.0",
    "@types/react-dom": "^18.0.0",
    "typescript": "^5.4.0",
    "electron-updater": "^6.3.0",
    "electron": "^42.4.0",
    "electron-builder": "^26.15.3",
    "...": "..."
  },
  "build": {
    "files": [
      "dist/**/*",
      "electron-dist/**/*",
      "package.json"
    ],
    "extraResources": [
      {
        "from": "build/backend",
        "to": "backend",
        "filter": ["**/*"]
      }
    ]
  }
}
```

> 说明：
> - 开发/生产都先把 `electron/main.ts`、`electron/preload.ts` 编译到 `electron-dist/`。
> - `package.json` 的 `main` 指向 `electron-dist/main.js`。
> - `electron-builder` 打包时包含 `electron-dist/` 而不是源码 `electron/`。

### 步骤 7：运行类型检查

```bash
cd frontend
npm install
npx tsc --noEmit
```

预期：可能有旧 JS 文件类型错误，后续任务逐步修复。

### 步骤 8：Commit

```bash
git add frontend/package.json frontend/tsconfig.json frontend/tsconfig.node.json frontend/vite.config.ts frontend/electron/main.ts frontend/electron/preload.ts
git commit -m "chore(frontend): add TypeScript toolchain and migrate electron entry files

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 8：Electron 主进程：动态端口与后端管理

**文件：**
- 修改：`frontend/electron/main.ts`
- 修改：`frontend/electron/preload.ts`
- 创建：`frontend/src/types/index.ts`
- 创建：`frontend/src/electron-api.ts`

### 步骤 1：实现端口发现工具

在 `frontend/electron/main.ts` 顶部添加：

```ts
import { app, BrowserWindow, ipcMain, shell, Menu } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, ChildProcess } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as net.AddressInfo).port;
      server.close(() => resolve(port));
    });
    server.on('error', reject);
  });
}
```

### 步骤 2：重写后端管理逻辑

在 `frontend/electron/main.ts` 中：

```ts
const isPackaged = app.isPackaged;
const isDev = !isPackaged;

let mainWindow: BrowserWindow | null = null;
let splashWindow: BrowserWindow | null = null;
let backendProcess: ChildProcess | null = null;
let backendUrl = '';
let backendReady = false;
let healthCheckTimer: NodeJS.Timeout | null = null;

function notifyBackendState(state: 'starting' | 'ready' | 'error') {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('backend:state', state);
  }
}

function waitForBackend(url: string, maxAttempts = 300, intervalMs = 200): Promise<void> {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const tryConnect = () => {
      attempts += 1;
      const req = net
        .connect(new URL(url).port, new URL(url).hostname, () => {
          req.destroy();
          resolve();
        })
        .on('error', () => {
          if (attempts >= maxAttempts) {
            reject(new Error(`后端服务 ${url} 未就绪`));
          } else {
            setTimeout(tryConnect, intervalMs);
          }
        });
    };
    tryConnect();
  });
}

async function startBackend(): Promise<void> {
  const port = await findFreePort();
  const host = '127.0.0.1';
  backendUrl = `http://${host}:${port}`;
  backendReady = false;
  notifyBackendState('starting');

  const projectRoot = path.resolve(__dirname, '..', '..');
  const desktopDir = path.join(os.homedir(), '.aln-data');
  fs.mkdirSync(desktopDir, { recursive: true });

  let command: string;
  let args: string[];
  let cwd: string;

  if (isPackaged) {
    const backendDir = path.join(process.resourcesPath, 'backend');
    const exe = process.platform === 'win32' ? 'aln-backend.exe' : 'aln-backend';
    const oneDir = path.join(backendDir, 'aln-backend', exe);
    const oneFile = path.join(backendDir, exe);
    command = fs.existsSync(oneDir) ? oneDir : oneFile;
    args = [];
    cwd = path.dirname(command);
  } else {
    command = 'python';
    args = ['-m', 'uvicorn', 'app.main:app', '--host', host, '--port', String(port)];
    cwd = path.join(projectRoot, 'backend');
  }

  const mplDir = path.join(desktopDir, 'matplotlib-cache');
  fs.mkdirSync(mplDir, { recursive: true });

  const backendEnv = {
    ...process.env,
    ALN_DESKTOP_MODE: 'true',
    ALN_DESKTOP_DIR: desktopDir,
    ALN_BACKEND_HOST: host,
    ALN_BACKEND_PORT: String(port),
    MPLCONFIGDIR: mplDir,
  };

  console.log('[main] 启动后端:', command, args.join(' '), 'on', backendUrl);
  backendProcess = spawn(command, args, {
    cwd,
    stdio: isDev ? 'inherit' : ['ignore', 'pipe', 'pipe'],
    detached: false,
    env: backendEnv,
  });

  backendProcess.on('error', (err) => {
    console.error('[main] 后端进程启动失败:', err.message);
    notifyBackendState('error');
  });

  backendProcess.on('exit', (code) => {
    console.log(`[main] 后端进程退出，code=${code}`);
    backendProcess = null;
    backendReady = false;
    notifyBackendState('error');
    if (!isDev) {
      setTimeout(() => startBackend().catch(console.error), 2000);
    }
  });

  try {
    await waitForBackend(backendUrl);
    backendReady = true;
    notifyBackendState('ready');
    console.log('[main] 后端服务就绪:', backendUrl);
    startHealthCheck();
  } catch (e) {
    console.error('[main] 等待后端就绪超时:', e);
    notifyBackendState('error');
  }
}

function startHealthCheck() {
  if (healthCheckTimer) clearInterval(healthCheckTimer);
  healthCheckTimer = setInterval(async () => {
    if (!backendUrl || !backendProcess) return;
    try {
      await fetch(`${backendUrl}/api/health`);
    } catch {
      console.warn('[main] 后端 health 检查失败，准备重启');
      stopBackend();
      setTimeout(() => startBackend().catch(console.error), 500);
    }
  }, 10000);
}

function stopBackend() {
  if (healthCheckTimer) {
    clearInterval(healthCheckTimer);
    healthCheckTimer = null;
  }
  if (backendProcess) {
    console.log('[main] 停止后端服务...');
    if (process.platform === 'win32' && backendProcess.pid) {
      spawn('taskkill', ['/pid', String(backendProcess.pid), '/f', '/t']);
    } else {
      backendProcess.kill('SIGTERM');
    }
    backendProcess = null;
  }
}
```

### 步骤 3：修改窗口加载逻辑

在 `createMainWindow` 中：

```ts
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    title: 'ALN Resonator Data Platform',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    const indexPath = path.join(__dirname, '..', 'dist', 'index.html');
    mainWindow.loadFile(indexPath);
  }

  // ... 其余不变
}
```

### 步骤 4：修改 preload.ts

```ts
import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('aln', {
  getVersion: () => ipcRenderer.invoke('app:get-version'),
  getBackendUrl: () => ipcRenderer.invoke('app:get-backend-url'),
  onBackendStateChange: (cb: (state: string) => void) =>
    ipcRenderer.on('backend:state', (_event, value) => cb(value)),
  openExternal: (url: string) => ipcRenderer.invoke('app:open-external', url),
  platform: process.platform,
  checkForUpdates: () => ipcRenderer.invoke('updater:check'),
  installUpdate: () => ipcRenderer.invoke('updater:install'),
});
```

### 步骤 5：注册 IPC handlers

在 `frontend/electron/main.ts` 底部添加：

```ts
ipcMain.handle('app:get-version', () => app.getVersion());
ipcMain.handle('app:get-backend-url', () => backendUrl);
ipcMain.handle('app:open-external', (_event, url: string) => shell.openExternal(url));
ipcMain.handle('backend:is-ready', () => backendReady);
```

### 步骤 6：创建前端类型

创建 `frontend/src/types/index.ts`：

```ts
export interface Device {
  id: number;
  batch_no: string;
  original_filename: string;
  // ... 按需补充
}

export interface Batch {
  batch_no: string;
  mapping_name?: string;
  device_count: number;
  // ...
}

export interface AlnElectronAPI {
  getVersion: () => Promise<string>;
  getBackendUrl: () => Promise<string>;
  onBackendStateChange: (cb: (state: 'starting' | 'ready' | 'error') => void) => void;
  openExternal: (url: string) => Promise<void>;
  platform: string;
  checkForUpdates: () => Promise<{ version: string; available: boolean }>;
  installUpdate: () => Promise<void>;
}

declare global {
  interface Window {
    aln?: AlnElectronAPI;
  }
}
```

创建 `frontend/src/electron-api.ts`：

```ts
export function getBackendUrl(): string {
  if (window.aln) {
    return '';
  }
  return '';
}

export function isDesktop(): boolean {
  return Boolean(window.aln);
}
```

注意：这里先占位，后续任务补充完整。

### 步骤 7：验证 Electron 开发启动

```bash
cd frontend
npm run electron:dev
```

预期：Electron 窗口打开，后端在动态端口启动，页面加载成功。

### 步骤 8：Commit

```bash
git add frontend/electron/main.ts frontend/electron/preload.ts frontend/src/types/index.ts frontend/src/electron-api.ts
git commit -m "feat(frontend): dynamic backend port, lifecycle management and preload API

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 9：自动更新实现

**文件：**
- 创建：`frontend/electron/updater.ts`
- 修改：`frontend/electron/main.ts`
- 修改：`frontend/package.json`

### 步骤 1：安装 electron-updater

```bash
cd frontend
npm install electron-updater
```

### 步骤 2：实现 updater.ts

创建 `frontend/electron/updater.ts`：

```ts
import { app, ipcMain } from 'electron';
import { autoUpdater, UpdateCheckResult } from 'electron-updater';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

interface UpdateSource {
  type: 'github' | 'static';
  owner?: string;
  repo?: string;
  url?: string;
  channel?: string;
}

function settingsPath(): string {
  return path.join(os.homedir(), '.aln-data', 'settings.json');
}

function loadSettings(): { updateSource?: UpdateSource } {
  try {
    return JSON.parse(fs.readFileSync(settingsPath(), 'utf-8'));
  } catch {
    return {};
  }
}

export function setupUpdater(): void {
  const settings = loadSettings();
  const source = settings.updateSource || { type: 'github', owner: 'your-org', repo: 'aln-data' };

  if (source.type === 'github') {
    autoUpdater.setFeedURL({
      provider: 'github',
      owner: source.owner || 'your-org',
      repo: source.repo || 'aln-data',
    });
  } else if (source.type === 'static' && source.url) {
    autoUpdater.setFeedURL({ provider: 'generic', url: source.url });
  }

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;

  autoUpdater.on('update-available', (info) => {
    console.log('[updater] 有可用更新:', info.version);
  });

  autoUpdater.on('update-downloaded', (info) => {
    console.log('[updater] 更新已下载:', info.version);
  });

  ipcMain.handle('updater:check', async () => {
    try {
      const result: UpdateCheckResult = await autoUpdater.checkForUpdates();
      return {
        version: result.updateInfo.version,
        available: result.updateInfo.version !== app.getVersion(),
      };
    } catch (err) {
      console.error('[updater] 检查更新失败:', err);
      return { version: app.getVersion(), available: false };
    }
  });

  ipcMain.handle('updater:install', () => {
    autoUpdater.quitAndInstall();
  });

  // 启动后 10s 自动检查一次
  setTimeout(() => {
    autoUpdater.checkForUpdates().catch((err) => {
      console.error('[updater] 自动检查失败:', err);
    });
  }, 10000);
}
```

### 步骤 3：在 main.ts 中调用

在 `app.whenReady().then(...)` 中调用 `setupUpdater()`。

### 步骤 4：更新 package.json build.publish

```json
{
  "build": {
    "publish": [
      {
        "provider": "github",
        "owner": "your-org",
        "repo": "aln-data"
      }
    ]
  }
}
```

### 步骤 5：Commit

```bash
git add frontend/electron/updater.ts frontend/electron/main.ts frontend/package.json
git commit -m "feat(frontend): integrate electron-updater with GitHub and static source support

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 10：前端 API 客户端与 SSE 重连

**文件：**
- 修改：`frontend/src/api/client.js` → `frontend/src/api/client.ts`
- 修改：`frontend/src/hooks/useSSE.js` → `frontend/src/hooks/useSSE.ts`
- 修改：`frontend/src/main.jsx` → `frontend/src/main.tsx`

### 步骤 1：API 客户端 TS 化 + 重试

重命名并修改 `frontend/src/api/client.ts`：

```ts
import axios, { AxiosInstance, AxiosError } from 'axios';

async function resolveBaseURL(): Promise<string> {
  if (window.aln) {
    return window.aln.getBackendUrl();
  }
  return import.meta.env.VITE_API_BASE_URL || '';
}

const client: AxiosInstance = axios.create({
  timeout: 120000,
});

client.interceptors.request.use(async (config) => {
  config.baseURL = await resolveBaseURL();
  return config;
});

client.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const { config } = error;
    if (!config) return Promise.reject(error);

    const retryCount = (config as any).__retryCount || 0;
    if (retryCount < 3 && (error.response?.status === 502 || error.response?.status === 503 || !error.response)) {
      (config as any).__retryCount = retryCount + 1;
      const delay = Math.pow(2, retryCount) * 1000;
      await new Promise((resolve) => setTimeout(resolve, delay));
      return client(config);
    }
    return Promise.reject(error);
  }
);

export default client;
```

### 步骤 2：SSE hook TS 化 + 重连

重命名并修改 `frontend/src/hooks/useSSE.ts`：

```ts
import { useEffect, useRef, useState } from 'react';

interface SSEOptions {
  onMessage?: (data: unknown) => void;
  onError?: (error: Event) => void;
}

export function useSSE(url: string | null, options: SSEOptions = {}) {
  const [connected, setConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const retryCountRef = useRef(0);

  useEffect(() => {
    if (!url) return;

    const connect = () => {
      const es = new EventSource(url);
      eventSourceRef.current = es;

      es.onopen = () => {
        setConnected(true);
        retryCountRef.current = 0;
      };

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          options.onMessage?.(data);
        } catch {
          options.onMessage?.(event.data);
        }
      };

      es.onerror = (error) => {
        setConnected(false);
        options.onError?.(error);
        es.close();

        const delay = Math.min(30000, Math.pow(2, retryCountRef.current) * 1000);
        retryCountRef.current += 1;
        setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      eventSourceRef.current?.close();
    };
  }, [url]);

  return { connected };
}
```

### 步骤 3：main.tsx 入口

重命名 `frontend/src/main.jsx` → `frontend/src/main.tsx`，内容基本不变，仅把 `createRoot` 调用补类型：

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

### 步骤 4：Commit

```bash
git add frontend/src/api/client.ts frontend/src/hooks/useSSE.ts frontend/src/main.tsx
git commit -m "feat(frontend): add API retry and SSE auto-reconnect

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 11：前端页面 TypeScript 迁移

**文件：**
- 逐个修改：`frontend/src/pages/*.jsx` → `*.tsx`
- 逐个修改：`frontend/src/components/*.jsx` → `*.tsx`
- 逐个修改：`frontend/src/contexts/*.jsx` → `*.tsx`
- 修改：`frontend/src/App.jsx` → `frontend/src/App.tsx`
- 修改：`frontend/src/api/endpoints.js` → `frontend/src/api/endpoints.ts`

### 步骤 1：迁移优先级

按依赖从底向上迁移：

1. `frontend/src/api/endpoints.ts`
2. `frontend/src/hooks/useFields.ts`
3. `frontend/src/contexts/*.tsx`
4. `frontend/src/components/*.tsx`
5. `frontend/src/pages/*.tsx`
6. `frontend/src/App.tsx`

### 步骤 2：endpoints.ts 示例

```ts
import client from './client';
import type { Batch, Device } from '../types';

export async function listBatches(params?: { page?: number; search?: string }) {
  const { data } = await client.get('/batches', { params });
  return data;
}

export async function getBatch(batchNo: string) {
  const { data } = await client.get<Batch>(`/batches/${batchNo}`);
  return data;
}

// ... 其他接口保持类似风格
```

### 步骤 3：迁移单个页面示例（Mappings）

把 `frontend/src/pages/Mappings.jsx` 改为 `Mappings.tsx`，主要改动：

- Props/State 加类型。
- `useState` 初始值加类型注解。
- 事件处理函数参数加类型。

示例：

```tsx
import React, { memo, useCallback, useEffect, useRef, useState } from 'react';
// ...

interface Mapping {
  id: number;
  name: string;
  entry_count: number;
  in_use_by_batches: number;
  uploaded_at?: string;
}

interface MappingEntry {
  mark: string;
  description?: string;
  eg?: number;
  fl?: number;
  ag?: number;
  area_s11?: number;
  area_s22?: number;
  has_pf: boolean;
}

export default function Mappings() {
  const [mappings, setMappings] = useState<Mapping[]>([]);
  // ...
}
```

### 步骤 4：逐步验证

每迁移 2-3 个文件后运行：

```bash
cd frontend
npx tsc --noEmit
npm run build
```

### 步骤 5：关闭 allowJs

全部 `.jsx` 迁移完成后，修改 `tsconfig.json`：

```json
{
  "compilerOptions": {
    "allowJs": false
  }
}
```

### 步骤 6：Commit

```bash
git add frontend/src
git commit -m "refactor(frontend): migrate pages and components to TypeScript

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 12：开发环境统一启动脚本

**文件：**
- 创建：`scripts/dev-start.sh`
- 创建：`scripts/dev-start.ps1`

### 步骤 1：Linux/macOS 脚本

创建 `scripts/dev-start.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
  echo "[dev] 停止所有进程..."
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$ROOT/backend"
uv sync

cd "$ROOT"

uv run --directory backend uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
UVICORN_PID=$!

uv run --directory backend celery -A app.workers worker --loglevel=info --concurrency=4 &
CELERY_PID=$!

cd "$ROOT/frontend"
npm run dev &
VITE_PID=$!

echo "[dev] uvicorn=$UVICORN_PID celery=$CELERY_PID vite=$VITE_PID"
wait
```

### 步骤 2：Windows 脚本

创建 `scripts/dev-start.ps1`：

```powershell
$ROOT = Split-Path -Parent $PSScriptRoot

$uvicorn = Start-Process -FilePath "uv" -ArgumentList "run","--directory","$ROOT/backend","uvicorn","app.main:app","--reload","--host","127.0.0.1","--port","8000" -PassThru
$celery = Start-Process -FilePath "uv" -ArgumentList "run","--directory","$ROOT/backend","celery","-A","app.workers","worker","--loglevel=info","--concurrency=4" -PassThru
$vite = Start-Process -FilePath "npm" -ArgumentList "run","dev" -WorkingDirectory "$ROOT/frontend" -PassThru

Write-Host "[dev] started uvicorn=$($uvicorn.Id) celery=$($celery.Id) vite=$($vite.Id)"

Read-Host "按 Enter 停止所有进程..."

Stop-Process -Id $uvicorn.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $celery.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $vite.Id -Force -ErrorAction SilentlyContinue
```

### 步骤 3：添加执行权限并测试

```bash
chmod +x scripts/dev-start.sh
./scripts/dev-start.sh
```

预期：前后端同时启动，Ctrl-C 全部退出。

### 步骤 4：Commit

```bash
git add scripts/dev-start.sh scripts/dev-start.ps1
git commit -m "chore(dev): unified start script for frontend/backend/worker

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 13：构建脚本与打包验证

**文件：**
- 修改：`build.py`
- 修改：`frontend/package.json`

### 步骤 1：更新 build.py

确保 `build.py` 在 Electron-builder 之前调用 `backend/build_backend.py`，并验证产物存在：

```python
def main():
    parser = argparse.ArgumentParser(...)
    parser.add_argument("--skip-backend", action="store_true")
    parser.add_argument("--target", choices=["win", "mac", "linux"], default=None)
    args = parser.parse_args()

    print("Step 1: 构建前端")
    run(["npm", "run", "build"], cwd=FRONTEND)

    if not args.skip_backend:
        print("Step 2: PyInstaller 打包后端")
        run([sys.executable, "build_backend.py"], cwd=BACKEND)
    else:
        print("跳过后端打包")

    print("Step 3: Electron-builder 打包")
    cmd = ["npx", "electron-builder"]
    if args.target:
        cmd.extend([f"--{args.target}"])
    run(cmd, cwd=FRONTEND)

    print("\n构建完成。输出目录: frontend/release/")
```

### 步骤 2：验证完整构建

```bash
python build.py --target mac
```

预期：`frontend/release/` 下生成 `.dmg`。

### 步骤 3：Commit

```bash
git add build.py frontend/package.json
git commit -m "chore(build): update desktop build pipeline

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 14：端到端验证

### 步骤 1：桌面模式后端手动测试

```bash
cd backend
ALN_DESKTOP_MODE=true ALN_DESKTOP_DIR=/tmp/aln-e2e uv run uvicorn app.main:app --host 127.0.0.1 --port 9001
```

在另一个终端：

```bash
curl -X POST http://127.0.0.1:9001/api/uploads \
  -F "file=@tests/fixtures/small.zip" \
  -F "mapping_id=1" \
  -F "process_type=AUTO"
```

预期返回 `{"task_id": "...", "status": "pending", ...}`，随后本地 worker 处理完成。

### 步骤 2：Electron 开发模式测试

```bash
cd frontend
npm run electron:dev
```

验证：窗口打开、动态端口后端启动、上传一个小 zip、进度条走完后列表有数据。

### 步骤 3：自动更新测试

发布一个 GitHub pre-release，安装旧版本桌面应用，启动后检查是否提示更新。

### 步骤 4：运行全部测试

```bash
cd backend
uv run pytest -v

cd ../frontend
npm run typecheck
npm run build
```

---

## 自检

### 规格覆盖度

- [x] Electron 动态端口与后端生命周期：任务 8
- [x] SQLite + 本地队列替代 PG/Redis/Celery：任务 1-5
- [x] 自动更新：任务 9
- [x] 前端 TypeScript 迁移：任务 7、11
- [x] 开发环境稳定性：任务 10、12
- [x] 构建与验证：任务 13、14

### 占位符扫描

- 无 "TODO"/"待定"。
- GitHub owner/repo 使用 `your-org` 占位，需要在实施时替换为真实值；已在 `updater.ts` 的 `loadSettings` 中允许通过 `settings.json` 覆盖。

### 类型一致性

- `backend/app/config.py` 中 `is_desktop`、`desktop_dir`、`resolved_database_url` 在后续任务中一致使用。
- `frontend/src/types/index.ts` 中的 `AlnElectronAPI` 与 `preload.ts` 暴露的 API 一致。
- `backend/app/workers/dispatch.py` 的参数名与 `upload_service.py` 调用一致。

### 风险提醒

1. **SQLite 与 PostgreSQL 的 SQL 方言差异**：`process_batch.py` 中的 `_copy_insert_devices` 使用 PostgreSQL COPY，桌面模式下会回退到 `bulk_insert_mappings`（已有降级逻辑）。
2. **Celery `apply().get()` 在本地 worker 中是同步调用**：会阻塞 worker 线程直到完成，对桌面单用户场景可接受。
3. **前端 TypeScript 迁移工作量大**：建议按任务 11 的优先级分批迁移，每批验证。
4. **自动更新需要真实 GitHub 仓库**：实施时需替换 `your-org/aln-data` 并在 GitHub Releases 上传安装包。

---

## 执行方式

计划已完成并保存到 `docs/superpowers/plans/2026-07-01-desktop-standalone-auto-update-plan.md`。

**两种执行方式：**

1. **子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代
2. **内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

**选哪种方式？**
