# AGENTS.md

> 本文件面向 AI 编码代理，假设读者对本项目一无所知。内容与 `CLAUDE.md` 互为补充，如两处冲突以代码现状为准。

## 一、项目概述

**谐振器测试数据平台**（ALN Resonator Test Data Platform）：所内多用户共用的 RF 谐振器（AlN BAW）测试数据管理平台，用于替代客户原来的「Excel + CLI Python 脚本」流程。核心链路一句话：

> 上传 zip（S 参数 Touchstone 文件）→ 自动入库（参数提取）→ 跨批次交互式作图 → 导出

第一阶段只覆盖**谐振器**；滤波器为二期预留。同一套代码有两种交付形态：

1. **内网服务器版**：PostgreSQL + Redis + FastAPI + Celery + Nginx 五容器（Podman Compose）。
2. **桌面版**：Electron + PyInstaller 打包的后端，SQLite 数据库 + 本地 worker 线程（无 Celery/Redis 依赖），数据落 `~/.aln-data/`。桌面端可与内网服务器做快照/批次双向同步（replication v3）。

代理模型不在平台内：独立离线 HTML 版由 `surrogate-html/` 一键生成（产物 `~/Desktop/AlN代理模型.html`）；训练链保留在 `backend/app/ml/` 与 ML worktree。

技术栈（全部成熟方案）：

| 层 | 技术 |
|---|---|
| 后端 | Python ≥ 3.12 · FastAPI 0.115+ · SQLAlchemy 2.0（typed `Mapped[...]`）· Celery 5 + Redis 7 · PostgreSQL 15（桌面版 SQLite） |
| 算法 | numpy / scipy / pandas / scikit-rf（与客户原 CLI 脚本同栈，移植记录见 `docs/algorithm-port.md`） |
| ML | torch · scikit-learn · onnx / onnxruntime（PINN 代理模型、稀疏重建；`backend/app/ml/`） |
| 前端 | React 18 · Vite 5 · TypeScript · React Router 6 · Plotly.js · axios · Electron 42 + electron-builder |
| 包管理 | **uv**（Python，绝不全局 pip install）· **npm**（前端） |

## 二、目录结构与模块划分

```
backend/
├── app/
│   ├── main.py            FastAPI 入口，19 个路由模块统一挂 /api 前缀
│   ├── config.py          Settings（环境变量）+ AlgorithmConfig（算法常数/魔法数集中处）
│   ├── db.py              引擎/Session；含 psycopg 兼容补丁
│   ├── api/               路由层：upload, batches, devices, query, comparison, mappings,
│   │                      export(_jobs), tasks, system, trash, files, process,
│   │                      sync, desktop_sync, replication(_v3) 等
│   ├── core/              算法层【纯函数】：touchstone.py, deembed.py, extract.py,
│   │                      mapping.py, filename.py, curves.py, process_params.py, sparse_sampler.py
│   ├── models/            SQLAlchemy ORM：batch, device, mapping, task, process, replica,
│   │                      file_node, export_job, sync_operation 等
│   ├── schemas/           Pydantic 请求/响应契约
│   ├── services/          业务服务层（约 40 个模块：ingest/export/query/replica/sync/proxy…）
│   ├── workers/           Celery 任务 + 桌面版 local_queue/local_worker（进程内队列）
│   ├── ml/                代理模型：dataset / inference / losses / models / sparse / surrogate
│   ├── watch/             目录监听自动上传（WATCH_ENABLED）
│   ├── desktop_setup.py   桌面模式初始化：目录、SQLite 建表、schema 版本
│   └── schema_upgrades.py 桌面 SQLite 的轻量 schema 升级（服务器端走 Alembic）
├── alembic/               数据库迁移（服务器 PostgreSQL）
├── scripts/               ML 训练/评估/导出脚本、批量上传等工具
├── tests/                 pytest，镜像 app/ 结构（api/core/services/workers/ml/…）
├── desktop_entry.py       PyInstaller 入口（桌面版）
└── build_backend.py       PyInstaller 打包脚本

frontend/
├── src/
│   ├── pages/             路由级页面（Dashboard, Explore, Batches, BatchDetail, Upload,
│   │                      Tasks, TaskDetail, Mappings, Process, Settings, Trash…）
│   ├── components/        共享组件（Sidebar, Charts, FilterPanel, ServerSyncPanel…）
│   ├── features/analysis/ 分析工作区：worksheet / chart / comparison / result / model
│   ├── api/               axios 客户端与端点封装
│   ├── sync/ export/ tasks/ upload/ lifecycle/  按领域组织的功能模块
│   ├── hooks/ contexts/   自定义 hook 与 React Context（无 Redux/Zustand）
│   └── styles.css         自定义 CSS（不引入大型 UI 组件库）
├── electron/              Electron 主进程/preload/自动更新（tsc 编译到 electron-dist/）
└── e2e/analysis/          Playwright 端到端测试（html harness + *.spec.ts）

deploy/
├── docker-compose.yml     五容器编排（postgres/redis/api/worker/nginx）
├── nginx/                 反代配置
└── native/ native-v2/     免 Docker 的 Windows 原生部署包脚本

docs/                      架构、API、数据库 schema、算法移植、部署、运维、PINN runbook 等
scripts/                   仓库级脚本（dev-desktop、原生包审计等）
surrogate-html/            代理模型独立 HTML 生成器（build.py → ~/Desktop/AlN代理模型.html）
data/                      本地 DATA_ROOT（pgdata/redis/uploads/files/exports/logs/watch，gitignored）
```

