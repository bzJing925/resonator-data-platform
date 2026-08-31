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

# 确保 electron 启动的后端子进程使用 .venv 里的 Python/uvicorn
export PATH="$ROOT/backend/.venv/bin:$PATH"

echo "[dev-desktop] 启动桌面开发模式（Electron + Vite + 本地 Python 后端）..."
cd "$ROOT/frontend"
# 在 VSCode / Trae 等 Electron 编辑器内置终端中，ELECTRON_RUN_AS_NODE=1 会污染子进程，
# 导致 electron 以 Node 模式启动而拿不到 app/BrowserWindow 等 API。
unset ELECTRON_RUN_AS_NODE ELECTRON_FORCE_IS_PACKAGED ICUBE_IS_ELECTRON VSCODE_RUN_IN_ELECTRON 2>/dev/null || true
npm run electron:dev
