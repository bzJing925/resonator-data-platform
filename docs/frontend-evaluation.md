# 前端原型契合度评估

**评估时间**：2026-05-09
**评估范围**：`/tmp/frontend_inspect/`（8 个源文件，3 162 行）
**对比基准**：`docs/api.md`（24 个端点）+ `backend/app/schemas/` Pydantic 模型 + `backend/app/api/` 路由实现
**结论一句话**：原型是一个**纯静态、无任何 HTTP 调用**的 React 18 视觉稿，覆盖了 6 个核心视图，整体信息架构与后端契约**契合度高（约 85%）**，但所有数据均为前端 mock 生成，且字段命名与后端存在系统性差异，**不能直接联调**，需要新建一层 `src/api/` 客户端 + 字段名 / 形态适配层。

---

## 1. UI 清单（Inventory）

`app.jsx:38-58` 定义了 6 个一级视图（外加 1 个占位“滤波器（二期）”）。视图切换是单组件 `useState("analysis")` 驱动，**没有引入 React Router**。

| ID | 名称 | 文件 / 行号 | 截屏式描述 |
|---|---|---|---|
| `dashboard` | Dashboard | `other-views.jsx:7-140` | 6 个 KPI tile（批次/器件/对照表/磁盘/任务/Pass 率）+ 趋势图 + 箱型图 + “最近上传任务”表 |
| `analysis` | 数据分析 | `analysis.jsx:235-359` | 三栏工作台：左 FilterPanel、中 Chart Card（散点/箱型/折线/Wafer Map 四种图）、右 Inspector（轴编码、渲染选项、查询统计、选中器件、导出） |
| `device` | 单器件查看 | `other-views.jsx:549-688` | 三栏：左元数据 + 提取参数表、中 S 参数曲线 + 邻近 wafer 小图、右叠图列表（最多 8 条曲线） |
| `batches` | 批次管理 | `other-views.jsx:372-449` | 顶部搜索/筛选条 + 批次大表格（11 列：批次号 / 对照表 / 器件数 / Pass 率 / fs 范围 / Wafer / De-embed / 上传时间 / 状态 / 操作） |
| `mappings` | 对照表 | `other-views.jsx:454-544` | 左侧对照表列表（卡片式）、右侧条目大表（Mark / Description / EG / FL / AG / Area S11 / Area S22） |
| `upload` | 上传 | `other-views.jsx:145-354` | 4 步向导（选择文件 / 处理选项 / 预检报告 / 处理中）+ 模拟终端日志 |

**模态层**：`SparamModal`（`analysis.jsx:397-438`）从 Inspector 的“查看 S 参数曲线”按钮唤起。

**复用情况**（基本上 ad-hoc，没有抽取）：
- `chart-card / chart-head / chart-body / stat-strip / dtable / panel / toolbar / btn / chip / badge / cb / switch / field` 这些**只是 CSS class**，没有对应 React 组件封装。
- 表格全部直接 `<table className="dtable">` 手写 thead/tbody，没有数据驱动的 DataTable 组件。
- 唯一被多处引用的组件是 `Charts.{ScatterPlot, BoxPlot, WaferMap, TrendChart, SParamCurve}`（`charts.jsx`）。
- `tweaks-panel.jsx` 是**演示工具**（变体切换面板），生产环境应整体移除。

---

## 2. 数据来源：纯 mock，零 HTTP

```
$ grep -nE "fetch\(|axios\.|XMLHttpRequest|EventSource" /tmp/frontend_inspect/*.jsx
（无输出）
```

**没有任何**网络层代码。所有数据来自三处：

1. **算法生成的合成数据**（`charts.jsx:30-110`）：`genScatter(n, seed)` 返回 1 500 行随机点；`genWafer(seed)` 返回 hex 圆形 die 网格；`genSparam(seed)` 返回 401 点的 S 参数曲线。
2. **写死在组件里的 mock 数组**：
   - 批次表 `other-views.jsx:373-383`（10 行）
   - 任务表 `other-views.jsx:8-14`（5 行）
   - Mapping 列表 / 条目 `other-views.jsx:456-472`（3 + 10 行）
   - 单器件元数据表 `other-views.jsx:572-601`（全是字面量 `<td>14.7843 GHz</td>`）
