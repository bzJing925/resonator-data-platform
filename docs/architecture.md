# 架构总览

**版本**：v0.1
**日期**：2026-05-09
**状态**：草案，已与产品确认主要决策

---

## 1. 系统目标

把客户现有的"Excel + CLI Python 脚本"流程，重构成所内多用户共用的 Web 平台：上传测试数据 → 自动入库 → 跨批次交互式作图 → 导出。先做**谐振器**，滤波器二期。

详见 `谐振器数据平台-需求文档.md`。

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      浏览器（所内多用户）                         │
│                React 18 + Vite + React Router 6                 │
│         HTML 表格（虚拟滚动按需加） + Plotly.js（可视化）         │
└────────────────────┬────────────────────────────────────────────┘
                     │ HTTP / SSE
┌────────────────────▼────────────────────────────────────────────┐
│                       Nginx（反向代理）                          │
│              静态文件 + API 路由 + 大文件上传                    │
└────────────────────┬────────────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
┌───────▼─────────┐    ┌──────────▼──────────┐
│  FastAPI (api)  │    │  Celery Worker(s)   │
│  - 上传/查询/导出│◄──►│  - S2P→S1P 拆分     │
│  - SSE 推进度   │    │  - 参数提取算法      │
│  - 现读现画曲线 │    │  - 入库              │
└───┬─────────┬───┘    └──┬──────────────┬───┘
    │         │           │              │
    │    ┌────▼───┐  ┌────▼────┐    ┌────▼───┐
    │    │ Redis  │  │ Postgres│    │ /data3 │
    │    │ broker │  │   15    │    │  files │
    │    └────────┘  └─────────┘    └────────┘
    │
    └── 同步访问 PG（查询/导出）
