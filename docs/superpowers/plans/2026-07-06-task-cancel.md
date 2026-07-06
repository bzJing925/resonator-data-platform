# 取消任务并清理上传文件 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为进行中的上传/重处理任务增加取消功能，取消后删除该任务对应的批次、devices、file_nodes、原始 zip 和解压目录，并在 Tasks / TaskDetail / BatchDetail 三个页面提供取消入口。

**架构：** 后端新增 `cancelled_at` 字段和 `/api/tasks/{id}/cancel` 端点；端点先清理数据库与文件，再对 Celery 调用 `revoke(terminate=True)` / 对 desktop 本地队列发送取消信号；worker 在关键节点通过共享的取消检测工具协作退出。前端通过新增的 `cancelTask` API 调用端点并刷新状态。

**技术栈：** FastAPI、SQLAlchemy 2.0、Celery / 本地线程队列、React 18、TypeScript、Axios。

---

## 文件清单

| 文件 | 职责 |
|------|------|
| `backend/alembic/versions/2026_07_06_150000_add_task_cancelled.py` | 添加 `cancelled_at` 列并扩展 `status` check constraint |
| `backend/app/models/task.py` | 模型新增 `cancelled_at` 列，更新 status check constraint |
| `backend/app/workers/cancel.py` | 取消异常、取消检测辅助函数 |
| `backend/app/workers/local_queue.py` | 增加 `cancelled_ids` 集合与 pending 移除方法 |
| `backend/app/workers/local_worker.py` | 启动任务前/异常时识别 `TaskCancelled` |
| `backend/app/workers/extract_batch.py` | 解压/去嵌过程中检查取消并终止子进程 |
| `backend/app/workers/compute_batch.py` | 指标计算过程中检查取消 |
| `backend/app/workers/reprocess_batch.py` | 重处理任务中检查取消 |
| `backend/app/workers/progress.py` | 增加 `cancel()` 方法，把任务标为 `cancelled` |
| `backend/app/services/cleanup_service.py` | 统一删除 batch（级联删 devices/file_nodes）和对应物理文件 |
| `backend/app/api/tasks.py` | 新增 `POST /api/tasks/{task_id}/cancel` 端点 |
| `backend/app/api/batches.py` | 复用 `cleanup_service` 重构 `delete_batch` |
| `backend/tests/models/test_task_model.py` | 验证 `cancelled` status 合法 |
| `backend/tests/api/test_cancel_task.py` | 取消端点的 API 测试 |
| `frontend/src/types/index.ts` | `Task` 类型增加 `cancelled_at` |
| `frontend/src/api/endpoints.ts` | 新增 `cancelTask` 封装 |
| `frontend/src/pages/Tasks.tsx` | 列表增加取消按钮与确认 |
| `frontend/src/pages/TaskDetail.tsx` | 详情页增加取消按钮 |
| `frontend/src/pages/BatchDetail.tsx` | 工具栏增加取消按钮 |

---

### 任务 1：数据库迁移与模型更新

**文件：**
- 创建：`backend/alembic/versions/2026_07_06_150000_add_task_cancelled.py`
- 修改：`backend/app/models/task.py`
- 测试：`backend/tests/models/test_task_model.py`

- [ ] **步骤 1：新增 Alembic migration**

```python
"""add cancelled_at to upload_tasks and extend status check."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "2026_07_06_150000"
down_revision = "2026_07_03_120000"  # 当前 head，请根据实际 alembic history 调整
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_tasks",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_uptask_status", "upload_tasks", type_="check")
    op.create_check_constraint(
        "ck_uptask_status",
        "upload_tasks",
        sa.text("status IN ('pending','running','success','failed','cancelled')"),
    )


def downgrade() -> None:
    op.drop_constraint("ck_uptask_status", "upload_tasks", type_="check")
    op.create_check_constraint(
        "ck_uptask_status",
        "upload_tasks",
        sa.text("status IN ('pending','running','success','failed')"),
    )
    op.drop_column("upload_tasks", "cancelled_at")
```

- [ ] **步骤 2：更新 UploadTask 模型**

在 `backend/app/models/task.py` 的 `UploadTask` 类中：

