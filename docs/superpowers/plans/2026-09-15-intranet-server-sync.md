# 内网服务器同步 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 内网 Windows 服务器部署全栈平台作为唯一事实来源；桌面版经内网 HTTP 下行同步表格快照、上行回传原始 snp（服务器重新提参后删本地文件）、本地无文件时曲线查看透明代理到服务器。

**架构：** 服务器端新增 `/api/sync/*` 五个端点（快照导出/mapping 确保/推送入库/自然键曲线/自然键下载）；桌面端新增 `/api/desktop-sync/*` 本地端点（配置/拉取/推送/状态）+ 两个服务模块（HTTP 客户端、同步编排）；现有 `devices.py` 曲线端点加服务器代理降级。快照导入逻辑从 `web_sync_service` 抽出共用。规格见 `docs/superpowers/specs/2026-09-15-intranet-server-sync-design.md`。

**技术栈：** FastAPI + SQLAlchemy 2.0 + Alembic + Celery + httpx（新增依赖）+ pyarrow + React/TS。

**执行环境：** 在 `/Users/jingbozuo/Projects/aln-data-backend-api` worktree 执行（backend 为主体；frontend 改动同 worktree 随带，文件不相交）。后端命令均在 `backend/` 下用 `uv run`；测试用内存 SQLite，不依赖 PG/Redis。提交信息结尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

**已核实的关键事实（不要重复调查）：**
- `create_batch_and_dispatch(db, zip_path, batch_no, mapping_id, *, source, f_start_ghz, f_end_ghz, deembed, deembed_method, process_type, uploaded_by)` 位于 `backend/app/services/upload_service.py:96`，**批次名冲突时自动追加数字后缀**（`_generate_unique_batch_no`），push 端点必须先自行检查精确名称。
- 任务状态值：`pending / running / success / failed / cancelled`（`backend/app/workers/progress.py`）。
- `BatchDetail` 含 `device_count`（`backend/app/schemas/batch.py:64`），`GET /api/batches/{batch_no}` 可用于推送后校验。
- API 测试 fixture 在 `backend/tests/api/conftest.py`：`settings`（tmp DATA_ROOT）、`engine`（内存 SQLite + StaticPool）、`db`、`client`（TestClient + get_db 覆盖）。
- `deploy/docker-compose.yml` 各服务已有 `restart: unless-stopped`，无需改动。
- Device 自然键：`batch_id + original_filename + s_param_port`（有唯一约束，migration `43bc63841421`）。
- 桌面模式 `settings.is_desktop` 时 `data_root = ~/.aln-data`，`files_dir = data_root/files`。
- `gzip` 原始 snp 由 `PIPELINE_COMPRESS_RAW=True` 产生，落地为 `files/<batch_no>/*.s1p.gz`。

## 文件结构

| 文件 | 职责 | 新建/修改 |
| --- | --- | --- |
| `backend/app/models/batch.py` | 加 `synced_at` 列 | 修改 |
| `backend/alembic/versions/2026_09_15_120000_add_batch_synced_at.py` | 迁移 | 新建 |
| `backend/app/schemas/batch.py` | BatchListItem/BatchDetail 加 `synced_at`、`source` | 修改 |
| `backend/app/workers/celery_app.py` | `CELERY_TASK_ALWAYS_EAGER` 环境变量钩子 | 修改 |
| `backend/app/services/snapshot_service.py` | 快照导出 `build_snapshot` + 传输无关导入 `import_snapshot` | 新建 |
| `backend/app/services/web_sync_service.py` | 改为调用 `snapshot_service.import_snapshot`（行为不变） | 修改 |
| `backend/app/api/devices.py` | 抽出 `build_sparam_response`/`build_bodeq_response`/`resolve_device_sparam_path`；加服务器代理降级 | 修改 |
| `backend/app/api/sync.py` | 服务器角色端点 `/api/sync/*` | 新建 |
| `backend/app/services/server_client.py` | 桌面端配置存取 + httpx 客户端 | 新建 |
| `backend/app/services/desktop_sync_service.py` | 桌面端拉取/推送编排 + zip 打包 | 新建 |
| `backend/app/api/desktop_sync.py` | 桌面角色端点 `/api/desktop-sync/*` | 新建 |
| `backend/app/main.py` | 注册两个新 router | 修改 |
| `backend/app/services/upload_service.py` | `source` Literal 加 `"sync"` | 修改 |
| `backend/tests/...` | 各任务测试 | 新建 |
| `frontend/src/api/endpoints.ts` | desktop-sync API 封装 | 修改 |
| `frontend/src/types.ts` | Batch 加 `synced_at`/`source`，新增 ServerSyncStatus 类型 | 修改 |
| `frontend/src/components/ServerSyncPanel.tsx` | 同步面板组件 | 新建 |
| `frontend/src/pages/Batches.tsx` | 挂载面板 + BatchRow 同步标记 | 修改 |
| `docs/deployment-windows.md` | "作为内网服务器"章节 | 修改 |

---

### 任务 1：batches.synced_at 列

**文件：**
- 修改：`backend/app/models/batch.py`（在 `uploaded_by` 附近加列）
- 新建：`backend/alembic/versions/2026_09_15_120000_add_batch_synced_at.py`
- 修改：`backend/app/schemas/batch.py:38-47`（BatchListItem）与 `:60-73`（BatchDetail）
- 测试：`backend/tests/models/test_batch_synced_at.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""batches.synced_at：桌面端标记原始文件已回传服务器。"""

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Mapping


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_batch_synced_at_defaults_null_and_settable() -> None:
    db = _session()
    mapping = Mapping(name="M1", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no="B-1", mapping_id=mapping.id, file_path="f.zip")
    db.add(batch)
    db.commit()

    loaded = db.scalar(select(Batch).where(Batch.batch_no == "B-1"))
    assert loaded.synced_at is None

    now = datetime.now(UTC)
    loaded.synced_at = now
    db.commit()
    assert db.scalar(select(Batch).where(Batch.batch_no == "B-1")).synced_at is not None
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run pytest tests/models/test_batch_synced_at.py -v`
预期：FAIL，`AttributeError: ... 'Batch' object has no attribute 'synced_at'`（或 create_all 时 mapped attribute 缺失报错）

- [ ] **步骤 3：实现模型列 + schema 字段 + 迁移**

`backend/app/models/batch.py`，在 `task_id` 行之前加：

```python
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`backend/app/schemas/batch.py` 两个类各加一行（BatchListItem 加在 `uploaded_at` 后；BatchDetail 加在 `uploaded_by` 后）：

```python
    synced_at: datetime | None = None
```

新建 `backend/alembic/versions/2026_09_15_120000_add_batch_synced_at.py`：

```python
"""add batch synced_at

Revision ID: 2026_09_15_120000
Revises: 2026_07_20_120000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026_09_15_120000"
down_revision: str = "2026_07_20_120000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("batches", "synced_at")
```

同时给 BatchListItem/BatchDetail 加 `source`（前端区分快照批次用）——在 schemas 两个类中再加：

```python
    source: str = ""
```

- [ ] **步骤 4：运行测试验证通过 + 回归**

运行：`cd backend && uv run pytest tests/models/test_batch_synced_at.py tests/api/test_batch_mv.py -v`
预期：全部 PASS（`source`/`synced_at` 带默认值，不破坏既有构造）

- [ ] **步骤 5：Commit**

```bash
git add backend/app/models/batch.py backend/app/schemas/batch.py backend/alembic/versions/2026_09_15_120000_add_batch_synced_at.py backend/tests/models/test_batch_synced_at.py
git commit -m "feat(sync): batches 增加 synced_at/source 字段支撑服务器回传标记"
```

---

### 任务 2：Celery eager 钩子 + httpx 依赖

**文件：**
- 修改：`backend/app/workers/celery_app.py`
- 修改：`backend/pyproject.toml`（uv add 自动改）
- 测试：`backend/tests/workers/test_celery_eager_env.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""CELERY_TASK_ALWAYS_EAGER=1 时任务内联执行（供无 Redis 的端到端测试）。"""

import importlib
import os
import subprocess
import sys


def test_celery_eager_via_env() -> None:
    code = (
        "from app.workers.celery_app import celery_app; "
        "print(celery_app.conf.task_always_eager)"
    )
    env = {**os.environ, "CELERY_TASK_ALWAYS_EAGER": "1"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    )
    assert out.stdout.strip() == "True"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run pytest tests/workers/test_celery_eager_env.py -v`
预期：FAIL，输出 `False`

- [ ] **步骤 3：实现**

`backend/app/workers/celery_app.py` 顶部加 `import os`，`celery_app.conf.update(...)` 中加一行：

```python
    # CELERY_TASK_ALWAYS_EAGER=1 时任务在请求进程内联执行（端到端测试免 Redis/worker）
    task_always_eager=os.environ.get("CELERY_TASK_ALWAYS_EAGER") == "1",
