$ROOT = Split-Path -Parent $PSScriptRoot

Write-Host "[dev-desktop] 同步后端依赖..."
& uv sync --directory "$ROOT/backend"

Write-Host "[dev-desktop] 启动桌面开发模式（Electron + Vite + 本地 Python 后端）..."
Set-Location "$ROOT/frontend"
& npm run electron:dev
