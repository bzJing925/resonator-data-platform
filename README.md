# 谐振器测试数据平台

> 把客户原本用 Excel 管理的 RF 测试数据搬到 Web 平台：上传 → 自动入库 → 跨批次交互式作图 → 导出。

面向所内多用户，浏览器访问，免安装客户端。第一阶段覆盖**谐振器**（AlN BAW），滤波器二期。

---

## 核心特性

- **上传 zip 一键入库**：选 zip + mapping + 频率范围，后端 Celery 异步解压、拆 S2P→S1P、跑参数提取、批量入库，进度条实时回显（SSE）。
- **跨批次探索分析**：拖拽筛选条件（批次 / wafer / 端口 / type / Area / 任意提取参数），现场出散点 / 箱型 / 折线 / 版图分布。
- **单器件 S 参数曲线现读现画**：表格点一行 → 后端读对应 .s1p 文件 → 前端 Plotly 画 dB / phase / Smith。
- **CSV / Excel 导出**：筛选结果一键带走，5 万行量级走流式同步、更大量走 Celery 异步导出。
- **多用户并发**：内网部署，无登录，靠所内网络隔离。

---

## 技术栈

- **后端**：FastAPI 0.115 + SQLAlchemy 2.0 + PostgreSQL 15 + Celery 5 + Redis 7
- **算法层**：numpy / scipy / scikit-rf（与客户原 CLI 脚本同栈，纯函数化重写）
- **前端**：React 18 + Vite 5 + React Router 6 + Plotly.js + axios
- **部署**：5 容器 compose 编排（postgres / redis / api / worker / nginx）；Linux 用 Podman，Windows 用 Docker Desktop
- **包管理**：uv (Python) + npm (frontend)

---

## 快速开始

> Windows 同事直接看 [`docs/deployment-windows.md`](docs/deployment-windows.md)。

### Linux / macOS

```bash
git clone <repo>
cd aln-data
cp .env.example .env
# 至少改 POSTGRES_PASSWORD；DATA_ROOT 默认 /data3/aln，按需改
cd frontend && npm install && npm run build && cd ..
./bootstrap.sh up
# 浏览器打开 http://localhost:8080
```

`bootstrap.sh` 会替你做：检查 podman / 数据目录 / 启动 5 容器 / 跑 alembic 迁移 / 验证 `/api/health`。

### Windows

```powershell
git clone <repo>
cd aln-data
Copy-Item .env.example .env
# 用 notepad .env 改 POSTGRES_PASSWORD + DATA_ROOT（推荐 D:/aln-data 这种绝对路径）
cd frontend; npm install; npm run build; cd ..
.\bootstrap.ps1 up
# 浏览器打开 http://localhost:8080
```

`bootstrap.ps1` 复刻同样的流程，但用 Docker Desktop 而不是 podman。详细步骤与排错见 [Windows 部署指南](docs/deployment-windows.md)。

---

## 目录结构

```
aln-data/
├── backend/              # FastAPI + Celery 后端
│   ├── app/              # 路由 / 算法层 / ORM / Celery 任务 / Pydantic 模型
│   ├── alembic/          # 数据库迁移
│   ├── scripts/          # 批量导入等运维脚本
│   └── tests/            # 单元 + 集成测试
├── frontend/             # Vite + React 前端
│   ├── src/              # api / components / pages / router / hooks
│   └── vite.config.js
├── deploy/               # compose 编排 + nginx 配置（podman/docker 通用）
├── docs/                 # 8 份设计文档（见下）
├── bootstrap.sh          # Linux/macOS：一键启动 / 停止 / 重置 / 查状态
├── bootstrap.ps1         # Windows PowerShell 等价物
├── .env.example          # 环境变量模板
└── 客户提供的材料/        # 客户原始物料（不进 git）
```

数据目录路径由 `.env` 中的 `DATA_ROOT` 决定：Linux 默认 `/data3/aln/`，Windows 推荐 `D:/aln-data/`。
落盘结构都是 `{pgdata,redis,uploads,files,mappings,exports,logs}/`。

---

## 文档导航

| 文档 | 用途 |
|---|---|
| [需求文档](谐振器数据平台-需求文档.md) | 业务背景与功能边界 |
| [架构总览](docs/architecture.md) | 技术栈选型 + 模块划分 + 关键决策 |
| [API 契约](docs/api.md) | 24 个端点定义、请求/响应 schema |
| [DB schema](docs/database-schema.md) | 5 张表结构 + 索引 |
| [算法移植规格](docs/algorithm-port.md) | 客户脚本 → 后端纯函数的逐函数对照 |
| [部署 (Linux)](docs/deployment.md) | 首次部署详解（podman 路线） |
| [部署 (Windows)](docs/deployment-windows.md) | Docker Desktop + PowerShell 路线 |
| [运维](docs/operations.md) | 重启 / 备份 / 日常排错 cookbook |
| [前端评估](docs/frontend-evaluation.md) | 外部原型评估 + 改造记录 |

---

## 开发

**后端**

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload          # API on :8000
uv run celery -A app.workers worker --loglevel=info
uv run pytest
```

详见 `backend/README.md`。

**前端**

```bash
cd frontend
npm install
npm run dev          # Vite dev on :5173
npm run build        # 产出到 frontend/dist/
```

---

## 数据流（一图说清）

```
浏览器 ──POST /api/uploads (zip + mapping_id + freq_range)──▶ FastAPI
                                                                │
                                          写 batches/upload_tasks(pending)
                                                                │
                                                       enqueue Celery
                                                                ▼
                              Celery worker
                              ├─ 解压 zip → /data3/aln/files/<batch>/
                              ├─ 拆 S2P → S11.s1p / S22.s1p
                              ├─ extract_resonator_params × N（numpy/scipy/scikit-rf）
                              ├─ bulk INSERT INTO devices
                              └─ UPDATE upload_tasks(progress / success)
                                                                │
                                              Redis publish 'task:<id>'
                                                                ▼
浏览器 SSE EventSource ◀── /api/tasks/<id>/stream ── FastAPI ── Redis pub/sub

浏览器 ──GET /api/query/scatter?...──▶ FastAPI ──▶ PG ──▶ JSON ──▶ Plotly.js
浏览器 ──点散点点击──▶ /api/devices/<id>/sparam ──▶ scikit-rf 现读 ──▶ Plotly.js
```

---

## 联系 / 反馈

- 业务需求：见 `谐振器数据平台-需求文档.md`
- 系统问题：先翻 `docs/operations.md` 排错章节
- 前端原型背景：见 `docs/frontend-evaluation.md`