3. **UI 占位字符串**：状态栏的 `api · 12 ms`、`pg · 4 ms`（`app.jsx:101`），Inspector 里的 `SQL exec 42 ms / Net 118 ms / Render 86 ms`（`analysis.jsx:192-194`）—— 这些都是装饰，没有任何后端探针。

**上传组件无真实 POST**。`other-views.jsx:204-217` 的拖拽区点击只是 `setHasFile(true)`，启动入库按钮 `onClick={() => setRunning(true)}` 仅切换本地 state；终端日志 `:338-346` 全是字面量字符串。

**SSE / EventSource 同样为零**。`Statusbar` 显示 “1 task running · T8907P.01 64%” 是硬编码字符串。

---

## 3. 数据结构契合度（关键差异表）

### 3.1 通用字段命名差异

前端 `FIELDS` 定义见 `analysis.jsx:6-22`，`genScatter` 输出形态见 `charts.jsx:50-60`：

```js
{ id, fs, qs, k2eff, eg, fl, pf, batch, wafer }   // 前端
```

后端 `ResonatorRow`（`backend/app/schemas/resonator.py:17-73`）+ `/api/query/devices` 返回字段：

```py
{ batch_no, wafer, coord, x, y, fs_ghz, fp_ghz, qs, qp, k2eff_pct, zs_ohm, zp_ohm, ... }
```

**差异表**（① 改前端 / ② 改后端 / ③ 双向）：

| 前端字段 | 后端字段 | 差异类型 | 推荐 |
|---|---|---|---|
| `fs` | `fs_ghz` | 命名 | ① 前端改 |
| `fp` | `fp_ghz` | 命名 | ① 前端改 |
| `qs` / `qp` | `qs` / `qp` | 一致 | — |
| `k2eff` | `k2eff_pct` | 命名 | ① 前端改 |
| `zs` / `zp` | `zs_ohm` / `zp_ohm` | 命名 | ① 前端改 |
| `batch` | `batch_no` | 命名 | ① 前端改 |
| `id`（int 0~1499） | 后端用 device_id（PK） | 类型/语义 | ① 前端改 |
| —（前端没有） | `coord` / `x` / `y` / `mark` / `folder_name` / `display_name` / `s_param_path` | 缺失 | ① 前端补 |
| —（前端没有） | `qs_bodeq` / `qp_bodeq` / `dbqs` / `dbqp` / `bodeq_*` / `fbode_ghz` / `area_n` / `area_um2` / `fs2_ghz` / `fp2_ghz` ... | 缺失 | ① 前端按需补 |

**展示层映射**：`/api/query/fields` 已经规定了 `label`（如 `fs (GHz)`、`Zs (Ω)`），前端应直接消费这套元数据，**而不是** 在 `analysis.jsx:6` 写死一份 `FIELDS`。

### 3.2 批次列表（`/api/batches`）

`other-views.jsx:373-383` 的 mock 行：

```js
{ batch, mapping, devices, pf, date, status }
```

后端 `BatchListItem`（`schemas/batch.py:10-18`）：

```py
{ batch_no, mapping_name, device_count, f_start_ghz, f_end_ghz, deembedded, process_type, uploaded_at }
```

| 前端 | 后端 | 差异类型 | 推荐 |
|---|---|---|---|
| `batch` | `batch_no` | 命名 | ① 前端改 |
| `mapping` | `mapping_name` | 命名 | ① 前端改 |
| `devices` | `device_count` | 命名 | ① 前端改 |
| `pf`（数值百分比） | 无（`BatchDetail.stats.pass_rate` 才有） | 列表不返回 | ② 后端在 `BatchListItem` 加 `pass_rate`，或 ① 前端列表此列改 “—” |
| `date` | `uploaded_at`（ISO datetime） | 命名 + 格式 | ① 前端改：`new Date(uploaded_at).toLocaleString()` |
| `status`（`ok/running/failed`） | 后端没这字段 | 缺失 | ③ 后端 `BatchListItem` 补 `last_task_status` 或前端从 `/api/tasks` 联表显示 |
| —（前端没渲染） | `f_start_ghz` / `f_end_ghz` / `deembedded` / `process_type` | 后端有但前端列硬编码 | ① 前端改：表格列 `fs 范围` 当前固定写 `14.02 – 15.98 GHz`（`other-views.jsx:426`），应换成 `${f_start_ghz} – ${f_end_ghz}` |