```

然后加依赖：`cd backend && uv add httpx`

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run pytest tests/workers/test_celery_eager_env.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/workers/celery_app.py backend/pyproject.toml backend/uv.lock backend/tests/workers/test_celery_eager_env.py
git commit -m "feat(sync): CELERY_TASK_ALWAYS_EAGER 钩子 + httpx 依赖"
```

---

### 任务 3：snapshot_service（导入逻辑抽取 + 服务器侧导出）

**文件：**
- 新建：`backend/app/services/snapshot_service.py`
- 修改：`backend/app/services/web_sync_service.py`（import_web_snapshot 改为薄封装）
- 测试：`backend/tests/services/test_snapshot_service.py`

- [ ] **步骤 1：编写失败的测试（round-trip + 差集 + 空差集）**

```python
"""快照导出→导入 round-trip：空库导入后数据逐行一致；known 差集过滤。"""

import base64
import gzip

import pyarrow as pa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Device, Mapping, MappingEntry
from app.services import snapshot_service


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> None:
    mapping = Mapping(name="MAP-A", file_path="a.csv", entry_count=1)
    db.add(mapping)
    db.flush()
    db.add(MappingEntry(mapping_id=mapping.id, mark="A1", description="d",
                        eg=1.0, fl=0.5, ag=None, area_s11=700, area_s22=5500,
                        has_pf=True, raw_tokens=None))
    batch = Batch(batch_no="B-1", mapping_id=mapping.id, f_start_ghz=4.0,
                  f_end_ghz=5.0, process_type="S1P", file_path="f", source="http",
                  device_count=2, uploaded_by="tester")
    db.add(batch)
    db.flush()
    for i, port in enumerate(("S11", "S22")):
        db.add(Device(batch_id=batch.id, original_filename=f"d{i}.s1p", mark="A1",
                      s_param_port=port, pf="Y", fs_ghz=4.8, deembedded=False))
    db.commit()


def _decode_devices(payload_b64: str) -> list[dict]:
    if not payload_b64:
        return []
    raw = gzip.decompress(base64.b64decode(payload_b64))
    return pa.ipc.open_file(pa.py_buffer(raw)).read_all().to_pylist()


def test_snapshot_round_trip() -> None:
    src = _session()
    _seed(src)
    snap = snapshot_service.build_snapshot(src, known_batch_nos=set())
    assert [b["batch_no"] for b in snap["batches"]] == ["B-1"]
    assert snap["batches"][0]["uploaded_by"] == "tester"
    assert [m["name"] for m in snap["mappings"]] == ["MAP-A"]
    devices = _decode_devices(snap["devices_arrow_gz_b64"])
    assert len(devices) == 2

    dst = _session()
    stats = snapshot_service.import_snapshot(
        dst, snap["batches"], snap["mappings"], devices, source="server"
    )
    assert stats == {"mappings_added": 1, "batches_added": 1, "devices_added": 2}
    batch = dst.scalar(select(Batch).where(Batch.batch_no == "B-1"))
    assert batch.source == "server"
    assert batch.file_path == "server://B-1"
    assert dst.scalar(select(Mapping).where(Mapping.name == "MAP-A")).file_path == "server://MAP-A"
    # 幂等：重复导入不再新增
    again = snapshot_service.import_snapshot(
        dst, snap["batches"], snap["mappings"], devices, source="server"
    )
    assert again == {"mappings_added": 0, "batches_added": 0, "devices_added": 0}


def test_snapshot_known_batches_excluded() -> None:
    src = _session()
    _seed(src)
    snap = snapshot_service.build_snapshot(src, known_batch_nos={"B-1"})
    assert snap["batches"] == []
    assert snap["mappings"] == []
    assert snap["devices_arrow_gz_b64"] == ""
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run pytest tests/services/test_snapshot_service.py -v`
预期：FAIL，`ModuleNotFoundError: app.services.snapshot_service`

- [ ] **步骤 3：实现 snapshot_service + 重构 web_sync_service**

新建 `backend/app/services/snapshot_service.py`：

```python
"""批次快照的导出与导入（传输无关）。

服务器角色用 build_snapshot 导出差集快照；桌面/网页角色用
import_snapshot 增量导入。GitHub 网页版同步与内网服务器同步共用本模块。
"""

from __future__ import annotations

import base64
import gzip
import io
from collections import defaultdict
from collections.abc import Iterable

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, Device, Mapping, MappingEntry

_DEVICE_COLUMNS = {column.name for column in Device.__table__.columns}
_SHARED_DEVICE_COLUMNS = sorted(_DEVICE_COLUMNS - {"id", "batch_id", "s_param_path"})


def _nullable(value):
    # PyArrow 的 to_pylist 通常已转换为 None；这里也兼容 pandas/numpy 的 NaN。
    try:
        return None if value != value else value
    except Exception:  # noqa: BLE001
        return value


def _mapping_payload(mapping: Mapping) -> dict:
    return {
        "id": mapping.id,
        "name": mapping.name,
        "entries": [
            {
                "mark": entry.mark,
                "description": entry.description,
                "eg": entry.eg,
                "fl": entry.fl,
                "ag": entry.ag,
                "area_s11": entry.area_s11,
                "area_s22": entry.area_s22,
                "has_pf": entry.has_pf,
            }
            for entry in mapping.entries
        ],
    }


def build_snapshot(db: Session, known_batch_nos: set[str]) -> dict:
    """导出 known_batch_nos 之外的全部批次，返回可 JSON 序列化的 dict。"""
    batches = (
        db.scalars(select(Batch).where(Batch.batch_no.notin_(known_batch_nos or {"\0"}))).all()
        if known_batch_nos
        else db.scalars(select(Batch)).all()
    )
    mappings_payload: dict[int, dict] = {}
    batches_payload: list[dict] = []
    devices_payload: list[dict] = []
    for batch in batches:
        batches_payload.append(
            {
                "batch_no": batch.batch_no,
                "mapping_id": batch.mapping_id,
                "f_start_ghz": batch.f_start_ghz,
                "f_end_ghz": batch.f_end_ghz,
                "deembedded": batch.deembedded,
                "deembed_method": batch.deembed_method,
                "process_type": batch.process_type,
                "source": batch.source,
                "device_count": batch.device_count,
                "uploaded_by": batch.uploaded_by,
            }
        )
        if batch.mapping_id not in mappings_payload and batch.mapping is not None:
            mappings_payload[batch.mapping_id] = _mapping_payload(batch.mapping)
        for device in batch.devices:
            row = {column: getattr(device, column) for column in _SHARED_DEVICE_COLUMNS}
            row["batch_no"] = batch.batch_no
            devices_payload.append(row)

    arrow_b64 = ""
    if devices_payload:
        table = pa.Table.from_pylist(devices_payload)
        sink = pa.BufferOutputStream()
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
        arrow_b64 = base64.b64encode(gzip.compress(sink.getvalue().to_pybytes())).decode("ascii")

    return {
        "batches": batches_payload,
        "mappings": list(mappings_payload.values()),
        "devices_arrow_gz_b64": arrow_b64,
    }


def decode_devices(payload_b64: str) -> list[dict]:
    """build_snapshot 的逆操作；客户端拉取后调用。"""
    if not payload_b64:
        return []
    raw = gzip.decompress(base64.b64decode(payload_b64))
    return pa.ipc.open_file(pa.py_buffer(raw)).read_all().to_pylist()


def import_snapshot(
    db: Session,
    batches: list[dict],
    mappings: list[dict],
    devices: Iterable[dict],
    *,
    source: str,
) -> dict[str, int]:
    """增量导入快照。同名本机批次/对照表优先，不覆盖本地工作。"""
    local_mappings = {row.name: row for row in db.scalars(select(Mapping)).all()}
    mapping_ids: dict[int, int] = {}
    added_mappings = 0
    for remote in mappings:
        name = str(remote.get("name") or "").strip()
        if not name:
            continue
        mapping = local_mappings.get(name)
        if mapping is None:
            mapping = Mapping(name=name, file_path=f"{source}://{name}", entry_count=0)
            db.add(mapping)
            db.flush()
            entries = remote.get("entries") or []
            db.bulk_insert_mappings(
                MappingEntry,
                [
                    {
                        "mapping_id": mapping.id,
                        "mark": str(entry.get("mark") or ""),
                        "description": _nullable(entry.get("description")),
                        "eg": _nullable(entry.get("eg")),
                        "fl": _nullable(entry.get("fl")),
                        "ag": _nullable(entry.get("ag")),
                        "area_s11": _nullable(entry.get("area_s11")),
                        "area_s22": _nullable(entry.get("area_s22")),
                        "has_pf": bool(entry.get("has_pf")),
                        "raw_tokens": None,
                    }
                    for entry in entries
                    if entry.get("mark") is not None
                ],
            )
            mapping.entry_count = len(entries)
            local_mappings[name] = mapping
            added_mappings += 1
        if remote.get("id") is not None:
            mapping_ids[int(remote["id"])] = mapping.id

    local_batch_nos = set(db.scalars(select(Batch.batch_no)).all())
    batch_ids: dict[str, int] = {}
    added_batches = 0
    for remote in batches:
        batch_no = str(remote.get("batch_no") or "").strip()
        if not batch_no or batch_no in local_batch_nos:
            continue
        mapping_id = mapping_ids.get(int(remote.get("mapping_id") or 0))
        if mapping_id is None:
            mapping = local_mappings.get(str(remote.get("mapping_name") or ""))
            if mapping is None:
                raise ValueError(f"批次 {batch_no} 引用了快照中不存在的对照表")
            mapping_id = mapping.id
        batch = Batch(
            batch_no=batch_no,
            mapping_id=mapping_id,
            f_start_ghz=_nullable(remote.get("f_start_ghz")),
            f_end_ghz=_nullable(remote.get("f_end_ghz")),
            deembedded=bool(remote.get("deembedded")),
            deembed_method=str(remote.get("deembed_method") or "default"),
            process_type=str(remote.get("process_type") or "AUTO"),
            file_path=f"{source}://{batch_no}",
            raw_zip_path=None,
            source=source,
            device_count=int(remote.get("device_count") or 0),
            uploaded_by=str(remote.get("uploaded_by") or f"{source}-sync"),
        )
        db.add(batch)
        db.flush()
        batch_ids[batch_no] = batch.id
        added_batches += 1

    rows_by_batch: dict[str, list[dict]] = defaultdict(list)
    for index, remote in enumerate(devices):
        batch_id = batch_ids.get(str(remote.get("batch_no") or ""))
        if batch_id is None:
            continue
        values = {
            key: _nullable(value)
            for key, value in remote.items()
            if key in _DEVICE_COLUMNS and key not in {"id", "batch_id", "s_param_path"}
        }
        values["batch_id"] = batch_id
        values["original_filename"] = str(values.get("original_filename") or f"{source}-{index}")
        values["s_param_port"] = "S22" if values.get("s_param_port") == "S22" else "S11"
        if values.get("pf") is not None:
            values["pf"] = "Y" if str(values["pf"]).upper() in {"Y", "1", "TRUE"} else "N"
        values["deembedded"] = bool(values.get("deembedded"))
        rows_by_batch[str(remote.get("batch_no"))].append(values)
    for rows in rows_by_batch.values():
        db.bulk_insert_mappings(Device, rows)
    db.commit()
    return {
        "mappings_added": added_mappings,
        "batches_added": added_batches,
        "devices_added": sum(len(rows) for rows in rows_by_batch.values()),
    }
```

