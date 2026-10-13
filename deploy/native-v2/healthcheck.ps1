param([string]$ServerUrl = '')
$ErrorActionPreference = 'Stop'
$ConfigPath = Join-Path $PSScriptRoot 'server-config.json'
$Config = if (Test-Path $ConfigPath) { Get-Content $ConfigPath -Raw | ConvertFrom-Json } else { $null }
if (-not $ServerUrl) {
    $Port = if ($Config -and $Config.port) { [int]$Config.port } else { 8000 }
    $ServerUrl = "http://127.0.0.1:$Port"
}
try {
    $Health = Invoke-RestMethod "$ServerUrl/api/health" -TimeoutSec 15
} catch {
    $Task = Get-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue
    $State = if ($Task) { $Task.State } else { 'Missing' }
    $Info = if ($Task) {
        Get-ScheduledTaskInfo -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue
    } else { $null }
    $Result = if ($Info) { $Info.LastTaskResult } else { 'n/a' }
    $DataDir = if ($Config -and $Config.data_dir) { $Config.data_dir } else { Join-Path $PSScriptRoot 'data' }
    $LogPath = Join-Path $DataDir 'logs\server.log'
    throw "Cannot connect to $ServerUrl. TaskState=$State; LastTaskResult=$Result. Check $LogPath and Windows Application events."
}
if ($Health.status -ne 'ok') { throw ($Health | ConvertTo-Json) }
$Catalog = Invoke-RestMethod "$ServerUrl/api/sync/v2/catalog" -TimeoutSec 15
if ($Catalog.protocol -ne 2) { throw 'Server protocol mismatch' }
Write-Host "Healthy. Server ID: $($Catalog.server_id); batches: $($Catalog.batches.Count)"
