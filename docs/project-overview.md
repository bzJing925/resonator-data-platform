# 谐振器测试数据平台 — 项目全景文档

> 本文档面向新成员快速上手，详细讲解项目功能模块与每个文件的作用。  
> 与 `README.md` 不同：README 面向使用者，本文面向开发者与维护者。

---

## 一、项目简介

### 1.1 背景与目标

客户原本用 **Excel + CLI Python 脚本** 管理 RF（射频）谐振器测试数据，数据量达几十万条，Excel 已严重卡顿。本项目将其重构为**所内多用户共用的 Web 平台**，浏览器访问，免安装客户端。

**第一阶段**：谐振器（AlN BAW）  
**第二阶段**：滤波器（结构类似，预留入口）

### 1.2 一句话概括

> 上传 zip → 自动入库 → 跨批次交互式作图 → 导出

### 1.3 技术栈

| 层级 | 技术 | 版本 | 说明 |
|---|---|---|---|
| 后端 | FastAPI | 0.115+ | Web 框架，自动生成 OpenAPI 文档 |
| ORM | SQLAlchemy | 2.0+ | 数据库操作，typed `Mapped[...]` 风格 |
| 数据库 | PostgreSQL | 15 | 类型严格、复杂聚合性能好 |
| 任务队列 | Celery + Redis | 5.4+ / 7 | 异步处理上传任务 |
| 算法 | numpy / scipy / scikit-rf | — | 与客户原 CLI 脚本同栈 |
| 前端 | React + Vite | 18 / 5 | 构建工具与 UI 框架 |
| 可视化 | Plotly.js | — | 散点/箱型/折线/版图分布/S 参数曲线 |
| 部署 | Podman/Docker + Nginx | — | 5 容器 compose 编排 |
| Python 包管理 | uv | 0.10+ | 替代 pip |
| Node 包管理 | npm | — | 前端依赖 |

---

## 二、核心功能模块详解

### 2.1 上传与入库（Upload → Ingestion）

**用户操作**：
1. 选择 zip 压缩包（内含 S2P 测试数据文件）
2. 选择一份 **mapping 对照表**（不同批次可能不同）
3. 配置频率范围（可选，默认全频段）
4. 勾选是否执行 **ShortOpen De-embedding**（可选，默认关闭）

**后端处理管线**（Celery 异步）：
```
zip 上传 → 保存到 uploads/ → 解压到 files/<batch_no>/
  → 拆 S2P → S11.s1p + S22.s1p
  → (可选) ShortOpen 去嵌校准
  → 加载 mapping，逐器件跑参数提取算法
  → 批量 INSERT into devices 表
  → UPDATE upload_tasks = success
```

**实时进度**：Worker 每处理完一个文件就更新进度，通过 **Redis pub/sub → FastAPI SSE → 浏览器 EventSource** 实时推送进度条。

**关键约束**：
- 批次号 = zip 文件名（去扩展名），重名拒绝
- 数据清洗：14 个关键数值列任一含 NA → 整行丢弃
- 仅支持 zip；大文件走分块上传

---

### 2.2 数据查询与探索分析（Query & Explore）⭐ 核心模块

这是平台**最常用**的功能。用户通过筛选面板拖拽条件，实时出图。

**支持的图表类型**：

| 图表 | 数据源 | 说明 |
|---|---|---|
| **散点图**（Scatter） | SQL 参数表 | X/Y/Color 任意字段组合；WebGL 渲染支持 5 万点流畅交互 |
| **箱型图**（Box） | SQL 参数表 | 按分组看分布（min / p25 / p50 / p75 / max） |
| **折线图**（Line） | SQL 参数表 | 跨批次趋势图，看参数随批次变化 |
| **版图分布图**（Wafer Map） | SQL 参数表 | X/Y 锁定为几何坐标，颜色编码任意参数；直观看到 wafer 上各位置的参数分布 |
| **单器件 S 参数曲线** | 原始 S1P 文件 | 现读现画，支持 dB / phase / Smith / 阻抗 / BodeQ |

**筛选能力**：
- 按批次、wafer、端口（S11/S22）、type、Area、任意提取参数过滤
- 支持 `eq` / `in` / `not_in` / `gte` / `lte` / `like` 等操作符
- 查询上限 20 万行（硬保护，防爆内存）