```

---

## 3. 技术栈（全部成熟方案）

### 3.1 后端

| 组件 | 选型 | 版本 | 理由 |
|---|---|---|---|
| Web 框架 | **FastAPI** | 0.115+ | Python 生态最成熟，自动 OpenAPI 文档，与算法同语言 |
| ASGI server | uvicorn | 0.30+ | FastAPI 标配 |
| ORM | **SQLAlchemy** | 2.0+ | 事实标准，typed `Mapped[...]` 风格 |
| 迁移 | Alembic | 1.13+ | 与 SQLAlchemy 配套 |
| 数据库 | **PostgreSQL** | 15 | 类型严格、复杂聚合性能好、支持 partial index |
| 任务队列 | **Celery** | 5.4+ | Python 最成熟的分布式任务库 |
| 消息中间件 | **Redis** | 7 | Celery broker + 结果后端 |
| 进度推送 | **SSE**（Server-Sent Events） | FastAPI 原生 | 比 WebSocket 简单，单向推送够用 |
| 验证 | Pydantic | 2.x | FastAPI 标配 |
| 包管理 | uv | 0.10+ | 项目已采用 |

### 3.2 算法层依赖（与客户脚本一致）

| 库 | 用途 |
|---|---|
| numpy | 数值计算 |
| scipy | savgol_filter / find_peaks / curve_fit / interp1d |
| pandas | mapping 表读取、Excel 导出 |
| **scikit-rf** | Touchstone 解析 + ShortOpen de-embedding |
| openpyxl | Excel 读写 |

**移除**：matplotlib（前端 Plotly 接管），tkinter / 阻塞式 input（CLI 改 API）。

### 3.3 前端

| 组件 | 选型 | 理由 |
|---|---|---|
| 框架 | **React 18** + Hooks | 外部高保真原型已采用，复用避免重写 |
| 构建 | Vite 5 | 标配，`@vitejs/plugin-react` |
| 状态 | useState / useContext | 原型未引入 Redux/Zustand，简单状态够用 |
| UI 组件 | 自写组件 + `styles.css` | 原型已自带成体系组件，不再叠加大组件库 |
| 路由 | React Router 6 | React 生态主流 |
| HTTP | axios | 主流 |
| 表格 | HTML `<table>` + 虚拟滚动按需加 | 原型用原生表格；几十万行场景再引 react-window |
| 可视化 | **Plotly.js**（`react-plotly.js` 包装） | 散点/箱型/折线/版图分布全覆盖，原生交互 + HTML 导出 |

### 3.4 部署

| 组件 | 选型 | 理由 |
|---|---|---|
| 容器 | **Podman 4.9 + podman compose** | 本机已装，行业标准 |
| 反向代理 | Nginx（容器） | |
| 部署目标 | 所内服务器 fineserver | 当前开发机即部署机 |

---

## 4. 关键产品决策（已确认）

| # | 决策 | 备注 |
|---|---|---|
| 1 | 输出列**保留中间峰**（fp2/fs2/Zp2/Zs2），**mBVD 6 列废弃** | 客户决策：mBVD 没人看过，直接删掉简化实现 |
| 2 | De-embedding **可选启用，默认关闭** | 上传时勾选；需 zip 含 OPEN/SHORT 校准 .s2p；缺校准件直接失败 |
| 3 | "文件分选"功能**砍掉** | 客户脚本里有，但不重要 |
| 4 | mapping 解析**直接 split `&` 取面积** | 不用客户脚本里硬编码的 Area 表 |
| 5 | Fail 数据**全部入库** | 88% 是 Fail，分析时也要看分布 |
| 6 | mapping **每次上传时选一份**绑定批次 | 独立维护 |
| 7 | 数据库 **PostgreSQL 15** | |
| 8 | 历史 18 批次**用户逐个上传** | 不做批量导入工具 |
| 9 | 文件存储**本地 `/data3/aln/files/`** | 按 batch/wafer/port 分目录 |
| 10 | 后端 **FastAPI + Celery + Redis** | 异步处理上传 |
| 11 | 前端 **React 18 + Vite + Plotly.js**，无大组件库 | 见 §13 决策记录（2026-05-09 由 Vue 3 切换） |
| 12 | 作图先**下拉选字段**，不做拖拽 | |
| 13 | 大数据量**先全画**，性能不够再降采样 | |
| 14 | 登录**裸开** | 内网隔离 |
| 15 | 滤波器**仅放占位入口** | 二期实装 |
| 16 | 频率范围**不设默认值**，用户每次填 | |
| 17 | 重名批次号**报错拒绝** | 不允许覆盖 |
| 18 | 部署 **podman compose** | 不用 systemd 直跑 |

---

## 5. 目录结构

### 5.1 代码仓库（`$REPO/`）

```
aln-data/
├── 客户提供的材料/             # 原始物料（只读，不进 git，已 .gitignore）
├── 谐振器数据平台-需求文档.md
├── docs/                      # 设计文档
│   ├── architecture.md       # 本文
│   ├── algorithm-port.md     # 算法移植规格
│   ├── database-schema.md    # 数据库设计
│   ├── api.md                # API 契约
│   └── deployment.md         # 部署指南
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI 路由
│   │   ├── core/             # 算法层（纯函数）
│   │   │   ├── touchstone.py
│   │   │   ├── deembed.py
│   │   │   ├── extract.py
│   │   │   ├── mapping.py
│   │   │   └── filename.py
│   │   ├── models/           # SQLAlchemy ORM
│   │   ├── schemas/          # Pydantic 请求/响应模型
│   │   ├── workers/          # Celery 任务
│   │   ├── config.py         # AlgorithmConfig + Settings
│   │   └── main.py           # FastAPI 入口
│   ├── alembic/              # 数据库迁移
│   ├── tests/                # 单元/集成测试
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/              # axios 客户端
│   │   ├── components/       # *.jsx 组件
│   │   ├── pages/            # 路由级页面 *.jsx
│   │   ├── router/           # React Router 6 配置
│   │   ├── hooks/            # 自定义 hooks（替代 Pinia store）
│   │   └── main.jsx          # ReactDOM.createRoot 入口
│   ├── Dockerfile
│   ├── nginx.conf            # 前端容器内 Nginx 配置
│   └── package.json
├── deploy/
│   ├── docker-compose.yml    # 开发环境
│   ├── compose.prod.yml      # 生产环境（如有差异）
│   └── nginx/
│       └── default.conf      # 反向代理配置
├── .env.example              # 环境变量模板
└── pyproject.toml            # 仓库根（保留 uv 项目）
```

### 5.2 数据目录（`/data3/aln/`）

```
/data3/aln/
├── pgdata/        # PostgreSQL 数据目录（容器 volume）
├── redis/         # Redis 持久化
├── uploads/       # 用户上传的原始 zip（保留 7 天后清理）
│   └── 2026-05/<task_id>.zip
├── files/         # 解压 + 处理后的 S2P/S1P
│   └── <batch_no>/
│       ├── <wafer>/
│       │   ├── S11/*.s1p
│       │   ├── S22/*.s1p
│       │   └── deembed/    # 可选
│       └── ...
├── mappings/      # 上传的 mapping xlsx 原文件
├── exports/       # 导出 CSV/Excel 临时文件（24 小时后清理）
└── logs/          # 应用日志
    ├── api/
    └── worker/
```

---

## 6. 数据流

### 6.1 上传 → 入库

```
1. 用户在浏览器点上传，选 zip + mapping + 频率范围
2. 前端分块上传 zip 到 /api/uploads，落到 /data3/aln/uploads/
3. /api/uploads 返回 task_id，前端跳到任务进度页
4. FastAPI 写一行 upload_tasks(status=pending)，触发 Celery 任务
5. Worker：
   a. 解压 zip → /data3/aln/files/<batch_no>/
   b. 读批次号（= 文件夹名），查重，重名则报错回任务
   c. 对每个 .s2p：拆 S11/S22 → .s1p
   d. (可选)对每个 .s1p：跑 ShortOpen de-embedding
   e. 加载 mapping，对每个 .s1p 跑 extract → ResonatorRow
   f. 批量 INSERT into devices；UPDATE batches.device_count
   g. UPDATE upload_tasks(status=success, progress=100)
6. 期间每完成一个文件就 UPDATE upload_tasks(progress_msg, progress_pct)
7. FastAPI 的 /api/tasks/<id>/stream（SSE）订阅 Redis pub/sub，把进度推到前端
```

### 6.2 探索分析（散点/箱型/折线/版图分布）

```
1. 前端筛选面板提交 /api/query/scatter?x=...&y=...&color=...&filters=...
2. FastAPI 走 SQLAlchemy 拼 SQL，PG 出结果（< 50000 行直接返回 JSON）
3. 前端 Plotly 渲染（WebGL 模式）
4. 用户点击某个点 → /api/devices/<id>/sparam → 现读现画 S 参数曲线
   - 后端读 .s1p（scikit-rf）→ JSON {freq, s11_db, s11_phase, ...}
   - 前端 Plotly 画线
```

### 6.3 导出

```
1. 用户在筛选结果上点"导出 CSV"
2. /api/export/csv 同步流式返回（pandas to_csv → StreamingResponse）
3. 大数据集异步走 Celery，导出文件落 /data3/aln/exports/，前端下载
```

---

## 7. 配置与魔数管理

所有"算法魔数"集中在 `backend/app/config.py` 的 `AlgorithmConfig` dataclass：

- `min_separation_hz = 20e6`（fs/fp 最小间距）
- `savgol_window = 51`
- `savgol_polyorder = 3`
- `lorentz_peak_range_ratio = 0.3`
- `intermediate_peak_prominence_db = 3.0`
- `c0_iqr_threshold = 1.5`
- `cm_c0_min = 0.001`, `cm_c0_max = 0.5`
- ...（详见 `algorithm-port.md` 第 4 节）

环境变量与运行时配置在 `Settings`（pydantic-settings）：
- `DATABASE_URL`、`REDIS_URL`、`DATA_ROOT=/data3/aln`、`UPLOAD_MAX_GB=20` 等。

---

## 8. 安全与权限

**当前阶段（v1）**：
- 无登录、无权限分级（用户决策）
- 完全依赖**内网隔离**
- Nginx 限制上传大小（默认 50 GB）
- 文件名/路径做严格校验，防止 zip slip 攻击（Python `zipfile` 配 `os.path.realpath` 检查）

**二期可加**：
- 所内 LDAP / SSO 集成
- 上传/删除操作审计

---

## 9. 性能预估

| 场景 | 预估 | 备注 |
|---|---|---|
| 单批次处理（~1 万器件） | 5–15 分钟 | 取决于是否做 de-embedding；Celery 并发可调 |
| 散点图查询（50 万行筛选 → 5 万点） | < 500 ms | PG 复合索引 + 字段直查 |
| 散点图前端渲染（5 万点 WebGL） | < 1 s | Plotly scattergl |
| 单器件 S 参数曲线（现读 .s1p） | < 200 ms | 文件 600 KB，scikit-rf 解析快 |
| Excel 导出（5 万行 × 26 列） | 5–10 s | openpyxl write_only 模式 |

---

## 10. 开发与部署形态

### 10.1 本地开发

- 启动数据服务：`podman compose up -d postgres redis`
- 后端：`cd backend && uv run uvicorn app.main:app --reload`
- 前端：`cd frontend && npm run dev`（Vite 默认 5173）
- worker：`cd backend && uv run celery -A app.workers worker --loglevel=info`

### 10.2 生产部署

- 一条命令：`cd deploy && podman compose up -d`
- 5 个容器：postgres / redis / api / worker / nginx
- 数据 volume 全部挂 `/data3/aln/<...>`
- 日志直接 stdout，podman logs 查看；可后续接入 journald

详见 `deployment.md`。

---

## 11. 实施分期回顾

- **阶段 0** 环境准备：1 天 ✅（数据目录、文档）
- **阶段 1** 算法层移植：2–3 天
- **阶段 2** 后端骨架：3–4 天
- **阶段 3** 前端骨架：4–5 天
- **阶段 4** 联调 + 历史数据导入：2 天

总工期 12–15 工作日。

---

## 12. 待解决/待确认事项

1. 并发任务上限（Celery worker concurrency 默认 4，看实际负载调）
2. 历史数据导入完成后视性能再决定是否需要降采样

## 13. 已关闭的决策（2026-05-09）

- ✅ mBVD 6 列：废弃，不实现、不入库
- ✅ 黄金参考 baseline：暂不要求客户提供，靠单元测试 + 物理合理性验证
- ✅ R0 公式 bug：因 mBVD 删除而作废
- ✅ `Area` / `Area(um2)` 含义：当作 type/数值字段直接入库，不做语义解析
- ✅ **前端选型**：从 Vue 3 + Element Plus + Pinia + AG Grid 切换到 **React 18 + Vite**。原因：外部工具产出的高保真原型采用 React + Babel CDN 形式（~2500 行 JSX），顺势采纳可避免重写。后续以 Vite + React 真工程化（`@vitejs/plugin-react`），状态用 `useState/useContext`，表格用原生 `<table>`、需要时再加 `react-window`，可视化用 `react-plotly.js`。详见 `docs/frontend-evaluation.md`。