```python
status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

并把 `__table_args__` 里的 status check 改为：

```python
CheckConstraint(
    "status IN ('pending','running','success','failed','cancelled')",
    name="ck_uptask_status",
),
```

- [ ] **步骤 3：编写模型测试**

```python
def test_upload_task_accepts_cancelled_status(db):
    from app.models import UploadTask

    t = UploadTask(batch_no="T.03", status="cancelled", cancelled_at=datetime.now(UTC))
    db.add(t)
    db.commit()
    db.refresh(t)
    assert t.status == "cancelled"
```

- [ ] **步骤 4：运行测试确认通过**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/backend
uv run pytest tests/models/test_task_model.py -v
```

预期：`test_upload_task_accepts_cancelled_status` 通过。

- [ ] **步骤 5：Commit**

```bash
git add backend/alembic/versions/2026_07_06_150000_add_task_cancelled.py \
        backend/app/models/task.py \
        backend/tests/models/test_task_model.py
git commit -m "feat(models): add cancelled_at and cancelled status to upload_tasks

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 2：取消检测工具与本地队列支持

**文件：**
- 创建：`backend/app/workers/cancel.py`
- 修改：`backend/app/workers/local_queue.py`
- 测试：`backend/tests/workers/test_cancel.py`（新建）

- [ ] **步骤 1：创建取消工具模块**

```python
"""任务取消检测。"""

from __future__ import annotations

from app.config import get_settings
from app.db import SessionLocal
from app.models import UploadTask


class TaskCancelled(Exception):
    """任务已被请求取消。"""


def is_task_cancelled(task_id: int) -> bool:
    """检测任务是否已请求取消。"""
    settings = get_settings()
    if settings.is_desktop:
        from app.workers.local_queue import get_local_queue

        return get_local_queue().is_cancelled(task_id)

    # server 模式直接查数据库（Celery revoke 会终止进程，这里作为兜底）
    try:
        with SessionLocal() as db:
            task = db.get(UploadTask, task_id)
            return task is not None and task.cancelled_at is not None
    except Exception:
        return False


def raise_if_cancelled(task_id: int) -> None:
    if is_task_cancelled(task_id):
        raise TaskCancelled()
```

- [ ] **步骤 2：扩展 LocalTaskQueue**

在 `backend/app/workers/local_queue.py` 的 `LocalTaskQueue.__init__` 中增加：

```python
self._cancelled_ids: set[int] = set()
```

新增方法：

```python
    def request_cancel(self, task_id: int) -> bool:
        """请求取消任务；若任务在 pending 队列中则移除。返回是否从队列移除。"""
        with self._lock:
            self._cancelled_ids.add(task_id)
            for idx, t in enumerate(self._pending):
                if t.task_id == task_id:
                    del self._pending[idx]
                    self._event.clear()
                    return True
            return False

    def is_cancelled(self, task_id: int) -> bool:
        with self._lock:
            return task_id in self._cancelled_ids

    def clear_cancel(self, task_id: int) -> None:
        with self._lock:
            self._cancelled_ids.discard(task_id)
```

- [ ] **步骤 3：编写测试**

```python
def test_local_queue_request_cancel_removes_pending():
    from app.workers.local_queue import LocalTaskQueue, LocalTask

    q = LocalTaskQueue()
    q.put(LocalTask(task_id=1, batch_no="B1", mapping_id=1))
    q.put(LocalTask(task_id=2, batch_no="B2", mapping_id=1))

    assert q.request_cancel(1) is True
    assert q.list_pending() == [LocalTask(task_id=2, batch_no="B2", mapping_id=1)]
    assert q.is_cancelled(1) is True


def test_is_task_cancelled_queries_db(db):
    from app.models import UploadTask
    from app.workers.cancel import is_task_cancelled

    t = UploadTask(batch_no="T.04", status="running", cancelled_at=datetime.now(UTC))
    db.add(t)
    db.commit()
    db.refresh(t)

    assert is_task_cancelled(t.id) is True
