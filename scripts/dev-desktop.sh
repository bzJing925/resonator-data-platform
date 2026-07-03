#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
  echo "[dev-desktop] 停止 Electron / Vite..."
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[dev-desktop] 同步后端依赖..."
cd "$ROOT/backend"
uv sync

echo "[dev-desktop] 启动桌面开发模式（Electron + Vite + 本地 Python 后端）..."
cd "$ROOT/frontend"
npm run electron:dev
