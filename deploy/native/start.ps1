# 静默启动数据平台（原生模式：单进程 API + 内嵌任务 worker + 前端托管）
# 不弹 PowerShell/控制台窗口；日志写入 data\logs\server*.log
$Root = $PSScriptRoot
$env:ALN_DESKTOP_MODE = "true"
$env:ALN_DESKTOP_DIR = "$Root\data"
$env:ALN_SERVER_MODE = "true"  # 作为内网同步源（允许桌面端 push 批次到本机）
$env:DEBUG = "false"

$logDir = "$Root\data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null

# pythonw.exe 不分配控制台；Start-Process 隐藏窗口启动后端（uvicorn 0.0.0.0:8000）
Start-Process -WindowStyle Hidden -FilePath "$Root\venv\Scripts\pythonw.exe" `
    -ArgumentList '-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000' `
    -WorkingDirectory "$Root\backend" `
    -RedirectStandardOutput "$logDir\server.log" `
    -RedirectStandardError "$logDir\server-error.log"
