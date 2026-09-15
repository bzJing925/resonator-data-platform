# Repository Guidelines

## Project Structure & Module Organization

- `backend/app/` contains the FastAPI application: `api/` routes, `core/` pure RF-data algorithms, `models/` SQLAlchemy models, `schemas/` Pydantic contracts, `services/`, `workers/`, and `ml/`.
- `backend/tests/` mirrors backend concerns (`api/`, `core/`, `services/`, `workers/`, `ml/`). Alembic migrations live in `backend/alembic/`.
- `frontend/src/` contains the React app: route-level `pages/`, shared `components/`, `api/`, browser-side `ml/`, hooks, contexts, and tests. Electron files are in `frontend/electron/`.
- `deploy/` holds the five-service Podman Compose stack and Nginx config. `docs/` contains architecture, API, deployment, and algorithm references.

## Build, Test, and Development Commands

Use `uv` for Python and `npm` for the frontend; do not install Python packages globally.

```bash
# Backend (run from backend/)
uv sync
uv run uvicorn app.main:app --reload --port 8000
uv run celery -A app.workers worker --loglevel=info --concurrency=4
uv run alembic upgrade head

# Frontend (run from frontend/)
npm ci
npm run dev          # Vite on :5173, proxies /api to :8000
npm run build        # outputs dist/

# Full container stack (from repository root)
./bootstrap.sh up

# Desktop installer
python build.py
```

`bootstrap.sh up` requires a configured `.env` and an already-built `frontend/dist/`; it starts PostgreSQL, Redis, API, worker, and Nginx.

## Coding Style & Naming Conventions

Python targets 3.12, uses four-space indentation, typed SQLAlchemy `Mapped[...]` models, and Ruff (`100`-character lines; E/F/W/I/N/B/UP rules). Keep algorithm functions side-effect free and put tunable constants in `backend/app/config.py`.

Use TypeScript/TSX for new frontend code, two-space indentation, PascalCase React components (for example `BatchDetail.tsx`), camelCase functions, and `*.test.ts(x)` tests. Prefer existing custom CSS and components over adding a UI library.

## Testing Guidelines

```bash
cd backend && uv run ruff check . && uv run pytest
cd frontend && npm run typecheck && npm test
```

Add focused tests beside the relevant backend area or under `frontend/src/`. Integration tests marked `integration` are skipped by default and require live services: `uv run pytest -m integration tests/test_real_pipeline.py`.

## Commits, Pull Requests, and Configuration

Follow the existing Conventional Commit style: `feat(frontend): ...`, `fix(deploy): ...`, `docs: ...`, or `chore(release): ...`. Keep each commit scoped to one concern. PRs should state the user-visible change, list verification commands, link related issues, and include screenshots for UI changes. Never commit `.env`, secrets, generated data, databases, or build/release artifacts; copy `.env.example` and configure local values instead.
