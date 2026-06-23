# 谐振器测试数据平台 - Windows 一键启动 (PowerShell 版)
# 用法（在仓库根目录打开 PowerShell）：
#   .\bootstrap.ps1 up        # 启动全部 5 容器（默认）
#   .\bootstrap.ps1 down      # 停止全部容器（保留数据）
#   .\bootstrap.ps1 reset     # 销毁容器 + 删除数据目录（二次确认）
#   .\bootstrap.ps1 status    # 查看容器状态 + /api/health + 磁盘
#   .\bootstrap.ps1 url       # 仅打印访问入口
#
# 前置要求：
#   1. Docker Desktop for Windows（已启动；WSL2 后端推荐）
#   2. Node.js 18+（首次部署需要 npm run build 前端）
#   3. 已 cp .env.example .env 并改 POSTGRES_PASSWORD

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'reset', 'status', 'url', 'help')]
    [string]$Action = 'up'
)

$ErrorActionPreference = 'Stop'

# ---------- 路径常量 ----------
$ScriptDir    = $PSScriptRoot
$ComposeFile  = Join-Path $ScriptDir 'deploy\docker-compose.yml'
$EnvFile      = Join-Path $ScriptDir '.env'
$EnvExample   = Join-Path $ScriptDir '.env.example'
$FrontendDist = Join-Path $ScriptDir 'frontend\dist'
$DataRootDefault = '/data3/aln'  # 仅作为 .env 缺失时的回落，Windows 上几乎不会用
$DataSubdirs  = @('pgdata', 'redis', 'uploads', 'files', 'mappings', 'exports', 'logs\api', 'logs\worker')
$NginxPortDefault = '8080'
$HealthTimeoutSecs = 90

# 全局：load_env 后填充
$Script:DataRoot   = $null
$Script:NginxPort  = $null

