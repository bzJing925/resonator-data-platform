#!/bin/bash
# 代理模型一键启动（macOS 可双击 .command / 终端直接运行）
#
# 单进程：FastAPI 直接托管 frontend/dist 构建产物（含代理模型页）。
# 关闭终端窗口或 Ctrl+C → trap 自动杀后端进程，不留孤儿。
#
# 用法：双击本文件，或终端执行 ./start-surrogate.command [端口=8620]
set -euo pipefail
cd "$(dirname "$0")/backend"

PORT="${1:-8620}"
UVICORN_PID=""

cleanup() {
  if [[ -n "$UVICORN_PID" ]] && kill -0 "$UVICORN_PID" 2>/dev/null; then
    echo ""
    echo "清理：停止后端进程 (pid $UVICORN_PID)"
    kill "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM HUP

# 前端产物缺失时尝试构建（node_modules 不存在则提示手动装依赖）
if [[ ! -f ../frontend/dist/index.html ]]; then
  echo "frontend/dist 不存在，尝试构建…"
  if [[ -e ../frontend/node_modules ]]; then
    (cd ../frontend && npm run build)
  else
    echo "缺少 frontend/node_modules，请先执行：cd frontend && npm install && npm run build" >&2
    exit 1
  fi
fi

echo "启动代理模型服务（端口 ${PORT}，Ctrl+C 或关闭窗口即停止）…"
uv run uvicorn app.main:app --port "$PORT" &
UVICORN_PID=$!

# 等待就绪（最多 30s）
ready=0
for _ in $(seq 1 60); do
  if curl -sf "http://localhost:$PORT/api/surrogate/status" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done
if [[ "$ready" != "1" ]]; then
  echo "服务未在 30s 内就绪，请查看上方日志" >&2
  exit 1
fi

URL="http://localhost:$PORT/surrogate"
echo "就绪，打开 $URL"
open "$URL"

# 前台等待；退出/Ctrl+C/关窗都会触发 trap 清理
wait "$UVICORN_PID"