根目录 `pyproject.toml`/`uv.lock` 只服务根级分析脚本（pandas/openpyxl 等）；**后端依赖以 `backend/pyproject.toml` 为准**。

## 三、运行时架构

```
浏览器 (React) ──HTTP/SSE──▶ Nginx ──┬──▶ FastAPI (uvicorn ×4)
                                     └──▶ Celery Worker(s)
                       Redis (broker/进度) ◀──┴──▶ PostgreSQL 15
                                                  DATA_ROOT/files/<batch_no>/…（文件不落库）
```

- 上传链路：`POST /api/uploads` → 建 `batches`/`upload_tasks` 行并入队 Celery → worker 解压 zip、S2P 拆 S1P、`extract_resonator_params` 提参、批量写 `devices` → 进度经 Redis 发布，前端走 SSE `/api/tasks/{id}/stream`。
- 含去嵌的大 zip 走 `aln.pipeline_batch` 边解压边计算流水线（`PIPELINE_*` 环境变量控制）。
- 桌面模式（`ALN_DESKTOP_MODE=true`）：同一 FastAPI 应用内嵌本地 worker 线程（`workers/local_queue.py`），SQLite 库在 `~/.aln-data/aln-data.db`，启动时做文件生命周期清理。
- 服务器与桌面端同步：`api/replication_v3.py` + `services/replica_service.py` / `server_client.py` / `server_proxy.py`，本地缺文件时曲线/下载可透明代理到内网服务器。

## 四、常用命令

环境铁律：Python 一律走 `uv`（项目本地 `.venv`），**不要全局 pip install**；加依赖在 `backend/` 下 `uv add <pkg>`。

```bash
# ---- 全栈（仓库根目录）----
cd frontend && npm install && npm run build && cd ..
./bootstrap.sh up        # 启动 5 容器；另有 down / reset / status
# 浏览器访问 http://localhost:${NGINX_PORT:-8080}

# ---- 后端（backend/）----
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
uv run celery -A app.workers worker --loglevel=info --concurrency=4   # 另开终端

# ---- 前端（frontend/）----
npm ci
npm run dev            # Vite :5173，代理 /api → :8000
npm run build          # 产物到 frontend/dist/（同时编译 electron 主进程）

# ---- 桌面开发 ----
./scripts/dev-desktop.sh           # 或 dev.sh（Redis+uvicorn+celery+Vite 一把梭）

# ---- 桌面安装包（前端 build + PyInstaller 后端 + electron-builder）----
python build.py                    # 输出 frontend/release/（mac dmg-arm64 / win nsis+portable）

# ---- 代理模型独立 HTML（离线单文件，输出到桌面）----
backend/.venv/bin/python surrogate-html/build.py
```

`bootstrap.sh up` 需要已配置的 `.env`（由 `.env.example` 复制）和已构建的 `frontend/dist/`。compose 端口映射：nginx `${NGINX_PORT:-8080}:80`、api `8001:8000`、postgres `15432:5432`、redis `6379:6379`；数据全部挂在 `${DATA_ROOT}`（本地开发推荐 `./data`）。

## 五、代码风格与约定

**Python（后端）**

- Python 3.12，四空格缩进；Ruff 管理（`backend/pyproject.toml`：行宽 100，规则集 E/F/W/I/N/B/UP）。无 mypy/black。
- **算法层（`app/core/`）必须是纯函数**：不 print、不写文件、不 input、无全局状态；所有可调常数/魔法数进 `AlgorithmConfig`（`app/config.py`）。
- ORM 用 SQLAlchemy 2.0 typed `Mapped[...]`；表名为复数小写下划线；主键 `bigint generated by default as identity`；时间戳用 `timestamptz`。
- 修 bug 修根因：grep 所有调用方，在共享函数里修一次。

**TypeScript（前端）**

- 新代码用 TS/TSX，两空格缩进；组件 PascalCase（如 `BatchDetail.tsx`），函数 camelCase；测试文件 `*.test.ts(x)`。
- 状态共享用自定义 hooks/Context，不引 Redux；样式用现有自定义 CSS，不引 UI 组件库。
- Plotly 体积大，Vite 已配置 manualChunks 单独拆包，改构建配置时注意保留。

