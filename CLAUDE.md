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

This repository has domain-specific git worktrees under `/Users/jingbozuo/Projects`. For future code changes, choose the worktree that matches the task before editing. If a task spans multiple domains, use the worktree for the primary area being changed; ask the user only when the primary area is ambiguous.

- `/Users/jingbozuo/Projects/aln-data-master` (`main`) — integration/default workspace. Use for repo-wide coordination, general documentation, cross-cutting changes that do not fit a domain worktree, and final merge/release checks.
- `/Users/jingbozuo/Projects/aln-data-backend-algo` (`feat/backend-algo`) — algorithm work: `backend/app/core/`, algorithm constants in `backend/app/config.py`, algorithm tests, and `docs/algorithm-port.md`.
- `/Users/jingbozuo/Projects/aln-data-backend-api` (`feat/backend-api`) — backend API/data work: FastAPI routes in `backend/app/api/`, schemas, SQLAlchemy models, Alembic migrations, workers/tasks, query/export/upload/device endpoints, and backend API tests.
- `/Users/jingbozuo/Projects/aln-data-backend-ml` (`feat/backend-ml`) — ML/PINN/sparse reconstruction work: `backend/app/ml/`, related training scripts under `backend/scripts/`, sparse/sparam inference integration, and PINN runbook/changelog docs.
- `/Users/jingbozuo/Projects/aln-data-frontend` (`feat/frontend`) — frontend work: React/Vite app under `frontend/`, frontend API wrappers, routing, components, pages, hooks, styles, and UI behavior.
- `/Users/jingbozuo/Projects/aln-data-deploy` (`feat/deploy`) — deployment/ops/packaging work: Docker/Compose, nginx, bootstrap scripts, desktop build/installer files, deployment and operations documentation.

## Python environment rules

- This project uses **uv**. Always use the project-local virtual environment at `.venv`.
- Never install packages globally. Never run `pip install` directly unless explicitly requested.
- Use `uv add <package>` (from `backend/`) to add dependencies.
- Use `uv sync` to sync the environment.
- Use `uv run <command>` to run Python, pytest, scripts, and tools.
- Python version: `>=3.12`. The root `pyproject.toml` is minimal; backend dependencies live in `backend/pyproject.toml`.

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
```

Ruff is configured in `backend/pyproject.toml` (line length 100, target Python 3.12, rules E/F/W/I/N/B/UP). There is no mypy or black setup.

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

<!-- superpowers-zh:begin (do not edit between these markers) -->
# Superpowers-ZH 中文增强版

本项目已安装 superpowers-zh 技能框架（20 个 skills）。

## 核心规则

1. **收到任务时，先检查是否有匹配的 skill** — 哪怕只有 1% 的可能性也要检查
2. **设计先于编码** — 收到功能需求时，先用 brainstorming skill 做需求分析
3. **测试先于实现** — 写代码前先写测试（TDD）
4. **验证先于完成** — 声称完成前必须运行验证命令

## 可用 Skills

Skills 位于 `.claude/skills/` 目录，每个 skill 有独立的 `SKILL.md` 文件。

- **brainstorming**: 在任何创造性工作之前必须使用此技能——创建功能、构建组件、添加功能或修改行为。在实现之前先探索用户意图、需求和设计。
- **chinese-code-review**: 中文 review 沟通参考——话术模板、分级标注（必须修复/建议修改/仅供参考）、国内团队常见反模式应对。仅在用户显式 /chinese-code-review 时调用，不要根据上下文自动触发。
- **chinese-commit-conventions**: 中文 commit 与 changelog 配置参考——Conventional Commits 中文适配、commitlint/husky/commitizen 中文模板、conventional-changelog 中文配置。仅在用户显式 /chinese-commit-conventions 时调用，不要根据上下文自动触发。
- **chinese-documentation**: 中文文档排版参考——中英文空格、全半角标点、术语保留、链接格式、中文文案排版指北约定。仅在用户显式 /chinese-documentation 时调用，不要根据上下文自动触发。
- **chinese-git-workflow**: 国内 Git 平台配置参考——Gitee、Coding.net、极狐 GitLab、CNB 的 SSH/HTTPS/凭据/CI 接入差异与镜像同步配置。仅在用户显式 /chinese-git-workflow 时调用，不要根据上下文自动触发。
- **dispatching-parallel-agents**: 当面对 2 个以上可以独立进行、无共享状态或顺序依赖的任务时使用
- **executing-plans**: 当你有一份书面实现计划需要在单独的会话中执行，并设有审查检查点时使用
- **finishing-a-development-branch**: 当实现完成、所有测试通过、需要决定如何集成工作时使用——通过提供合并、PR 或清理等结构化选项来引导开发工作的收尾
- **mcp-builder**: MCP 服务器构建方法论 — 系统化构建生产级 MCP 工具，让 AI 助手连接外部能力
- **receiving-code-review**: 收到代码审查反馈后、实施建议之前使用，尤其当反馈不明确或技术上有疑问时——需要技术严谨性和验证，而非敷衍附和或盲目执行
- **requesting-code-review**: 完成任务、实现重要功能或合并前使用，用于验证工作成果是否符合要求
- **subagent-driven-development**: 当在当前会话中执行包含独立任务的实现计划时使用
- **systematic-debugging**: 遇到任何 bug、测试失败或异常行为时使用，在提出修复方案之前执行
- **test-driven-development**: 在实现任何功能或修复 bug 时使用，在编写实现代码之前
- **using-git-worktrees**: 当需要开始与当前工作区隔离的功能开发，或在执行实现计划之前使用——通过原生工具或 git worktree 回退机制确保隔离工作区存在
- **using-superpowers**: 在开始任何对话时使用——确立如何查找和使用技能，要求在任何响应（包括澄清性问题）之前调用 Skill 工具
- **verification-before-completion**: 在宣称工作完成、已修复或测试通过之前使用，在提交或创建 PR 之前——必须运行验证命令并确认输出后才能声称成功；始终用证据支撑断言
- **workflow-runner**: 在 Claude Code / OpenClaw / Cursor 中直接运行 agency-orchestrator YAML 工作流——无需 API key，使用当前会话的 LLM 作为执行引擎。当用户提供 .yaml 工作流文件或要求多角色协作完成任务时触发。
- **writing-plans**: 当你有规格说明或需求用于多步骤任务时使用，在动手写代码之前
- **writing-skills**: 当创建新技能、编辑现有技能或在部署前验证技能是否有效时使用

## 如何使用

当任务匹配某个 skill 时，使用 `Skill` 工具加载对应 skill 并严格遵循其流程。绝不要用 Read 工具读取 SKILL.md 文件。

如果你认为哪怕只有 1% 的可能性某个 skill 适用于你正在做的事情，你必须调用该 skill 检查。
<!-- superpowers-zh:end -->
