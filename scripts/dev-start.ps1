$ROOT = Split-Path -Parent $PSScriptRoot

$uvicorn = Start-Process -FilePath "uv" -ArgumentList "run","--directory","$ROOT/backend","uvicorn","app.main:app","--reload","--host","127.0.0.1","--port","8000" -PassThru
$celery = Start-Process -FilePath "uv" -ArgumentList "run","--directory","$ROOT/backend","celery","-A","app.workers","worker","--loglevel=info","--concurrency=4" -PassThru
$vite = Start-Process -FilePath "npm" -ArgumentList "run","dev" -WorkingDirectory "$ROOT/frontend" -PassThru

Write-Host "[dev] started uvicorn=$($uvicorn.Id) celery=$($celery.Id) vite=$($vite.Id)"

Read-Host "按 Enter 停止所有进程..."

Stop-Process -Id $uvicorn.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $celery.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $vite.Id -Force -ErrorAction SilentlyContinue