修改 `backend/app/services/web_sync_service.py`：删除文件内 `import_web_snapshot` 的函数体，改为薄封装（保留函数名与签名，网页行为不变）：

```python
def import_web_snapshot(
    db: Session, batches: list[dict], mappings: list[dict], devices: Iterable[dict]
) -> dict[str, int]:
    """增量导入网页快照（薄封装，逻辑在 snapshot_service）。"""
    from app.services.snapshot_service import import_snapshot

    try:
        return import_snapshot(db, batches, mappings, devices, source="web")
    except ValueError as exc:
        raise WebSyncError(str(exc)) from exc
```

（`import_snapshot` 对缺失对照表抛 `ValueError`，这里转成 `WebSyncError`，保持 `/api/web-sync/pull` 返回 502 的既有行为。）

同时把 web_sync_service 中随之不再使用的 `_DEVICE_COLUMNS`、`_nullable`、`defaultdict`、相关 import 清理掉（保留 `fetch_web_snapshot`、`push_batch_to_web` 等其余功能不动）。

- [ ] **步骤 4：运行测试验证通过 + 网页回归**

运行：`cd backend && uv run pytest tests/services/test_snapshot_service.py tests/services/test_web_sync_service.py -v`
预期：全部 PASS（web_sync 既有测试不改动即通过，证明重构无行为变化）

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/snapshot_service.py backend/app/services/web_sync_service.py backend/tests/services/test_snapshot_service.py
git commit -m "refactor(sync): 快照导入逻辑抽入 snapshot_service 并新增服务器侧差集导出"
```

---

### 任务 4：devices.py 曲线响应 helper 抽取

**文件：**
- 修改：`backend/app/api/devices.py`
- 测试：复用现有 `backend/tests/api/test_devices_cache.py` 等（无需新增）

- [ ] **步骤 1：基线回归**

运行：`cd backend && uv run pytest tests/api/test_devices_cache.py tests/api/test_query_cache.py -v`
预期：PASS（重构前基线）

- [ ] **步骤 2：抽出 helper（端点改为薄壳）**

在 `backend/app/api/devices.py` 中，把 `device_sparam` 的 skrf 路径主体抽成（PINN 快速路径留在端点内）：

```python
def resolve_device_sparam_path(device: Device) -> Path:
    """器件 snp 的绝对路径（batch_no 子目录 + 相对路径拼接）。"""
    batch_no = device.batch.batch_no if device.batch else None
    return _resolve_sparam_path(device.s_param_path or "", batch_no=batch_no)


def build_sparam_response(device: Device, device_id: int, param: str) -> dict[str, Any]:
    """skrf 标准路径：读 snp → 计算曲线 → 响应 dict。文件缺失抛 HTTPException(404)。"""
    if not device.s_param_path:
        raise HTTPException(status_code=404, detail="该器件没有 S 参数文件")
    path = resolve_device_sparam_path(device)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"S 参数文件不存在: {path}")
    try:
        net = _load_network(str(path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取 S 参数失败: {exc!s}") from exc
    try:
        curve = compute_sparam_curve(net, param)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response: dict[str, Any] = {
        "device_id": device_id,
        "param": param,
        "file_path": device.s_param_path,
        "source": "skrf",
    }
    response.update(curve)
    return response


def build_bodeq_response(device: Device, device_id: int) -> dict[str, Any]:
    if not device.s_param_path:
        raise HTTPException(status_code=404, detail="该器件没有 S 参数文件")
    path = resolve_device_sparam_path(device)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"S 参数文件不存在: {path}")
    try:
        result = _calc_bodeq_cached(str(path))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"BodeQ 计算失败: {exc!s}") from exc
    return {
        "device_id": device_id,
        **result,
        "fs_ghz": device.fs_ghz,
        "fp_ghz": device.fp_ghz,
        "fbode_ghz": device.fbode_ghz,
    }
