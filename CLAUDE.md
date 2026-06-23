# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

谐振器测试数据平台 (Resonator Test Data Platform): a full-stack web app for managing RF resonator (AlN BAW) test data. Users upload zip files of S-parameter data via the browser; the backend ingests, extracts parameters, stores them in PostgreSQL, and serves interactive plots and exports. Phase 1 covers resonators; filters are planned for phase 2.

- **Backend**: FastAPI 0.115 + SQLAlchemy 2.0 + Celery 5 + PostgreSQL 15 + Redis 7
- **Frontend**: React 18 + Vite 5 + React Router 6 + Plotly.js
- **Algorithm stack**: numpy / scipy / scikit-rf (ported from the customer's CLI scripts)
- **Package managers**: uv (Python) + npm (frontend)
- **Deployment**: 5-container compose (postgres / redis / api / worker / nginx) via `bootstrap.sh` / `bootstrap.ps1`

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
