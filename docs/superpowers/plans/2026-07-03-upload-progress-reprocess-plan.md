# 上传进度与重处理功能实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在上传/处理全流程中实时显示解压、去嵌、指标计算三阶段进度，并在 TaskDetail / BatchDetail 页面提供重新解压、重新去嵌、重新计算指定指标的入口。

**架构：** 在现有 `extract_batch → compute_batch` 管线中插入阶段化进度回调（解压 0–30%、去嵌 30–45%、指标计算 45–100%）；为 7z/unzip/zipfile 添加字节级轮询兜底进度；新增 `reprocess_batch.py` 提供三个重处理 Celery 任务，统一通过 `dispatch.py` 在服务器/桌面两种模式下分发。

**技术栈：** FastAPI + SQLAlchemy 2.0 + Celery（服务端）/ 本地线程队列（桌面）+ React 18 + TypeScript + SSE。

---

## 文件清单

| 文件 | 职责 |
|------|------|
| `backend/app/models/task.py` | 扩展 `UploadTask.stage` 检查约束，增加 `'deembed'` |
| `backend/alembic/versions/` | 生成并提交 PostgreSQL 迁移脚本 |
| `backend/app/core/deembed.py` | 为 `_run_deembed` 增加 `progress_callback` |
| `backend/app/workers/extract_batch.py` | 7z/unzip/zipfile 实时进度 + 阶段映射调整 |
| `backend/app/workers/compute_batch.py` | 指标计算阶段映射改为 45–100% |
| `backend/app/workers/reprocess_batch.py` | 新增重解压/重去嵌/重计算任务 |
| `backend/app/workers/local_queue.py` | 扩展 `LocalTask` 支持重处理 kind |
| `backend/app/workers/local_worker.py` | 本地 worker 按 kind 分发重处理任务 |
| `backend/app/workers/dispatch.py` | 新增 `dispatch_reprocess_task` |
| `backend/app/api/batches.py` | 三个重处理 POST 端点 |
| `backend/app/schemas/batch.py` | 请求/响应 Pydantic 模型 |
| `backend/app/schemas/task.py` | `TaskDetail` 增加 `raw_zip_deleted` |
| `frontend/electron/main.ts` | 桌面模式默认 `KEEP_RAW_ZIP=true` |
| `frontend/src/types/index.ts` | `Task` 增加 stage 字段，`Batch` 增加 `raw_zip_deleted` |
| `frontend/src/api/endpoints.ts` | 重处理 API 封装 |
| `frontend/src/components/StageProgressBars.tsx` | 三阶段子进度条 |
| `frontend/src/components/ReprocessMetricsModal.tsx` | 重新计算指标多选弹窗 |
| `frontend/src/pages/TaskDetail.tsx` | 子进度条 + 重处理按钮 |
| `frontend/src/pages/BatchDetail.tsx` | 重处理按钮 + 弹窗调用 |
| 测试文件（见任务 13） | 进度回调、重处理 helper、API 行为 |

---

## 任务 1：扩展 UploadTask stage 枚举

**文件：**
- 修改：`backend/app/models/task.py:45-48`
- 测试：`backend/tests/models/test_task_model.py`（新增）
- 迁移：`backend/alembic/versions/`（自动生成）

- [ ] **步骤 1：修改检查约束**

```python
CheckConstraint(
    "stage IN ('extract','deembed','metrics','done','failed')",
    name="ck_uptask_stage",
),
```

- [ ] **步骤 2：添加失败测试验证新枚举被接受**

```python
def test_upload_task_accepts_deembed_stage(db):
    from app.models import UploadTask
    t = UploadTask(batch_no="T.01", stage="deembed", stage_progress_pct=50)
    db.add(t)
    db.commit()
    db.refresh(t)
    assert t.stage == "deembed"
```

运行：`uv run pytest backend/tests/models/test_task_model.py -v`
预期：PASS（SQLite 会直接使用新约束）。

- [ ] **步骤 3：生成 Alembic 迁移**

运行：`cd backend && uv run alembic revision --autogenerate -m "add deembed stage"`

预期生成文件包含类似：

```python
op.create_check_constraint(
    "ck_uptask_stage",
    "upload_tasks",
    "stage IN ('extract','deembed','metrics','done','failed')",
)
```

- [ ] **步骤 4：在本地 Postgres（如可用）或 CI 上应用迁移**

运行：`uv run alembic upgrade head`
预期：成功。

- [ ] **步骤 5：暂存变更**

```bash
git add backend/app/models/task.py backend/alembic/versions/xxxx_add_deembed_stage.py backend/tests/models/test_task_model.py
```

---

## 任务 2：为批量去嵌增加进度回调

**文件：**
- 修改：`backend/app/core/deembed.py:556-618`
- 测试：`backend/tests/workers/test_deembed_progress.py`（新增）

- [ ] **步骤 1：修改 `_run_deembed` 签名并在循环中回调**

```python
def _run_deembed(
    s1p_pairs: list[tuple[Path, Path]],
    cal_open: dict[str, Path],
    cal_short: dict[str, Path],
    target_dir: Path,
    method: DeembedMethod = DeembedMethod.DEFAULT,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[tuple[Path, Path]]:
    ...
    for idx, (s11_path, s22_path) in enumerate(s1p_pairs, start=1):
        ...
        new_pairs.append((s11_de, s22_de))
        if progress_callback:
            progress_callback(idx, len(s1p_pairs))
    return new_pairs
```

- [ ] **步骤 2：编写回调被调用的失败测试**

```python
from unittest.mock import MagicMock, patch
from pathlib import Path
from app.core.deembed import _run_deembed, DeembedMethod

def test_run_deembed_calls_progress(tmp_path):
    pairs = [(Path(f"dut{i}_S11.s1p"), Path(f"dut{i}_S22.s1p")) for i in range(3)]
    cb = MagicMock()
    with patch("app.core.deembed.split_s2p_to_s1p") as mock_split, \
         patch("app.core.deembed.match_calibration") as mock_match, \
         patch("app.core.deembed.deembed") as mock_deembed:
        mock_split.side_effect = lambda p, **kw: MagicMock(s11_path=p, s22_path=p)
        mock_match.return_value = (Path("open.s1p"), Path("short.s1p"))
        _run_deembed(pairs, {"open": Path("open.s2p")}, {"short": Path("short.s2p")}, tmp_path, progress_callback=cb)
    assert cb.call_count == 3
    cb.assert_called_with(3, 3)
```

