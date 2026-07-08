# 上传任务进度与重处理功能设计

## 背景

桌面端上传大 zip 时，UI 在解压完成时卡在 15%，原因是后续去嵌、指标计算阶段没有向 `upload_tasks` 写入进度。用户需要：

1. 看到当前正在运行的子程序/阶段和实时进度；
2. 支持重新解压、重新去嵌、分别重新计算 Qbode/Qs/Qp/kt2；
3. 研究第三方解压软件是否能显示进度。

本设计采用**轻量级补丁方案**：在现有 `extract_batch → compute_batch` 管线中插入进度回调，增加重处理任务，不重构整体调度。

---

## 目标

- 解压阶段能按 0%–30% 实时显示进度；
- 去嵌阶段能按 30%–45% 实时显示进度；
- 指标计算阶段按 45%–100% 显示进度；
- 任务详情页和批次详情页提供重处理入口；
- 桌面端默认保留原始 zip，保证“重新解压”可用。

---

## 总体进度分配

| 阶段 | 总体进度范围 | 说明 |
|------|-------------|------|
| 解压 | 0% – 30% | 包含第三方/系统/Pythond 解压 |
| 去嵌 | 30% – 45% | 基于 .s2p DUT 和 OPEN/SHORT 校准件逐对处理 |
| 指标计算 | 45% – 100% | 包含 Qs、Qp、kt2、Qbode 等参数提取 |

> 该分配基于大文件解压耗时通常最长，去嵌次之，指标计算涉及大量 curve fitting 也可能耗时较长的观察。如果实际分布与预期差异较大，可通过 `AlgorithmConfig` 调整映射端点。

---

## 后端：进度实时上报

### 1. 解压进度

位置：`backend/app/workers/extract_batch.py`

- 将 `_extract_with_7z` 从 `subprocess.run` 改为 `subprocess.Popen`；
- 命令行增加 `-bsp1 -bb0`，让 7z 输出百分比进度；
- 逐行读取 stdout，用正则 `\(\s*(\d+)%\)` 解析当前百分比；
- 每次解析到进度即调用 `ProgressPublisher.stage_update(...)`：
  - `stage="extract"`
  - `stage_progress_pct=解压百分比`
  - `progress_pct=0 + 解压百分比 * 30 / 100`
  - `progress_msg="7z 解压中… 45%"`
- 若解析失败（平台差异或旧版本 7z），回退到**基于已解压字节数 / ZIP 未压缩总字节数**轮询：
  - 解压前先通过 `zipfile.ZipFile` 读取所有 `file_size` 之和作为 total_uncompressed；
  - 解压开始后，每 500ms 轮询 `target_dir` 下已写入文件总大小；
  - 估算百分比，同样映射到 0%–30%。
- `_extract_with_unzip` / `_extract_zip`（zipfile）同理：
  - `zipfile` 可基于每成员写入量估算；
  - `unzip` 暂无百分比，则消息改为 `unzip 解压中… 已写入 X MB`，百分比按文件数估算。

### 2. 去嵌进度

位置：`backend/app/core/deembed.py` + `backend/app/workers/extract_batch.py`

- 为 `_run_deembed` 增加可选参数：
  ```python
  progress_callback: Callable[[int, int], None] | None = None
  ```
- 每处理完一对 `(S11, S22)`，调用 `progress_callback(current, total)`；
- 在 `extract_batch_task` 的去嵌阶段：
  - `stage="deembed"`
  - 把 `current / total` 映射到 `stage_progress_pct`，总体进度 `30 + ratio * 15`；
  - 消息示例：`去嵌中… 已处理 1200/3800 对`。

### 3. 指标计算进度

位置：`backend/app/workers/compute_batch.py`

- 保留现有按文件计数上报；
- 调整总体进度映射为 45%–100%；
- 消息示例：`指标计算（Qs/Qp/kt2/Qbode）… 已处理 1200/3800`。

### 4. 数据模型约束更新

位置：`backend/app/models/task.py`

- `UploadTask.stage` 的检查约束从：
  ```python
  "stage IN ('extract','metrics','done','failed')"
  ```
  扩展为：
  ```python
  "stage IN ('extract','deembed','metrics','done','failed')"
  ```
- 新增 `deembed` 枚举后，Alembic 需要生成迁移脚本（PostgreSQL）。桌面端 SQLite 通过 `Base.metadata.create_all` 自动生效。

---

## 后端：重处理任务

新增文件 `backend/app/workers/reprocess_batch.py`，并在 `backend/app/api/batches.py` 增加三个 POST 端点。

### 1. 重新解压 `POST /api/batches/{batch_no}/reextract`

行为：

1. 校验批次存在且原始 zip 仍保留（通过 `file_nodes` 的 `node_type='zip'` 或本地文件路径判断）；
2. 若原始 zip 已删除，返回 `400 Bad Request`，消息：`原始数据包已清理，无法重新解压`；
3. 删除 `files/{batch_no}/` 下所有已解压内容（保留原始 zip）；
4. 清空该批次 `devices` / `file_nodes` 除 zip 节点外的数据；
5. 重置 `upload_tasks` 状态为 `pending`、stage=`extract`、进度 0；
6. 投递新的 `extract_batch → compute_batch` 任务链（Celery 或 LocalTaskQueue）。

> 桌面端需默认设置 `KEEP_RAW_ZIP=true`，保证重新解压可用；服务器端仍可按需清理以节省空间。

### 2. 重新去嵌 `POST /api/batches/{batch_no}/redeembed`

行为：

