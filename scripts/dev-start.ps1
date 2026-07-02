$ROOT = Split-Path -Parent $PSScriptRoot

Write-Host "[dev] 同步后端依赖..."
& uv sync --directory "$ROOT/backend"

Write-Host "[dev] 检查 PostgreSQL / Redis 可用性..."
try {
  $pg = Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue
  if (-not $pg.TcpTestSucceeded) {
    Write-Host "[dev] 警告：PostgreSQL 未在 127.0.0.1:5432 运行。可用以下命令启动："
    Write-Host "  cd deploy && podman compose --env-file ../.env up -d postgres redis"
  }
} catch {
  Write-Host "[dev] 警告：无法检测 PostgreSQL 状态。"
}

try {
  $rd = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -WarningAction SilentlyContinue
  if (-not $rd.TcpTestSucceeded) {
    Write-Host "[dev] 警告：Redis 未在 127.0.0.1:6379 运行。"
  }
} catch {
  Write-Host "[dev] 警告：无法检测 Redis 状态。"
}

Write-Host "[dev] 启动 uvicorn、celery 和 vite..."
$uvicorn = Start-Process -FilePath "uv" -ArgumentList "run","--directory","$ROOT/backend","uvicorn","app.main:app","--reload","--host","127.0.0.1","--port","8000" -PassThru
$celery = Start-Process -FilePath "uv" -ArgumentList "run","--directory","$ROOT/backend","celery","-A","app.workers","worker","--loglevel=info","--concurrency=4" -PassThru
$vite = Start-Process -FilePath "npm" -ArgumentList "run","dev" -WorkingDirectory "$ROOT/frontend" -PassThru

Write-Host "[dev] started uvicorn=$($uvicorn.Id) celery=$($celery.Id) vite=$($vite.Id)"
Write-Host "[dev] 打开浏览器访问 http://localhost:5173"

Read-Host "按 Enter 停止所有进程..."

Stop-Process -Id $uvicorn.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $celery.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $vite.Id -Force -ErrorAction SilentlyContinue
