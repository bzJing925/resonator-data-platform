# 内网服务器部署 + 桌面端数据同步 设计

日期：2026-09-15
状态：已确认

## 背景与目标

现有一台 **Windows 10/11 工作站**（常开、仅内网）作为服务器，部署数据平台全栈；原始 snp 数据集中存放在服务器上。笔记本只安装桌面版平台软件，不长期保存原始数据。

需求：

1. 笔记本连接内网后，可从服务器**同步表格数据**（批次/对照表/器件参数）到本地，离线也能使用全部分析功能。
2. 连接内网时，能从服务器**下载 snp 文件**，也能直接点开器件查看阻抗曲线、相位、S11 等（不必先下载文件）。
3. 数据上传可在**本地（桌面版）或服务器（浏览器）**任一端进行；本地上传了大量 snp 后，连接内网可把原始文件**回传到服务器**，服务器入库成功后笔记本删除本地原始文件。全程走内网 HTTP，不经过 GitHub。

已确认的关键决策：

- 服务器形态：Windows 10/11 工作站当服务器，按 `docs/deployment-windows.md` 用 Docker Desktop + WSL2 跑 5 容器。
- 使用模式：**多人使用，都会上传**。服务器是唯一事实来源。
- 回传策略：**方案 A —— 服务器重新提参**。笔记本推送原始 snp 打包 zip，服务器走标准 Celery 管线入库，保证服务器上所有数据经过同一条入库管线。
- 本地文件生命周期：推送成功（服务器入库完成且 device_count 一致）后**立即删除**本地原始文件，保留数据库行。

## 非目标（YAGNI）

- 不做用户认证/权限（沿用 v1 决策：内网隔离即可）；仅记录 `uploaded_by` 便于排查。
- 不做实时/自动后台同步；同步由用户显式触发（辅以启动时服务器可达性提示）。
- 不做笔记本之间直接同步；一切经服务器中转。
- 不改动 GitHub 静态网页版同步（`web_sync_service.py`）的现有行为；仅把可复用的导入逻辑抽出来共用。
- BodeQ 曲线不做服务器代理（实现决策：BodeQ 为分析增强功能，主曲线代理已覆盖查看需求）。
- 配置项落点为批次页"内网服务器同步"面板（应用本无设置页，规格中的"设置页"随之调整）。
- 离线镜像导出落为文档命令（deployment-windows.md 附录），未单独提供脚本。

## 部署拓扑

```
内网
┌──────────────────────────────┐        HTTP :8080        ┌─────────────────────────────┐
│ 服务器 (Win10/11 工作站, 常开) │ ◀──────────────────────▶ │ 笔记本 (Electron 桌面版)      │
│ Docker Desktop + WSL2:       │   快照下行 / 推送上行 /    │ 本地 SQLite (~/.aln-data)     │
│ postgres/redis/api/worker/   │   曲线与文件代理           │ 本地 Celery worker            │
│ nginx, DATA_ROOT=大容量盘     │                            │ 原始文件推送成功后即删         │
└──────────────────────────────┘                            └─────────────────────────────┘
```

- 服务器：`restart: unless-stopped` + Docker Desktop 开机自启；固定内网 IP。若服务器完全不能联网，提供 `docker save/load` 离线镜像包制作脚本。
- 笔记本：桌面版设置页新增配置项 **服务器地址**（如 `http://192.168.x.x:8080`）与 **用户名**（写入 `uploaded_by`）。服务器地址为空时，所有同步/远程功能隐藏或禁用提示。

## 组件设计

