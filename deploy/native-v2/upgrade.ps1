#Requires -RunAsAdministrator
param([Parameter(Mandatory=$true)][string]$NewPackagePath, [Parameter(Mandatory=$true)][string]$BackupDir)
$ErrorActionPreference = 'Stop'
$NewRoot = (Resolve-Path $NewPackagePath).Path
if (-not (Test-Path "$NewRoot\native-bundle.json")) { throw 'Not a native v2 package' }
if ($NewRoot -eq $PSScriptRoot) { throw 'Extract the new package to a separate directory first' }
& "$PSScriptRoot\backup.ps1" -Destination $BackupDir
& "$PSScriptRoot\stop.ps1"
$CodeBackup = Join-Path $BackupDir ('code-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force $CodeBackup | Out-Null
foreach ($Name in @('backend','frontend','runtime','wheelhouse','desktop')) {
    if (Test-Path "$PSScriptRoot\$Name") { Copy-Item "$PSScriptRoot\$Name" $CodeBackup -Recurse }
    & robocopy "$NewRoot\$Name" "$PSScriptRoot\$Name" /E /R:2 /W:2
    if ($LASTEXITCODE -ge 8) { throw "Upgrade failed at $Name; restore $CodeBackup" }
}
Get-ChildItem $NewRoot -File | Where-Object { $_.Name -ne 'server-config.json' } | Copy-Item -Destination $PSScriptRoot -Force
& "$PSScriptRoot\install.ps1" -Role Server
& "$PSScriptRoot\start.ps1"
Write-Host 'Upgraded. Run healthcheck.ps1 and the acceptance checklist.'