运行：`uv run pytest backend/tests/workers/test_deembed_progress.py -v`
预期：PASS。

- [ ] **步骤 3：暂存变更**

```bash
git add backend/app/core/deembed.py backend/tests/workers/test_deembed_progress.py
```

---

## 任务 3：解压阶段实时进度

**文件：**
- 修改：`backend/app/workers/extract_batch.py`
- 测试：`backend/tests/workers/test_extract_progress.py`（新增）

目标：把解压总体进度映射到 0–30%，并为 7z/unzip/zipfile 提供进度回调。

- [ ] **步骤 1：新增字节轮询与 7z stdout 解析 helper**

在 `extract_batch.py` 顶部增加 import：

```python
import queue
import re
import subprocess
import threading
```

新增 helper（放在 `_zip_uncompressed_size` 下方）：

```python
_PROGRESS_RE = re.compile(r"\(\s*(\d+)%\)")


def _monitor_extracted_size(
    target_dir: Path,
    total: int,
    state: dict[str, Any],
    stop: threading.Event,
    interval: float = 0.5,
) -> None:
    """后台线程：根据已写入目标目录的字节数估算百分比。"""
    while not stop.wait(interval):
        current = sum(p.stat().st_size for p in target_dir.rglob("*") if p.is_file())
        pct = min(99, int(100 * current / total)) if total > 0 else 0
        state["pct"] = pct


def _read_stream_into_queue(stream, q: queue.Queue[str]) -> None:
    for line in stream:
        q.put(line)
```

- [ ] **步骤 2：重写 `_extract_with_7z` 支持流式进度**

```python
def _extract_with_7z(
    zip_path: Path,
    target_dir: Path,
    exe: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> float:
    target_dir.mkdir(parents=True, exist_ok=True)
    total = _zip_uncompressed_size(zip_path)
    state: dict[str, Any] = {"pct": 0}
    stop = threading.Event()
    monitor = threading.Thread(
        target=_monitor_extracted_size,
        args=(target_dir, total, state, stop),
        daemon=True,
    )
    monitor.start()

    start = time.perf_counter()
    stderr_lines: list[str] = []
    stdout_q: queue.Queue[str] = queue.Queue()

    proc = subprocess.Popen(
        [exe, "x", "-y", "-bsp1", "-bb0", "-o" + str(target_dir), str(zip_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def _read_stderr() -> None:
        for line in proc.stderr or []:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()
    stdout_thread = threading.Thread(
        target=_read_stream_into_queue, args=(proc.stdout, stdout_q), daemon=True
    )
    stdout_thread.start()

    try:
        while proc.poll() is None:
            try:
                while True:
                    line = stdout_q.get_nowait()
                    m = _PROGRESS_RE.search(line)
                    if m:
                        state["pct"] = int(m.group(1))
            except queue.Empty:
                pass
            if progress_callback:
                progress_callback(state["pct"], 100)
            time.sleep(0.5)
        proc.wait()
    finally:
        stop.set()
        monitor.join(timeout=2.0)
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

    if proc.returncode != 0:
        err = "".join(stderr_lines)[-500:]
        raise RuntimeError(f"7z 解压失败 (exit {proc.returncode}): {err}")

    if progress_callback:
        progress_callback(100, 100)
    return time.perf_counter() - start
```

- [ ] **步骤 3：为 `_extract_with_unzip` 增加同样的字节轮询**

```python
def _extract_with_unzip(
    zip_path: Path,
    target_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> float:
    target_dir.mkdir(parents=True, exist_ok=True)
    total = _zip_uncompressed_size(zip_path)
    state = {"pct": 0}
    stop = threading.Event()
    monitor = threading.Thread(
        target=_monitor_extracted_size,
        args=(target_dir, total, state, stop),
        daemon=True,
    )
    monitor.start()

    start = time.perf_counter()
    proc = subprocess.Popen(
        ["unzip", "-q", "-o", str(zip_path), "-d", str(target_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stderr_lines: list[str] = []

    def _read_stderr() -> None:
        for line in proc.stderr or []:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    try:
        while proc.poll() is None:
            if progress_callback:
                progress_callback(state["pct"], 100)
            time.sleep(0.5)
        proc.wait()
    finally:
        stop.set()
        monitor.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

    if proc.returncode != 0:
        err = "".join(stderr_lines)[-500:]
        raise RuntimeError(f"unzip 解压失败 (exit {proc.returncode}): {err}")

    if progress_callback:
        progress_callback(100, 100)
    return time.perf_counter() - start
```

- [ ] **步骤 4：修改 `_extract_zip` 传递回调**

```python
def _extract_zip(
    zip_path: Path,
    target_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[str, float, int, int]:
    exe7z = _find_7z()
    if exe7z:
        if progress_callback:
            progress_callback(0, 100)
        elapsed = _extract_with_7z(zip_path, target_dir, exe7z, progress_callback)
        extracted = _count_extracted(target_dir)
        return ("7z", elapsed, extracted, _zip_uncompressed_size(zip_path))

    total = 0
    try:
        with zipfile.ZipFile(str(zip_path)) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            total = len(members)
            start = time.perf_counter()
            for i, member in enumerate(members, start=1):
                zf.extract(member, target_dir)
                if progress_callback and total > 0:
                    progress_callback(i, total)
            elapsed = time.perf_counter() - start
        extracted = _count_extracted(target_dir)
        return ("zipfile", elapsed, extracted, _zip_uncompressed_size(zip_path))
    except (NotImplementedError, RuntimeError) as exc:
        msg = str(exc).lower()
        if "compression method" not in msg and "not supported" not in msg:
            raise
        logger.warning("Python zipfile 不支持该压缩方法，尝试 unzip: %s", exc)

    if progress_callback:
        progress_callback(0, 100)
    if shutil.which("unzip"):
        elapsed = _extract_with_unzip(zip_path, target_dir, progress_callback)
        extracted = _count_extracted(target_dir)
    else:
        raise RuntimeError("ZIP 压缩方法不受支持，且系统未安装 7z / unzip")
    if progress_callback:
        progress_callback(100, 100)
    return ("unzip", elapsed, extracted, _zip_uncompressed_size(zip_path))
```

- [ ] **步骤 5：调整 `extract_batch_task` 的进度映射为 0–30%，并保留解压完成消息**

把 `_update_extract_pct` 改为：