### 1. 服务器端同步 API（`backend/app/api/sync.py`，新增）

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/sync/snapshot` | POST | 请求体 `{"known_batch_nos": [...]}`；响应只含**新增批次**的 batches/mappings JSON + devices Arrow(gzip) |
| `/api/sync/push-batch` | POST | 接收笔记本推送的批次 zip + 元数据（批次名/mapping 名/频段/去嵌配置/用户名），幂等入库 |
| `/api/sync/mapping` | POST | 推送前确保服务器存在同名 mapping；比对内容，不一致返回冲突错误 |
| `/api/sync/sparam` | GET | 按自然键 `batch_no + filename + port` 返回曲线 JSON（阻抗/相位/S11），格式与 `/api/devices/{id}/sparam` 一致 |
| `/api/sync/download` | GET | 按自然键下载单个 s1p 文件 |

`push-batch` 状态机（幂等核心）：

- 批次不存在 → 创建批次与上传任务，走标准 Celery ingest 管线，返回任务 id。
- 批次已存在且上次任务成功 → 返回"已同步" + device_count。
- 批次已存在且上次任务失败/中断 → 清理旧任务后重新入库。

### 2. 快照导出/导入（服务层）

- 新增 `snapshot_service.py`（服务器侧导出 + 传输无关的导入逻辑）。
- 把现有 `web_sync_service.import_web_snapshot` 的增量导入逻辑（按批次名跳过已存在、mapping 按名匹配、整体事务 commit/rollback）抽入该模块；`web_sync_service` 改为调用它，GitHub 行为不变。
- 导出格式沿用网页版验证过的 batches/mappings JSON + devices Arrow(gzip)。同步响应只含客户端未知批次，十万行级数据量也只有几 MB。

### 3. 桌面端同步服务（`desktop_sync_service.py`，新增）

- **下行**："从服务器同步"按钮 → 收集本地全部 batch_no → `POST /api/sync/snapshot` → 事务性导入本地 SQLite → 返回新增统计。
- **上行**：本地批次新增同步状态标记（`synced_at` 时间戳，见 schema 变更）。推送对象仅限**本地上传且原始文件仍在本地**的批次（从快照导入的批次数据本来就在服务器上，不参与推送）。对每个未同步批次：
  1. `POST /api/sync/mapping` 确保 mapping 存在；
  2. 从 `files/<batch_no>/` 重新打包 zip（gzip 的 snp 直接装包），`POST /api/sync/push-batch`；
  3. 轮询 `/api/tasks/{id}` 直至完成；
  4. 校验服务器 device_count 与本地一致 → 删除 `files/<batch_no>/`，写入 `synced_at`；不一致 → 报"同名不同数据"，提示改名，不删文件。
- 断网/失败可随时重试，服务器幂等兜底。

### 4. 曲线查看与下载的透明代理（桌面后端）

- 现有 `/api/devices/{id}/sparam`、`/bodeq`、`/download-s1p` 增加降级分支：本地文件存在 → 现有逻辑不变；文件不存在且配置了服务器地址 → 用该 device 的自然键（batch_no + original_filename + s_param_port）调服务器 `/api/sync/sparam`（或 `/download`）并透传结果。
- 未配置服务器或服务器不可达 → 返回明确错误"需连接内网"。
- **前端零改动**：返回格式与本地一致。

### 5. Schema 变更（Alembic 迁移，桌面 SQLite 与服务器 PG 同一套）

- `batches.synced_at timestamptz NULL`：桌面端标记"原始文件已回传并删除"；服务器侧该列始终为 NULL（不影响）。
- 复用现有 `batches.source` / `uploaded_by` 字段区分数据来源。

### 6. 前端改动（仅桌面版设置/批次页）

- 设置页：服务器地址、用户名两项配置（存本地，如 `localStorage` 或桌面配置文件）。
- 批次页：每个本地批次显示同步状态（未同步/已同步），提供"推送到服务器"按钮与"从服务器同步"全局按钮。
- 其余页面不动。

## 数据流

**下行同步**：本地批次号列表 → 服务器差集快照 → 本地 SQLite 增量导入 → 分析功能离线可用。

**上行回传**：本地批次 zip → 服务器 push-batch（幂等）→ Celery 提参入库 → 桌面轮询确认 → 校验 count → 删本地文件、记 synced_at。

**在线看曲线**：前端请求本地后端 → 本地无文件 → 代理服务器自然键端点 → 曲线 JSON 透传 → 前端照常绘图。

## 冲突与错误处理

- **批次名冲突**：服务器唯一约束是幂等基石；重复推送返回"已同步"。device_count 不一致判定为"同名不同数据"，拒绝删除本地文件并提示改名。
- **mapping 同名不同内容**：推送前比对，冲突报错提示改名。
- **半推送/断网**：任何时刻重推都安全；快照导入整体事务，失败 rollback 不污染本地库。
- **服务器不可达**：同步按钮明确报错；曲线代理返回"需连接内网"；本地已有数据不受影响。

## 部署与运维

- 服务器首次部署按 `docs/deployment-windows.md`；补充：`restart: unless-stopped`、Docker 开机自启、固定 IP、`docker save/load` 离线镜像脚本。
- 备份沿用 `docs/operations.md`（pg_dump + DATA_ROOT 备份），建议服务器侧加定时任务。

## 测试

- **后端单测**：快照导出→导入 round-trip（空库导入后参数逐行一致）；push-batch 三种状态（新批次/已成功/失败重来）幂等；mapping 冲突；自然键 sparam/download 端点。
- **桌面端**：同步状态机（推送→轮询→校验→删文件→标记 synced_at）；曲线代理降级（有文件/无文件/服务器不可达三分支）。
- **端到端**：本机同时起服务器栈与桌面后端，跑"本地上传→推送→删本地→远程看曲线→另一客户端拉快照"全流程。

## 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| Docker Desktop 重启后容器未起 | `restart: unless-stopped` + 开机自启 + operations.md 排查手册 |
| 大批量回传耗时长 | 服务器异步任务 + 桌面端队列化逐批推送；zip 内 snp 已 gzip |
| 两端算法版本不一致导致参数差异 | 回传由服务器重新提参（方案 A），桌面端参数仅作本地过渡，以服务器为准 |
| 误删本地文件 | 仅在服务器入库成功且 count 校验通过后删除；删除只针对 `files/<batch_no>/`，数据库行保留 |
