# 启动数据平台（原生模式，单进程：API + 内嵌任务 worker + 前端静态托管）
$Root = $PSScriptRoot
$env:ALN_DESKTOP_MODE = "true"
$env:ALN_DESKTOP_DIR = "$Root\data"
$env:DEBUG = "false"
Set-Location "$Root\backend"
& "$Root\venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