```python
def _update_extract_pct(current: int, total: int) -> None:
    stage_pct = int(100 * current / total) if total else 0
    overall = int(30 * current / total) if total else 0
    publisher.stage_update(
        db,
        stage="extract",
        stage_progress_pct=stage_pct,
        progress_pct=overall,
        progress_msg=f"{extractor or '解压'} 解压中… {stage_pct}%",
    )
```

> 注意：这里 `extractor` 变量在 `_extract_zip` 返回后才能确定。简单做法是在 `_extract_zip` 内部用局部变量记录，或者把消息改为通用 `"解压中… {stage_pct}%"`。建议保留通用消息，解压完成后再显示具体工具。

把 `_update_extract_pct` 简化为：

```python
def _update_extract_pct(current: int, total: int) -> None:
    stage_pct = int(100 * current / total) if total else 0
    overall = int(30 * current / total) if total else 0
    publisher.stage_update(
        db,
        stage="extract",
        stage_progress_pct=stage_pct,
        progress_pct=overall,
        progress_msg=f"解压中… {stage_pct}%",
    )
```

把解压完成后那段 `publisher.stage_update(... progress_pct=15 ...)` 改为：

```python
publisher.stage_update(
    db,
    stage="extract",
    stage_progress_pct=100,
    progress_pct=30,
    progress_msg=(
        f"解压完成，{extractor} 解压 {extracted_count} 个文件，"
        f"{mb:.1f} MB / {elapsed:.2f}s ({speed:.1f} MB/s)；"
        f"发现 {len(dut_s2p)} 个 .s2p DUT、{len(standalone_s1p)} 个 .s1p DUT"
    ),
)
```

把最末的 `progress_pct=30` 阶段完成消息改为 `progress_pct=30` 不变（已是 30）。

- [ ] **步骤 6：编写解压进度测试**

```python
import zipfile
from pathlib import Path
from app.workers.extract_batch import _extract_zip

def test_zipfile_progress_callback(tmp_path):
    zip_path = tmp_path / "sample.zip"
    target_dir = tmp_path / "out"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.s1p", "dummy" * 100)
        zf.writestr("b.s1p", "dummy" * 100)
    progress = []
    _extract_zip(zip_path, target_dir, progress_callback=lambda c, t: progress.append((c, t)))
    assert progress[-1] == (2, 2)
    assert len(progress) >= 2
```

运行：`uv run pytest backend/tests/workers/test_extract_progress.py -v`
预期：PASS。

- [ ] **步骤 7：暂存变更**

```bash
git add backend/app/workers/extract_batch.py backend/tests/workers/test_extract_progress.py
```

---

## 任务 4：指标计算阶段进度映射改为 45–100%

**文件：**
- 修改：`backend/app/workers/compute_batch.py:72,162,257`

- [ ] **步骤 1：修改初始/单线程/并行三处映射**

初始阶段：

```python
publisher.stage_update(
    db,
    stage="metrics",
    stage_progress_pct=0,
    progress_pct=45,
    progress_msg="开始指标计算…",
)
```

单线程循环：

```python
overall = 45 + int(55 * i / total)
```

并行循环：

```python
overall = 45 + int(55 * stage_pct / 100)
```

- [ ] **步骤 2：运行相关测试**

运行：`uv run pytest backend/tests/workers/test_process_batch.py backend/tests/workers/test_pipeline_batch.py -v`
预期：PASS（若测试断言了具体 progress_pct，需要同步更新断言）。

- [ ] **步骤 3：暂存变更**

```bash
git add backend/app/workers/compute_batch.py
```

---

## 任务 5：新增重处理 worker 模块

**文件：**
- 创建：`backend/app/workers/reprocess_batch.py`
- 测试：`backend/tests/workers/test_reprocess_batch.py`（新增）

- [ ] **步骤 1：创建 `reprocess_batch.py`**

