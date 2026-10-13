#Requires -RunAsAdministrator
param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$Cfg = Get-Content "$PSScriptRoot\server-config.json" -Raw | ConvertFrom-Json
$Source = [IO.Path]::GetFullPath($Cfg.data_dir).TrimEnd('\')
$Dest = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
if ($Dest -eq $Source -or $Dest.StartsWith($Source + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Backup must be outside the data directory' }
$Task = Get-ScheduledTask -TaskName 'ALN-Data-Server' -ErrorAction SilentlyContinue
$WasRunning = $Task -and $Task.State -eq 'Running'
& "$PSScriptRoot\stop.ps1"
$Output = Join-Path $Dest ('aln-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
try {
    New-Item -ItemType Directory -Force $Output | Out-Null
    & robocopy $Source "$Output\data" /E /COPY:DAT /R:2 /W:2
    if ($LASTEXITCODE -ge 8) { throw 'Backup copy failed' }
    Copy-Item "$PSScriptRoot\server-config.json" $Output
    Write-Host "Backup created: $Output"
} finally {
    if ($WasRunning) { & "$PSScriptRoot\start.ps1" }
}