**通用原则（Ponytail：懒惰的高级开发）**

- 删除优先于新增；复用现有 helper/模式优先于重写；最短可工作的 diff 获胜——但只在端到端理解问题之后。
- 不写没人要求的抽象/样板，不加可避免的新依赖。
- 不偷懒的例外：信任边界的输入校验、防数据丢失的错误处理、安全、以及任何显式要求的东西。

**Git 约定**

- Conventional Commits，描述用中文：`feat(sync): …`、`fix(deploy): …`、`docs: …`、`test(sync): …`、`chore(release): …`，一个提交只装一个关注点。
- `main` 是唯一集成分支；ML/训练链改动在 worktree `/Users/jingbozuo/Projects/aln-data-backend-ml`（分支 `feat/backend-ml-surrogate`）进行；`feat/backend-ml` 为存档分支勿动。

## 六、测试与质量检查

```bash
# 后端（backend/）
uv run ruff check .            # lint（--fix 自动修）
uv run pytest                  # 全量；tests/core 为算法层单测
uv run pytest -m integration tests/test_real_pipeline.py   # 集成测试，需真实 worker+uvicorn，默认跳过
uv run vulture                 # 死代码检测，当前基线为零（配置在 pyproject [tool.vulture]）

# 前端（frontend/）
npm run typecheck              # tsc --noEmit
npm test                       # vitest run（jsdom，setup 在 src/test/setup.ts）
npm run test:e2e               # Playwright（先编译 electron 主进程，自动起 Vite harness）
npm run knip                   # 未使用文件/导出/依赖检测（knip.json）
```

- 后端测试放 `backend/tests/` 下与被测对象同域的目录；前端测试与被测文件同目录。
- `pytest-asyncio` 为 auto 模式；`tests/fixtures/` 被 Ruff/Vulture 排除。

## 七、部署

- **服务器（Linux/macOS）**：`./bootstrap.sh up`（Podman Compose；Windows 用 `bootstrap.ps1`）。细节见 `docs/deployment.md`、`docs/deployment-windows.md`、`docs/deployment-macos-lan.md`，运维手册见 `docs/operations.md`。
- **免 Docker 原生部署**（内网 Windows 关 VT 的场景）：`deploy/native-v2/` + 根目录 `make_server_pack.sh` / `make_server_pack_lite.sh`，产物在 `dist-server-pack/`（gitignored），说明见 `部署说明-内网服务器.txt`。
- **桌面安装包**：`python build.py`；CI 在 `.github/workflows/build-desktop.yml`（macos + windows 矩阵，workflow_dispatch 或推 `feat/deploy` 触发）。
- 数据库迁移：服务器端 `uv run alembic upgrade head`；桌面 SQLite 走 `app/schema_upgrades.py` + `desktop_setup.py` 的轻量升级路径。

## 八、安全与配置注意事项

- **v1 无认证**，靠内网物理隔离；不要把实例暴露到公网。CORS 仅放行 `localhost:5173/5174` 与桌面 `null` origin。
- `.env` 绝不入库；从 `.env.example` 复制并修改。生产必须改 `POSTGRES_PASSWORD`（避免 `& $ 引号` 等特殊字符）。
- `.gitignore` 已覆盖：客户材料、`data/`、`frontend/dist`、`frontend/release`、`backend/build`、ML 训练产物（`checkpoints_*`、`surrogate_data/` 等）、`dist-server-pack/`。生成数据、数据库、构建/发布产物一律不提交。
- 资源保护默认值（`app/config.py`）：查询上限 200,000 行；导出上限 500 万行 / 2 GB；上传默认上限 100 GB（服务器 `.env` 的 `UPLOAD_MAX_GB` 可调）。
- 关键产品决策：mBVD 六列（`C0/Cm/Lm/Rm/R0/Rs`）有意未实现；去嵌默认关闭（需 zip 内含 OPEN/SHORT 校准 s2p）；Fail 器件保留入库以看全分布；批次名全局唯一，重名拒绝。

## 九、重要文档索引

- `README.md` / `README-desktop.md` — 快速上手（服务器/桌面）
- `CLAUDE.md` — 含更细的编码原则与 worktree 路由说明
- `docs/project-overview.md` — 面向新成员的全景文档
- `docs/architecture.md` — 系统架构与关键决策
- `docs/api.md` — API 契约
- `docs/database-schema.md` — 表结构与索引策略
- `docs/algorithm-port.md` — 客户脚本 → 后端算法逐函数映射及移植期修复的 bug
- `docs/operations.md` — 运维 cookbook（重启/备份/排障）
- `docs/PINN-RUNBOOK.md` / `docs/PINN-CHANGELOG.md` — 代理模型训练与推理