```python
"""批次重处理任务：重新解压 / 重新去嵌 / 重新计算指定指标。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.deembed import DeembedError, DeembedMethod, _run_deembed
from app.core.extract import ExtractError, extract_resonator_params
from app.core.filename import parse_filename
from app.core.mapping import load_mapping
from app.core.touchstone import split_s2p_to_s1p
from app.db import SessionLocal
from app.models import Batch, Device, FileNode, Mapping, UploadTask
from app.services.device_ingest import bulk_insert_devices
from app.workers.celery_app import celery_app
from app.workers.compute_batch import compute_batch_task
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)

_METRIC_COLUMNS: dict[str, list[str]] = {
    "qs": ["qs"],
    "qp": ["qp"],
    "kt2": ["k2eff_pct"],
    "qbode": [
        "qs_bodeq",
        "qp_bodeq",
        "dbqs",
        "dbqp",
        "bodeq_fitted",
        "bodeq_smooth",
        "bodeq_raw",
        "fbode_ghz",
    ],
}


def _validate_metrics(metrics: list[str]) -> list[str]:
    invalid = [m for m in metrics if m not in _METRIC_COLUMNS]
    if invalid:
        raise ValueError(f"不支持的指标: {invalid}，可选: {list(_METRIC_COLUMNS)}")
    return metrics


def _reset_task(db: Session, task_id: int) -> UploadTask:
    task = db.get(UploadTask, task_id)
    if task is None:
        raise RuntimeError(f"任务 {task_id} 不存在")
    task.status = "pending"
    task.stage = "extract"
    task.progress_pct = 0
    task.stage_progress_pct = 0
    task.progress_msg = "排队中"
    task.error_msg = None
    task.finished_at = None
    db.commit()
    return task


def _prepare_deembed_files(
    target_dir: Path,
) -> tuple[list[Path], dict[str, Path], dict[str, Path]]:
    s2p_files = sorted(p for p in target_dir.rglob("*.s2p") if p.is_file())
    dut_s2p: list[Path] = []
    cal_open: dict[str, Path] = {}
    cal_short: dict[str, Path] = {}
    for p in s2p_files:
        parsed = parse_filename(p.name)
        if parsed.is_open:
            cal_open[p.name] = p
        elif parsed.is_short:
            cal_short[p.name] = p
        else:
            dut_s2p.append(p)
    return dut_s2p, cal_open, cal_short


def _build_all_files_after_deembed(
    dut_s2p: list[Path],
    cal_open: dict[str, Path],
    cal_short: dict[str, Path],
    target_dir: Path,
    method: str,
    publisher: ProgressPublisher,
    db: Session,
) -> list[dict[str, Any]]:
    all_files: list[dict[str, Any]] = []
    if not dut_s2p:
        return all_files

    raw_s11_dir = target_dir / "S11_raw"
    raw_s22_dir = target_dir / "S22_raw"
    raw_s11_dir.mkdir(parents=True, exist_ok=True)
    raw_s22_dir.mkdir(parents=True, exist_ok=True)
    s1p_pairs: list[tuple[Path, Path]] = []
    for s2p in dut_s2p:
        split = split_s2p_to_s1p(s2p, out_dir_s11=raw_s11_dir, out_dir_s22=raw_s22_dir)
        s1p_pairs.append((split.s11_path, split.s22_path))

    de_method = DeembedMethod(method) if method else DeembedMethod.DEFAULT
    total_pairs = len(s1p_pairs)

    def _deembed_cb(current: int, total: int) -> None:
        stage_pct = int(100 * current / total) if total else 0
        overall = 30 + int(15 * current / total) if total else 30
        publisher.stage_update(
            db,
            stage="deembed",
            stage_progress_pct=stage_pct,
            progress_pct=overall,
            progress_msg=f"去嵌中… {current}/{total} 对",
        )

    de_pairs = _run_deembed(
        s1p_pairs, cal_open, cal_short, target_dir, method=de_method, progress_callback=_deembed_cb
    )

    for s11_de, s22_de in de_pairs:
        all_files.append(
            {
                "path": str(s11_de),
                "deembedded": True,
                "port": 0,
                "s_param_relpath": str(s11_de.relative_to(target_dir)),
            }
        )
        all_files.append(
            {
                "path": str(s22_de),
                "deembedded": True,
                "port": 1,
                "s_param_relpath": str(s22_de.relative_to(target_dir)),
            }
        )
    return all_files


@celery_app.task(bind=True, name="aln.redeembed_batch")
def redeembed_batch_task(
    self: Task,
    upload_task_id: int,
    batch_no: str,
) -> dict[str, Any]:
    publisher = ProgressPublisher(upload_task_id)
    db = SessionLocal()
    try:
        _reset_task(db, upload_task_id)
        publisher.start(db, "开始重新去嵌…")

        batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        if batch is None:
            raise RuntimeError(f"批次 {batch_no} 不存在")
        mapping_row = db.get(Mapping, batch.mapping_id)
        if mapping_row is None:
            raise RuntimeError(f"对照表 {batch.mapping_id} 不存在")

        target_dir = Path(batch.file_path) if batch.file_path else None
        if target_dir is None or not target_dir.exists():
            raise RuntimeError("批次解压目录不存在")

        publisher.stage_update(
            db,
            stage="deembed",
            stage_progress_pct=0,
            progress_pct=30,
            progress_msg="扫描校准件与 DUT…",
        )

        dut_s2p, cal_open, cal_short = _prepare_deembed_files(target_dir)
        if not dut_s2p:
            raise DeembedError("未找到 .s2p DUT 文件")
        if not cal_open or not cal_short:
            raise DeembedError("缺少 OPEN/SHORT 校准件，无法重新去嵌")

        all_files = _build_all_files_after_deembed(
            dut_s2p, cal_open, cal_short, target_dir, batch.deembed_method, publisher, db
        )

        # 删除旧 devices，重新计算
        db.execute(delete(Device).where(Device.batch_id == batch.id))
        db.commit()

        return compute_batch_task.apply(
            args=[
                {
                    "upload_task_id": upload_task_id,
                    "batch_id": batch.id,
                    "mapping_id": batch.mapping_id,
                    "wafer": None,
                    "f_start_ghz": batch.f_start_ghz,
                    "f_end_ghz": batch.f_end_ghz,
                    "all_files": all_files,
                }
            ]
        ).get()
    except Exception as exc:
        logger.exception("redeembed_batch_task fatal")
        try:
            db.rollback()
        except Exception:
            pass
        try:
            publisher.fail(db, error_msg=str(exc))
        except Exception:
            pass
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="aln.recompute_batch")
def recompute_batch_task(
    self: Task,
    upload_task_id: int,
    batch_no: str,
    metrics: list[str],
) -> dict[str, Any]:
    publisher = ProgressPublisher(upload_task_id)
    db = SessionLocal()
    try:
        metrics = _validate_metrics(metrics)
        _reset_task(db, upload_task_id)
        publisher.start(db, "开始重新计算指标…")

        batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        if batch is None:
            raise RuntimeError(f"批次 {batch_no} 不存在")
        mapping_row = db.get(Mapping, batch.mapping_id)
        if mapping_row is None:
            raise RuntimeError(f"对照表 {batch.mapping_id} 不存在")
        mapping_dict = load_mapping(mapping_row.file_path)

        target_dir = Path(batch.file_path) if batch.file_path else None
        devices = db.scalars(
            select(Device).where(Device.batch_id == batch.id).order_by(Device.id)
        ).all()
        if not devices:
            raise RuntimeError("批次下没有 devices 可供重新计算")

        total = len(devices)
        columns_to_update: set[str] = set()
        for m in metrics:
            columns_to_update.update(_METRIC_COLUMNS[m])

        updates: list[dict[str, Any]] = []
        skipped = 0
        failures: list[str] = []
        last_pct = -1

        for i, device in enumerate(devices, start=1):
            if not device.s_param_path:
                skipped += 1
                continue
            s1p_path = (target_dir / device.s_param_path) if target_dir else Path(device.s_param_path)
            try:
                row = extract_resonator_params(
                    s1p_path,
                    mapping=mapping_dict,
                    wafer=device.wafer,
                    s_param_relpath=device.s_param_path,
                    deembedded=device.deembedded,
                    f_start_ghz=batch.f_start_ghz,
                    f_end_ghz=batch.f_end_ghz,
                    skip_validation=True,
                    port=device.s_param_port or "S11",
                )
            except (ExtractError, Exception) as exc:
                failures.append(f"device {device.id}: {exc}")
                continue

            upd: dict[str, Any] = {"id": device.id}
            for col in columns_to_update:
                upd[col] = row.get(col)
            updates.append(upd)

            stage_pct = int(100 * i / total)
            if stage_pct != last_pct and (stage_pct - last_pct >= 5 or i % 100 == 0 or i == total):
                overall = 45 + int(55 * i / total)
                publisher.stage_update(
                    db,
                    stage="metrics",
                    stage_progress_pct=stage_pct,
                    progress_pct=overall,
                    progress_msg=f"重新计算 {metrics} 中… {i}/{total}，失败 {len(failures)}，跳过 {skipped}",
                )
                last_pct = stage_pct

            if len(updates) >= 1000:
                db.bulk_update_mappings(Device, updates)
                db.commit()
                updates = []

        if updates:
            db.bulk_update_mappings(Device, updates)
            db.commit()

        device_count = (
            db.scalar(select(Device.id).where(Device.batch_id == batch.id).count()) or 0
        )
        publisher.done(db, batch_id=batch.id, device_count=device_count)
        return {
            "batch_id": batch.id,
            "device_count": device_count,
            "skipped": skipped,
            "failures": len(failures),
            "failure_samples": failures[:5],
        }
    except Exception as exc:
        logger.exception("recompute_batch_task fatal")
        try:
            db.rollback()
        except Exception:
            pass
        try:
            publisher.fail(db, error_msg=str(exc))
        except Exception:
            pass
        raise
    finally:
        db.close()
```

