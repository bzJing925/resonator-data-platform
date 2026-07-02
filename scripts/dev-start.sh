#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
  echo "[dev] 停止所有进程..."
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[dev] 同步后端依赖..."
cd "$ROOT/backend"
uv sync

echo "[dev] 检查 PostgreSQL / Redis 可用性..."
if ! (timeout 2 bash -c 'cat < /dev/null > /dev/tcp/127.0.0.1/5432' 2>/dev/null); then
  echo "[dev] 警告：PostgreSQL 未在 127.0.0.1:5432 运行。可用以下命令启动："
  echo "  cd deploy && podman compose --env-file ../.env up -d postgres redis"
  echo "[dev] 继续启动，但后端可能连接失败。"
fi
if ! (timeout 2 bash -c 'cat < /dev/null > /dev/tcp/127.0.0.1/6379' 2>/dev/null); then
  echo "[dev] 警告：Redis 未在 127.0.0.1:6379 运行。"
fi

cd "$ROOT"

echo "[dev] 启动 uvicorn、celery 和 vite..."
uv run --directory backend uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
UVICORN_PID=$!

uv run --directory backend celery -A app.workers worker --loglevel=info --concurrency=4 &
CELERY_PID=$!

cd "$ROOT/frontend"
npm run dev &
VITE_PID=$!

echo "[dev] uvicorn=$UVICORN_PID celery=$CELERY_PID vite=$VITE_PID"
echo "[dev] 打开浏览器访问 http://localhost:5173"
wait
