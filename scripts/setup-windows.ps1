#Requires -RunAsAdministrator
<#
.SYNOPSIS
    ALN 谐振器数据平台 - Windows 一键开发环境安装脚本

.DESCRIPTION
    在全新 Windows 电脑上自动安装 Python/Node/PostgreSQL/Redis/uv，
    克隆项目，初始化数据库并启动桌面版。

.PARAMETER ProjectDir
    项目克隆目标目录，默认为 $env:USERPROFILE\resonator-data-platform

.PARAMETER PythonVersion
    要安装的 Python 主版本，默认 3.14

.PARAMETER PostgresPassword
    PostgreSQL 管理员/应用密码。若未提供，脚本会交互式询问。

.PARAMETER SkipSystemDeps
    跳过系统依赖安装（Python/Node/PostgreSQL/Redis）

.PARAMETER SkipClone
    跳过 git clone（假设 ProjectDir 已存在）

.EXAMPLE
    # 全新机器，交互式运行
    .\scripts\setup-windows.ps1

    # 指定目录和密码，无人值守
    .\scripts\setup-windows.ps1 -ProjectDir D:\aln-data -PostgresPassword MyP@ssw0rd
#>
param(
    [string]$ProjectDir = "$env:USERPROFILE\resonator-data-platform",
    [string]$PythonVersion = "3.14",
    [string]$PostgresPassword = "",
    [switch]$SkipSystemDeps,
    [switch]$SkipClone
)

$ErrorActionPreference = "Stop"

# -----------------------------------------------------------------------------
# 日志辅助
# -----------------------------------------------------------------------------
function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  OK: $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  WARN: $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "  ERROR: $Message" -ForegroundColor Red
    exit 1
}