```

- [ ] **步骤 4：运行测试**

```bash
uv run pytest tests/workers/test_cancel.py -v
```

预期：全部通过。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/workers/cancel.py \
        backend/app/workers/local_queue.py \
        backend/tests/workers/test_cancel.py
git commit -m "feat(workers): add task cancellation detection and local queue support

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 3：清理服务

**文件：**
- 创建：`backend/app/services/cleanup_service.py`
- 修改：`backend/app/api/batches.py`
- 测试：`backend/tests/services/test_cleanup_service.py`（新建）

- [ ] **步骤 1：创建 cleanup_service**

```python
"""统一清理批次及其上传的物理文件。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Batch

logger = logging.getLogger(__name__)


def delete_batch_and_files(db: Session, batch_no: str) -> bool:
    """删除 batch（级联删 devices/file_nodes）及其上传文件。

    返回是否实际删除了 batch。
    """
    batch = db.get(Batch, batch_no)  # 注意：Batch 主键是 id，不是 batch_no
    # 实际上应该按 batch_no 查询；见步骤 2 修正
    ...
```

**修正：** 按 `batch_no` 查询：

```python
from sqlalchemy import select

def delete_batch_and_files(db: Session, batch_no: str) -> bool:
    from app.models import Batch

    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        return False

    settings = get_settings()
    files_dir = settings.files_dir / batch_no
    raw_zip = Path(batch.raw_zip_path) if batch.raw_zip_path else None

    db.delete(batch)
    db.commit()

    if files_dir.exists():
        try:
            shutil.rmtree(files_dir)
        except Exception:
            logger.exception("删除解压目录失败: %s", files_dir)
    if raw_zip and raw_zip.exists():
        try:
            raw_zip.unlink()
        except Exception:
            logger.exception("删除原 zip 失败: %s", raw_zip)

    return True
```

- [ ] **步骤 2：重构 batches.py 的 delete_batch**

把 `backend/app/api/batches.py` 中 `delete_batch` 的实现替换为调用服务：

```python
from app.services.cleanup_service import delete_batch_and_files

@router.delete("/{batch_no}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_no: str, db: DbSession) -> None:
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")
    delete_batch_and_files(db, batch_no)
```

- [ ] **步骤 3：编写服务测试**

```python
def test_delete_batch_and_files_removes_batch_and_files(db, tmp_path):
    from app.models import Batch, Mapping
    from app.services.cleanup_service import delete_batch_and_files
    from app.config import get_settings

    settings = get_settings()
    mapping = Mapping(name="M1")
    db.add(mapping)
    db.commit()

    raw_zip = tmp_path / "raw.zip"
    raw_zip.write_text("zip")
    files_dir = settings.files_dir / "B.01"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "a.s1p").write_text("s1p")

    batch = Batch(
        batch_no="B.01",
        mapping_id=mapping.id,
        file_path=str(files_dir),
        raw_zip_path=str(raw_zip),
    )
    db.add(batch)
    db.commit()

    assert delete_batch_and_files(db, "B.01") is True
    assert db.scalar(select(Batch).where(Batch.batch_no == "B.01")) is None
    assert not files_dir.exists()
    assert not raw_zip.exists()
```

- [ ] **步骤 4：运行测试**

```bash
uv run pytest tests/services/test_cleanup_service.py tests/api/test_batches.py -v
```

预期：通过（`test_batches` 需要本地 Postgres；若未启动可跳过）。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/cleanup_service.py \
        backend/app/api/batches.py \
        backend/tests/services/test_cleanup_service.py
git commit -m "feat(services): extract batch cleanup service and reuse in delete endpoint

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 4：取消端点

**文件：**
- 修改：`backend/app/api/tasks.py`
- 测试：`backend/tests/api/test_cancel_task.py`（新建）

- [ ] **步骤 1：实现 POST /api/tasks/{task_id}/cancel**

在 `backend/app/api/tasks.py` 中新增：

```python
from datetime import UTC, datetime
from celery.result import AsyncResult

from app.services.cleanup_service import delete_batch_and_files
from app.workers.celery_app import celery_app
from app.workers.local_queue import get_local_queue


@router.post("/{task_id}/cancel", response_model=TaskDetail)
def cancel_task(task_id: int, db: DbSession) -> TaskDetail:
    task = db.get(UploadTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    if task.status not in ("pending", "running"):
        raise HTTPException(status_code=409, detail="任务已结束，无法取消")

    task.cancelled_at = datetime.now(UTC)
    db.commit()

    # 清理数据库与文件
    if task.batch_no:
        delete_batch_and_files(db, task.batch_no)

    # 通知 worker
    settings = get_settings()
    if settings.is_desktop:
        queue = get_local_queue()
        removed = queue.request_cancel(task_id)
        if not removed and task.celery_task_id:
            # 已在运行，request_cancel 已标记 cancelled_ids
            pass
    else:
        if task.celery_task_id:
            AsyncResult(task.celery_task_id).revoke(
                terminate=task.status == "running"
            )

    task.status = "cancelled"
    task.progress_msg = "已取消并清理文件"
    task.finished_at = datetime.now(UTC)
    db.commit()

    return TaskDetail.model_validate(task)
```

注意：若 `delete_batch_and_files` 会 `db.commit()`，需要确保 `task` 对象仍然有效。可以在调用前 `db.flush()` 或让服务函数接受 `batch_no` 并在内部自行 commit。当前实现可接受，因为 `delete_batch_and_files` commit 后 task 仍是 attached 的；再修改 status 并 commit 即可。

- [ ] **步骤 2：编写 API 测试**

```python
def test_cancel_pending_task_cleans_up(db, client):
    from app.models import Batch, Mapping, UploadTask

    mapping = Mapping(name="M1")
    db.add(mapping)
    db.commit()

    task = UploadTask(batch_no="B.02", status="pending", progress_msg="排队中")
    db.add(task)
    db.flush()
    batch = Batch(
        batch_no="B.02",
        mapping_id=mapping.id,
        file_path="/tmp/fake",
        raw_zip_path="/tmp/fake.zip",
        task_id=task.id,
    )
    db.add(batch)
    db.commit()

    res = client.post(f"/tasks/{task.id}/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"
    assert db.scalar(select(Batch).where(Batch.batch_no == "B.02")) is None


def test_cancel_finished_task_returns_409(db, client):
    from app.models import UploadTask

    task = UploadTask(batch_no="B.03", status="success")
    db.add(task)
    db.commit()

    res = client.post(f"/tasks/{task.id}/cancel")
    assert res.status_code == 409
```

- [ ] **步骤 3：运行测试**

```bash
uv run pytest tests/api/test_cancel_task.py -v
```

预期：通过。

- [ ] **步骤 4：Commit**

```bash
git add backend/app/api/tasks.py backend/tests/api/test_cancel_task.py
git commit -m "feat(api): add POST /tasks/{id}/cancel endpoint

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 5：Worker 协作退出

**文件：**
- 修改：`backend/app/workers/progress.py`
- 修改：`backend/app/workers/local_worker.py`
- 修改：`backend/app/workers/extract_batch.py`
- 修改：`backend/app/workers/compute_batch.py`
- 修改：`backend/app/workers/reprocess_batch.py`

- [ ] **步骤 1：ProgressPublisher 增加 cancel 方法**

```python
def cancel(self, db: Session, error_msg: str = "已取消") -> None:
    db.execute(
        update(UploadTask)
        .where(UploadTask.id == self.task_id)
        .values(
            status="cancelled",
            stage="failed",
            stage_progress_pct=0,
            progress_msg=error_msg,
            finished_at=datetime.now(UTC),
        )
    )
    db.commit()
    self._publish(
        {
            "task_id": self.task_id,
            "status": "cancelled",
            "stage": "failed",
            "stage_progress_pct": 0,
            "progress_msg": error_msg,
            "event": "error",
        }
    )
```

- [ ] **步骤 2：local_worker 识别 TaskCancelled**

在 `backend/app/workers/local_worker.py` 中：

```python
from app.workers.cancel import TaskCancelled
```

把 `_run_upload_or_reextract` 的 except 块改为：

```python
    except TaskCancelled:
        logger.info("本地任务 %s 已取消", task.task_id)
        try:
            publisher = ProgressPublisher(task.task_id)
            publisher.cancel(db, "已取消并清理文件")
        except Exception:
            pass
    except Exception as exc:
        ...
```

并在 `local_worker_loop` 中，取到任务后先检查：

```python
        if queue.is_cancelled(task.task_id):
            logger.info("跳过已取消的本地任务 %s", task.task_id)
            continue
```

- [ ] **步骤 3：extract_batch.py 中检查取消**

在 `extract_batch_task` 的 try 块开头、`_extract_with_7z` / `_extract_with_unzip` 的轮询循环中、以及去嵌循环中加入：

```python
from app.workers.cancel import raise_if_cancelled

raise_if_cancelled(upload_task_id)
```

在 `_extract_with_7z` 的 `while proc.poll() is None:` 循环体中：

```python
            try:
                raise_if_cancelled(upload_task_id)
            except Exception:
                proc.terminate()
                raise
```

并把 `upload_task_id` 传入 `_extract_with_7z` / `_extract_with_unzip` / `_extract_zip`。

- [ ] **步骤 4：compute_batch.py 中检查取消**

在 `compute_batch_task` 的设备处理循环中每隔一批调用 `raise_if_cancelled(upload_task_id)`。

- [ ] **步骤 5：reprocess_batch.py 中检查取消**

在 `redeembed_batch_task` 和 `recompute_batch_task` 的循环中加入 `raise_if_cancelled(upload_task_id)`。

- [ ] **步骤 6：运行相关测试**

```bash
uv run pytest tests/workers/test_extract_progress.py tests/workers/test_deembed_progress.py tests/workers/test_reprocess_batch.py -v
```

预期：通过。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/workers/progress.py \
        backend/app/workers/local_worker.py \
        backend/app/workers/extract_batch.py \
        backend/app/workers/compute_batch.py \
        backend/app/workers/reprocess_batch.py
git commit -m "feat(workers): cooperative cancellation checks in extract/compute/reprocess

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 6：前端类型与 API 封装

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/api/endpoints.ts`

- [ ] **步骤 1：扩展 Task 类型**

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
  cancelled_at?: string;
  stage?: string;
  stage_progress_pct?: number;
  raw_zip_deleted?: boolean;
}
```

- [ ] **步骤 2：新增 cancelTask 封装**

在 `frontend/src/api/endpoints.ts` 中：

```typescript
export const cancelTask = (taskId: number | string) =>
  api.post(`/tasks/${taskId}/cancel`).then((r: AxiosResponse<Task>) => r.data);
```

- [ ] **步骤 3：Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/endpoints.ts
git commit -m "feat(frontend): add cancelTask API and cancelled_at type

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 7：Tasks 列表增加取消按钮

**文件：**
- 修改：`frontend/src/pages/Tasks.tsx`

- [ ] **步骤 1：引入 cancelTask 和 formatApiError**

```typescript
import { listTasks, cancelTask } from '../api/endpoints';
```

在组件内增加 helper：

```typescript
function formatApiError(e: any, fallback: string): string {
  const detail = e?.response?.data?.detail;
  return typeof detail === 'string' && detail.length > 0 ? detail : e?.message || fallback;
}
```

- [ ] **步骤 2：TaskRow 增加取消按钮**

修改 `TaskRowProps` 为 `{ task: Task; onCancel: (t: Task) => void; }`。

在操作列增加：

```tsx
{(t.status === 'pending' || t.status === 'running') && (
  <button
    className="btn ghost sm fail"
    onClick={() => onCancel(t)}
    title="取消任务并删除上传文件"
  >
    取消
  </button>
)}
```

- [ ] **步骤 3：Tasks 组件处理取消**

```typescript
const handleCancel = useCallback(async (t: Task) => {
  if (!window.confirm(`取消任务 ${t.id} 将删除批次 ${t.batch_no || ''} 及上传文件，是否继续？`)) return;
  try {
    await cancelTask(t.id);
    const d = await listTasks();
    setTasks(Array.isArray(d) ? d : (d as { items?: Task[] })?.items || []);
    setError(null);
  } catch (e: any) {
    setError(formatApiError(e, '取消失败'));
  }
}, []);
```

把 `onCancel={handleCancel}` 传给 `TaskRow`。

- [ ] **步骤 4：运行前端 typecheck**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/frontend && npm run typecheck
```

预期：无错误。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/pages/Tasks.tsx
git commit -m "feat(frontend): add cancel button to tasks list

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 8：TaskDetail 增加取消按钮

**文件：**
- 修改：`frontend/src/pages/TaskDetail.tsx`

- [ ] **步骤 1：导入 cancelTask**

```typescript
import { getTask, cancelTask, reextractBatch, redeembedBatch, recomputeBatch } from '../api/endpoints';
```

- [ ] **步骤 2：在工具栏添加取消按钮**

```tsx
{(status === 'pending' || status === 'running') && (
  <button
    className="btn fail"
    onClick={async () => {
      if (!window.confirm(`取消任务将删除批次 ${task?.batch_no || ''} 及上传文件，是否继续？`)) return;
      try {
        await cancelTask(taskId!);
        const updated = await getTask(taskId!);
        setTask(updated);
        setError(null);
      } catch (e: any) {
        setError(formatApiError(e, '取消失败'));
      }
    }}
  >
    取消任务
  </button>
)}
```

- [ ] **步骤 3：添加 formatApiError helper**

```typescript
function formatApiError(e: any, fallback: string): string {
  const detail = e?.response?.data?.detail;
  return typeof detail === 'string' && detail.length > 0 ? detail : e?.message || fallback;
}
```

- [ ] **步骤 4：运行 typecheck**

```bash
npm run typecheck
```

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/pages/TaskDetail.tsx
git commit -m "feat(frontend): add cancel button to task detail

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 9：BatchDetail 增加取消按钮

**文件：**
- 修改：`frontend/src/pages/BatchDetail.tsx`

- [ ] **步骤 1：导入 cancelTask**

```typescript
import { getBatch, getTask, cancelTask, ... } from '../api/endpoints';
```

- [ ] **步骤 2：在工具栏增加取消按钮**

在 `isTaskActive` 判断后，在“重新解压”按钮之前或之后加入：

```tsx
{isTaskActive && detail?.task_id && (
  <button
    className="btn fail"
    onClick={async () => {
      if (!window.confirm(`取消任务将删除批次 ${detail.batch_no} 及上传文件，是否继续？`)) return;
      try {
        setError(null);
        await cancelTask(detail.task_id);
        setTaskStatus('cancelled');
      } catch (e: any) {
        setError(formatApiError(e, '取消失败'));
      }
    }}
  >
    取消任务
  </button>
)}
```

- [ ] **步骤 3：处理取消后 batch 被删的情况**

`getBatch` 在批次不存在时返回 404，当前会设置 `error`。取消后页面会显示“批次不存在”，用户可手动返回。可接受。

- [ ] **步骤 4：运行 typecheck**

```bash
npm run typecheck
```

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/pages/BatchDetail.tsx
git commit -m "feat(frontend): add cancel button to batch detail

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 10：验证与收尾

- [ ] **步骤 1：后端 lint**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/backend
uv run ruff check .
uv run ruff format .
```

预期：无错误。

- [ ] **步骤 2：后端测试（在 Postgres 可用时）**

```bash
DATABASE_URL="postgresql+psycopg://aln:aln@localhost:15432/aln" uv run pytest tests/models/test_task_model.py tests/api/test_cancel_task.py tests/services/test_cleanup_service.py -v
```

预期：通过。

- [ ] **步骤 3：前端构建**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/frontend
npm run typecheck
npm run build
```

预期：通过。

- [ ] **步骤 4：最终 commit（如有格式改动）**

```bash
git add -A
git commit -m "style: ruff format after cancellation feature

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" || true
```

---

## 自检

- 规格中的每个需求都有对应任务：
  - 取消 `pending` / `running` 任务 → 任务 4 端点 + 任务 5 worker 检查。
  - 删除 batch/devices/file_nodes → 任务 3 cleanup_service。
  - 删除原始 zip 和解压目录 → 任务 3 cleanup_service。
  - `UploadTask` 保留并标为 `cancelled` → 任务 1 模型 + 任务 4 端点 + 任务 5 publisher.cancel。
  - 三页面取消入口 → 任务 7 / 8 / 9。
  - 支持 server + desktop → 任务 2 本地队列 + 任务 4 端点分支。
- 无占位符：每个任务都包含具体代码/命令。
- 类型一致性：`cancelled_at`、`TaskCancelled`、`cancelTask` 命名在前后端一致。

**计划已完成并保存到 `docs/superpowers/plans/2026-07-06-task-cancel.md`。两种执行方式：**

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代

**2. 内联执行** - 在当前会话中使用 `executing-plans` 执行任务，批量执行并设有检查点

**选哪种方式？**