### 3.3 任务（`/api/tasks` + SSE）

`other-views.jsx:8-14`：

```js
{ id: "9f8e", batch, status, pct, msg, elapsed }
```

后端 `TaskDetail`（`schemas/task.py:10-20`）：

```py
{ id: int, batch_no, status, progress_pct, progress_msg, started_at, finished_at, error_msg }
```

| 前端 | 后端 | 差异 | 推荐 |
|---|---|---|---|
| `id`（4 位 hex 截断） | `id`（int，api.md 又写 UUID 字符串） | 类型不一致 + api.md 与 schema 自相矛盾 | ③ 先确认后端真实类型（schema 写 int，api.md 写 UUID），统一后再让前端跟随 |
| `batch` | `batch_no` | 命名 | ① 前端改 |
| `pct` | `progress_pct` | 命名 | ① 前端改 |
| `msg` | `progress_msg` | 命名 | ① 前端改 |
| `elapsed`（`"04:32"` 字符串） | `started_at` / `finished_at`（datetime） | 计算责任 | ① 前端按 now - started_at 即时算 |
| 4 个状态字面量：`running/success/failed`（前端）vs api.md 写 `pending/running/success/failed/error` | — | 状态枚举不全 | ① 前端补 `pending` 与 `error` 两个状态徽章 |

**SSE 适配**：上传向导的“处理中”面板（`other-views.jsx:302-350`）当前是静态 4 步进度条 + 字面量日志。需要替换为：
- 上传成功后从 `/api/uploads` 返回的 `stream_url` 建立 `EventSource`；
- 监听 `progress` / `done` / `error` 三种事件；
- `progress.data.progress_pct` 推进进度条，`progress_msg` 显示在副文案；
- `done` 关闭连接并跳转批次详情。

### 3.4 对照表（`/api/mappings/{id}/entries`）

`other-views.jsx:462-472`：

```js
{ mark, desc, eg, fl, ag, s11, s22 }
```

后端 `MappingEntryItem`（`schemas/mapping.py:18-26`）：

```py
{ mark, description, eg, fl, ag, area_s11, area_s22, has_pf }
```

| 前端 | 后端 | 差异 | 推荐 |
|---|---|---|---|
| `desc` | `description` | 命名 | ① 前端改 |
| `s11` / `s22` | `area_s11` / `area_s22` | 命名 | ① 前端改 |
| 列名 “Area S11 (μm²)” | `area_um2` 是 ResonatorRow 的派生字段 | 单位标注 OK | — |
| —（前端没渲染） | `has_pf` | 缺失 | ① 前端补一列 |

### 3.5 上传表单字段

上传向导（`other-views.jsx:226-261`）的 UI 控件：

| UI 字段 | 后端 form 字段（`api.md:46-54`） | 差异 |
|---|---|---|
| 对照表 select（用 name 当 value） | `mapping_id`（int） | ① 前端 select 的 value 必须改为 mapping.id，不能是 name |
| 频率范围两个 input（默认 14.0 / 16.0） | `f_start_ghz` / `f_end_ghz` | 命名一致，前端转 number |
| 处理类型 segmented（`S2P / S1P / BOTH`） | `process_type`（`S1P`/`S2P`/`BOTH`） | 一致 |
| De-embedding switch | `deembed`（bool） | 命名一致 |
| —（前端没有） | `file` | ① 前端拖拽未真正绑定 FormData |

### 3.6 单器件 S 参数曲线

`SParamCurve` 接收的 `data` 是 `genSparam` 生成的 `{freqs, s11, s11ph, bode, fs, fp}`（`charts.jsx:81-110`）。后端 `/api/devices/{id}/sparam` 返回（`api.md:288-300`）：

```json
{ "device_id", "freq_ghz", "values", "param", "file_path" }
```