```

`device_sparam` 端点保留 param 校验与 PINN 快速路径，skrf 段改为 `return build_sparam_response(device, device_id, param)`；`device_bodeq` 改为 `return build_bodeq_response(device, device_id)`。`sparam_sparse` 与 `download_s1p` 暂不动。

- [ ] **步骤 3：回归验证**

运行：`cd backend && uv run pytest tests/api/ -v -x -q`
预期：全部 PASS（行为等价重构）

- [ ] **步骤 4：Commit**

```bash
git add backend/app/api/devices.py
git commit -m "refactor(sync): devices 曲线响应抽 helper 供自然键端点复用"
```

---

### 任务 5：服务器角色端点 /api/sync/*

**文件：**
- 新建：`backend/app/api/sync.py`
- 修改：`backend/app/main.py`（注册 router）
- 修改：`backend/app/services/upload_service.py:102`（source Literal 加 "sync"）
- 测试：`backend/tests/api/test_sync_server.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""服务器角色同步端点：快照/mapping 确保/推送入库/自然键曲线。"""

from __future__ import annotations

import gzip
import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Batch, Device, Mapping, MappingEntry, UploadTask


def _make_zip(path: Path) -> Path:
    s1p = (
        "# GHz S MA R 50\n"
        + "\n".join(f"{4.0 + i * 0.001:.3f} {0.5 - i * 1e-4:.6f} {170 + i * 0.01:.2f}" for i in range(801))
        + "\n"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("dut1.s1p", s1p)
    return path


def test_snapshot_endpoint_excludes_known(client, db):
    mapping = Mapping(name="M", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    db.add(Batch(batch_no="S-1", mapping_id=mapping.id, file_path="f", device_count=0))
    db.add(Batch(batch_no="S-2", mapping_id=mapping.id, file_path="f", device_count=0))
    db.commit()

    resp = client.post("/api/sync/snapshot", json={"known_batch_nos": ["S-1"]})
    assert resp.status_code == 200
    payload = resp.json()
    assert [b["batch_no"] for b in payload["batches"]] == ["S-2"]


def test_mapping_ensure_create_and_conflict(client, db):
    entries = [{"mark": "A1", "description": "d", "has_pf": False}]
    resp = client.post("/api/sync/mapping", json={"name": "NEW-MAP", "entries": entries})
    assert resp.status_code == 200 and resp.json()["created"] is True

    again = client.post("/api/sync/mapping", json={"name": "NEW-MAP", "entries": entries})
    assert again.status_code == 200 and again.json()["created"] is False

    conflict = client.post(
        "/api/sync/mapping",
        json={"name": "NEW-MAP", "entries": [{"mark": "B2", "description": "x", "has_pf": True}]},
    )
    assert conflict.status_code == 409


def test_push_batch_idempotent(client, db, tmp_path, monkeypatch):
    # eager 模式下推送即完成入库；重复推送返回 already_synced
    from app.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    client.post("/api/sync/mapping", json={"name": "PUSH-MAP", "entries": [{"mark": "A1"}]})
    zip_path = _make_zip(tmp_path / "PUSH-1.zip")

    with zip_path.open("rb") as fh:
        resp = client.post(
            "/api/sync/push-batch",
            files={"file": ("PUSH-1.zip", fh, "application/zip")},
            data={"batch_no": "PUSH-1", "mapping_name": "PUSH-MAP", "uploaded_by": "tester"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in {"accepted", "already_synced"}

    with zip_path.open("rb") as fh:
        again = client.post(
            "/api/sync/push-batch",
            files={"file": ("PUSH-1.zip", fh, "application/zip")},
            data={"batch_no": "PUSH-1", "mapping_name": "PUSH-MAP", "uploaded_by": "tester"},
        )
    assert again.status_code == 200
    assert again.json()["status"] == "already_synced"


def test_push_batch_unknown_mapping_422(client):
    resp = client.post(
        "/api/sync/push-batch",
        files={"file": ("X.zip", io.BytesIO(b"PK\x05\x06" + b"\0" * 18), "application/zip")},
        data={"batch_no": "X-1", "mapping_name": "NOPE"},
    )
    assert resp.status_code == 422


def test_sparam_by_natural_key_404(client, db):
    resp = client.get("/api/sync/sparam", params={"batch_no": "NONE", "filename": "x.s1p", "port": "S11"})
    assert resp.status_code == 404
```

注：`test_push_batch_idempotent` 里 eager 执行会真实跑提参；合成 s1p 找不到谐振峰时器件按 Fail 入库（Fail 数据保留是产品决策），不影响断言。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run pytest tests/api/test_sync_server.py -v`
预期：FAIL，404 / 模块不存在

- [ ] **步骤 3：实现 `backend/app/api/sync.py`**

```python
"""服务器角色同步端点：供内网桌面端拉快照、推批次、按自然键取曲线。"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.devices import build_sparam_response, resolve_device_sparam_path
from app.config import get_settings
from app.models import Batch, Device, Mapping, MappingEntry, UploadTask
from app.services.snapshot_service import build_snapshot
from app.services.upload_service import create_batch_and_dispatch

router = APIRouter(prefix="/sync", tags=["sync"])


class SnapshotRequest(BaseModel):
    known_batch_nos: list[str] = Field(default_factory=list, max_length=100000)


class MappingEntryIn(BaseModel):
    mark: str
    description: str | None = None
    eg: float | None = None
    fl: float | None = None
    ag: float | None = None
    area_s11: float | None = None
    area_s22: float | None = None
    has_pf: bool = False


class MappingEnsureRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    entries: list[MappingEntryIn] = Field(default_factory=list)


def _entries_fingerprint(entries: list[dict]) -> str:
    canonical = json.dumps(entries, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post("/snapshot")
def snapshot(body: SnapshotRequest, db: DbSession) -> dict[str, Any]:
    return build_snapshot(db, set(body.known_batch_nos))


@router.post("/mapping")
def ensure_mapping(body: MappingEnsureRequest, db: DbSession) -> dict[str, Any]:
    name = body.name.strip()
    incoming = [entry.model_dump() for entry in body.entries]
    existing = db.scalar(select(Mapping).where(Mapping.name == name))
    if existing is not None:
        current = [
            {
                "mark": e.mark, "description": e.description, "eg": e.eg, "fl": e.fl,
                "ag": e.ag, "area_s11": e.area_s11, "area_s22": e.area_s22, "has_pf": e.has_pf,
            }
            for e in sorted(existing.entries, key=lambda x: x.mark)
        ]
        if _entries_fingerprint(current) != _entries_fingerprint(
            sorted(incoming, key=lambda x: x["mark"])
        ):
            raise HTTPException(status_code=409, detail=f"对照表 {name} 已存在且内容不同，请改名")
        return {"mapping_id": existing.id, "created": False}

    mapping = Mapping(name=name, file_path=f"sync://{name}", entry_count=len(incoming))
    db.add(mapping)
    db.flush()
    db.bulk_insert_mappings(
        MappingEntry,
        [{"mapping_id": mapping.id, "raw_tokens": None, **entry} for entry in incoming],
    )
    db.commit()
    return {"mapping_id": mapping.id, "created": True}


@router.post("/push-batch")
def push_batch(
    db: DbSession,
    file: Annotated[UploadFile, File(...)],
    batch_no: Annotated[str, Form(min_length=1, max_length=300)],
    mapping_name: Annotated[str, Form(min_length=1, max_length=200)],
    f_start_ghz: Annotated[float | None, Form()] = None,
    f_end_ghz: Annotated[float | None, Form()] = None,
    deembed: Annotated[bool, Form()] = False,
    deembed_method: Annotated[str, Form()] = "default",
    process_type: Annotated[str, Form()] = "AUTO",
    uploaded_by: Annotated[str, Form()] = "anonymous",
) -> dict[str, Any]:
    mapping = db.scalar(select(Mapping).where(Mapping.name == mapping_name.strip()))
    if mapping is None:
        raise HTTPException(status_code=422, detail=f"对照表 {mapping_name} 不存在，先推送对照表")

    existing = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if existing is not None:
        task = db.get(UploadTask, existing.task_id) if existing.task_id else None
        if task is not None and task.status in {"pending", "running"}:
            raise HTTPException(status_code=409, detail="服务器上该批次任务进行中，稍后重试")
        if task is not None and task.status == "success":
            return {
                "status": "already_synced",
                "task_id": task.id,
                "device_count": existing.device_count,
            }
        # 失败/取消/无任务：清理后重传
        db.delete(existing)
        db.flush()
        shutil.rmtree(get_settings().files_dir / batch_no, ignore_errors=True)

    settings = get_settings()
    month_dir = settings.uploads_dir / datetime.now(UTC).strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    saved_path = month_dir / f"{uuid.uuid4().hex}.zip"
    max_bytes = settings.UPLOAD_MAX_GB * 1024**3
    written = 0
    try:
        with saved_path.open("wb") as out:
            while chunk := file.file.read(8 * 1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail="文件超过上限")
                out.write(chunk)
    except Exception:
        saved_path.unlink(missing_ok=True)
        raise

    task = create_batch_and_dispatch(
        db,
        zip_path=saved_path,
        batch_no=batch_no,
        mapping_id=mapping.id,
        source="sync",
        f_start_ghz=f_start_ghz,
        f_end_ghz=f_end_ghz,
        deembed=deembed,
        deembed_method=deembed_method,
        process_type=process_type,
        uploaded_by=uploaded_by,
    )
    if task is None:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="创建批次失败")
    if task.batch_no != batch_no:
        # create_batch_and_dispatch 对冲突名自动加后缀——推送语义要求精确名，
        # 说明与并发请求撞名，回滚本次创建并按“已同步”处理由对方完成的批次。
        db.delete(db.scalar(select(Batch).where(Batch.batch_no == task.batch_no)))
        db.delete(task)
        db.commit()
        saved_path.unlink(missing_ok=True)
        winner = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        return {
            "status": "already_synced",
            "task_id": winner.task_id if winner else None,
            "device_count": winner.device_count if winner else 0,
        }
    return {"status": "accepted", "task_id": task.id, "device_count": None}


def _find_device(db, batch_no: str, filename: str, port: str) -> Device:
    device = db.scalars(
        select(Device)
        .join(Batch, Device.batch_id == Batch.id)
        .where(
            Batch.batch_no == batch_no,
            Device.original_filename == filename,
            Device.s_param_port == port,
        )
    ).first()
    if device is None:
        raise HTTPException(status_code=404, detail=f"器件不存在: {batch_no}/{filename}/{port}")
    return device


@router.get("/sparam")
def sparam_by_natural_key(
    db: DbSession,
    batch_no: Annotated[str, Query()],
    filename: Annotated[str, Query()],
    port: Annotated[str, Query()] = "S11",
    param: Annotated[str, Query()] = "s11_db",
) -> dict[str, Any]:
    device = _find_device(db, batch_no, filename, port)
    return build_sparam_response(device, 0, param)