**聚合操作**：max / min / mean / p50(median) / p25 / p75

---

### 2.3 单器件 S 参数曲线（S-Parameter Curves）

**触发方式**：在数据表格或散点图中点击任意一行/点。

**后端**：
- 根据 `device.s_param_path` 找到磁盘上的 `.s1p` 文件
- 用 `scikit-rf` 实时读取解析
- 返回 `{freq_ghz, values, param}` JSON

**前端 Plotly 渲染**：
- `s11_db`：S11 幅度（dB）
- `s11_phase`：S11 相位（度）
- `s11_re_im`：实部/虚部
- `z_mag_db`：阻抗幅度（dB）
- `z_phase`：阻抗相位

**BodeQ 曲线**：额外计算 raw / smooth / fitted 三条曲线，标记 fs/fp 位置。

---

### 2.4 数据导出（Export）

| 导出类型 | 方式 | 适用场景 |
|---|---|---|
| **CSV** | 同步流式导出 | ≤ 5 万行，pandas `to_csv` → `StreamingResponse` |
| **Excel** | 异步 Celery 任务 | > 5 万行，后台生成后下载 |
| **图表图片** | Plotly 自带 | 当前视图 PNG/SVG |
| **交互式 HTML** | Plotly 自带 | 带缩放/hover 的独立 HTML 文件 |

导出文件临时存放在 `/data3/aln/exports/`，24 小时后自动清理。

---

### 2.5 系统管理（System）

- **批次列表**：分页、排序、查看统计（Pass 率、fs 均值等）
- **批次详情**：wafer 列表、器件分页、统计概览
- **删除批次**：级联删除 DB 记录 + 磁盘文件
- **对照表管理**：上传、查看条目、删除（被引用时拒绝）
- **任务列表**：查看上传/处理任务状态
- **健康检查**：`/api/health` 返回 DB/Redis/磁盘状态
- **系统统计**：`/api/stats` 返回批次数/器件数/磁盘用量/任务数

---

### 2.6 算法层（Algorithm Core）

算法全部从客户原始 CLI 脚本移植，**纯函数化重写**，集中在 `backend/app/core/`：

| 算法 | 文件 | 说明 |
|---|---|---|
| **谐振峰检测** | `extract.py::find_resonances` | 从阻抗曲线自动找 fs（串联谐振）和 fp（并联谐振） |
| **BodeQ 平滑与拟合** | `extract.py::calculate_bodeq` | Savitzky-Golay 平滑 + Lorentz 拟合 |
| **中间寄生峰检测** | `extract.py::detect_intermediate_peak` | 检测 fp2/fs2/Zp2/Zs2 |
| **参数提取总管** | `extract.py::extract_resonator_params` |  orchestrate 以上全部，输出 `ResonatorRow` |
| **S2P→S1P 拆分** | `touchstone.py` | 从 9 列 S2P 拆出 S11(3列) 和 S22(3列) |
| **ShortOpen 去嵌** | `deembed.py` | 用 OPEN/SHORT 校准件对 DUT 做去嵌 |
| **文件名解析** | `filename.py` | 从文件名提取 mark/coord/x/y/port/pf 等 |
| **Mapping 解析** | `mapping.py` | 从 xlsx 解析对照表，提取 EG/FL/AG/Area 等 |

**所有算法魔数**（窗口大小、阈值、比例等）集中在 `config.py::AlgorithmConfig`，不允许硬编码在算法实现里。

---

## 三、数据流图解

