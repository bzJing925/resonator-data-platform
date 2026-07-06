# 取消任务并清理上传文件

## Context

上传/重处理任务可能耗时较长（大 ZIP 解压、去嵌、指标计算）。用户需要在任务列表、任务详情、批次详情三个页面都能取消进行中的任务，并且取消后要把数据库记录和上传的物理文件一起清掉，避免留下孤儿数据。

## Goals

1. 允许取消状态为 `pending` 或 `running` 的 `UploadTask`。
2. 取消后删除：
   - `batches` 行（级联删除 `devices`、`file_nodes`）；
   - 原始上传 zip；
   - 该批次解压目录 `files/<batch_no>/`。
3. `UploadTask` 保留，状态变为 `cancelled`，并记录结束时间。
4. 在 **Tasks**、**TaskDetail**、**BatchDetail** 三个页面提供取消按钮。
5. 同时支持服务器模式（Celery）和桌面模式（本地线程队列）。

## Non-goals

- 不实现“取消后保留批次记录”的软删除。
- 不实现撤销取消/恢复任务。
- 不处理已完成（`success`/`failed`）的任务清理（仍走删除批次接口）。

## Design

### 数据库变更

在 `upload_tasks` 表增加一个可选时间戳，用于幂等地标识“已请求取消”：

```sql
ALTER TABLE upload_tasks ADD COLUMN cancelled_at TIMESTAMPTZ;
```

`UploadTask.status` 的 check constraint 需要扩展为包含 `'cancelled'`：

```sql
status IN ('pending','running','success','failed','cancelled')
```

> 新增 migration：`backend/alembic/versions/2026_07_06_xxxxxx_add_cancelled_status.py`

### 后端 API

新增端点：

```http
POST /api/tasks/{task_id}/cancel
```

行为：

1. 取 `UploadTask`，不存在返回 `404`。
2. 若 `status` 不在 `('pending', 'running')`，返回 `409 Conflict`，提示“任务已结束，无法取消”。
3. 记录 `cancelled_at = now()`。
4. 先停止 worker：
   - 对 **Celery**：若 `task.celery_task_id` 存在，调用 `celery_app.control.revoke(task.celery_task_id, terminate=(task.status == "running"))`；
   - 对 **Desktop**：调用 `get_local_queue().request_cancel(task_id)`，从 pending 队列移除或标记 running 任务。
5. 根据 `task.batch_no` 取 `Batch`：
   - 若存在，删除 batch（级联删 devices / file_nodes）。`batches.task_id` 已设置 `ondelete="SET NULL"`，删除 batch 不会影响 `UploadTask`。
   - 删除 `settings.files_dir / batch_no`（解压目录）。
   - 删除 `batch.raw_zip_path`（原始 zip）。
6. 若清理或撤销 worker 时发生异常，记录日志并将简要信息写入 `UploadTask.error_msg`，但仍把任务标为 cancelled。
7. 更新 `UploadTask.status = 'cancelled'`，`progress_msg = '已取消并清理文件'`（若清理失败则使用更合适的消息），`finished_at = now()`。
8. 返回 `TaskDetail`，并设置 `raw_zip_deleted = True`。

> 注意：实际实现中先通知 worker 停止，再删除文件，以减小 running 任务在文件被删后继续写入的 race。

### Worker 协作退出

worker 任务需要在关键节点检查“是否被取消”，如果是则立即抛出一个 `TaskCancelled` 异常，由外层捕获后把任务标为 `cancelled`（API 已经清理过 DB/文件，所以这里主要是更新任务状态）。

检查点：

- `extract_batch_task` 开始、每解压完一个文件后、去嵌每对前后；
- `compute_batch_task` 开始、每处理一定数量 devices 后；
- `reprocess_batch.py` 中的 `redeembed_batch_task`、`recompute_batch_task` 同理。

取消检测辅助函数（伪代码）：

```python
def is_task_cancelled(db: Session, task_id: int) -> bool:
    task = db.get(UploadTask, task_id)
    return task is not None and task.cancelled_at is not None
```

Desktop 本地 worker：

- `LocalTaskQueue` 维护一个 `set[int]` 记录已请求取消的任务 ID；
- 取消接口调用 `queue.request_cancel(task_id)`；
- 各任务函数通过 `get_local_queue().is_cancelled(task_id)` 检查；
- 对于正在解压的子进程，`_extract_with_7z` / `_extract_with_unzip` 等函数在循环中检查取消标志，发现后 `proc.terminate()` 并抛 `TaskCancelled`。

### 前端

新增 API 封装：

```ts
export const cancelTask = (taskId: number | string) =>
  api.post(`/tasks/${taskId}/cancel`).then((r) => r.data);
```

#### Tasks 列表

- 每行 `pending` / `running` 的任务增加“取消”按钮；
- 点击后弹出确认框：“取消后将删除该批次及上传文件，是否继续？”；
- 确认后调用 `cancelTask`，成功后刷新列表。

#### TaskDetail

- 在任务状态为 `pending` / `running` 时显示“取消任务”按钮；
- 取消成功后跳转回 `/tasks` 或留在当前页显示 `cancelled` 状态。

#### BatchDetail

- 工具栏增加“取消任务”按钮（仅当该批次关联任务为 `pending` / `running` 时可见/可点）；
- 由于 `BatchDetail` 已经轮询 `/api/tasks/{task_id}`，可直接用 `taskStatus` 判断；
- 取消成功后该页面会显示批次不存在（已被删除），自动跳回 `/batches`。

### 错误与边界

- 任务不存在：`404`。
- 任务已结束：`409`。
- 取消时文件/目录删除失败：记录 warning 日志，但不阻塞任务标为 `cancelled`。
- 取消请求重复：幂等，直接返回当前 `TaskDetail`。
- 删除 batch 前先把 `batch.task_id` 置空，避免级联误删 `UploadTask`。

## Verification

1. 启动 desktop dev，上传一个大 zip；
2. 在解压/去嵌/计算过程中：
   - 从任务列表点击取消 → 任务状态变为 `cancelled`，对应 batch 和 devices 被删，zip 和解压目录不存在；
   - 从 TaskDetail 点击取消 → 同上；
   - 从 BatchDetail 点击取消 → 同上，页面自动回到批次列表。
3. 对已完成任务点击取消 → 提示“任务已结束，无法取消”。
4. 后端测试：
   - `test_cancel_pending`：取消 pending 任务，验证 DB 清理；
   - `test_cancel_running`：mock running 任务，验证 revoke 调用；
   - `test_cancel_already_done`：验证 409。
5. 运行 `uv run ruff check .` 和 `npm run build` 无报错。

## Notes

- 本次改动会新增一个 Alembic migration；桌面 SQLite 会在启动时自动应用。
- 任务取消后 `UploadTask` 保留，用于审计和展示历史记录。
- 取消时若 worker 正在写文件，可能遇到“文件已删除”异常；worker 捕获后把任务标为 `cancelled` 即可。