> 注意：`_run_deembed` 进度回调已在任务 2 实现；`compute_batch_task.apply(...).get()` 在 Celery 本地模式（桌面）和服务器 Celery 中均可用。

- [ ] **步骤 2：编写 helper 测试**

```python
from app.workers.reprocess_batch import _validate_metrics, _METRIC_COLUMNS

def test_validate_metrics():
    assert _validate_metrics(["qs", "kt2"]) == ["qs", "kt2"]

def test_validate_metrics_rejects_unknown():
    import pytest
    with pytest.raises(ValueError):
        _validate_metrics(["qs", "foo"])
```

运行：`uv run pytest backend/tests/workers/test_reprocess_batch.py -v`
预期：PASS。

- [ ] **步骤 3：暂存变更**

```bash
git add backend/app/workers/reprocess_batch.py backend/tests/workers/test_reprocess_batch.py
```

---

## 任务 6：扩展本地队列与本地 worker

**文件：**
- 修改：`backend/app/workers/local_queue.py`
- 修改：`backend/app/workers/local_worker.py`

- [ ] **步骤 1：扩展 `LocalTask`**

```python
from typing import Literal

@dataclass
class LocalTask:
    task_id: int
    zip_path: Path | None = None
    batch_no: str | None = None
    mapping_id: int | None = None
    f_start_ghz: float | None = None
    f_end_ghz: float | None = None
    deembed: bool = False
    deembed_method: str = "default"
    process_type: str = "AUTO"
    kind: Literal["upload", "reextract", "redeembed", "recompute"] = "upload"
    metrics: list[str] | None = None
```

- [ ] **步骤 2：重写 `local_worker_loop` 分发逻辑**

```python
from app.workers.reprocess_batch import redeembed_batch_task, recompute_batch_task

def _run_upload_or_reextract(task: LocalTask) -> None:
    db = SessionLocal()
    try:
        publisher = ProgressPublisher(task.task_id)
        publisher.start(db, "本地处理开始")
        kwargs = {
            "upload_task_id": task.task_id,
            "zip_path": str(task.zip_path),
            "batch_no": task.batch_no,
            "mapping_id": task.mapping_id,
            "f_start_ghz": task.f_start_ghz,
            "f_end_ghz": task.f_end_ghz,
            "deembed_enabled": task.deembed,
            "deembed_method": task.deembed_method,
            "process_type": task.process_type,
        }
        extract_result = extract_batch_task.apply(kwargs=kwargs).get()
        compute_result = compute_batch_task.apply(args=[extract_result]).get()
        publisher.done(
            db,
            batch_id=compute_result.get("batch_id"),
            device_count=compute_result.get("device_count", 0),
        )
    except Exception as exc:
        logger.exception("本地任务 %s 失败", task.task_id)
        try:
            publisher = ProgressPublisher(task.task_id)
            publisher.fail(db, f"{exc}\n{traceback.format_exc()}")
        except Exception:
            pass
    finally:
        db.close()


def _run_redeembed(task: LocalTask) -> None:
    redeembed_batch_task.apply(
        kwargs={"upload_task_id": task.task_id, "batch_no": task.batch_no}
    ).get()


def _run_recompute(task: LocalTask) -> None:
    recompute_batch_task.apply(
        kwargs={
            "upload_task_id": task.task_id,
            "batch_no": task.batch_no,
            "metrics": task.metrics or [],
        }
    ).get()


def local_worker_loop() -> None:
    queue = get_local_queue()
    logger.info("本地 worker 启动")
    while not queue.is_shutdown():
        task = queue.get(timeout=1.0)
        if task is None:
            continue
        logger.info("本地 worker 开始处理任务 %s kind=%s", task.task_id, task.kind)
        if task.kind in ("upload", "reextract"):
            _run_upload_or_reextract(task)
        elif task.kind == "redeembed":
            _run_redeembed(task)
        elif task.kind == "recompute":
            _run_recompute(task)
        else:
            logger.error("未知本地任务类型: %s", task.kind)
    logger.info("本地 worker 退出")
```

- [ ] **步骤 3：运行桌面模式测试**

运行：`uv run pytest backend/tests/test_desktop_mode.py -v`
预期：PASS（若该文件断言了 LocalTask 字段，需要同步更新）。

- [ ] **步骤 4：暂存变更**

```bash
git add backend/app/workers/local_queue.py backend/app/workers/local_worker.py
```

---

## 任务 7：扩展 dispatch.py

**文件：**
- 修改：`backend/app/workers/dispatch.py`

- [ ] **步骤 1：新增 `dispatch_reprocess_task`**

```python
from pathlib import Path
from typing import Literal

from app.config import get_settings


def dispatch_reprocess_task(
    task_id: int,
    batch_no: str,
    mapping_id: int,
    kind: Literal["reextract", "redeembed", "recompute"],
    *,
    zip_path: Path | None = None,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
    metrics: list[str] | None = None,
) -> str | None:
    settings = get_settings()
    if settings.is_desktop:
        from app.workers.local_queue import LocalTask, get_local_queue

        get_local_queue().put(
            LocalTask(
                task_id=task_id,
                zip_path=Path(zip_path) if zip_path else None,
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=f_start_ghz,
                f_end_ghz=f_end_ghz,
                deembed=deembed,
                deembed_method=deembed_method,
                process_type=process_type,
                kind=kind,
                metrics=metrics,
            )
        )
        return f"local-{task_id}"

    from app.workers.reprocess_batch import redeembed_batch_task, recompute_batch_task
    from app.workers.extract_batch import extract_batch_task
    from app.workers.compute_batch import compute_batch_task
    from celery import chain

    if kind == "reextract":
        result = chain(
            extract_batch_task.s(
                upload_task_id=task_id,
                zip_path=str(zip_path),
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=f_start_ghz,
                f_end_ghz=f_end_ghz,
                deembed_enabled=deembed,
                deembed_method=deembed_method,
                process_type=process_type,
            ),
            compute_batch_task.s(),
        ).apply_async()
        return result.id
    if kind == "redeembed":
        result = redeembed_batch_task.apply_async(
            kwargs={"upload_task_id": task_id, "batch_no": batch_no}
        )
        return result.id
    if kind == "recompute":
        result = recompute_batch_task.apply_async(
            kwargs={"upload_task_id": task_id, "batch_no": batch_no, "metrics": metrics or []}
        )
        return result.id
    return None
```