```
┌──────────┐   选 zip + mapping + 频率范围    ┌──────────┐
│  浏览器   │ ───────────────────────────────▶ │ FastAPI  │
│  (React) │                                  │  (API)   │
└──────────┘                                  └────┬─────┘
     ▲                                             │
     │         返回 task_id + stream_url            │
     │         ━━━━━━━━━━━━━━━━━━━━━━━━━            │
     │◀─────────────────────────────────────────────┘
     │                                             │
     │    SSE EventSource /api/tasks/{id}/stream   │
     │◄════════════════════════════════════════════┤
     │         实时进度条推送                        │
     │                                             ▼
     │                                    ┌──────────────┐
     │                                    │ Redis broker │
     │                                    └──────┬───────┘
     │                                           │
     │                                           ▼
     │                                    ┌──────────────┐
     │                                    │ Celery Worker│
     │                                    │              │
     │                                    │ 1. 解压 zip  │
     │                                    │ 2. S2P→S1P   │
     │                                    │ 3. (去嵌)    │
     │                                    │ 4. 提参算法   │
     │                                    │ 5. 批量入库   │
     │                                    │ 6. 更新进度   │
     │                                    └──────┬───────┘
     │                                           │
     │                                           ▼
     │                                    ┌──────────────┐
     │                                    │  PostgreSQL  │
     │                                    │  (devices)   │
     │                                    └──────────────┘
     │
     │    GET /api/query/devices (筛选条件)
     │◄──────────────────────────────────────────────┐
     │         返回 JSON (≤20万行)                    │
     │                                                │
     │    GET /api/devices/{id}/sparam                │
     │◄──────────────────────────────────────────────┤
     │         返回 {freq, values}                    │
     │                                                │
     ▼                                                │
┌──────────┐   Plotly.js 渲染                      │
│  图表区   │ ──────────────────────────────────────┘
│ (散点/箱型/折线/版图/S曲线)                    │
└──────────┘
```

---

## 四、文件作用总览

### 4.1 后端（`backend/`）

#### 入口与配置

| 文件 | 作用 |
|---|---|
| `app/main.py` | FastAPI 入口。创建 app 实例、挂载 CORS、注册全部路由（system/upload/tasks/batches/mappings/query/devices/export） |
| `app/config.py` | **配置中心**。`Settings` 读取环境变量（DATABASE_URL / REDIS_URL / DATA_ROOT 等）；`AlgorithmConfig` 集中管理全部算法魔数（savgol 窗口、峰值阈值等） |
| `app/db.py` | 数据库引擎 + Session 工厂。`get_db()` 作为 FastAPI Dependency，每请求一个 session |

#### API 路由（`app/api/`）

| 文件 | 作用 |
|---|---|
| `api/system.py` | `/health` 健康检查（DB/Redis/磁盘）；`/stats` 系统统计（批次/器件/任务数/磁盘） |
| `api/upload.py` | `POST /uploads` 接收 zip + mapping + 参数，校验后写入 upload_tasks 表，enqueue Celery 任务 |
| `api/tasks.py` | `/tasks` 任务列表；`/tasks/{id}` 任务详情；`/tasks/{id}/stream` **SSE 实时流**（Redis pub/sub 订阅） |
| `api/batches.py` | `/batches` 批次列表（分页/排序）；`/batches/{batch_no}` 详情；`/batches/{batch_no}/devices` 批次内器件分页；`DELETE` 级联删除 |
| `api/mappings.py` | `/mappings` 对照表 CRUD；解析 xlsx/csv → 入库 mapping_entries；删除时检查是否被批次引用 |
| `api/query.py` | **核心查询接口**。`/query/devices` 跨批次筛选；`/query/aggregate` 箱型图聚合统计；`/query/fields` 字段元数据；`/query/distinct` 去重值列表 |
| `api/devices.py` | `/devices/{id}/sparam` 现读现画 S 参数曲线（scikit-rf 读 .s1p）；`/devices/{id}/bodeq` BodeQ 三条曲线 |
| `api/export.py` | `/export/csv` 同步流式 CSV；`/export/xlsx` 异步 Excel；`/exports/{id}` 下载导出文件 |
| `api/deps.py` | FastAPI Dependencies：DbSession、字段白名单（防注入）、列映射表 |

#### 算法层（`app/core/`）

| 文件 | 作用 |
|---|---|
| `core/extract.py` | **谐振参数提取核心**。`find_resonances` 找 fs/fp；`calculate_bodeq` BodeQ 平滑拟合；`detect_intermediate_peak` 中间寄生峰；`extract_resonator_params` 总管函数。约 540 行 |
| `core/touchstone.py` | S2P → S1P 拆分。处理 Touchstone 文件头，提取 S11(3列) 和 S22(3列)，写入独立 .s1p 文件 |
| `core/deembed.py` | ShortOpen 去嵌封装。用 scikit-rf `ShortOpen` 类对 DUT 做校准，v1 默认关闭但接口保留 |
| `core/filename.py` | 文件名解析器。从文件名提取 mark(A1-1)、coord(X0Y0)、x/y 坐标、port(S11/S22)、Pass/Fail 标记、识别校准件(OPEN/SHORT) |
| `core/mapping.py` | 对照表加载与解析。读 xlsx/csv，从 Description 字段提取 EG/FL/AG/Area/HasPF 等结构化数据 |