@router.get("/download")
def download_by_natural_key(
    db: DbSession,
    batch_no: Annotated[str, Query()],
    filename: Annotated[str, Query()],
    port: Annotated[str, Query()] = "S11",
) -> FileResponse:
    device = _find_device(db, batch_no, filename, port)
    if not device.s_param_path:
        raise HTTPException(status_code=404, detail="该器件没有 S 参数文件路径")
    path = resolve_device_sparam_path(device)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"S 参数文件不存在: {path}")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)
```

`backend/app/services/upload_service.py:102`：`source: Literal["http", "watch"] = "http"` 改为 `source: Literal["http", "watch", "sync"] = "http"`。

`backend/app/main.py`：`from app.api import ... sync, ...`，并在 web_sync 注册后加 `app.include_router(sync.router, prefix="/api")`（desktop_sync 在任务 8 一并注册）。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run pytest tests/api/test_sync_server.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/api/sync.py backend/app/main.py backend/app/services/upload_service.py backend/tests/api/test_sync_server.py
git commit -m "feat(sync): 服务器角色端点 快照/mapping确保/幂等推送/自然键曲线与下载"
```

---

### 任务 6：server_client（桌面端配置 + HTTP 客户端）

**文件：**
- 新建：`backend/app/services/server_client.py`
- 测试：`backend/tests/services/test_server_client.py`

- [ ] **步骤 1：编写失败的测试**

```python
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


def test_client_calls(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/sync/snapshot":
            return httpx.Response(200, json={"batches": [], "mappings": [], "devices_arrow_gz_b64": ""})
        if request.url.path == "/api/tasks/7":
            return httpx.Response(200, json={"id": 7, "status": "success"})
        return httpx.Response(404, json={"detail": "nope"})

    cfg = server_client.ServerConfig(server_url="http://server", username="u")
    client = server_client.ServerClient(cfg, transport=httpx.MockTransport(handler))
    assert client.reachable() is True
    assert client.fetch_snapshot({"A"}) == {"batches": [], "mappings": [], "devices_arrow_gz_b64": ""}
    assert json.loads(seen["/api/sync/snapshot"].content) == {"known_batch_nos": ["A"]}
    assert client.task_status(7)["status"] == "success"
    with pytest.raises(server_client.ServerError):
        client.task_status(999)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run pytest tests/services/test_server_client.py -v`
预期：FAIL，`ModuleNotFoundError`

- [ ] **步骤 3：实现 `backend/app/services/server_client.py`**

```python
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
        return ServerConfig(server_url=str(data["server_url"]), username=str(data.get("username") or "anonymous"))
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def save_config(config: ServerConfig) -> None:
    _config_dir().mkdir(parents=True, exist_ok=True)
    _config_path().write_text(
        json.dumps({"server_url": config.server_url, "username": config.username}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class ServerClient:
    def __init__(self, config: ServerConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._http = httpx.Client(
            base_url=f"{config.server_url}/api", timeout=httpx.Timeout(60.0, read=300.0), transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ServerClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            raise ServerError(f"服务器返回 {response.status_code}: {response.text[:300]}")
        return response

    def reachable(self) -> bool:
        try:
            return self._http.get("/health").status_code == 200
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
                return self._check(
                    self._http.post(
                        "/sync/push-batch",
                        files={"file": (zip_path.name, fh, "application/zip")},
                        data=meta,
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
                    params={"batch_no": batch_no, "filename": filename, "port": port, "param": param},
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
                self._check(response)
                with dest.open("wb") as out:
                    for chunk in response.iter_bytes():
                        out.write(chunk)
            return dest
        except httpx.HTTPError as exc:
            raise ServerError(f"无法从服务器下载文件: {exc}") from exc
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run pytest tests/services/test_server_client.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/server_client.py backend/tests/services/test_server_client.py
git commit -m "feat(sync): 桌面端服务器配置存取与 httpx 客户端"
```

---

### 任务 7：desktop_sync_service（拉取/推送编排）

**文件：**
- 新建：`backend/app/services/desktop_sync_service.py`
- 测试：`backend/tests/services/test_desktop_sync_service.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""桌面端同步编排：未同步批次识别、gz 重打包、推送状态机、删本地文件。"""

import gzip
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Device, Mapping
from app.services import desktop_sync_service as svc
from app.services.server_client import ServerError


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _batch(db: Session, batch_no: str, *, source="http", count=1, synced=None) -> Batch:
    mapping = Mapping(name=f"MAP-{batch_no}", file_path="m.csv", entry_count=0)
    db.add(mapping)
    db.flush()
    batch = Batch(batch_no=batch_no, mapping_id=mapping.id, file_path="f",
                  source=source, device_count=count, synced_at=synced)
    db.add(batch)
    db.commit()
    return batch


def _files(settings_dir: Path, batch_no: str, gz: bool = True) -> Path:
    d = settings_dir / "files" / batch_no
    d.mkdir(parents=True, exist_ok=True)
    content = b"# GHz S MA R 50\n4.0 0.5 170\n"
    if gz:
        (d / "dut1.s1p.gz").write_bytes(gzip.compress(content))
    else:
        (d / "dut1.s1p").write_bytes(content)
    return d


def test_list_unsynced_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    local = _batch(db, "L-1")
    _files(tmp_path, "L-1")
    _batch(db, "WEB-1", source="server")          # 快照批次不推送
    _batch(db, "DONE-1", synced=datetime.now(UTC))  # 已同步不推送
    _batch(db, "EMPTY-1", count=0)                  # 未完成入库不推送
    _batch(db, "NOFILES-1")                          # 文件目录不存在不推送
    assert [b.batch_no for b in svc.list_unsynced_batches(db)] == ["L-1"]


def test_pack_batch_zip_decompresses_gz(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    batch = _batch(db, "P-1")
    _files(tmp_path, "P-1", gz=True)
    out = svc.pack_batch_zip(batch, tmp_path / "out.zip")
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["P-1/dut1.s1p"]
        assert zf.read("P-1/dut1.s1p").startswith(b"# GHz S MA")


class _StubClient:
    """预置推送结果的任务成功桩。"""

    def __init__(self, push_result, batch_count):
        self._push = push_result
        self._count = batch_count

    def ensure_mapping(self, name, entries):
        return {"mapping_id": 1, "created": True}

    def push_batch(self, zip_path, meta):
        return self._push

    def task_status(self, task_id):
        return {"id": task_id, "status": "success"}

    def get_batch(self, batch_no):
        return {"batch_no": batch_no, "device_count": self._count}


def test_push_success_deletes_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    db = _session()
    _batch(db, "OK-1", count=5)
    files_dir = _files(tmp_path, "OK-1")
    client = _StubClient({"status": "already_synced", "device_count": 5}, batch_count=5)

    result = svc.push_batch_to_server(db, client, "OK-1")
    assert result["status"] == "synced"
    assert not files_dir.exists()
    assert db.scalar(select(Batch).where(Batch.batch_no == "OK-1")).synced_at is not None


def test_push_count_mismatch_keeps_files(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    db = _session()
    _batch(db, "BAD-1", count=3)
    files_dir = _files(tmp_path, "BAD-1")
    client = _StubClient({"status": "already_synced", "device_count": 99}, batch_count=99)

    with pytest.raises(ServerError, match="同名不同数据"):
        svc.push_batch_to_server(db, client, "BAD-1")
    assert files_dir.exists()
    assert db.scalar(select(Batch).where(Batch.batch_no == "BAD-1")).synced_at is None


def test_pull_merges_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    db = _session()
    _batch(db, "LOCAL-1")

    class PullClient:
        def fetch_snapshot(self, known):
            assert known == {"LOCAL-1"}
            return {
                "batches": [{"batch_no": "R-1", "mapping_id": 9, "device_count": 1,
                             "process_type": "S1P", "deembedded": False}],
                "mappings": [{"id": 9, "name": "R-MAP", "entries": [{"mark": "A1"}]}],
                "devices_arrow_gz_b64": "",
            }

    stats = svc.pull_from_server(db, PullClient())
    assert stats["batches_added"] == 1
    assert db.scalar(select(Batch).where(Batch.batch_no == "R-1")).source == "server"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run pytest tests/services/test_desktop_sync_service.py -v`
预期：FAIL，`ModuleNotFoundError`

- [ ] **步骤 3：实现 `backend/app/services/desktop_sync_service.py`**

