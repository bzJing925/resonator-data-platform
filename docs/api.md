# API 设计

**版本**：v0.1
**基础路径**：`/api`
**契约**：JSON over HTTP，错误用标准 HTTP 状态码 + `{"detail": "..."}` 体
**认证**：v1 无认证（裸开，靠内网隔离）
**OpenAPI 自动文档**：FastAPI 提供 `/api/docs`（Swagger UI）和 `/api/redoc`

---

## 1. 端点总览

| 模块 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 上传 | POST | `/api/uploads` | 上传 zip + 启动处理任务 |
| 上传 | POST | `/api/uploads/chunk` | 分块上传（>100MB 文件用） |
| 任务 | GET | `/api/tasks/{task_id}` | 任务详情 |
| 任务 | POST | `/api/tasks/{task_id}/cancel` | 取消进行中的任务并清理上传文件 |
| 任务 | GET | `/api/tasks/{task_id}/stream` | SSE 实时推进度 |
| 任务 | GET | `/api/tasks` | 任务列表（最近 50） |
| 批次 | GET | `/api/batches` | 批次列表（分页） |
| 批次 | GET | `/api/batches/{batch_no}` | 批次详情 |
| 批次 | DELETE | `/api/batches/{batch_no}` | 删除批次（含文件） |
| 批次 | GET | `/api/batches/{batch_no}/devices` | 批次内器件列表（分页 + 筛选） |
| 对照表 | GET | `/api/mappings` | 对照表列表 |
| 对照表 | POST | `/api/mappings` | 上传新对照表 |
| 对照表 | GET | `/api/mappings/{id}/entries` | 查看对照表内容 |
| 对照表 | DELETE | `/api/mappings/{id}` | 删除（仅当无批次引用） |
| 查询 | POST | `/api/query/devices` | 跨批次器件查询（筛选 + 字段裁剪） |
| 查询 | POST | `/api/query/aggregate` | 聚合统计（用于箱型图分组） |
| 查询 | GET | `/api/query/fields` | 可筛选/作图的字段元数据 |
| 曲线 | GET | `/api/devices/{id}/sparam` | 单器件 S 参数曲线（现读现画） |
| 曲线 | GET | `/api/devices/{id}/bodeq` | 单器件 BodeQ 曲线（按需算） |
| 导出 | POST | `/api/export/csv` | CSV 导出（同步流式） |
| 导出 | POST | `/api/export/xlsx` | Excel 导出（异步落 exports/） |
| 导出 | GET | `/api/exports/{id}` | 下载导出文件 |
| 系统 | GET | `/api/health` | 健康检查 |
| 系统 | GET | `/api/stats` | 系统总览（批次/器件/磁盘） |

---

## 2. 上传

### POST `/api/uploads`

**请求**（`multipart/form-data`）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| file | binary | 是 | zip 包，文件名（去扩展名）即批次号 |
| mapping_id | int | 是 | 对照表 ID |
| f_start_ghz | float | 否 | 起始频率，留空全频段 |
| f_end_ghz | float | 否 | 结束频率，留空全频段 |
| process_type | str | 否 | `S1P` / `S2P` / `BOTH`，默认 `BOTH` |
| deembed | bool | 否 | 是否做 ShortOpen 去嵌，默认 `false`。`true` 且 zip 含 OPEN/SHORT 校准件时走 `aln.pipeline_batch` 边解压边计算链路 |
| deembed_method | str | 否 | 去嵌匹配方法：`default` / `original` / `vz` / `gsg100` / `basic`，默认 `default` |

**响应** `202 Accepted`：

```json
{
  "task_id": "9f8e7d6c-...",
  "batch_no": "T8901P.01",
  "status": "pending",
  "stream_url": "/api/tasks/9f8e7d6c-.../stream"
}
```

**错误**：
- `400` 文件不是 zip / 没传 file / 没传 mapping_id
- `409` 批次号已存在（`{"detail": "batch T8901P.01 already exists"}`）
- `413` 文件超过 `UPLOAD_MAX_GB` 限制
- `422` mapping_id 不存在

### POST `/api/uploads/chunk`