差异：
- 前端按 `s11 / s11ph / bode` 分别准备好三组数组、一次性传给同一个组件；后端是 `?param=s11_db|s11_phase|bodeq` **三次请求**才能拿齐。
- 前端 key 用 `freqs`，后端是 `freq_ghz`；前端 `s11` 数组对应后端 `values`。
- 推荐：① 前端改造 `SParamCurve`，在 modal/tab 切换时拉对应 param。或保留单次合并的接口，需 ② 后端新增聚合端点（不推荐，增加耦合）。

### 3.7 查询过滤器结构

筛选面板（`analysis.jsx:67-117`）的 UI 状态目前是 `useState({})` 空对象，从未被消费。后端契约（`api.md:198-211`）期望：

```json
{
  "batch_no": ["T8901P.01", "T8902"],
  "wafer": [2, 3],
  "pf": ["Y"],
  "eg": {"in": [0, 0.75]},
  "fl": {"gte": 0, "lte": 2},
  "fs_ghz": {"gte": 14, "lte": 16}
}
```

需要 ① 前端：把 FilterPanel 的勾选 / 范围输入序列化到该 schema，再 POST。

---

## 4. 功能覆盖差异（24 端点对账）

| # | 端点 | 前端是否覆盖 | 备注 |
|---|---|---|---|
| 1 | `POST /api/uploads` | UI 有 | 仅占位，未发请求 |
| 2 | `POST /api/uploads/chunk` | 无 | v1 后端也是 501 |
| 3 | `GET /api/tasks` | UI 有（Dashboard 表） | mock |
| 4 | `GET /api/tasks/{id}` | 无（轮询用） | 上传向导需补 |
| 5 | `GET /api/tasks/{id}/stream` | 无（关键缺失） | 上传向导必须接 |
| 6 | `GET /api/batches` | UI 有 | mock |
| 7 | `GET /api/batches/{batch_no}` | 无 | BatchesView 缺“详情”侧栏/页 |
| 8 | `DELETE /api/batches/{batch_no}` | UI 有删除按钮（mappings 区） | 批次表无删除入口 |
| 9 | `GET /api/batches/{batch_no}/devices` | UI 有“器件列表”按钮（无具体页） | 缺 |
| 10 | `GET /api/mappings` | UI 有 | mock |
| 11 | `POST /api/mappings` | UI 有“上传对照表”按钮 | 未实现表单 |
| 12 | `GET /api/mappings/{id}/entries` | UI 有 | mock |
| 13 | `DELETE /api/mappings/{id}` | UI 有删除按钮 | mock |
| 14 | `POST /api/query/devices` | 隐含（AnalysisView）| 全部走 mock |
| 15 | `POST /api/query/aggregate` | 隐含（BoxPlot 用）| 用 mock |
| 16 | `GET /api/query/fields` | **无**（前端硬写 `FIELDS`） | 应改为消费 |
| 17 | `GET /api/query/distinct` | 隐含（FilterPanel 批次列表）| 应消费 |
| 18 | `GET /api/devices/{id}/sparam` | UI 有 | mock |
| 19 | `GET /api/devices/{id}/bodeq` | UI 有（BodeQ tab）| mock |
| 20 | `POST /api/export/csv` | UI 有按钮 | 未连 |
| 21 | `POST /api/export/xlsx` | UI 有按钮 | 未连 |
| 22 | `GET /api/exports/{id}` | 无 | 异步导出回调缺 |
| 23 | `GET /api/health` | 无（状态栏 fake）| Titlebar 三个 pill 应轮询 health |
| 24 | `GET /api/stats` | UI 有（Dashboard tile） | 应消费 |

**前端做了但后端不需要的**：
- “WebGL 加速 / 自动降采样 / 对数 Y 轴”（`analysis.jsx:175-186`）属于纯前端 Plotly/canvas 配置，无需后端配合。
- 状态栏的 PostgreSQL / Redis / Celery 健康指示，后端 `/api/health` 只暴露聚合的 `db / redis / disk_free_gb`，没有 `worker count`。前端要么简化展示，要么后端 ② 扩展 `health` 返回 worker 数。