# ---------- 日志 ----------
function Log-Info  ($msg) { Write-Host "[INFO]  $msg" -ForegroundColor Yellow }
function Log-Ok    ($msg) { Write-Host "[OK]    $msg" -ForegroundColor Green }
function Log-Error ($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Die ($msg) { Log-Error $msg; exit 1 }

# ---------- 工具 ----------
function Strip-Quotes($v) {
    if ($null -eq $v) { return '' }
    $v = $v.Trim()
    if ($v.Length -ge 2) {
        if (($v.StartsWith('"') -and $v.EndsWith('"')) -or
            ($v.StartsWith("'") -and $v.EndsWith("'"))) {
            return $v.Substring(1, $v.Length - 2)
        }
    }
    return $v
}

function Read-EnvValue($key) {
    if (-not (Test-Path $EnvFile)) { return $null }
    $line = Select-String -Path $EnvFile -Pattern "^$key=" -SimpleMatch:$false |
            Select-Object -Last 1
    if (-not $line) { return $null }
    $eq = $line.Line.IndexOf('=')
    if ($eq -lt 0) { return $null }
    return (Strip-Quotes $line.Line.Substring($eq + 1))
}

function Load-Env {
    if (-not (Test-Path $EnvFile)) {
        Log-Error ".env 不存在"
        Log-Info  "请先执行：Copy-Item .env.example .env  并修改 POSTGRES_PASSWORD"
        exit 1
    }
    $dr = Read-EnvValue 'DATA_ROOT'
    if (-not $dr) { $dr = $DataRootDefault }
    # 相对路径展开为相对项目根的绝对路径，避免 docker compose 把它解析到 deploy/ 子目录下
    if (-not ([System.IO.Path]::IsPathRooted($dr))) {
        $dr = Join-Path $ScriptDir ($dr -replace '^\.\\?', '' -replace '^\./', '')
        Log-Info "DATA_ROOT 是相对路径，已展开为 $dr"
    }
    # 统一用正斜杠传给 docker compose，避免反斜杠在 yaml/shell 里的歧义
    $dr = $dr -replace '\\', '/'
    $Script:DataRoot = $dr
    $env:DATA_ROOT = $dr

    $np = Read-EnvValue 'NGINX_PORT'
    if (-not $np) { $np = $NginxPortDefault }
    $Script:NginxPort = $np

    Log-Ok ".env 已就位（DATA_ROOT=$($Script:DataRoot)）"
}

function Check-Docker {
    try { docker --version | Out-Null } catch {
        Die "未找到 docker。请先安装 Docker Desktop for Windows 并启动 daemon。"
    }
    try { docker compose version | Out-Null } catch {
        Die "docker compose 子命令不可用。请升级到 Docker Desktop 4.x+ (Compose v2)。"
    }
    try { docker info --format '{{.ServerVersion}}' 2>$null | Out-Null } catch {
        Die "Docker daemon 未运行。请打开 Docker Desktop 并等候 'Engine running'。"
    }
    Log-Ok "docker + docker compose 可用"
}

function Ensure-DataDirs {
    if (-not (Test-Path $Script:DataRoot)) {
        Log-Info "创建数据根 $($Script:DataRoot)"
        New-Item -ItemType Directory -Path $Script:DataRoot -Force | Out-Null
    }
    foreach ($d in $DataSubdirs) {
        $full = Join-Path $Script:DataRoot $d
        if (-not (Test-Path $full)) {
            New-Item -ItemType Directory -Path $full -Force | Out-Null
        }
    }
    Log-Ok "数据目录就绪：$($Script:DataRoot)"
}

function Compose {
    # docker compose 调用统一入口；stdout/stderr 直通终端，
    # 调用方读 $LASTEXITCODE 判断成功，不要把 Compose 当函数捕获返回值。
    & docker compose -f $ComposeFile --env-file $EnvFile @args
}

function Wait-PostgresHealthy {
    Log-Info "等待 postgres healthy..."
    $i = 0
    while ($i -lt $HealthTimeoutSecs) {
        $status = (docker inspect -f '{{.State.Health.Status}}' aln-postgres 2>$null)
        if ($status -eq 'healthy') {
            Log-Ok "postgres healthy"
            return
        }
        Start-Sleep -Seconds 2
        $i += 2
    }
    Die "postgres 在 $HealthTimeoutSecs 秒内未变 healthy；检查 .env 密码 / 数据目录权限"
}

function Run-Migrations {
    Log-Info "跑 alembic 迁移..."
    $running = docker ps --format '{{.Names}}' | Select-String -Quiet '^aln-api$'
    if ($running) {
        Compose exec -T api alembic upgrade head
    } else {
        Compose run --rm api alembic upgrade head
    }
    if ($LASTEXITCODE -ne 0) { Die "alembic 迁移失败（exit=$LASTEXITCODE）" }
    Log-Ok "alembic 迁移完成"
}

function Check-FrontendDist {
    if (-not (Test-Path $FrontendDist) -or
        -not (Get-ChildItem $FrontendDist -ErrorAction SilentlyContinue)) {
        Log-Error "frontend\dist 不存在或为空"
        Log-Info  "请先构建前端："
        Log-Info  "  cd frontend"
        Log-Info  "  npm install"
        Log-Info  "  npm run build"
        Log-Info  "再重新运行 .\bootstrap.ps1 up"
        exit 1
    }
    Log-Ok "frontend\dist 已就绪"
}

function Verify-Health {
    $url = "http://localhost:$($Script:NginxPort)/api/health"
    Log-Info "验证 $url ..."
    $i = 0
    while ($i -lt $HealthTimeoutSecs) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                Log-Ok "API 健康检查通过"
                return
            }
        } catch { }
        Start-Sleep -Seconds 2
        $i += 2
    }
    Die "API 在 $HealthTimeoutSecs 秒内未通过健康检查；用 .\bootstrap.ps1 status 排查"
}

function Detect-LanIp {
    try {
        $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
               Where-Object {
                   $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and
                   $_.InterfaceAlias -notmatch '(Loopback|vEthernet|WSL|Docker)' -and
                   $_.PrefixOrigin -ne 'WellKnown'
               } |
               Select-Object -ExpandProperty IPAddress
        if ($ips) { return $ips[0] }
    } catch { }
    return $null
}

function Print-AccessInfo {
    $lanIp = Detect-LanIp
    Write-Host ""
    Log-Ok "全部启动完毕"
    Write-Host "      本机访问： http://localhost:$($Script:NginxPort)"
    if ($lanIp) {
        Write-Host "      局域网入口：http://${lanIp}:$($Script:NginxPort)    <- 同事打开这个"
    } else {
        Write-Host "      局域网入口：未探测到对外网卡 IP；用 'ipconfig' 自己查"
    }
    Write-Host "      API 直连： http://localhost:8001"
    Write-Host "      健康检查： http://localhost:$($Script:NginxPort)/api/health"
    Write-Host ""
}

# ---------- 命令 ----------
function Cmd-Up {
    Load-Env
    Check-Docker
    Ensure-DataDirs

    Log-Info "启动 postgres + redis"
    Compose up -d postgres redis
    if ($LASTEXITCODE -ne 0) { Die "postgres/redis 启动失败" }
    Wait-PostgresHealthy
    Run-Migrations
    Check-FrontendDist

    Log-Info "启动 api + worker + nginx"
    Compose up -d api worker nginx
    if ($LASTEXITCODE -ne 0) { Die "api/worker/nginx 启动失败" }

    Verify-Health
    Print-AccessInfo
}