# -----------------------------------------------------------------------------
# 环境变量刷新（让新安装的软件立即可用，无需重开终端）
# -----------------------------------------------------------------------------
function Update-EnvPath {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

# -----------------------------------------------------------------------------
# 依赖安装（优先用 winget）
# -----------------------------------------------------------------------------
function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-WithWinget {
    param(
        [string]$Id,
        [string]$FriendlyName,
        [string]$OverrideArgs = ""
    )
    Write-Step "安装 $FriendlyName ($Id)"
    try {
        $wingetArgs = @("install", "--id", $Id, "-e", "--source", "winget",
                       "--accept-source-agreements", "--accept-package-agreements")
        if ($OverrideArgs) { $wingetArgs += @("--override", $OverrideArgs) }
        & winget @wingetArgs
        Update-EnvPath
        Write-Ok "$FriendlyName 安装完成"
        return $true
    }
    catch {
        Write-Warn "安装 $FriendlyName ($Id) 失败：$_"
        return $false
    }
}

function Install-FirstAvailableWingetPackage {
    param(
        [string]$FriendlyName,
        [string[]]$Ids,
        [string]$OverrideArgs = ""
    )
    foreach ($id in $Ids) {
        if (Install-WithWinget -Id $id -FriendlyName $FriendlyName -OverrideArgs $OverrideArgs) {
            return $true
        }
    }
    return $false
}

function Install-SystemDependencies {
    if (-not (Test-Command "winget")) {
        Write-Err "未检测到 winget。请先安装 '应用安装程序'（App Installer）或手动安装依赖后重试。"
    }

    $failedDeps = @()
    Write-Step "检查并安装系统依赖"

    # Python
    if (-not (Test-Command "python") -or (python --version) -notmatch "^Python $PythonVersion") {
        $ok = Install-WithWinget -Id "Python.Python.$PythonVersion" -FriendlyName "Python $PythonVersion" `
            -OverrideArgs '/quiet InstallAllUsers=1 PrependPath=1 Include_test=0'
        if (-not $ok) { $failedDeps += "Python $PythonVersion" }
    }
    else {
        Write-Ok "Python 已满足版本要求"
    }

    # Node.js
    if (-not (Test-Command "node")) {
        $ok = Install-WithWinget -Id "OpenJS.NodeJS.LTS" -FriendlyName "Node.js LTS"
        if (-not $ok) { $failedDeps += "Node.js LTS" }
    }
    else {
        Write-Ok "Node.js 已安装"
    }

    # PostgreSQL - 尝试多个常见 winget ID
    if (-not (Test-Command "psql")) {
        $ok = Install-FirstAvailableWingetPackage -FriendlyName "PostgreSQL" `
            -Ids @("PostgreSQL.PostgreSQL.17", "PostgreSQL.PostgreSQL.16", "PostgreSQL.PostgreSQL.15", "PostgreSQL.PostgreSQL") `
            -OverrideArgs '--unattendedmodeui none --mode unattended'
        if (-not $ok) { $failedDeps += "PostgreSQL" }
    }
    else {
        Write-Ok "PostgreSQL 已安装"
    }

    # Redis - Memurai 对 Windows 最友好，失败则提示手动安装
    if (-not (Test-Command "redis-cli")) {
        $ok = Install-FirstAvailableWingetPackage -FriendlyName "Redis (Memurai)" `
            -Ids @("Memurai.Memurai", "Memurai.Memurai-Developer")
        if (-not $ok) { $failedDeps += "Redis" }
    }
    else {
        Write-Ok "Redis 已安装"
    }

    return $failedDeps
}

# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------

Write-Host @"
===============================================
  ALN 谐振器数据平台 - Windows 一键安装脚本
  Python: $PythonVersion
  项目目录: $ProjectDir
===============================================
"@ -ForegroundColor Cyan

if ($PythonVersion -eq "3.14") {
    Write-Warn "Python 3.14 较新，numpy/scipy/pandas/scikit-rf 等科学计算包可能没有预编译 wheel，"
    Write-Warn "uv sync 时可能因编译源码而耗时很长甚至失败。推荐生产/稳定环境使用 3.12。"
}

# 1. 系统依赖
$failedSystemDeps = @()
if (-not $SkipSystemDeps) {
    $failedSystemDeps = Install-SystemDependencies
    if ($failedSystemDeps) {
        Write-Warn "以下依赖通过 winget 自动安装失败：$($failedSystemDeps -join ', ')"
    }
}
else {
    Update-EnvPath
}

# 2. 验证关键命令
$required = @("python", "node", "npm", "psql", "redis-cli")
$missing = $required | Where-Object { -not (Test-Command $_) }
if ($missing) {
    Write-Warn "以下命令仍不可用：$($missing -join ', ')"
    Write-Host "`n请按以下方式手动安装缺失项：" -ForegroundColor Yellow
    if ("psql" -in $missing) {
        Write-Host "  PostgreSQL: https://www.postgresql.org/download/windows/" -ForegroundColor Yellow
        Write-Host "              安装时记住密码，保持默认端口 5432" -ForegroundColor Yellow
    }
    if ("redis-cli" -in $missing) {
        Write-Host "  Redis (Windows 推荐 Memurai): https://www.memurai.com/" -ForegroundColor Yellow
        Write-Host "  或安装 WSL2 后运行: sudo apt install redis-server" -ForegroundColor Yellow
    }
    Write-Host "`n安装完成后，重启 PowerShell 并运行：" -ForegroundColor Cyan
    Write-Host "  .\scripts\setup-windows.ps1 -SkipSystemDeps -PostgresPassword '你的密码'" -ForegroundColor Cyan
    exit 1
}

Write-Ok "系统依赖检测通过"
python --version
node --version
npm --version
psql --version
redis-cli ping | Out-Null
Write-Ok "Redis 可连接"

# 3. 安装 uv
if (-not (Test-Command "uv")) {
    Write-Step "安装 uv"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    Update-EnvPath
    if (-not (Test-Command "uv")) {
        Write-Err "uv 安装后仍不可用，请重启 PowerShell 后重试"
    }
}
Write-Ok "uv 版本: $(uv --version)"

# 4. 克隆项目
if (-not $SkipClone) {
    Write-Step "克隆项目到 $ProjectDir"
    if (Test-Path $ProjectDir) {
        Write-Warn "目录已存在，跳过克隆"
    }
    else {
        git clone https://github.com/bzJing925/resonator-data-platform.git $ProjectDir
    }
}

Set-Location $ProjectDir

# 5. 配置 .env
if (-not (Test-Path ".env")) {
    Write-Step "生成 .env 配置文件"
    Copy-Item ".env.example" ".env"

    if (-not $PostgresPassword) {
        $PostgresPassword = Read-Host "请输入 PostgreSQL 管理员密码（安装 PostgreSQL 时设置的密码）"
    }

    # Windows 开发环境简化：直接使用 postgres 系统用户，避免再创建 aln 用户
    (Get-Content ".env") `
        -replace '^POSTGRES_USER=aln$', 'POSTGRES_USER=postgres' `
        -replace '^POSTGRES_PASSWORD=changeme_in_prod$', "POSTGRES_PASSWORD=$PostgresPassword" |
        Set-Content ".env"

    Write-Ok ".env 已生成"
}
else {
    Write-Warn ".env 已存在，跳过配置"
}

# 6. 初始化数据库
Write-Step "初始化数据库"
$env:PGPASSWORD = $PostgresPassword
try {
    psql -U postgres -c "CREATE DATABASE aln;" 2>$null
    Write-Ok "数据库 aln 已创建"
}
catch {
    Write-Warn "数据库 aln 可能已存在，继续执行"
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

Set-Location "backend"
Write-Step "同步 Python 依赖 (uv sync)"
uv sync

Write-Step "运行数据库迁移"
uv run alembic upgrade head

Set-Location ..

# 7. 前端依赖
Set-Location "frontend"
Write-Step "安装前端依赖 (npm install)"
npm install
Set-Location ..

# 8. 启动桌面版
Write-Step "启动桌面版"
& ".\scripts\dev-desktop.ps1"
