# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

谐振器测试数据平台 (Resonator Test Data Platform): a full-stack web app for managing RF resonator (AlN BAW) test data. Users upload zip files of S-parameter data via the browser; the backend ingests, extracts parameters, stores them in PostgreSQL, and serves interactive plots and exports. Phase 1 covers resonators; filters are planned for phase 2.

- **Backend**: FastAPI 0.115 + SQLAlchemy 2.0 + Celery 5 + PostgreSQL 15 + Redis 7
- **Frontend**: React 18 + Vite 5 + React Router 6 + Plotly.js
- **Algorithm stack**: numpy / scipy / scikit-rf (ported from the customer's CLI scripts)
- **Package managers**: uv (Python) + npm (frontend)
- **Deployment**: 5-container compose (postgres / redis / api / worker / nginx) via `bootstrap.sh` / `bootstrap.ps1`

## Worktree routing

2026-09-21 起域 worktree 已裁撤（ algo/api/deploy/frontend/static-web 五个域分支经树级内容核对，确认已全部并入或落后于 main 后删除）。当前只保留：

- `/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master` (`main`) — 默认且唯一的集成工作区。后端/前端/部署/文档等所有常规改动直接在这里进行。
- `/Users/jingbozuo/Projects/aln-data-backend-ml` (`feat/backend-ml-surrogate`) — 代理模型 / Mason-sim 活跃工作区：`backend/app/ml/`、训练脚本、IUS 实验脚本与训练产物（gitignored）都在这边。ML/训练链改动在这个 worktree 做。
- `feat/backend-ml` 分支（无 worktree）— 旧 mason-sim 23 提交存档，勿动。

## Python environment rules

- This project uses **uv**. Always use the project-local virtual environment at `.venv`.
- Never install packages globally. Never run `pip install` directly unless explicitly requested.
- Use `uv add <package>` (from `backend/`) to add dependencies.
- Use `uv sync` to sync the environment.
- Use `uv run <command>` to run Python, pytest, scripts, and tools.
- Python version: `>=3.12`. The root `pyproject.toml` is minimal; backend dependencies live in `backend/pyproject.toml`.

## 编码原则（Ponytail：懒惰的高级开发）

懒惰 = 高效，不是马虎。最好的代码是没写的代码。写任何代码前，在第一个站得住的梯级停下：

1. 这个功能真的需要吗？（YAGNI）
2. 代码库里已有实现吗？——复用现有 helper/util/模式，不要重写
3. 标准库能搞定吗？用标准库
4. 平台原生能力能覆盖吗？用它
5. 已安装的依赖能解决吗？用它
6. 一行能写完吗？就写一行
7. 以上都不行：写能工作的最少代码

梯子是在理解问题**之后**爬的，不是代替理解：先读任务和它触及的代码，端到端 trace 真实流程，再爬梯子。

修 bug = 修根因，不是修症状：bug 报告描述的是症状。grep 你改的函数的**所有调用方**，在共享函数里修一次——一处 guard 比每个调用方各打一补丁的 diff 更小；只修工单里那条路径，兄弟调用方照样坏。

规则：

- 不写没人要求的抽象；不加可以避免的新依赖；不写没人要的样板代码
- 删除优先于新增；无聊优先于取巧；文件越少越好
- 最短可工作的 diff 获胜——但只在理解问题之后。在错误位置做最小改动不是懒，是第二个 bug
- 质疑复杂需求："你真的需要 X，还是 Y 就够了？"
- 两个标准库方案体量相当时，选边界情况正确的那个——懒是代码更少，不是算法更脆
- 有意简化且砍了真实能力上限的（全局锁、O(n²) 扫描、朴素启发式），用 `ponytail:` 注释标明上限和升级路径

**不偷懒的地方**：理解问题（读完任务、trace 完流程再选梯级）、信任边界的输入校验、防止数据丢失的错误处理、安全、可访问性、任何显式要求的东西。非平凡逻辑必须留下一个可运行的检查（一个 assert 自验/demo 或一个小测试文件；不要框架、不要 fixture）；平凡的一行代码不需要测试。

## Common commands

### Full stack (from repo root)

```bash
# Linux / macOS: build frontend then start all 5 containers (postgres, redis, api, worker, nginx)
cd frontend && npm install && npm run build && cd ..
./bootstrap.sh up
# open http://localhost:8080

# Stop / reset / status
./bootstrap.sh down
./bootstrap.sh reset
./bootstrap.sh status
```

### Backend development (from `backend/`)

```bash
uv sync

# Run API dev server with auto-reload on :8000
uv run uvicorn app.main:app --reload --port 8000

# Run Celery worker in another terminal
uv run celery -A app.workers worker --loglevel=info --concurrency=4

# Run database migrations
uv run alembic upgrade head
```

### Frontend development (from `frontend/`)

```bash
npm install
npm run dev          # Vite dev server on :5173, proxies /api to :8000
npm run build        # Production build into frontend/dist/
npm run preview      # Preview the production build
```

### Tests

```bash
# Backend (from backend/)
uv run pytest                        # all tests
uv run pytest tests/core -v          # algorithm-layer tests only
uv run pytest tests/test_extract.py  # single test file
uv run pytest -k test_filename       # single test by name
uv run pytest --cov=app --cov-report=html
```

Integration tests (marked with `integration`) are skipped by default because they require a running worker and uvicorn server.

### Lint / format

```bash
# Backend (from backend/)
uv run ruff check .
uv run ruff check . --fix
uv run ruff format .
uv run ruff check --select SIM .   # 代码简化规则（非默认启用，32 处存量待清理）
uv run vulture                      # 死代码检测（配置在 backend/pyproject.toml [tool.vulture]，当前基线为零）
```

Ruff is configured in `backend/pyproject.toml` (line length 100, target Python 3.12, rules E/F/W/I/N/B/UP). There is no mypy or black setup.

```bash
# Frontend (from frontend/)
npm run knip    # 未使用文件/导出/依赖检测（配置在 frontend/knip.json）
```

### Desktop build

```bash
python build.py      # Builds frontend + PyInstaller backend + Electron installer
```

## Architecture

### System layout

```
Browser (React + Vite) ──HTTP/SSE──▶ Nginx ──┬──▶ FastAPI (api)
                                             │
                                             └──▶ Celery Worker(s)
                                                    │
                        Redis (broker/results) ◀───┴──▶ PostgreSQL
                                                    │
                                                    ▼
                                              /data3/aln/files/
```

- **Nginx** serves the static frontend and proxies `/api` to the FastAPI container.
- **FastAPI** handles synchronous requests (upload, query, export, device curves) and SSE progress streams.
- **Celery workers** run the ingestion pipeline: unzip → split S2P into S1P → run parameter extraction → bulk insert into `devices` → update task progress in Redis, which FastAPI forwards over SSE.
- **Files** are stored on local disk under `DATA_ROOT/files/<batch_no>/...`, not in the database.

### Backend code organization

- `backend/app/api/` — FastAPI route modules (`upload`, `batches`, `mappings`, `query`, `devices`, `export`, `tasks`, `system`).
- `backend/app/core/` — Algorithm layer: pure functions for Touchstone parsing (`touchstone.py`), de-embedding (`deembed.py`), parameter extraction (`extract.py`), mapping parsing (`mapping.py`), and filename parsing (`filename.py`).
- `backend/app/models/` — SQLAlchemy 2.0 ORM models with typed `Mapped[...]` declarations.
- `backend/app/schemas/` — Pydantic request/response models.
- `backend/app/workers/` — Celery task definitions.
- `backend/app/config.py` — `Settings` (env vars) and `AlgorithmConfig` (all algorithmic constants / magic numbers).
- `backend/app/ml/` — Optional PINN / sparse-reconstruction module integrated into `devices.py`. Endpoints `/api/devices/{id}/sparam` and `/api/devices/{id}/sparam-sparse` can use trained models for fast curve inference. Training scripts live in `backend/scripts/`.
- `backend/alembic/` — Database migrations.

### Algorithm layer rules

- Core algorithm code must be **pure functions**: no `print`, no file writes, no `input()`, no global state.
- All tunable constants and magic numbers belong in `AlgorithmConfig` (`backend/app/config.py`).
- The layer was ported from the customer's CLI scripts; see `docs/algorithm-port.md` for the function-by-function mapping and list of bugs fixed during porting.

### Frontend code organization

- `frontend/src/api/` — axios client and API wrappers.
- `frontend/src/pages/` — Route-level page components (Dashboard, Explore, Batches, BatchDetail, Mappings, Upload, Tasks, TaskDetail).
- `frontend/src/components/` — Shared React components.
- `frontend/src/router/` — React Router 6 configuration.
- `frontend/src/hooks/` — Custom hooks; used instead of Redux/Zustand for state sharing.
- `frontend/src/styles.css` — Custom CSS; no large component library is used.

### Data flow for upload

1. Browser `POST /api/uploads` with zip + mapping id + frequency range.
2. FastAPI creates `batches` and `upload_tasks` rows, enqueues a Celery task.
3. Worker extracts the zip to `files/<batch_no>/`, splits S2P files into S1P per port, runs `extract_resonator_params`, and bulk inserts into `devices`.
4. Progress is published to Redis; browser receives it via SSE on `/api/tasks/{id}/stream`.

### Key product decisions

- mBVD 6 columns (`C0`, `Cm`, `Lm`, `Rm`, `R0`, `Rs`) are intentionally **not implemented**.
- De-embedding is **optional and default off**; enabling it requires `OPEN`/`SHORT` calibration `.s2p` files in the uploaded zip.
- Fail data is **kept in the database** (most devices are Fail) so analysts can see full distributions.
- Batch names must be **unique**; duplicate names are rejected.
- No authentication in v1; access is restricted by internal network isolation.
- Query results are capped at 200,000 rows to prevent memory issues.

### Database conventions

- PostgreSQL 15; table names are plural lowercase underscore (e.g., `batches`, `devices`, `mapping_entries`).
- Primary keys use `bigint generated by default as identity`.
- Timestamp columns use `timestamptz`.
- Partial indexes exist for `pf = 'Y'` (Pass-only queries); result columns like `qs`/`qp`/`k2eff_pct` intentionally have no single-column indexes.

## Important documentation

- `README.md` — Quick start, tech stack, data directory layout.
- `backend/README.md` — Backend local development and test commands.
- `docs/architecture.md` — System architecture and key decisions.
- `docs/api.md` — API contract for all 24 endpoints.
- `docs/database-schema.md` — Schema and indexing strategy.
- `docs/algorithm-port.md` — Customer script → backend algorithm mapping.
- `docs/operations.md` — Restart, backup, and troubleshooting runbook.
- `docs/deployment.md` / `docs/deployment-windows.md` — Linux/macOS and Windows deployment guides.
- `docs/PINN-RUNBOOK.md` / `docs/PINN-CHANGELOG.md` — PINN / sparse-reconstruction training and inference.

<!-- superpowers-zh:begin -->
# 项目 Skills 清单

本项目在 `.claude/skills/` 安装了以下 skills。**规则：不自动调用任何 skill**——仅在用户显式输入 `/<skill-name>` 或明确要求时，才通过 Skill 工具加载。

## 流程类

- **brainstorming** — 创造性工作前的需求/设计探索
- **writing-plans** — 多步骤任务的实现计划编写
- **executing-plans** — 在独立会话中执行书面计划（带审查检查点）
- **subagent-driven-development** — 在当前会话中执行含独立任务的计划
- **dispatching-parallel-agents** — 2 个以上独立任务的并行分发
- **test-driven-development** — 先写测试再实现（TDD）
- **systematic-debugging** — bug / 测试失败的纪律化诊断流程
- **verification-before-completion** — 声称完成前运行验证命令
- **requesting-code-review** — 完成后/合并前请求审查
- **receiving-code-review** — 收到审查反馈后的处理流程
- **finishing-a-development-branch** — 分支收尾（合并/PR/清理）
- **using-git-worktrees** — 隔离工作区开发
- **using-superpowers** — 技能查找与使用规则
- **writing-skills** — 创建/编辑/验证 skill

## 中文参考类

- **chinese-code-review** — 中文 review 话术模板与分级标注
- **chinese-commit-conventions** — Conventional Commits 中文适配与工具链配置
- **chinese-documentation** — 中文排版约定（中英文空格/标点/术语）
- **chinese-git-workflow** — Gitee/Coding/极狐 GitLab/CNB 平台接入差异

## 专项类

- **mcp-builder** — 生产级 MCP 服务器构建方法论
- **workflow-runner** — 直接运行 agency-orchestrator YAML 工作流
<!-- superpowers-zh:end -->