function Cmd-Down {
    Load-Env
    Check-Docker
    Log-Info "停止全部容器"
    Compose down | Out-Null
    Log-Ok "已停止（数据保留在 $($Script:DataRoot)）"
}

function Cmd-Reset {
    Load-Env
    Check-Docker
    if (-not $Script:DataRoot -or $Script:DataRoot.Length -lt 6 -or
        $Script:DataRoot -in @('/', 'C:/', 'C:\', $env:USERPROFILE)) {
        Die "DATA_ROOT 看起来太宽 ($($Script:DataRoot))，拒绝 reset 防误删"
    }
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "  WARNING: 即将销毁容器 + 全部业务数据"  -ForegroundColor Red
    Write-Host "  目录：$($Script:DataRoot)\{pgdata,redis,uploads,files,exports}" -ForegroundColor Red
    Write-Host "  此操作不可逆。" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    $confirm = Read-Host "输入 RESET 继续，其它任何输入取消"
    if ($confirm -ne 'RESET') { Log-Info "已取消"; return }

    Log-Info "停止 + 删除容器"
    Compose down -v 2>&1 | Out-Null

    Log-Info "删除业务数据"
    foreach ($d in @('pgdata', 'redis', 'uploads', 'files', 'exports')) {
        $full = Join-Path $Script:DataRoot $d
        if (Test-Path $full) {
            Remove-Item -Recurse -Force $full
            Log-Ok "已删 $full"
        }
    }
    Log-Ok "重置完成。下次 .\bootstrap.ps1 up 将从空数据库开始"
}

function Cmd-Status {
    Load-Env
    Check-Docker

    Log-Info "容器状态："
    Compose ps

    Write-Host ""
    $url = "http://localhost:$($Script:NginxPort)/api/health"
    Log-Info "API 健康检查 ($url)："
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        Write-Host $resp.Content
        Log-Ok "健康"
    } catch {
        Log-Error "不健康或未启动: $($_.Exception.Message)"
    }

    Write-Host ""
    Log-Info "磁盘 ($($Script:DataRoot))："
    if (Test-Path $Script:DataRoot) {
        $drive = (Get-Item $Script:DataRoot).PSDrive
        if ($drive) {
            $used = $drive.Used
            $free = $drive.Free
            $total = $used + $free
            if ($total -gt 0) {
                $pct = [math]::Round($used / $total * 100, 1)
                Write-Host ("      盘 {0}:\  已用 {1:N1} GiB / 总 {2:N1} GiB ({3}%)" -f `
                    $drive.Name, ($used / 1GB), ($total / 1GB), $pct)
            }
        }
    } else {
        Log-Error "$($Script:DataRoot) 不存在"
    }

    Write-Host ""
    $lanIp = Detect-LanIp
    Log-Info "访问入口："
    Write-Host "      本机访问： http://localhost:$($Script:NginxPort)"
    if ($lanIp) {
        Write-Host "      局域网入口：http://${lanIp}:$($Script:NginxPort)"
    }
}

function Cmd-Url {
    Load-Env
    $lanIp = Detect-LanIp
    Write-Host "本机访问：  http://localhost:$($Script:NginxPort)"
    if ($lanIp) {
        Write-Host "局域网入口：http://${lanIp}:$($Script:NginxPort)    <- 告诉同事这个"
    } else {
        Log-Error "未探测到对外网卡 IP，用 ipconfig 自己查"
    }
}

function Cmd-Help {
    Write-Host @"
谐振器测试数据平台 - Windows 启动脚本

用法：
  .\bootstrap.ps1 up        启动全部 5 容器（默认）
  .\bootstrap.ps1 down      停止全部容器（保留数据）
  .\bootstrap.ps1 reset     销毁容器 + 删除数据目录（二次确认）
  .\bootstrap.ps1 status    查看容器状态 + /api/health + 磁盘
  .\bootstrap.ps1 url       仅打印访问入口

前置要求：
  - Docker Desktop for Windows（已启动，引擎处于 Running）
  - Node.js 18+（首次需要 npm run build 前端）
  - 已复制 .env.example -> .env 并改密码

若 PowerShell 提示禁止运行脚本，先执行（管理员）：
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
"@
}

# ---------- 入口 ----------
switch ($Action) {
    'up'     { Cmd-Up }
    'down'   { Cmd-Down }
    'reset'  { Cmd-Reset }
    'status' { Cmd-Status }
    'url'    { Cmd-Url }
    'help'   { Cmd-Help }
    default  { Cmd-Help }
}
