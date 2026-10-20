#!/bin/bash
# 一键停止内网共享服务
set -u
cd "$(dirname "$0")/.." || exit 1

PIDFILE="data/intranet-server.pid"
killed=0

if [ -f "$PIDFILE" ]; then
  PID=$(cat "$PIDFILE")
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null && killed=1
  fi
  rm -f "$PIDFILE"
fi

# 兜底：按命令行模式清理（含 pidfile 丢失的情况）
if pgrep -f "uvicorn app.main:app --host 0.0.0.0 --port 8000" >/dev/null 2>&1; then
  pkill -f "uvicorn app.main:app --host 0.0.0.0 --port 8000" && killed=1
fi

sleep 1
if curl -s --max-time 2 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  echo "停止失败：8000 端口仍在响应，请检查日志 data/logs/intranet-server.log"
  exit 1
elif [ "$killed" -eq 1 ]; then
  echo "内网服务已停止。"
else
  echo "没有在运行的内网服务。"
fi