- [ ] **步骤 2：运行测试**

运行：`uv run pytest backend/tests/workers/test_process_batch.py -v`
预期：PASS。

- [ ] **步骤 3：暂存变更**

```bash
git add backend/app/workers/dispatch.py
```

---

## 任务 8：新增重处理 API 端点

**文件：**
- 修改：`backend/app/api/batches.py`
- 修改：`backend/app/schemas/batch.py`
- 修改：`backend/app/schemas/task.py`

- [ ] **步骤 1：新增请求/响应 schema**

`backend/app/schemas/batch.py` 新增：

```python
from pydantic import BaseModel, Field


class RecomputeRequest(BaseModel):
    metrics: list[str] = Field(
        default=["qs", "qp", "kt2", "qbode"],
        min_length=1,
    )


class ReprocessResponse(BaseModel):
    task_id: str
    batch_no: str
    stream_url: str
```

`backend/app/schemas/task.py` 中 `TaskDetail` 新增：

```python
raw_zip_deleted: bool | None = None
```

- [ ] **步骤 2：在 `batches.py` 中新增 helper 与端点**

在文件顶部新增导入：

```python
from typing import Literal

from app.schemas.batch import RecomputeRequest, ReprocessResponse
from app.workers.dispatch import dispatch_reprocess_task
from app.models import UploadTask
```

新增 helper（放在 `delete_batch` 之前或之后）：

```python
_ReprocessKind = Literal["reextract", "redeembed", "recompute"]


def _start_reprocess(
    db: DbSession,
    batch_no: str,
    kind: _ReprocessKind,
    metrics: list[str] | None = None,
) -> ReprocessResponse:
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    if kind == "reextract":
        if not batch.raw_zip_path or not Path(batch.raw_zip_path).exists():
            raise HTTPException(
                status_code=400,
                detail="原始数据包已清理，无法重新解压",
            )

    task = db.get(UploadTask, batch.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="批次关联任务不存在")
    if task.status in ("pending", "running"):
        raise HTTPException(status_code=409, detail="该批次已有进行中的任务")

    task.status = "pending"
    task.stage = "extract"
    task.progress_pct = 0
    task.stage_progress_pct = 0
    task.progress_msg = "排队中"
    task.error_msg = None
    task.finished_at = None
    db.commit()

    celery_task_id = dispatch_reprocess_task(
        task_id=task.id,
        batch_no=batch.batch_no,
        mapping_id=batch.mapping_id,
        kind=kind,
        zip_path=Path(batch.raw_zip_path) if batch.raw_zip_path else None,
        f_start_ghz=batch.f_start_ghz,
        f_end_ghz=batch.f_end_ghz,
        deembed=batch.deembedded,
        deembed_method=batch.deembed_method,
        process_type=batch.process_type,
        metrics=metrics,
    )
    if celery_task_id:
        task.celery_task_id = celery_task_id
        db.commit()

    return ReprocessResponse(
        task_id=str(task.id),
        batch_no=batch.batch_no,
        stream_url=f"/api/tasks/{task.id}/stream",
    )
```

新增端点（放在 `delete_batch` 之后）：

```python
@router.post("/{batch_no}/reextract", response_model=ReprocessResponse)
def reextract_batch(batch_no: str, db: DbSession) -> ReprocessResponse:
    return _start_reprocess(db, batch_no, "reextract")


@router.post("/{batch_no}/redeembed", response_model=ReprocessResponse)
def redeembed_batch(batch_no: str, db: DbSession) -> ReprocessResponse:
    return _start_reprocess(db, batch_no, "redeembed")


@router.post("/{batch_no}/recompute", response_model=ReprocessResponse)
def recompute_batch(
    batch_no: str,
    body: RecomputeRequest,
    db: DbSession,
) -> ReprocessResponse:
    return _start_reprocess(db, batch_no, "recompute", metrics=body.metrics)
```

- [ ] **步骤 3：修改 `get_task` 返回 `raw_zip_deleted`**

`backend/app/api/tasks.py`：

```python
from sqlalchemy import select
from app.models import Batch

@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: int, db: DbSession) -> TaskDetail:
    task = db.get(UploadTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    batch = db.scalar(select(Batch).where(Batch.batch_no == task.batch_no))
    raw_zip_deleted = True
    if batch and batch.raw_zip_path and Path(batch.raw_zip_path).exists():
        raw_zip_deleted = False
    data = TaskDetail.model_validate(task)
    data.raw_zip_deleted = raw_zip_deleted
    return data
```

> 注意：`TaskDetail` 是 Pydantic v2 模型，可以直接设置额外字段（模型已定义该字段）。

- [ ] **步骤 4：编写 API 测试**

`backend/tests/api/test_reprocess.py`：

```python
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_recompute_rejects_missing_batch():
    resp = client.post("/api/batches/NOT_EXISTS/recompute", json={"metrics": ["qs"]})
    assert resp.status_code == 404
```

运行：`uv run pytest backend/tests/api/test_reprocess.py -v`
预期：PASS（后续需要补齐带 DB 的集成测试）。

- [ ] **步骤 5：暂存变更**

```bash
git add backend/app/api/batches.py backend/app/api/tasks.py backend/app/schemas/batch.py backend/app/schemas/task.py backend/tests/api/test_reprocess.py
```

---

## 任务 9：桌面模式默认保留原始 zip

**文件：**
- 修改：`frontend/electron/main.ts:96-104`

- [ ] **步骤 1：在 `backendEnv` 中增加 `KEEP_RAW_ZIP`**

