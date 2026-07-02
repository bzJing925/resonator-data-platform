#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
  echo "[dev] 停止所有进程..."
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$ROOT/backend"
uv sync

cd "$ROOT"

uv run --directory backend uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
UVICORN_PID=$!

uv run --directory backend celery -A app.workers worker --loglevel=info --concurrency=4 &
CELERY_PID=$!

cd "$ROOT/frontend"
npm run dev &
VITE_PID=$!

echo "[dev] uvicorn=$UVICORN_PID celery=$CELERY_PID vite=$VITE_PID"
wait