```python
"""桌面端同步编排：下行拉快照、上行推原始 snp 并清理本地文件。

推送对象仅限本地上传且文件仍在本地的批次（快照导入批次的数据本来就在
服务器上）。所有失败都可安全重试，服务器端幂等兜底。
"""

from __future__ import annotations

import gzip
import shutil
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Batch, Mapping
from app.services.server_client import ServerClient, ServerError
from app.services.snapshot_service import decode_devices, import_snapshot

# 轮询服务器任务状态的节奏与上限
POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 3600.0


def _files_root() -> Path:
    return get_settings().files_dir


def _zip_staging_dir() -> Path:
    path = get_settings().data_root / "sync-staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_unsynced_batches(db: Session) -> list[Batch]:
    """本地上传、入库完成、未推送且文件仍在本地的批次。"""
    batches = db.scalars(
        select(Batch).where(
            Batch.synced_at.is_(None),
            Batch.device_count > 0,
            Batch.source.notin_(("web", "server")),
        ).order_by(Batch.uploaded_at)
    ).all()
    return [b for b in batches if (_files_root() / b.batch_no).is_dir()]


def pack_batch_zip(batch: Batch, out_path: Path) -> Path:
    """把 files/<batch_no>/ 打成服务器 ingest 可吃的 zip（.gz 解压回 .s1p）。"""
    src = _files_root() / batch.batch_no
    if not src.is_dir():
        raise ServerError(f"本地批次目录不存在: {src}")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(src.rglob("*")):
            if not file.is_file():
                continue
            rel = f"{batch.batch_no}/{file.relative_to(src).as_posix()}"
            if file.suffix == ".gz":
                with gzip.open(file, "rb") as fh:
                    zf.writestr(rel[: -len(".gz")], fh.read())
            else:
                zf.write(file, arcname=rel)
    return out_path


def _mapping_entries_payload(mapping: Mapping) -> list[dict]:
    return [
        {
            "mark": e.mark, "description": e.description, "eg": e.eg, "fl": e.fl,
            "ag": e.ag, "area_s11": e.area_s11, "area_s22": e.area_s22, "has_pf": e.has_pf,
        }
        for e in mapping.entries
    ]


def ensure_mapping_on_server(db: Session, client: ServerClient, batch: Batch) -> None:
    mapping = db.get(Mapping, batch.mapping_id)
    if mapping is None:
        raise ServerError("批次关联的对照表不存在")
    try:
        client.ensure_mapping(mapping.name, _mapping_entries_payload(mapping))
    except ServerError as exc:
        if "409" in str(exc) or "内容不同" in str(exc):
            raise ServerError(f"服务器上对照表 {mapping.name} 内容不同，请改名后重试") from exc
        raise


def wait_task(client: ServerClient, task_id: int) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        task = client.task_status(task_id)
        if task["status"] == "success":
            return task
        if task["status"] in {"failed", "cancelled"}:
            raise ServerError(f"服务器入库失败: {task.get('error_msg') or task['status']}")
        time.sleep(POLL_INTERVAL_S)
    raise ServerError("等待服务器入库超时")


def finalize_synced_batch(db: Session, client: ServerClient, batch_no: str) -> dict[str, Any]:
    """校验服务器 device_count 与本地一致后，删除本地原始文件并打 synced_at。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise ServerError(f"本机没有批次 {batch_no}")
    remote_count = int(client.get_batch(batch_no)["device_count"])
    if remote_count != batch.device_count:
        raise ServerError(
            f"疑似同名不同数据：服务器 {remote_count} 条 vs 本地 {batch.device_count} 条，"
            "请给本地批次改名后重推，本地文件未删除"
        )
    shutil.rmtree(_files_root() / batch_no, ignore_errors=True)
    batch.synced_at = datetime.now(UTC)
    db.commit()
    return {"status": "synced", "device_count": remote_count}


def push_batch_to_server(db: Session, client: ServerClient, batch_no: str) -> dict[str, Any]:
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise ServerError(f"本机没有批次 {batch_no}")
    if batch.synced_at is not None:
        return {"status": "synced", "device_count": batch.device_count, "note": "此前已同步"}

    ensure_mapping_on_server(db, client, batch)
    zip_path = pack_batch_zip(batch, _zip_staging_dir() / f"{batch_no}.zip")
    try:
        result = client.push_batch(
            zip_path,
            {
                "batch_no": batch_no,
                "mapping_name": batch.mapping.name,
                "f_start_ghz": "" if batch.f_start_ghz is None else str(batch.f_start_ghz),
                "f_end_ghz": "" if batch.f_end_ghz is None else str(batch.f_end_ghz),
                "deembed": "true" if batch.deembedded else "false",
                "deembed_method": batch.deembed_method,
                "process_type": batch.process_type,
                "uploaded_by": client.config.username,
            },
        )
    finally:
        zip_path.unlink(missing_ok=True)

    if result["status"] == "accepted":
        wait_task(client, int(result["task_id"]))
    return finalize_synced_batch(db, client, batch_no)


def pull_from_server(db: Session, client: ServerClient) -> dict[str, int]:
    known = set(db.scalars(select(Batch.batch_no)).all())
    payload = client.fetch_snapshot(known)
    return import_snapshot(
        db,
        payload["batches"],
        payload["mappings"],
        decode_devices(payload["devices_arrow_gz_b64"]),
        source="server",
    )
```

注：`push_batch` 的 form 空字符串字段——服务器端 `f_start_ghz: float | None = Form()` 收到 `""` 会 422，因此客户端 meta 中频段为空时**不要传该键**。实现时把 meta 构造改为：

```python
            meta = {
                "batch_no": batch_no,
                "mapping_name": batch.mapping.name,
                "deembed": "true" if batch.deembedded else "false",
                "deembed_method": batch.deembed_method,
                "process_type": batch.process_type,
                "uploaded_by": client.config.username,
            }
            if batch.f_start_ghz is not None:
                meta["f_start_ghz"] = str(batch.f_start_ghz)
            if batch.f_end_ghz is not None:
                meta["f_end_ghz"] = str(batch.f_end_ghz)
            result = client.push_batch(zip_path, meta)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run pytest tests/services/test_desktop_sync_service.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/desktop_sync_service.py backend/tests/services/test_desktop_sync_service.py
git commit -m "feat(sync): 桌面端拉快照/推批次编排与本地文件清理"
```

---

### 任务 8：桌面角色端点 /api/desktop-sync/*

**文件：**
- 新建：`backend/app/api/desktop_sync.py`
- 修改：`backend/app/main.py`（注册 router）
- 测试：`backend/tests/api/test_desktop_sync_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""桌面角色端点：配置存取、状态、拉取、推送；非桌面模式 enabled=false。"""

from unittest.mock import patch

from app.services.server_client import ServerConfig


def test_status_disabled_when_not_desktop(client):
    resp = client.get("/api/desktop-sync/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_config_round_trip(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.desktop_sync.server_client._config_dir", lambda: tmp_path)
    resp = client.put(
        "/api/desktop-sync/config", json={"server_url": "http://192.168.1.10:8080", "username": "u1"}
    )
    assert resp.status_code == 200
    loaded = client.get("/api/desktop-sync/config").json()
    assert loaded == {"server_url": "http://192.168.1.10:8080", "username": "u1"}


def test_pull_requires_config(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.desktop_sync.server_client._config_dir", lambda: tmp_path)
    resp = client.post("/api/desktop-sync/pull")
    assert resp.status_code == 400


def test_pull_calls_service(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.desktop_sync.server_client._config_dir", lambda: tmp_path)
    client.put("/api/desktop-sync/config", json={"server_url": "http://s", "username": "u"})
    with patch("app.api.desktop_sync.desktop_sync_service.pull_from_server") as pull:
        pull.return_value = {"mappings_added": 0, "batches_added": 2, "devices_added": 10}
        resp = client.post("/api/desktop-sync/pull")
    assert resp.status_code == 200
    assert resp.json()["batches_added"] == 2
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run pytest tests/api/test_desktop_sync_api.py -v`
预期：FAIL，404

- [ ] **步骤 3：实现 `backend/app/api/desktop_sync.py` 并注册**