1. 校验批次存在；
2. 在 `files/{batch_no}/` 下重新扫描 `.s2p` DUT 和 `OPEN`/`SHORT` 校准件；
3. 清空该批次 `devices` 中 `deembedded=true` 的记录；
4. 调用 `_run_deembed` 并传入进度回调，把去嵌阶段上报到 `upload_tasks`（stage=`deembed`，进度 30%–45%）；
5. 去嵌完成后自动调用 `compute_batch_task` 重新计算指标（45%–100%）。

异常：

- 找不到 OPEN/SHORT 校准件 → 任务失败，error_msg 写入 `缺少 OPEN/SHORT 校准件，无法重新去嵌`。

### 3. 重新计算指定指标 `POST /api/batches/{batch_no}/recompute`

请求体：

```json
{
  "metrics": ["qbode", "qs", "qp", "kt2"]
}
```

行为：

1. 校验批次存在且已有 devices；
2. 根据 `s_param_path` 重新读取 s 参数文件；
3. 调用 `extract_resonator_params` 重新跑完整提参；
4. 只把请求中列出的指标写回 `devices` 对应列，其他列保留原值：
   - `qbode` → `qs_bodeq`, `qp_bodeq`, `dbqs`, `dbqp`, `bodeq_fitted`, `bodeq_smooth`, `bodeq_raw`, `fbode_ghz`
   - `qs` → `qs`
   - `qp` → `qp`
   - `kt2` → `k2eff_pct`
5. 进度同样映射到 45%–100%，消息：`重新计算 kt2 中… 已处理 1200/3800`。

异常：

- `s_param_path` 缺失的设备直接跳过，并在任务结束时在 `progress_msg` 中提示：`N 个设备因缺少 s_param_path 被跳过`。

### 4. 调度统一

- **桌面端**：通过 `LocalTaskQueue` 投递 `ReprocessTask`，`local_worker_loop` 中复用相同的重处理函数；
- **服务端**：通过 Celery 投递 `reextract_batch_task`, `redeembed_batch_task`, `recompute_batch_task`；
- 三个任务都复用 `ProgressPublisher` 写入 `upload_tasks` 表，前端通过现有 SSE / 轮询机制消费。

---

## 前端改动

### 1. TaskDetail 页

位置：`frontend/src/pages/TaskDetail.tsx`

- 在现有总进度条下方增加阶段子进度条：
  - 解压（0–30%）
  - 去嵌（30–45%）
  - 指标计算（45–100%）
- 子进度条只显示对应阶段内的完成比例，同时显示当前 stage 文字标签；
- 任务完成后显示操作按钮：
  - **重新解压**：仅在 `raw_zip_exists`（后端返回批次信息时附带）为 true 时启用；
  - **重新去嵌**；
  - **重新计算指标**（点击后弹出多选框）。

### 2. BatchDetail 页

位置：`frontend/src/pages/BatchDetail.tsx`

- 在标题栏或操作区增加同样的三个按钮；
- “重新计算指标”按钮点击后弹出多选框：Qbode、Qs、Qp、kt2，确认后调用对应 API。

### 3. 重处理确认弹窗

- 新增通用确认组件 `ReprocessConfirmModal`（或内联实现）；
- 提示用户“重处理会覆盖现有结果，是否继续？”；
- 重新计算指标时额外显示指标多选。

### 4. 类型与状态

- `frontend/src/api/batches.ts` 增加 `reextractBatch`, `redeembedBatch`, `recomputeBatch` 三个 wrapper；
- 更新 `UploadTask` 类型中的 `stage` 字段为 `'extract' | 'deembed' | 'metrics' | 'done' | 'failed'`。

---

## 7z 进度研究结论

- 7z 官方 CLI 支持 `-bsp1` 输出进度到 stdout，内容形如：
  ```
  Extracting archive: /path/to.zip
  ...
  45% 2345 - some/file.s2p
  ```
- 不同平台/版本输出格式略有差异，正则提取百分比的可靠性约 90%，因此必须保留“已解压字节数 / 未压缩总字节数”的兜底方案；
- 不需要包装 7z 的 GUI 窗口；命令行输出解析足够，且避免引入额外依赖；
- 若 7z 不可用，自动降级到 `unzip` 或 Python `zipfile`，`zipfile` 可通过成员回调估算百分比。

---

## 错误处理与边界

| 场景 | 处理 |
|------|------|
| 7z 输出解析不到百分比 | 回退到已解压字节数轮询 |
| 重新解压时原始 zip 已删除 | 400 + 提示“原始数据包已清理，无法重新解压” |
| 重新去嵌缺少校准件 | 任务失败，error_msg 明确提示 |
| 重新计算某设备缺少 s_param_path | 跳过该设备，任务结束时在消息中汇总 |
| 重处理任务进行中再次触发 | 拒绝并提示“已有进行中的任务” |
| 桌面端 SQLite 无物化视图 | 不影响本功能，相关统计已在 `batch_stats_service.py` 中兼容 |

---

## 测试计划

1. 上传含去嵌的大 zip，验证三阶段进度均持续更新；
2. 手动删除 7z/使用 `unzip`/使用 `zipfile`，验证降级路径仍有进度；
3. 调用重新解压/重新去嵌/重新计算 4 个指标 API，验证数据库结果正确覆盖；
4. 前端 `npm run typecheck` 与 `npm run build` 通过；
5. 后端 `uv run pytest tests/workers tests/api` 通过；
6. 手动验证 TaskDetail / BatchDetail 重处理按钮可用。

---

## 待决策/默认取值

- 三阶段总体进度分配：0–30 / 30–45 / 45–100（已通过）；
- “重新计算指标”实际会运行完整提参，但只更新选中的列；这是为了在代码复用和性能之间取得平衡，后续若成为瓶颈可拆分为独立算法函数。
