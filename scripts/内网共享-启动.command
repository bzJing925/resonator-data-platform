#!/bin/bash
# 一键启动内网共享：本机作为服务器，办公电脑浏览器访问 http://<本机IP>:8000
# 双击本文件即可（macOS .command）；服务在后台运行，关闭用「内网共享-停止.command」
#
# 注意：
# - 直接复用桌面版数据库（~/.aln-data），同事看到的就是本机全部数据。
# - 内网共享期间建议不要同时用桌面 App 上传/处理任务（两端 worker 可能竞争同一任务）。
# - 首次运行 macOS 可能弹出「是否允许 python 接受传入连接」，点允许。
set -u
cd "$(dirname "$0")/.." || exit 1

PORT=8000
PIDFILE="data/intranet-server.pid"
LOGFILE="data/logs/intranet-server.log"
mkdir -p data/logs

# 已在运行则直接报告
if curl -s --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "内网服务已在运行："
else
  echo "启动内网服务（首次启动需加载依赖，约 5-15 秒）..."
  env -u DEBUG ALN_DESKTOP_MODE=true nohup backend/.venv/bin/uvicorn app.main:app \
      --host 0.0.0.0 --port "$PORT" --app-dir backend \
      > "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in $(seq 1 30); do
    sleep 1
    curl -s --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  done
fi

if ! curl -s --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "启动失败，日志见 $LOGFILE（末尾 30 行）："
  tail -30 "$LOGFILE"
  exit 1
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "172.20.9.160")
echo ""
echo "  本机访问:  http://127.0.0.1:$PORT"
echo "  内网访问:  http://$IP:$PORT    ← 办公电脑浏览器打开这个"
echo ""
echo "日志: $LOGFILE   停止: 双击「内网共享-停止.command」"
open "http://127.0.0.1:$PORT" 2>/dev/null || true