```python
"""桌面角色同步端点：前端调用，驱动 server_client/desktop_sync_service。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import DbSession
from app.config import get_settings
from app.services import desktop_sync_service, server_client
from app.services.server_client import ServerClient, ServerConfig, ServerError

router = APIRouter(prefix="/desktop-sync", tags=["desktop-sync"])


class ConfigIn(BaseModel):
    server_url: str = Field(min_length=1, max_length=500)
    username: str = Field(default="anonymous", max_length=100)


def _load_client() -> ServerClient:
    config = server_client.load_config()
    if config is None:
        raise HTTPException(status_code=400, detail="尚未配置服务器地址")
    return ServerClient(config)


@router.get("/status")
def status(db: DbSession) -> dict[str, Any]:
    settings = get_settings()
    config = server_client.load_config()
    reachable = False
    if settings.is_desktop and config is not None:
        try:
            with ServerClient(config) as client:
                reachable = client.reachable()
        except Exception:  # noqa: BLE001 — 可达性探测不允许抛出
            reachable = False
    unsynced = (
        [b.batch_no for b in desktop_sync_service.list_unsynced_batches(db)]
        if settings.is_desktop
        else []
    )
    return {
        "enabled": settings.is_desktop,
        "configured": config is not None,
        "server_url": config.server_url if config else None,
        "username": config.username if config else None,
        "reachable": reachable,
        "unsynced_batches": unsynced,
    }


@router.get("/config")
def get_config() -> dict[str, str | None]:
    config = server_client.load_config()
    if config is None:
        return {"server_url": None, "username": None}
    return {"server_url": config.server_url, "username": config.username}


@router.put("/config")
def put_config(body: ConfigIn) -> dict[str, str]:
    config = ServerConfig(server_url=body.server_url, username=body.username)
    server_client.save_config(config)
    return {"server_url": config.server_url, "username": config.username}


@router.post("/pull")
def pull(db: DbSession) -> dict[str, int]:
    try:
        with _load_client() as client:
            return desktop_sync_service.pull_from_server(db, client)
    except ServerError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/push/{batch_no:path}")
def push(batch_no: str, db: DbSession) -> dict[str, Any]:
    try:
        with _load_client() as client:
            return desktop_sync_service.push_batch_to_server(db, client, batch_no)
    except ServerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

`backend/app/main.py`：import 加 `desktop_sync`，注册加 `app.include_router(desktop_sync.router, prefix="/api")`。

注意：`test_pull_calls_service` patch 路径要求 `desktop_sync.py` 用 `from app.services import desktop_sync_service, server_client` 形式导入（如上）。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd backend && uv run pytest tests/api/test_desktop_sync_api.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/api/desktop_sync.py backend/app/main.py backend/tests/api/test_desktop_sync_api.py
git commit -m "feat(sync): 桌面角色端点 状态/配置/拉取/推送"
```

---

### 任务 9：曲线查看透明代理降级

**文件：**
- 新建：`backend/app/services/server_proxy.py`
- 修改：`backend/app/api/devices.py`（sparam/bodeq/download-s1p 三处文件缺失分支）
- 测试：`backend/tests/api/test_devices_proxy.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""本地无 snp 文件时，桌面模式透明代理到内网服务器。"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.services import server_proxy
from app.services.server_client import ServerConfig, ServerError


class _Device:
    s_param_path = "missing.s1p"
    original_filename = "missing.s1p"
    s_param_port = "S11"

    class batch:
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run pytest tests/api/test_devices_proxy.py -v`
预期：FAIL，`ModuleNotFoundError`

- [ ] **步骤 3：实现 server_proxy + 接入 devices.py**

新建 `backend/app/services/server_proxy.py`：

```python
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
        raise HTTPException(status_code=503, detail=f"本地无文件且服务器不可用（需连接内网）: {exc}") from exc
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
        raise HTTPException(status_code=503, detail=f"本地无文件且服务器不可用（需连接内网）: {exc}") from exc
    return tmp, BackgroundTask(tmp.unlink, missing_ok=True)
```

`backend/app/api/devices.py` 三处接入（`device_sparam` 的 skrf 路径经任务 4 的 helper 抛 404，因此 sparam 的代理要在 helper 内部或端点内捕获；选择**端点内捕获**，helper 不变）：

`device_sparam` 端点改为：

```python
    # --- skrf 标准路径（本地无文件时桌面模式代理到服务器）---
    try:
        return build_sparam_response(device, device_id, param)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        from app.services.server_proxy import proxy_sparam

        proxied = proxy_sparam(device, param)
        if proxied is not None:
            proxied["device_id"] = device_id
            return proxied
        raise
```

`download_device_s1p` 文件缺失分支改为：

```python
    if not path.exists():
        from app.services.server_proxy import proxy_download

        proxied = proxy_download(device)
        if proxied is not None:
            tmp, cleanup = proxied
            return FileResponse(tmp, media_type="application/octet-stream",
                                filename=path.name, background=cleanup)
        raise HTTPException(status_code=404, detail=f"S 参数文件不存在: {path}")
```

`device_bodeq` 暂不代理（BodeQ 曲线为分析增强功能，断网可看主曲线；避免扩散范围）。

- [ ] **步骤 4：运行测试验证通过 + 回归**

运行：`cd backend && uv run pytest tests/api/test_devices_proxy.py tests/api/test_devices_cache.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/server_proxy.py backend/app/api/devices.py backend/tests/api/test_devices_proxy.py
git commit -m "feat(sync): 本地无文件时曲线/下载透明代理到内网服务器"
```

---

### 任务 10：前端同步面板

**文件：**
- 修改：`frontend/src/types.ts`（Batch 加字段 + ServerSyncStatus）
- 修改：`frontend/src/api/endpoints.ts`
- 新建：`frontend/src/components/ServerSyncPanel.tsx`
- 修改：`frontend/src/pages/Batches.tsx`

- [ ] **步骤 1：types 与 endpoints 封装**

`frontend/src/types.ts`：Batch 接口加 `synced_at?: string | null; source?: string;`，新增：

```typescript
export interface ServerSyncStatus {
  enabled: boolean;
  configured: boolean;
  server_url: string | null;
  username: string | null;
  reachable: boolean;
  unsynced_batches: string[];
}
```

`frontend/src/api/endpoints.ts` 追加：

```typescript
export const getServerSyncStatus = () =>
  api.get('/desktop-sync/status').then((r: AxiosResponse<ServerSyncStatus>) => r.data);
export const getServerSyncConfig = () =>
  api.get('/desktop-sync/config').then((r) => r.data as { server_url: string | null; username: string | null });
export const saveServerSyncConfig = (serverUrl: string, username: string) =>
  api.put('/desktop-sync/config', { server_url: serverUrl, username }).then((r) => r.data);
export const pullFromServer = () =>
  api.post('/desktop-sync/pull', undefined, { timeout: 0 }).then((r) => r.data as {
    mappings_added: number; batches_added: number; devices_added: number;
  });
export const pushBatchToServer = (batchNo: string) =>
  api.post(`/desktop-sync/push/${encodeURIComponent(batchNo)}`, undefined, { timeout: 0 }).then((r) => r.data as {
    status: string; device_count?: number;
  });
```

（`ServerSyncStatus` 加入顶部 types import 列表。）

- [ ] **步骤 2：ServerSyncPanel 组件**

新建 `frontend/src/components/ServerSyncPanel.tsx`：

```tsx
import { useCallback, useEffect, useState } from 'react';
import {
  getServerSyncStatus, pullFromServer, pushBatchToServer, saveServerSyncConfig,
} from '../api/endpoints';
import type { ServerSyncStatus } from '../types';

/** 内网服务器同步面板：仅桌面模式（enabled）渲染。 */
export default function ServerSyncPanel({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<ServerSyncStatus | null>(null);
  const [url, setUrl] = useState('');
  const [username, setUsername] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    getServerSyncStatus()
      .then((s) => {
        setStatus(s);
        if (s.server_url) setUrl((v) => v || s.server_url || '');
        if (s.username) setUsername((v) => v || s.username || '');
      })
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (!status || !status.enabled) return null;

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setMessage(null);
    try {
      setMessage(await action());
      refresh();
      onChanged();
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setMessage(`失败：${detail || (e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>内网服务器同步</strong>
        <span className="dim" style={{ fontSize: 11.5 }}>
          {status.configured
            ? status.reachable
              ? `已连接 ${status.server_url}`
              : `服务器不可达（需连接内网）${status.server_url ? `：${status.server_url}` : ''}`
            : '未配置服务器地址'}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <input
          style={{ flex: 2, minWidth: 220 }}
          placeholder="服务器地址，如 http://192.168.1.10:8080"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <input
          style={{ flex: 1, minWidth: 120 }}
          placeholder="用户名（记录上传人）"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <button
          disabled={busy || !url.trim()}
          onClick={() => run(async () => {
            await saveServerSyncConfig(url.trim(), username.trim() || 'anonymous');
            return '已保存配置';
          })}
        >
          保存配置
        </button>
        <button
          disabled={busy || !status.reachable}
          onClick={() => run(async () => {
            const r = await pullFromServer();
            return `同步完成：新增批次 ${r.batches_added}、器件 ${r.devices_added}`;
          })}
        >
          从服务器同步
        </button>
      </div>
      {status.unsynced_batches.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 12 }}>
          <span className="dim">待回传（{status.unsynced_batches.length}）：</span>
          {status.unsynced_batches.map((b) => (
            <span key={b} style={{ marginRight: 8 }}>
              <code>{b}</code>
              <button
                style={{ marginLeft: 4 }}
                disabled={busy || !status.reachable}
                onClick={() => run(async () => {
                  const r = await pushBatchToServer(b);
                  return `${b} 已回传（${r.device_count ?? '-'} 条），本地原始文件已清理`;
                })}
              >
                回传
              </button>
            </span>
          ))}
        </div>
      )}
      {message && <div className="dim" style={{ marginTop: 8, fontSize: 11.5 }}>{message}</div>}
    </div>
  );
}
```

- [ ] **步骤 3：Batches.tsx 挂载 + BatchRow 同步标记**

`frontend/src/pages/Batches.tsx`：
- import 加 `import ServerSyncPanel from '../components/ServerSyncPanel';`
- `Batches()` 主 return 中、批次表格之前加 `<ServerSyncPanel onChanged={load} />`（`load` 已存在，约 :89；直接传 `() => load()` 避免 cancelled flag 复用问题）。
- `BatchRow`（:16-66）在批次名旁加已同步标记：

```tsx
        {b.synced_at && (
          <span className="dim" style={{ fontSize: 11 }} title="原始文件已回传服务器">
            ☁️ 已回传
          </span>
        )}
        {b.source === 'server' && (
          <span className="dim" style={{ fontSize: 11 }} title="来自服务器快照，原始文件在服务器上">
            ☁️ 服务器
          </span>
        )}