#### 数据模型（`app/models/`）

| 文件 | 作用 |
|---|---|
| `models/base.py` | SQLAlchemy 2.0 `DeclarativeBase` + `TimestampMixin`（created_at / updated_at） |
| `models/batch.py` | `batches` 表。批次号（唯一）、mapping 关联、频率范围、deembedded 标记、器件数、上传时间 |
| `models/device.py` | `devices` 表（核心大表，~50万行）。元信息 + 35+ 提取参数字段（fs/fp/Zs/Zp/Q/BodeQ/k2eff/中间峰等）+ 复合索引 |
| `models/mapping.py` | `mappings` + `mapping_entries` 表。对照表主表 + 条目明细（mark/description/EG/FL/AG/Area） |
| `models/task.py` | `upload_tasks` 表。Celery 任务进度（status/pct/msg/error_msg/started/finished） |

#### Pydantic Schema（`app/schemas/`）

| 文件 | 作用 |
|---|---|
| `schemas/resonator.py` | `ResonatorRow`：单个器件参数提取结果的 35 字段 Pydantic 模型，算法层 → DB 的桥梁 |
| `schemas/batch.py` | Batch 列表/详情/统计的响应模型 |
| `schemas/mapping.py` | Mapping 列表/条目/上传的响应模型 |
| `schemas/query.py` | Query/Aggregate 请求体和响应模型 |
| `schemas/task.py` | Task 列表/详情的响应模型 |
| `schemas/upload.py` | Upload 接受的响应模型（202 Accepted） |

#### Celery Worker（`app/workers/`）

| 文件 | 作用 |
|---|---|
| `workers/__init__.py` | Celery app 实例创建与配置 |
| `workers/process_batch.py` | **主任务**：`process_batch_task`。解压 → 拆 S2P → (去嵌) → 提参 → 批量入库（500 行一 chunk）→ 更新进度。约 300 行 |
| `workers/progress.py` | `ProgressPublisher`：封装 Redis pub/sub + DB UPDATE，统一发布任务进度事件（start/update/done/fail） |

#### 测试（`tests/`）

| 文件/目录 | 作用 |
|---|---|
| `tests/conftest.py` | pytest fixtures：测试 DB session、测试客户端 |
| `tests/test_extract.py` | 参数提取算法单元测试 |
| `tests/test_touchstone.py` | S2P→S1P 拆分测试 |
| `tests/test_filename.py` | 文件名解析测试 |
| `tests/test_mapping_parser.py` | Mapping 解析测试 |
| `tests/workers/test_process_batch.py` | Celery 任务流程测试 |
| `tests/workers/test_deembed_path.py` | 去嵌路径测试 |
| `tests/api/test_integration.py` | API 集成测试 |
| `tests/api/test_export.py` | 导出功能测试 |
| `tests/api/test_query_safety.py` | 查询安全测试（字段白名单、防注入） |
| `tests/test_e2e_pipeline.py` | 端到端完整管线测试 |
| `tests/test_real_pipeline.py` | 真实数据管线测试 |
| `tests/scripts/test_bulk_upload.py` | 批量导入脚本测试 |

#### 迁移与脚本

| 文件 | 作用 |
|---|---|
| `alembic/versions/2f38e7f18d1b_initial_schema.py` | 初始数据库迁移（batches/devices/mappings/mapping_entries/upload_tasks） |
| `alembic/env.py` | Alembic 运行环境配置 |
| `scripts/bulk_upload.py` | 批量导入脚本（运维用，处理历史 18 批次数据） |
| `pyproject.toml` | Python 项目配置：依赖、脚本入口、工具配置 |
| `Dockerfile` | 后端容器镜像：多阶段构建，基于 python:3.12-slim |

---

### 4.2 前端（`frontend/src/`）

#### 入口与路由

| 文件 | 作用 |
|---|---|
| `main.jsx` | React 应用入口。`ReactDOM.createRoot`，挂载 `<App />` |
| `App.jsx` | 应用骨架。Titlebar（健康状态指示器）+ Sidebar（导航）+ 主内容区（Routes）+ Statusbar（底部状态栏）。每 15 秒轮询 `/api/health` |
| `styles.css` | 全局样式。暗色主题、变量系统、组件通用样式 |