**前端没做但应该做的**：
- 任务 SSE 订阅（关键路径）；
- 批次详情侧栏（含 wafer 列表 + stats）；
- 批次器件列表分页（`GET /api/batches/{batch_no}/devices`）；
- 异步导出任务 → 下载流（`/api/exports/{id}`）；
- `/api/query/distinct` 来填充 FilterPanel 的批次/wafer 选项（现在写死 4 个批次、3 片 wafer）；
- 统一字段元数据消费（`/api/query/fields`）。

---

## 5. 工程化问题

### 5.1 加载方式：CDN UMD + Babel standalone（生产不可接受）

`index.html:11-13`：

```html
<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" ...></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" ...></script>
<script type="text/babel" src="icons.jsx"></script>
```

问题：
- 浏览器运行时 transpile JSX，首屏 200~500 ms 阻塞，文件越多越慢；
- `react.development.js` 含开发警告，体积大；
- 6 个 `<script type="text/babel">` 串行加载，没有 tree-shaking、没有压缩；
- 全局污染：所有组件挂在 `window.AnalysisView / DashboardView / Charts / I` 上（如 `analysis.jsx:440-442`），没有 ES module。

**必须迁移到 Vite + 真打包**。

### 5.2 框架选型：React 18 vs 文档要求的 Vue 3

`docs/architecture.md:22, 83-88` 明确要求 **Vue 3 + Element Plus + Vite + Pinia + axios + Plotly + AG Grid**。

但拿到的原型是 **React 18** + 自写 SVG 图表（`charts.jsx` 没用 Plotly）+ 自写 CSS（无 Element Plus / no Tailwind / no antd）。

决策点：
1. **顺势改 React**：原型已经是 React，重写为 Vue 工作量约 80%（视图层全部要重写，唯一能复用的是 styles.css 和算法）。优势：少返工。代价：需更新 architecture.md，并重新选 React 生态对应物（Element Plus → Ant Design / shadcn-ui，Pinia → Zustand / Redux Toolkit，AG Grid 通用，Plotly 通用）。
2. **坚持 Vue 3**：把原型当“静态视觉稿”用，重写 Vue 工程，复用 styles.css + 设计稿。代价：扔掉约 2 500 行 React 组件代码。

**推荐方案 1**：原型工程量已经投入，UI 信息密度和 EDA 风格匹配本场景，改 Vue 没有实质收益。**前提是更新 architecture.md** 把技术栈一栏改成 React 18 + Vite + TanStack Query + Ant Design / Mantine（建议）/ shadcn 二选一 + Zustand + axios + Plotly.js。

### 5.3 路由 / 状态管理：完全没有

- 视图切换：`useState("analysis")` + 一堆 `view === "..." && <X />`（`app.jsx:14, 52-57`）。无法分享 URL、无法浏览器后退。
- 状态管理：每个视图独立 `useState`，跨视图无共享。当 AnalysisView 选中一个器件，跳到 DeviceView 不会带过去。
- 必须引入 React Router v6 + 全局状态（轻量用 Zustand，重量用 Redux Toolkit）。

### 5.4 样式：自写 CSS（666 行）

`styles.css` 是手写的 CSS 变量主题系统，包含 `--bg-panel / --primary / --t1..t5` 等。质量不错（EDA 工业风格，密度可调），但：
- 无 CSS Modules / 无 BEM 命名约束，class 全是全局；
- Tweaks Panel 注入的 CSS 直接写在 JS 里（`tweaks-panel.jsx:49-`）；
- 与 Ant Design / Element Plus 的设计语言冲突，引入组件库会有视觉撕裂。

**推荐**：保留 styles.css 作为定制层，组件库选无强视觉的（如 shadcn/ui 或 Radix Primitives）配合，避免重复造轮子又能保留这个工业风。

### 5.5 charts.jsx：自写 SVG vs Plotly

`charts.jsx` 467 行实现了 4 种图表（散点 / 箱型 / Wafer Map / 趋势 / S 参数），用纯 SVG。
- 优点：体积小、可控、风格统一。
- 缺点：性能在 5 万点散点上是炸的（PRD 要求 1 s 内渲染），没有 WebGL，没有 zoom/pan。

**对接后端时**：散点图必须换 Plotly 的 `scattergl`（架构文档 `architecture.md:283` 写过 5 万点目标）。Wafer Map / Box / 单器件曲线可以保留自写 SVG。

