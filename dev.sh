#!/usr/bin/env bash
# 一键启动本地开发环境：Redis + 后端 uvicorn + Celery worker + 前端 Vite
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# 让 uv 可用（常见安装路径）
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

LOG_DIR="$ROOT/.logs"
mkdir -p "$LOG_DIR"

REDIS_PORT=6379
BACKEND_PORT=8000
FRONTEND_PORT=5173

log() { echo "[dev] $*"; }

is_port_open() {
  local port=$1
  curl -s "http://127.0.0.1:$port/" >/dev/null 2>&1
}

# --- Redis ---
if redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
  log "Redis 已在端口 $REDIS_PORT 运行"
else
  log "启动 Redis (端口 $REDIS_PORT)..."
  redis-server --daemonize yes --port "$REDIS_PORT"
  sleep 1
  if ! redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    log "错误：Redis 启动失败" >&2
    exit 1
  fi
  log "Redis 已启动"
fi

# --- 后端 uvicorn ---
if is_port_open "$BACKEND_PORT"; then
  log "端口 $BACKEND_PORT 已被占用，后端可能已在运行"
else
  log "启动后端 uvicorn (端口 $BACKEND_PORT)..."
  nohup sh -c 'cd backend && uv run uvicorn app.main:app --reload --port 8000' \
    >"$LOG_DIR/uvicorn.log" 2>&1 &
  log "后端日志：$LOG_DIR/uvicorn.log"
fi

# --- Celery worker ---
if pgrep -f 'celery -A app.workers worker' >/dev/null 2>&1; then
  log "Celery worker 已在运行"
else
  log "启动 Celery worker..."
  nohup sh -c 'cd backend && uv run celery -A app.workers worker --loglevel=info --concurrency=4' \
    >"$LOG_DIR/celery.log" 2>&1 &
  log "worker 日志：$LOG_DIR/celery.log"
fi

# --- 前端 Vite ---
if is_port_open "$FRONTEND_PORT"; then
  log "端口 $FRONTEND_PORT 已被占用，前端可能已在运行"
else
  log "启动前端 Vite (端口 $FRONTEND_PORT)..."
  nohup sh -c 'cd frontend && npm run dev' \
    >"$LOG_DIR/vite.log" 2>&1 &
  log "前端日志：$LOG_DIR/vite.log"
fi

log "等待服务就绪..."
for i in {1..15}; do
  if curl -s "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1 && \
     curl -s "http://localhost:$FRONTEND_PORT/" >/dev/null 2>&1; then
    log "全部服务已就绪："
    log "  前端：http://localhost:$FRONTEND_PORT"
    log "  后端：http://localhost:$BACKEND_PORT"
    log "  API 文档：http://localhost:$BACKEND_PORT/api/docs"
    log "停止命令：./dev-down.sh"
    exit 0
  fi
  sleep 1
done

log "警告：服务未能在 15 秒内全部就绪，请查看 $LOG_DIR 日志" >&2
exit 1
