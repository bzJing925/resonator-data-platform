#Requires -RunAsAdministrator
param([string]$AllowedSubnet = 'LocalSubnet')
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Config = Get-Content "$Root\server-config.json" -Raw | ConvertFrom-Json
$Python = Join-Path $Root 'runtime\python.exe'
if (-not (Test-Path $Python)) { throw 'Run install.ps1 first' }
# LocalService has no interactive session dependency and limited machine privileges.
& icacls $Root /grant '*S-1-5-19:(OI)(CI)RX' /T /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot grant runtime read access' }
& icacls $Config.data_dir /grant '*S-1-5-19:(OI)(CI)M' /T /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot grant data write access' }
$Action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + $Root + '\run_server.py"') -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount
$Settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if (Get-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue) {
    # 任务存在但未运行时 Stop-ScheduledTask 可能报错（PS 5.1）；重注册不应因此中止
    Stop-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue
}
Register-ScheduledTask -TaskName 'ALN-Data-Server' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Get-NetFirewallRule -DisplayName 'ALN Data Platform LAN' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'ALN Data Platform LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Config.port -RemoteAddress $AllowedSubnet -Profile Any -Program $Python | Out-Null
Start-ScheduledTask -TaskName 'ALN-Data-Server'
$Ready = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 2
    $Task = Get-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue
    if (-not $Task -or $Task.State -ne 'Running') { continue }
    $Listener = Get-NetTCPConnection -LocalPort $Config.port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $Listener) { continue }
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$($Config.port)/api/health" -TimeoutSec 2
        if ($Health.status -ne 'ok') { continue }
    } catch { continue }
    $ServerProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($Listener.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $ServerProcess) { continue }
    $Owner = Invoke-CimMethod -InputObject $ServerProcess -MethodName GetOwnerSid -ErrorAction SilentlyContinue
    if ($Owner.Sid -eq 'S-1-5-19') { $Ready = $true; break }
}
if (-not $Ready) {
    throw 'Task registration succeeded, but no healthy LocalService-owned listener appeared. Check Task Scheduler history and server.log before logging out.'
}
Write-Host 'Registered and verified a healthy LocalService server. No Windows login is required.'
