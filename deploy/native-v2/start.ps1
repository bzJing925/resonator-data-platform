# 一键启动数据平台（原生模式）
# 双击 start-server.cmd 即调用本脚本；也可在 PowerShell 里直接运行。
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

$Port = 8000
$CfgFile = Join-Path $Root 'server-config.json'
if (Test-Path $CfgFile) {
    $Cfg = Get-Content $CfgFile -Raw | ConvertFrom-Json
    if ($Cfg.port) { $Port = [int]$Cfg.port }
}

# 已在运行就直接报告
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "平台已在运行：http://localhost:$Port" -ForegroundColor Green
    exit 0
}

if (Get-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue) {
    # 已注册开机任务：走任务通道（LocalService 运行，注销后也在）
    Enable-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue | Out-Null
    Start-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue
    Write-Host '已通过开机任务启动（需要管理员权限；失败时会自动尝试后台启动）。'
    Start-Sleep -Seconds 2
    if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
        Write-Host '任务通道未生效（可能权限不足），改用后台进程启动...'
        Start-Process -FilePath "$Root\runtime\python.exe" -ArgumentList 'run_server.py' -WorkingDirectory $Root -WindowStyle Hidden
        Write-Host '警告：当前是登录用户的后台进程，注销 Windows 后会停止；请用管理员重新执行 register-autostart.ps1。' -ForegroundColor Yellow
    }
} else {
    # 未注册开机任务：后台进程启动（本窗口可关闭；注销 Windows 会停止）
    Start-Process -FilePath "$Root\runtime\python.exe" -ArgumentList 'run_server.py' -WorkingDirectory $Root -WindowStyle Hidden
    Write-Host '已在后台启动（未注册开机任务；要无人值守常驻请先用管理员跑一次 register-autostart.ps1）。'
}

# 等待健康检查通过
$ok = $false
for ($i = 0; $i -lt 45; $i++) {
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if ($h.status -eq 'ok') { $ok = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}
if ($ok) {
    Write-Host ''
    Write-Host "平台已就绪：" -ForegroundColor Green
    Write-Host "  本机访问   http://localhost:$Port"
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -notlike '*Loopback*' -and $_.PrefixOrigin -ne 'Wellknown' } |
        Select-Object -ExpandProperty IPAddress -Unique
    if (-not $ips) {
        # 兜底：NetTCPIP 枚举失败/受限时改用 WMI（PS 5.1 均有）
        $ips = Get-WmiObject Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' -ErrorAction SilentlyContinue |
            ForEach-Object { $_.IPAddress } |
            Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -ne '127.0.0.1' -and -not $_.StartsWith('169.254.') } |
            Select-Object -Unique
    }
    if ($ips) {
        foreach ($ip in $ips) { Write-Host "  同事访问   http://${ip}:$Port" }
    } else {
        Write-Host '  同事访问   （未检测到内网 IP：请确认服务器已连上办公网，然后运行 ipconfig 查 IPv4 地址，端口同上）' -ForegroundColor Yellow
    }
} else {
    Write-Host "启动后健康检查未通过。请运行 healthcheck.ps1 查看细节，或查看 data\logs\server.log。" -ForegroundColor Red
    exit 1
}