---

## 6. 动作清单（按优先级）

### P0：技术栈决策（先确认再开干）

**① 决定 React 还是 Vue。** 推荐顺势改 React，更新 `docs/architecture.md` §3.3 表格、§7 工程结构。半天工作量。

### P1：工程骨架迁移（约 2 天）

**② 把 CDN 原型迁到 Vite + TypeScript 工程**：
- 新建 `frontend/` 目录，`pnpm create vite@latest frontend --template react-ts`
- 把 `*.jsx` → `*.tsx`，挪到 `src/views/`、`src/components/`、`src/charts/`
- 移除 `window.X = X` 全局污染，改 ES module export
- 移除 `tweaks-panel.jsx`（仅演示用，不需要进生产）
- 整体保留 styles.css，转 CSS Module 或 vanilla CSS @import

**③ 在 `src/api/` 实现 axios 客户端**：
- `client.ts`：baseURL = `/api`，统一拦截器把 `{detail, code}` 错误抛给上层
- `batches.ts` / `mappings.ts` / `query.ts` / `tasks.ts` / `devices.ts` / `uploads.ts` / `system.ts`
- 类型定义直接从 `backend/app/schemas/*.py` 用 `datamodel-code-generator` 或 OpenAPI client 生成（FastAPI 自带 `/api/openapi.json`，可一键生成 TS 类型）

### P2：字段映射 + 关键端点联调（约 3 天）

**④ 统一字段元数据**：删除 `analysis.jsx:6-22` 的硬写 `FIELDS`，改为启动时拉 `/api/query/fields` 缓存到全局 store；FilterPanel 的批次/wafer 选项拉 `/api/query/distinct`。

**⑤ 字段命名映射**：把前端 mock 数据里的 `fs / qs / k2eff / batch` 全部替换成 `fs_ghz / qs / k2eff_pct / batch_no`（参考第 3 节差异表）。这是搜索替换工作。

**⑥ 上传向导接 SSE**：上传成功后立即用 `EventSource(stream_url)` 接 progress 推送（关键路径，影响用户体验）。

### P3：补缺失视图（约 2 天）

**⑦ 批次详情页 / 批次内器件列表分页 / 异步导出回调下载 / 状态栏接 `/api/health` 轮询。**

---

## 7. 风险与决策点

### 7.1 是否值得继续用这份原型？

**值得**。理由：
- UI 信息架构（左筛选 + 中画布 + 右 Inspector + 顶部 Toolbar）和后端的查询模型契合；
- 视觉密度 / 工业风非常贴合“工程师的数据平台”定位（不是 To C 产品）；
- 4 种图表 + Wafer Map + S 参数模态都已实现；
- styles.css 质量好，不需要重做。

需要扔掉的部分：
- `tweaks-panel.jsx`（演示工具，540 行，整文件删）；
- 所有 mock 数组（约 100 行，散落各处）；
- `genScatter / genWafer / genSparam`（保留作为单元测试 fixture 还行，但生产替换）；
- 状态栏 / Titlebar 里的字面量延迟数字。

工作量估计：**约 7 个工作日** 完成迁移 + 联调（2 天工程骨架 + 3 天字段映射 + SSE + 2 天补视图）。

### 7.2 architecture.md 是否需要更新？

**需要**。如果决策是用 React（推荐），`docs/architecture.md` 第 22 行的图、第 83-88 行的前端选型表、第 116 行的“前端 Vue 3”、第 159-168 行的目录结构、第 295 行的启动命令都要改。建议在采纳本评估后做一次专项更新，并在新文件 `docs/frontend-stack.md` 中记录组件库选型 / 状态管理 / TS 类型生成流程。

### 7.3 命名风格的最终决断

字段名建议**前端跟随后端**（即 snake_case + `_ghz / _ohm / _pct` 后缀），而非反之。理由：
- 后端字段名直接对应 SQL 列名（`devices.fs_ghz`），改后端意味着改 schema、迁移、所有查询；
- 显示给用户的 label（`fs (GHz)`）已经在 `/api/query/fields` 里返回，前端读 label 即可；
- 减少前后端心智模型差异，避免每次新增字段都要双向映射。