仅当文件 > 100MB 时使用，分块上传协议（待实现细节，建议直接用 [tus](https://tus.io/) 或 element-plus 自带分片）。

---

## 3. 任务

### GET `/api/tasks/{task_id}`

```json
{
  "id": "9f8e7d6c-...",
  "batch_no": "T8901P.01",
  "status": "running",
  "kind": "upload",
  "progress_pct": 42,
  "progress_msg": "提取参数中（4382/10000）",
  "stage": "metrics",
  "stage_progress_pct": 35,
  "started_at": "2026-05-09T12:34:56Z",
  "finished_at": null,
  "cancelled_at": null,
  "error_msg": null,
  "raw_zip_deleted": false
}
```

- `kind`：`upload` / `reextract` / `redeembed` / `recompute`。
- `stage` / `stage_progress_pct`：当前阶段与阶段内进度。
- `raw_zip_deleted`：原始 zip 是否已清理。

### POST `/api/tasks/{task_id}/cancel`

取消状态为 `pending` 或 `running` 的任务。

- 对 `upload` 任务：停止 worker 后删除 batch（级联删 devices / file_nodes）、解压目录和原始 zip。
- 对重处理任务（`reextract`/`redeembed`/`recompute`）：仅停止 worker，不删除已有批次数据。
- 重复取消已 `cancelled` 的任务幂等，直接返回当前任务详情。

**响应** `200 OK`：当前 `TaskDetail`。

**错误**：
- `404` 任务不存在
- `409` 任务已结束（`success` / `failed`）

### GET `/api/tasks/{task_id}/stream`

**SSE 流**，事件格式：

```
event: progress
data: {"progress_pct": 42, "progress_msg": "提取参数中"}

event: done
data: {"status": "success", "batch_no": "T8901P.01", "device_count": 9876}

event: error
data: {"error_msg": "..."}
```

前端 EventSource 订阅，收到 `done` 或 `error` 后关闭连接。

---

## 4. 批次

### GET `/api/batches?page=1&size=20&sort=-uploaded_at`

```json
{
  "total": 18,
  "page": 1,
  "size": 20,
  "items": [
    {
      "batch_no": "T8901P.01",
      "mapping_name": "ELB003",
      "device_count": 9876,
      "f_start_ghz": null,
      "f_end_ghz": null,
      "deembedded": false,
      "uploaded_at": "2026-05-09T12:34:56Z"
    }
  ]
}
```

### GET `/api/batches/{batch_no}`

完整批次详情，含 wafer 列表、统计概览（fs 平均/中位、Pass 率）。

### DELETE `/api/batches/{batch_no}`

**响应** `204 No Content`，删除 DB 记录 + `/data3/aln/files/{batch_no}/` 目录。

### GET `/api/batches/{batch_no}/devices?wafer=2&pf=Y&page=1&size=100`

分页 + 筛选。返回器件行（与 query/devices 同结构）。

---

## 5. 对照表

### GET `/api/mappings`

```json
[
  {
    "id": 1,
    "name": "ELB003",
    "entry_count": 749,
    "uploaded_at": "...",
    "in_use_by_batches": 5
  }
]
```

### POST `/api/mappings`

`multipart/form-data`：`file` (xlsx) + `name` (str)。

后端读 xlsx → 解析每行 Description token → 写 `mappings` + `mapping_entries`。

### GET `/api/mappings/{id}/entries?page=1&size=100`

```json
{
  "total": 749,
  "items": [
    {"mark": "A1-1", "description": "EG0 FL0 700&5500", "eg": 0, "fl": 0, "ag": null, "area_s11": 700, "area_s22": 5500}
  ]
}
```

### DELETE `/api/mappings/{id}`

`409` 若有批次引用。

---

## 6. 查询（核心分析接口）

### POST `/api/query/devices`

**请求体**：

```json
{
  "filters": {
    "batch_no": ["T8901P.01", "T8902"],
    "wafer": [2, 3],
    "pf": ["Y"],
    "eg": {"in": [0, 0.75]},
    "fl": {"gte": 0, "lte": 2},
    "fs_ghz": {"gte": 14, "lte": 16}
  },
  "fields": ["batch_no", "wafer", "coord", "x", "y", "fs_ghz", "qs", "k2eff_pct"],
  "limit": 50000,
  "order_by": "fs_ghz"
}
```

**响应**：

```json
{
  "total": 31482,
  "returned": 31482,
  "truncated": false,
  "rows": [
    {"batch_no": "T8901P.01", "wafer": 2, "coord": "X0Y0", "x": 0, "y": 0, "fs_ghz": 14.523, "qs": 1234.5, "k2eff_pct": 6.78}
  ]
}
```

**约束**：
- `limit` 上限硬编码 200000（防爆内存）
- 超出 `limit` 时 `truncated=true`，前端提示用户加筛选

### POST `/api/query/aggregate`

用于箱型图、分组统计：

```json
{
  "filters": {...},
  "group_by": ["eg", "fl"],
  "metrics": [
    {"field": "qs", "agg": ["min", "p25", "p50", "p75", "max", "count"]}
  ]
}
```

**响应**：

```json
{
  "groups": [
    {"eg": 0, "fl": 0, "qs": {"min": 100, "p25": 800, "p50": 1200, "p75": 1500, "max": 2000, "count": 1234}}
  ]
}
```

### GET `/api/query/fields`

返回可作图字段元数据，给前端下拉框用：

```json
{
  "categorical": [
    {"name": "batch_no", "label": "批次号", "values_endpoint": "/api/query/distinct?field=batch_no"},
    {"name": "wafer", "label": "Wafer"},
    {"name": "pf", "label": "Pass/Fail", "values": ["Y", "N"]},
    {"name": "folder_name", "label": "端口", "values": ["S11", "S22"]}
  ],
  "geometric": [
    {"name": "x", "label": "X 坐标"},
    {"name": "y", "label": "Y 坐标"}
  ],
  "numeric": [
    {"name": "fs_ghz", "label": "fs (GHz)", "unit": "GHz"},
    {"name": "fp_ghz", "label": "fp (GHz)", "unit": "GHz"},
    {"name": "zs_ohm", "label": "Zs (Ω)", "unit": "Ω"},
    ...
  ],
  "process": [
    {"name": "eg", "label": "EG"},
    {"name": "fl", "label": "FL"},
    {"name": "ag", "label": "AG"}
  ]
}
```

---

## 7. 单器件曲线（现读现画）

### GET `/api/devices/{id}/sparam?param=s11_db`

`param` 可选：`s11_db` / `s11_phase` / `s11_re_im` / `z_mag_db` / `z_phase`。

```json
{
  "device_id": 12345,
  "freq_ghz": [3.0, 3.001, ..., 20.0],
  "values": [-0.1, -0.15, ..., -25.3],
  "param": "s11_db",
  "file_path": "T8901P.01/wafer2/S11/17_E6-1_X0Y0N18_Fail_S11.s1p.gz"
}
```

后端用 scikit-rf 现读 `.s1p` 或 `.s1p.gz`；pipeline 归档后的 gzip 文件会被透明解压。

#### PINN 快速路径

追加 `?fast=1` 可启用训练好的 PINN 模型（~0.6 ms，比 skrf 快 ~100 倍）。仅对 `param=s11_db` 生效；其他参数或 PINN 不可用时自动 fallback 到 skrf。

```json
{
  "device_id": 12345,
  "freq_ghz": [4.0, 4.003, ..., 7.0],
  "values": [-0.32, -0.33, ..., -1.85],
  "param": "s11_db",
  "file_path": "...",
  "source": "pinn"
}
```

### GET `/api/devices/{id}/bodeq`

返回 BodeQ raw / smooth / fitted 三条曲线 + 标记 fs/fp 的位置。

---

## 8. 导出

### POST `/api/export/csv`

请求体同 `/api/query/devices`，响应是 `Content-Type: text/csv` 的流式下载，`Content-Disposition: attachment; filename=devices_20260509.csv`。

### POST `/api/export/xlsx`

数据量大（>10 万行）时用，返回 `task_id`：

```json
{"task_id": "...", "stream_url": "/api/tasks/.../stream"}
```

任务完成后通过 `/api/exports/{id}` 下载。

### GET `/api/exports/{id}`

下载文件。文件 24 小时后自动清理。

---

## 9. 系统

### GET `/api/health`

```json
{"status": "ok", "db": "ok", "redis": "ok", "disk_free_gb": 6890}
```

### GET `/api/stats`

```json
{
  "batches": 18,
  "devices": 432156,
  "mappings": 3,
  "disk_used_gb": 28.4,
  "disk_free_gb": 6890,
  "tasks_pending": 0,
  "tasks_running": 1
}
```

---

## 10. 错误码约定

| 状态码 | 场景 |
|---|---|
| 400 | 请求体格式错、参数校验失败 |
| 404 | 资源不存在 |
| 409 | 冲突（批次重名、对照表被引用） |
| 413 | 上传文件过大 |
| 422 | Pydantic 验证失败（FastAPI 默认） |
| 500 | 服务端异常 |

错误响应统一：

```json
{"detail": "human readable message", "code": "BATCH_DUPLICATE"}
```

---

## 11. 待确认

1. 大文件上传：直接走 `multipart/form-data`（依赖 nginx `client_max_body_size`），还是引入 tus 协议？取决于客户网络情况。
2. 是否支持上传**未压缩的文件夹**？需要前端先打成 zip。建议：**仅接受 zip**，前端用 JSZip 在浏览器侧打包。
3. 滤波器入口是否也要 `/api/uploads?type=filter`？建议预留 `type` 字段，v1 只支持 `resonator`。
