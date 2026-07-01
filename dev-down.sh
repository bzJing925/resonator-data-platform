#!/usr/bin/env bash
# 一键停止本地开发环境
set -euo pipefail

log() { echo "[dev-down] $*"; }

log "停止本地开发服务..."

pkill -f 'vite --host 0.0.0.0' 2>/dev/null || true
pkill -f 'uvicorn app.main:app --reload --port 8000' 2>/dev/null || true
pkill -f 'celery -A app.workers worker' 2>/dev/null || true
redis-cli shutdown nosave 2>/dev/null || true

log "已停止"