```

（BatchRowProps 的 batch 类型来自 `../types` 的 Batch，任务步骤 1 已加 `synced_at`/`source`。）

- [ ] **步骤 4：构建验证**

运行：`cd frontend && npm run build`
预期：构建成功，无 TS 错误（若有 `npm test`/`vitest` 脚本，一并运行确认既有前端测试通过）

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/types.ts frontend/src/api/endpoints.ts frontend/src/components/ServerSyncPanel.tsx frontend/src/pages/Batches.tsx
git commit -m "feat(sync): 批次页内网服务器同步面板与回传状态标记"
```

---

### 任务 11：Windows 服务器部署文档

**文件：**
- 修改：`docs/deployment-windows.md`（末尾加章节）
- 修改：`docs/operations.md`（备份段加一句）

- [ ] **步骤 1：写文档**

`docs/deployment-windows.md` 末尾追加：

```markdown
---

## 附录：作为内网服务器长期运行

把这台机器当作课题组内网服务器（多人浏览器访问 + 桌面版同步目标）时，在上面 5 步之外再做这几件事：

1. **固定 IP**：在路由器给本机 MAC 绑定固定 IP（或设静态 IP），桌面版"服务器地址"配置依赖它不变。
2. **开机自启**：Docker Desktop → Settings → General 勾选 "Start Docker Desktop when you sign in"；compose 各服务已带 `restart: unless-stopped`，登录后自动恢复。建议给这台机器配一个自动登录的专用账户。
3. **防火墙放行**：首次启动 Docker 后 Windows 防火墙若提示，允许 8080 端口入站（ nginx ）；或 PowerShell 管理员执行：
   ```powershell
   New-NetFirewallRule -DisplayName "ALN-Data-8080" -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow
   ```
4. **完全离线（不能联网）的机器**：在能联网的机器上构建镜像后导出，U 盘拷入导入：
   ```powershell
   # 能联网的机器（构建完成后）
   docker save -o aln-images.tar deploy-api deploy-worker postgres:15-alpine redis:7-alpine deploy-nginx
   # 离线服务器
   docker load -i aln-images.tar
   ```
   前端 `dist/` 同样在外部机器 `npm run build` 后拷贝。
5. **磁盘**：`.env` 的 `DATA_ROOT` 指向大容量数据盘（如 `D:\aln-data`）；原始 snp 集中保存在此，桌面版回传后也汇聚到这里。
```

`docs/operations.md` 备份章节加一句：

```markdown
内网服务器部署时，建议用 Windows 任务计划程序把 pg_dump 与 DATA_ROOT 文件备份做成每日任务；桌面版只是缓存，服务器是唯一事实来源。
```

- [ ] **步骤 2：Commit**

```bash
git add docs/deployment-windows.md docs/operations.md
git commit -m "docs(sync): 内网服务器长期运行与离线部署指引"
```

---

### 任务 12：端到端集成测试（integration 标记）

**文件：**
- 新建：`backend/tests/test_server_sync_e2e.py`

- [ ] **步骤 1：编写 e2e 测试**

```python
"""端到端：子进程起真实服务器( SQLite + eager Celery )，桌面服务经 HTTP 推送/拉取。

标记 integration：默认跳过；手动运行：
    cd backend && uv run pytest tests/test_server_sync_e2e.py -v -m integration
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Mapping
from app.services import desktop_sync_service as svc
from app.services.server_client import ServerClient, ServerConfig

pytestmark = pytest.mark.integration

SERVER_PORT = 18123


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("aln-server")
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{root / 'server.db'}",
        "DATA_ROOT": str(root / "data"),
        "WATCH_ENABLED": "false",
        "CELERY_TASK_ALWAYS_EAGER": "1",
        "ALN_DESKTOP_MODE": "false",
        "DOTENV_PATH": str(root / "none.env"),  # 不加载仓库 .env
    }
    backend_dir = Path(__file__).resolve().parent.parent
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir, env=env, check=True, capture_output=True,
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(SERVER_PORT)],
        cwd=backend_dir, env=env,
    )
    base = f"http://127.0.0.1:{SERVER_PORT}"
    try:
        for _ in range(120):
            try:
                if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("服务器子进程启动超时")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _desktop_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_push_then_pull_e2e(server, sample_zip, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    monkeypatch.setattr(svc, "POLL_INTERVAL_S", 0.5)

    batch_no = "E2E-PUSH-1"
    # 模拟本地已完成 ingest 的批次：DB 行 + files 目录（来自样例 zip）
    db = _desktop_session()
    mapping = Mapping(name="E2E-MAP", file_path="m.csv", entry_count=1)
    db.add(mapping)
    db.flush()
    from app.models import MappingEntry

    db.add(MappingEntry(mapping_id=mapping.id, mark="A1", has_pf=False))
    db.commit()
    files_dir = tmp_path / "files" / batch_no
    files_dir.mkdir(parents=True)
    with zipfile.ZipFile(sample_zip) as zf:
        zf.extractall(files_dir)
    batch = Batch(batch_no=batch_no, mapping_id=mapping.id, file_path="local.zip",
                  source="http", device_count=1)  # count 在服务器入库后校准
    db.add(batch)
    db.commit()

    client = ServerClient(ServerConfig(server_url=server, username="e2e"))
    try:
        # 推送（eager 服务器请求内完成入库）
        svc.ensure_mapping_on_server(db, client, batch)
        zip_path = svc.pack_batch_zip(batch, tmp_path / "staging" / f"{batch_no}.zip")
        result = client.push_batch(zip_path, {
            "batch_no": batch_no, "mapping_name": "E2E-MAP", "deembed": "false",
            "deembed_method": "default", "process_type": "AUTO", "uploaded_by": "e2e",
        })
        assert result["status"] in {"accepted", "already_synced"}
        if result["status"] == "accepted":
            svc.wait_task(client, int(result["task_id"]))
        # 用服务器真实计数校准后收尾（count 不一致保护路径由单测覆盖）
        batch.device_count = int(client.get_batch(batch_no)["device_count"])
        assert batch.device_count > 0
        db.commit()
        done = svc.finalize_synced_batch(db, client, batch_no)
        assert done["status"] == "synced"
        assert not files_dir.exists()
        assert db.scalar(select(Batch).where(Batch.batch_no == batch_no)).synced_at is not None

        # 第二台"新笔记本"拉快照
        other = _desktop_session()
        stats = svc.pull_from_server(other, client)
        assert stats["batches_added"] == 1
        pulled = other.scalar(select(Batch).where(Batch.batch_no == batch_no))
        assert pulled.source == "server"
    finally:
        client.close()
```

- [ ] **步骤 2：运行 e2e 验证**

运行：`cd backend && uv run pytest tests/test_server_sync_e2e.py -v -m integration`
预期：PASS（无样例 fixture 时 SKIP，属预期）
若本机有 `T8901P.01.zip` fixture 而推送失败，先检查子进程日志（fixture 的 stdout/stderr 已 capture，失败时打印 `proc.stdout`）。

- [ ] **步骤 3：全量回归**

运行：`cd backend && uv run pytest -v -x -q --ignore=tests/test_server_sync_e2e.py`
预期：全部 PASS（含 web_sync、devices、upload 既有测试）

- [ ] **步骤 4：Commit**

```bash
git add backend/tests/test_server_sync_e2e.py
git commit -m "test(sync): 内网服务器推送/拉取端到端集成测试"
```

---

## 完成标准

1. `uv run pytest` 全量通过（含新测试；integration 默认跳过）。
2. `uv run ruff check .` 无新增告警。
3. `npm run build` 前端构建通过。
4. 手工冒烟（有内网环境时）：桌面版配置服务器地址 → 从服务器同步 → 断开本地文件批次的曲线显示"需连接内网"→ 回传本地批次 → 文件清理、批次页出现 ☁️ 标记。