#### 页面（`pages/`）

| 文件 | 作用 |
|---|---|
| `pages/Dashboard.jsx` | 仪表盘首页。显示系统统计卡片、最近批次、任务状态概览 |
| `pages/Upload.jsx` | 上传页面。选 zip → 选 mapping → 填频率范围 → 提交上传 → 跳转到任务详情 |
| `pages/Batches.jsx` | 批次列表页。分页表格、排序、搜索、删除操作 |
| `pages/BatchDetail.jsx` | 批次详情页。wafer 列表、器件分页表格、批次统计（fs 均值/中位数/Pass 率） |
| `pages/Mappings.jsx` | 对照表管理页。上传新 mapping、查看列表、删除 |
| `pages/Explore.jsx` | **探索分析主页面**。约 1165 行，最复杂页面。包含：筛选面板、图表类型切换（散点/箱型/折线/版图）、聚合选项、字段选择器、CSV 导出、框选隐藏点功能 |
| `pages/Tasks.jsx` | 任务列表页。显示最近上传/处理任务 |
| `pages/TaskDetail.jsx` | 任务详情页。SSE 实时进度条、任务日志、完成后跳转批次 |

#### 组件（`components/`）

| 文件 | 作用 |
|---|---|
| `components/Charts.jsx` | **可视化核心**。约 1655 行。封装 Plotly.js：散点图、箱型图、折线图、版图分布图、统一网格布局、颜色映射、图例、hover 样式、框选事件处理。`UnifiedChartGrid` + `WaferMap` |
| `components/FilterPanel.jsx` | 筛选面板。动态生成筛选条件（按字段类型展示不同输入框）、添加/删除条件组 |
| `components/DeviceModal.jsx` | 器件详情弹窗。显示器件全部参数字段 + S 参数曲线图 + BodeQ 曲线图 |
| `components/Sidebar.jsx` | 侧边导航栏。Logo、菜单项（Dashboard/Upload/Batches/Mappings/Explore/Tasks） |
| `components/Icons.jsx` | 图标组件库。封装 Lucide 图标，统一尺寸和样式 |

#### API 与状态（`api/` / `hooks/`）

| 文件 | 作用 |
|---|---|
| `api/client.js` | axios 实例。baseURL、30 秒超时、统一错误处理（提取 `detail` 字段） |
| `api/endpoints.js` | 所有后端 API 的封装函数：health/stats、batch CRUD、mapping CRUD、upload、task、query、device curves、export。约 66 行，纯函数式 |
| `hooks/useFields.js` | 自定义 Hook：获取可作图字段元数据（/query/fields），带全局缓存和订阅机制，多个组件共享同一份数据 |
| `hooks/useSSE.js` | 自定义 Hook：SSE EventSource 封装。订阅任务进度流，返回 `{event, progress, message, status, error, done}` |

#### 构建配置

| 文件 | 作用 |
|---|---|
| `package.json` | npm 依赖：react, react-router-dom, plotly.js, react-plotly.js, axios, lucide-react 等 |
| `vite.config.js` | Vite 构建配置：React 插件、代理配置（dev 时转发 /api 到 localhost:8000） |
| `index.html` | HTML 模板 |
| `Dockerfile` | 前端容器镜像：多阶段构建（node 构建 → nginx 托管静态文件） |
| `nginx.conf` | 前端容器内 Nginx 配置（单页应用路由回退 index.html） |

---

### 4.3 部署（`deploy/`）

| 文件 | 作用 |
|---|---|
| `deploy/docker-compose.yml` | **5 容器编排**：postgres(15) / redis(7) / api(FastAPI, 4 workers) / worker(Celery, 4 concurrency) / nginx(反向代理)。含健康检查、volume 挂载、网络配置 |
| `deploy/nginx/default.conf` | Nginx 反向代理配置。静态文件服务、API 路由转发 `/api → api:8000`、大文件上传支持、healthz 探活端点 |

---

### 4.4 根目录配置

