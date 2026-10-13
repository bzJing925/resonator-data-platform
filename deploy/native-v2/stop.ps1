# 一键关停数据平台（原生模式）
# 双击 stop-server.cmd 即调用本脚本；也可在 PowerShell 里直接运行。
# 停止开机任务（LocalService）运行的实例需要管理员权限——必要时自动请求提权一次。
param([switch]$Elevated)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

$Port = 8000
$CfgFile = Join-Path $Root 'server-config.json'
if (Test-Path $CfgFile) {
    $Cfg = Get-Content $CfgFile -Raw | ConvertFrom-Json
    if ($Cfg.port) { $Port = [int]$Cfg.port }
}

# 先停并禁用开机任务，防止任务自动重启把服务又拉起来（start.ps1 会重新启用）
if (Get-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue
    Disable-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue | Out-Null
}

$Targets = @{}
# 主路径：谁在给平台端口 Listen 就停谁。TCP 连接表对所有用户可见——
# LocalService 运行的实例读不到命令行，命令行匹配会漏，端口反查不会。
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { $Targets[[int]$_.OwningProcess] = $true }
# 兜底：命令行匹配（同用户进程；兼容相对/全路径启动及改端口场景）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains('run_server.py') -and
        $_.CommandLine.Contains([IO.Path]::GetFileName($Root))
    } |
    ForEach-Object { $Targets[[int]$_.ProcessId] = $true }

$Denied = $false
$Stopped = 0
foreach ($procId in $Targets.Keys) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    # 只停 python 进程，避免误伤碰巧占用同端口的其他程序
    if (-not $proc -or $proc.ProcessName -ne 'python') { continue }
    try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        $Stopped++
    } catch {
        $Denied = $true   # 进程属于 LocalService/其他用户，需要提权
    }
}

if ($Denied) {
    if (-not $Elevated) {
        Write-Host '进程属于系统服务账户，正在请求管理员权限完成关停...'
        Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated"
        exit
    }
    Write-Host '提权后仍未能停止全部进程，请检查任务管理器。' -ForegroundColor Red
    exit 1
}

if ($Stopped -gt 0) {
    Write-Host "已停止 $Stopped 个平台进程。" -ForegroundColor Green
} elseif (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "端口 $Port 仍被非 python 程序占用——正在运行的可能不是本平台，请检查任务管理器。" -ForegroundColor Yellow
} else {
    Write-Host '没有找到运行中的平台进程。'
}