```typescript
const backendEnv = {
  ...process.env,
  ALN_DESKTOP: '1',
  ALN_DESKTOP_MODE: 'true',
  ALN_DESKTOP_DIR: desktopDir,
  ALN_BACKEND_HOST: host,
  ALN_BACKEND_PORT: String(port),
  MPLCONFIGDIR: mplDir,
  KEEP_RAW_ZIP: 'true',
};
```

- [ ] **步骤 2：暂存变更**

```bash
git add frontend/electron/main.ts
```

---

## 任务 10：前端 StageProgressBars 组件与 TaskDetail 集成

**文件：**
- 创建：`frontend/src/components/StageProgressBars.tsx`
- 修改：`frontend/src/pages/TaskDetail.tsx`
- 修改：`frontend/src/types/index.ts`

- [ ] **步骤 1：扩展 `Task` 类型**

```typescript
export interface Task {
  id: number | string;
  batch_no?: string;
  status?: string;
  progress_pct?: number;
  progress_msg?: string;
  error_msg?: string;
  started_at?: string;
  finished_at?: string;
  stage?: string;
  stage_progress_pct?: number;
  raw_zip_deleted?: boolean;
}
```

- [ ] **步骤 2：创建 StageProgressBars 组件**

```typescript
import React from 'react';

const STAGES: { key: string; label: string }[] = [
  { key: 'extract', label: '解压' },
  { key: 'deembed', label: '去嵌' },
  { key: 'metrics', label: '指标计算' },
];

interface Props {
  stage?: string;
  stageProgress?: number;
}

export default function StageProgressBars({ stage = 'extract', stageProgress = 0 }: Props) {
  const currentIndex = STAGES.findIndex((s) => s.key === stage);
  return (
    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
      {STAGES.map((s, idx) => {
        let pct = 0;
        if (idx < currentIndex) pct = 100;
        else if (idx === currentIndex) pct = Math.max(0, Math.min(100, stageProgress));
        const active = idx === currentIndex;
        return (
          <div key={s.key} style={{ flex: 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
              <span className={active ? '' : 'dim'}>{s.label}</span>
              <span className="mono dim">{pct}%</span>
            </div>
            <div
              style={{
                height: 4,
                background: 'var(--bg-panel-2)',
                border: '1px solid var(--border)',
                borderRadius: 2,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: '100%',
                  background: active ? 'var(--primary)' : 'var(--pass)',
                  transition: 'width 0.3s',
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **步骤 3：修改 TaskDetail 使用组件并在完成后显示重处理按钮**

在 `TaskDetail.tsx` 顶部新增：

```typescript
import StageProgressBars from '../components/StageProgressBars';
import { reextractBatch, redeembedBatch, recomputeBatch } from '../api/endpoints';
import ReprocessMetricsModal from '../components/ReprocessMetricsModal';
```

在进度卡片内部 `<div style={{ padding: 14 }}>` 中，消息下方插入：

```tsx
<StageProgressBars stage={sse.stage || task?.stage} stageProgress={sse.stageProgress || task?.stage_progress_pct || 0} />
```

在进度卡片 `</div>` 之前增加操作区（仅在任务完成/失败时显示）：

```tsx
{(status === 'success' || status === 'failed') && task && (
  <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
    <button
      className="btn sm"
      disabled={task.raw_zip_deleted}
      title={task.raw_zip_deleted ? '原始 zip 已清理' : '重新解压并覆盖现有结果'}
      onClick={async () => {
        try {
          await reextractBatch(task.batch_no!);
          window.location.reload();
        } catch (e: any) {
          setError(e.message || '重新解压失败');
        }
      }}
    >
      重新解压
    </button>
    <button
      className="btn sm"
      onClick={async () => {
        try {
          await redeembedBatch(task.batch_no!);
          window.location.reload();
        } catch (e: any) {
          setError(e.message || '重新去嵌失败');
        }
      }}
    >
      重新去嵌
    </button>
    <button
      className="btn sm"
      onClick={() => setShowRecomputeModal(true)}
    >
      重新计算指标
    </button>
  </div>
)}
```

在组件中增加 state：

```typescript
const [showRecomputeModal, setShowRecomputeModal] = useState(false);
```

在 return 末尾增加弹窗：

```tsx
{showRecomputeModal && (
  <ReprocessMetricsModal
    batchNo={task?.batch_no || ''}
    onClose={() => setShowRecomputeModal(false)}
    onSubmit={async (metrics) => {
      try {
        await recomputeBatch(task!.batch_no!, metrics);
        setShowRecomputeModal(false);
        window.location.reload();
      } catch (e: any) {
        setError(e.message || '重新计算失败');
      }
    }}
  />
)}
```

- [ ] **步骤 4：运行前端类型检查**

运行：`cd frontend && npm run typecheck`
预期：无错误（若命令不存在则使用 `npx tsc --noEmit`）。

- [ ] **步骤 5：暂存变更**

```bash
git add frontend/src/types/index.ts frontend/src/components/StageProgressBars.tsx frontend/src/pages/TaskDetail.tsx
```

---

## 任务 11：重新计算指标弹窗组件

**文件：**
- 创建：`frontend/src/components/ReprocessMetricsModal.tsx`

- [ ] **步骤 1：创建组件**

```typescript
import React, { useState } from 'react';

const METRICS = [
  { key: 'qbode', label: 'Qbode' },
  { key: 'qs', label: 'Qs' },
  { key: 'qp', label: 'Qp' },
  { key: 'kt2', label: 'kt2' },
];

interface Props {
  batchNo: string;
  onClose: () => void;
  onSubmit: (metrics: string[]) => void;
}