| 文件 | 作用 |
|---|---|
| `.env.example` | 环境变量模板：POSTGRES_PASSWORD / DATA_ROOT / UPLOAD_MAX_GB / LOG_LEVEL / NGINX_PORT |
| `bootstrap.sh` | Linux/macOS 一键启动脚本：检查 podman → 启动 5 容器 → alembic 迁移 → 验证 health |
| `bootstrap.ps1` | Windows PowerShell 等价脚本（Docker Desktop 路线） |
| `pyproject.toml` | 仓库根 uv 配置（保留项目元信息） |
| `main.py` | 仓库根入口（开发快捷方式，实际入口在 backend/app/main.py） |

---

### 4.5 数据目录（运行时生成，不在 git 中）

由 `.env` 中的 `DATA_ROOT` 决定，默认 `/data3/aln/`：

```
/data3/aln/
├── pgdata/        # PostgreSQL 数据（容器 volume）
├── redis/         # Redis AOF 持久化
├── uploads/       # 原始 zip（保留 7 天）
├── files/         # 解压后的 S2P/S1P，按 batch/wafer/port 分目录
├── mappings/      # 上传的 mapping xlsx 原文件
├── exports/       # 导出临时文件（24h 清理）
└── logs/          # 应用日志（api / worker）
```

---

### 4.6 文档（`docs/`）

| 文件 | 作用 |
|---|---|
| `docs/architecture.md` | 架构总览。技术栈选型、模块划分、关键产品决策、性能预估 |
| `docs/api.md` | API 契约。24 个端点的请求/响应 schema、错误码约定 |
| `docs/database-schema.md` | 数据库设计。5 张表结构 + 索引说明 |
| `docs/algorithm-port.md` | 算法移植规格。客户脚本 → 后端纯函数的逐函数对照 |
| `docs/deployment.md` | Linux 部署指南（podman 路线） |
| `docs/deployment-windows.md` | Windows 部署指南（Docker Desktop + PowerShell） |
| `docs/operations.md` | 运维手册。重启/备份/日常排错 cookbook |
| `docs/frontend-evaluation.md` | 前端原型评估 + 改造记录（Vue→React 切换背景） |
| `docs/READY-TO-USE.md` | 快速上手指南 |
| `docs/superpowers/specs/2026-05-11-chart-hide-points-design.md` | 框选隐藏点功能的设计文档 |
| `谐振器数据平台-需求文档.md` | 业务需求文档。背景、数据规模、功能模块边界、变量分类 |
| `README.md` | 项目主 README。面向使用者的快速开始、目录结构、文档导航 |
| `CLAUDE.md` | 项目级 AI 助手上下文（如有） |

---

## 五、关键设计决策

| # | 决策 | 原因 |
|---|---|---|
| 1 | 前端用 Plotly.js 而非 ECharts/D3 | 散点/箱型/折线/版图/Smith 全覆盖，原生交互，HTML 导出 |
| 2 | 大数据量先全画，性能不够再降采样 | 产品决策：所见即所得，5 万点 WebGL 流畅 |
| 3 | 无登录、裸开 | 内网隔离，v1 简化 |
| 4 | mBVD 6 列废弃 | 客户确认没人看过，简化实现 |
| 5 | De-embedding 默认关闭 | 需要 zip 含 OPEN/SHORT 校准件，缺件直接失败 |
| 6 | mapping 每次上传时选一份绑定 | 不同批次/版图可能不同，独立维护 |
| 7 | S2P→S1P 拆分在服务端做 | 前端只处理 S1P，降低复杂度 |
| 8 | 原始 S2P/S1P 文件保留在磁盘 | 供 S 参数曲线现读现画，不做二进制入 DB |
| 9 | 算法纯函数化、魔数集中配置 | 方便单元测试、调参、与客户脚本对照 |

---

## 六、阅读顺序建议（新成员）

1. **先读业务**：`谐振器数据平台-需求文档.md` → `docs/architecture.md`
2. **再读数据流**：`backend/app/main.py` → `app/api/upload.py` → `app/workers/process_batch.py`
3. **理解算法**：`backend/app/core/extract.py`（只看函数签名和 docstring）
4. **理解查询**：`backend/app/api/query.py` → `frontend/src/pages/Explore.jsx`
5. **理解可视化**：`frontend/src/components/Charts.jsx`（先看 baseLayout 和主渲染函数）
6. **跑起来**：`README.md` 快速开始 → `bootstrap.sh up`

---

*文档版本：v1.0*  
*日期：2026-06-02*  
*维护者：开发团队*
