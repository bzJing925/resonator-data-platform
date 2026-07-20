$ROOT = Split-Path -Parent $PSScriptRoot

# 修复中文乱码：把当前控制台代码页切到 UTF-8
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 刷新 PATH，确保新安装的 Node.js / Python / uv 等立即可用（无需重开终端）
$machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
$user = [System.Environment]::GetEnvironmentVariable("Path", "User")
$sys32 = "C:\WINDOWS\system32"
# System32 放最前，确保 cmd.exe 可被并发子进程找到
$env:Path = "$sys32;$machine;$user;$env:Path"
$env:ComSpec = "C:\WINDOWS\system32\cmd.exe"

# 清理可能残留的 Vite / Electron 进程，避免旧代码被复用
$vitePort = 5173
Get-NetTCPConnection -LocalPort $vitePort -ErrorAction SilentlyContinue | ForEach-Object {
    $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "[dev-desktop] 结束占用 $vitePort 端口的进程: $($proc.ProcessName)($($proc.Id))"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}
$nodeProcs = Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='electron.exe'" | Where-Object {
    $_.CommandLine -like "*$ROOT*"
}
foreach ($p in $nodeProcs) {
    Write-Host "[dev-desktop] 结束残留开发进程: $($p.Name)($($p.ProcessId))"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

# 让 Electron 里的 spawn 能用上 backend 虚拟环境的 python，而不是系统 python
$venvPython = Join-Path $ROOT "backend\.venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $env:ALN_PYTHON_PATH = $venvPython
    Write-Host "[dev-desktop] 使用 backend 虚拟环境 Python: $venvPython"
} else {
    $pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $pythonPath) {
        Write-Error "未找到 python 命令，请确认 Python 已安装并加入 PATH"
        exit 1
    }
    $env:ALN_PYTHON_PATH = $pythonPath
    Write-Host "[dev-desktop] 使用系统 Python: $pythonPath"
}

Write-Host "[dev-desktop] 同步后端依赖..."
& uv sync --directory "$ROOT/backend"
if ($LASTEXITCODE -ne 0) {
    Write-Error "后端依赖同步失败"
    exit 1
}

Write-Host "[dev-desktop] 编译 Electron 主进程..."
Set-Location "$ROOT/frontend"
& npm run electron:build-main
if ($LASTEXITCODE -ne 0) {
    Write-Error "Electron 主进程编译失败"
    exit 1
}

# 使用 node_modules 里的 .cmd 直接启动，绕过 concurrently 在 Windows 下的 spawn 问题
$viteCmd = Join-Path $ROOT "frontend\node_modules\.bin\vite.cmd"
$electronCmd = Join-Path $ROOT "frontend\node_modules\.bin\electron.cmd"

Write-Host "[dev-desktop] 启动 Vite 开发服务器..."
$viteProc = Start-Process -FilePath $viteCmd -ArgumentList "--host","0.0.0.0" -WorkingDirectory "$ROOT/frontend" -PassThru -WindowStyle Normal

Write-Host "[dev-desktop] 等待 Vite 就绪..." -NoNewline
$maxAttempts = 60
$ready = $false
for ($i = 0; $i -lt $maxAttempts; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", 5173)
        $client.Close()
        $ready = $true
        break
    } catch {
        Write-Host "." -NoNewline
    }
}
Write-Host ""

if (-not $ready) {
    Write-Error "Vite 未在 30 秒内就绪"
    Stop-Process -Id $viteProc.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "[dev-desktop] 启动 Electron..."
# Electron 直接在前台运行，方便在主进程输出里看到后端启动错误
# 按 Ctrl+C 即可退出
try {
    & $electronCmd .
} finally {
    Write-Host "[dev-desktop] 正在停止 Vite..."
    Stop-Process -Id $viteProc.Id -Force -ErrorAction SilentlyContinue
}

Write-Host "[dev-desktop] 已停止"