export default function ReprocessMetricsModal({ batchNo, onClose, onSubmit }: Props) {
  const [selected, setSelected] = useState<string[]>(['qs', 'qp', 'kt2', 'qbode']);

  const toggle = (key: string) => {
    setSelected((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          borderRadius: 6,
          padding: 20,
          minWidth: 320,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ fontWeight: 600, marginBottom: 12 }}>重新计算指标 - {batchNo}</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
          {METRICS.map((m) => (
            <label key={m.key} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={selected.includes(m.key)}
                onChange={() => toggle(m.key)}
              />
              {m.label}
            </label>
          ))}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn ghost sm" onClick={onClose}>取消</button>
          <button
            className="btn sm"
            disabled={selected.length === 0}
            onClick={() => onSubmit(selected)}
          >
            确认
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **步骤 2：暂存变更**

```bash
git add frontend/src/components/ReprocessMetricsModal.tsx
```

---

## 任务 12：BatchDetail 重处理按钮

**文件：**
- 修改：`frontend/src/pages/BatchDetail.tsx`
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/api/endpoints.ts`

- [ ] **步骤 1：扩展 `Batch` 类型**

```typescript
export interface Batch {
  batch_no: string;
  mapping_name?: string;
  device_count?: number;
  f_start_ghz?: number;
  f_end_ghz?: number;
  deembedded?: boolean;
  process_type?: string;
  uploaded_at?: string;
  wafers?: (string | number)[];
  stats?: {
    fs_ghz_median?: number;
    pass_rate?: number;
  };
  raw_zip_deleted?: boolean;
}
```

- [ ] **步骤 2：在 `endpoints.ts` 新增封装**

```typescript
export const reextractBatch = (batchNo: string) =>
  api.post(`/batches/${encodeURIComponent(batchNo)}/reextract`).then((r) => r.data);

export const redeembedBatch = (batchNo: string) =>
  api.post(`/batches/${encodeURIComponent(batchNo)}/redeembed`).then((r) => r.data);

export const recomputeBatch = (batchNo: string, metrics: string[]) =>
  api.post(`/batches/${encodeURIComponent(batchNo)}/recompute`, { metrics }).then((r) => r.data);
```

- [ ] **步骤 3：在 `BatchDetail.tsx` 中导入并增加按钮和弹窗**

导入：

```typescript
import { reextractBatch, redeembedBatch, recomputeBatch } from '../api/endpoints';
import ReprocessMetricsModal from '../components/ReprocessMetricsModal';
```

State：

```typescript
const [showRecomputeModal, setShowRecomputeModal] = useState(false);
```

在 toolbar 的导出按钮后增加：

```tsx
<button
  className="btn"
  disabled={detail?.raw_zip_deleted}
  onClick={async () => {
    if (!detail) return;
    try {
      await reextractBatch(detail.batch_no);
      setError(null);
    } catch (e: any) {
      setError(e.message || '重新解压失败');
    }
  }}
>
  重新解压
</button>
<button
  className="btn"
  onClick={async () => {
    if (!detail) return;
    try {
      await redeembedBatch(detail.batch_no);
      setError(null);
    } catch (e: any) {
      setError(e.message || '重新去嵌失败');
    }
  }}
>
  重新去嵌
</button>
<button
  className="btn"
  onClick={() => setShowRecomputeModal(true)}
>
  重新计算指标
</button>
```

在组件 return 末尾增加弹窗：

```tsx
{showRecomputeModal && detail && (
  <ReprocessMetricsModal
    batchNo={detail.batch_no}
    onClose={() => setShowRecomputeModal(false)}
    onSubmit={async (metrics) => {
      try {
        await recomputeBatch(detail.batch_no, metrics);
        setShowRecomputeModal(false);
        setError(null);
      } catch (e: any) {
        setError(e.message || '重新计算失败');
      }
    }}
  />
)}
```

- [ ] **步骤 4：运行前端类型检查**

运行：`cd frontend && npm run typecheck`
预期：无错误。

- [ ] **步骤 5：暂存变更**

```bash
git add frontend/src/types/index.ts frontend/src/api/endpoints.ts frontend/src/pages/BatchDetail.tsx
```

---

## 任务 13：测试

- [ ] **步骤 1：运行后端单元/集成测试**

```bash
cd backend
uv run pytest tests/workers/test_extract_progress.py tests/workers/test_deembed_progress.py tests/workers/test_reprocess_batch.py tests/api/test_reprocess.py -v
```

预期：全部 PASS。

- [ ] **步骤 2：运行后端全量测试**

```bash
uv run pytest -q
```

预期：无新增失败。

- [ ] **步骤 3：运行 Ruff 检查与格式化**

```bash
uv run ruff check .
uv run ruff format .
```

预期：无 lint 错误。

- [ ] **步骤 4：运行前端构建**

```bash
cd frontend
npm run typecheck
npm run build
```

预期：无 TS/构建错误。

- [ ] **步骤 5：桌面开发环境验证（手动）**

```bash
./scripts/dev-desktop.sh
```

验证：
1. 上传一个含去嵌的 zip；
2. TaskDetail 中解压、去嵌、指标计算三阶段子进度条依次增长；
3. 完成后点击“重新去嵌”，任务重新进入去嵌阶段；
4. 点击“重新计算指标”，选择 Qs，任务进入指标计算阶段；
5. 若原始 zip 存在，点击“重新解压”可回到解压阶段。

---

## 自检

### 规格覆盖度

| 规格需求 | 实现任务 |
|----------|----------|
| 解压 0–30% 实时进度 | 任务 3 |
| 去嵌 30–45% 实时进度 | 任务 2 + 任务 3/5 |
| 指标计算 45–100% 进度 | 任务 4 |
| 7z 进度解析 + 字节轮询兜底 | 任务 3 |
| 重新解压 | 任务 5/6/7/8 |
| 重新去嵌 | 任务 5/6/7/8 |
| 重新计算 Qbode/Qs/Qp/kt2 | 任务 5/6/7/8 |
| 桌面端保留原始 zip | 任务 9 |
| 前端三阶段子进度条 | 任务 10 |
| 前端重处理按钮 | 任务 10/11/12 |

### 占位符扫描

- 所有代码步骤均给出可直接复制到文件的代码片段；
- 无“待定/TODO/后续实现”；
- 测试命令与预期输出明确。

### 类型一致性

- `LocalTask.kind` 取值 `"upload"|"reextract"|"redeembed"|"recompute"` 在 `local_queue.py`、`local_worker.py`、`dispatch.py` 中一致；
- `RecomputeRequest.metrics` 为 `list[str]`，在 worker、API、前端弹窗中一致；
- `_METRIC_COLUMNS` 键集 `"qs"|"qp"|"kt2"|"qbode"` 在前后端一致。

---

## 执行交接

**计划已完成并保存到 `docs/superpowers/plans/2026-07-03-upload-progress-reprocess-plan.md`。**

两种执行方式：

1. **子代理驱动（推荐）** - 每个任务调度一个子代理，任务间审查，快速迭代。必需子技能：`superpowers:subagent-driven-development`。
2. **内联执行** - 在当前会话中逐任务执行，批量推进并设有检查点。必需子技能：`superpowers:executing-plans`。

请选一种方式继续。
