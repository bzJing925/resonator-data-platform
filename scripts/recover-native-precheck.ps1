#Requires -Version 5.1
#Requires -RunAsAdministrator
param(
    [string]$InstallRoot = 'E:\JBZ\aln-platform-win64-20261008',
    [string]$GroupId = '3fa60c64-38a4-45a0-8123-1852c06dc184'
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $InstallRoot).Path
$Python = Join-Path $Root 'runtime\python.exe'
$Tool = Join-Path $PSScriptRoot 'recover_native_precheck.py'
$Config = Get-Content -LiteralPath (Join-Path $Root 'server-config.json') -Raw | ConvertFrom-Json
$Port = 8000
if ($Config.port) { $Port = [int]$Config.port }
$StopAttempted = $false

function Assert-InstalledFile([string]$Relative) {
    $Expected = $InstalledHashes.PSObject.Properties[$Relative]
    if (-not $Expected) { throw "Installed checksum is missing: $Relative" }
    $Actual = (Get-FileHash -LiteralPath (Join-Path $Root $Relative) -Algorithm SHA256).Hash
    if ($Actual -ne $Expected.Value) { throw "Installed file has changed: $Relative" }
}

try {
    # Verify the small recovery bundle and the existing stop/start/runtime files before use.
    $BundleHashes = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'SHA256SUMS.json') -Raw |
        ConvertFrom-Json
    foreach ($Entry in $BundleHashes.PSObject.Properties) {
        if ($Entry.Name -notin @('recover_native_precheck.py', 'recover-native-precheck.ps1',
                                'README.txt')) { throw 'Unexpected recovery bundle file' }
        $Actual = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $Entry.Name) -Algorithm SHA256).Hash
        if ($Actual -ne $Entry.Value) { throw "Recovery bundle checksum failed: $($Entry.Name)" }
    }
    if (-not $BundleHashes.PSObject.Properties['recover_native_precheck.py']) {
        throw 'Recovery tool checksum is missing'
    }
    $InstalledHashes = Get-Content -LiteralPath (Join-Path $Root 'SHA256SUMS.json') -Raw |
        ConvertFrom-Json
    foreach ($Relative in @('runtime/python.exe', 'stop.ps1', 'start.ps1', 'run_server.py')) {
        Assert-InstalledFile $Relative
    }

    # Inspection does not import the backend or start any worker.
    $Raw = & $Python $Tool --root $Root --group-id $GroupId
    if ($LASTEXITCODE -ne 0) { throw 'Read-only inspection failed; no recovery was attempted' }
    $Inspection = ($Raw -join "`n") | ConvertFrom-Json
    $Raw | Write-Host
    if ($Inspection.eligible_count -eq 0) {
        Write-Host 'No eligible interrupted draft. Server and database were left unchanged.'
        exit 0
    }
    foreach ($Blocker in $Inspection.maintenance_blockers.PSObject.Properties) {
        if ($Blocker.Value -gt 0) {
            throw "Other unfinished work exists: $($Blocker.Name)=$($Blocker.Value). Nothing was stopped"
        }
    }

    # stop.ps1 uses port ownership: refuse an unrelated listener before invoking it.
    $Listeners = @(Get-NetTCPConnection -ErrorAction Stop |
        Where-Object { $_.LocalPort -eq $Port -and $_.State -eq 'Listen' })
    foreach ($Listener in $Listeners) {
        $Owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($Listener.OwningProcess)" `
            -ErrorAction Stop
        if (-not $Owner.ExecutablePath -or
            $Owner.ExecutablePath -ine $Python) {
            throw 'The server port belongs to another or unknown process; nothing was stopped'
        }
    }
    $Fallbacks = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
        Where-Object {
            $_.CommandLine -and $_.CommandLine.Contains('run_server.py') -and
            $_.CommandLine.Contains([IO.Path]::GetFileName($Root))
        })
    foreach ($Fallback in $Fallbacks) {
        if (-not $Fallback.ExecutablePath -or $Fallback.ExecutablePath -ine $Python) {
            throw 'Stop script would target another or unknown installation; nothing was stopped'
        }
    }
    $Tasks = @(Get-ScheduledTask -ErrorAction Stop |
        Where-Object { $_.TaskName -eq 'ALN-Data-Server' })
    if ($Tasks.Count -gt 1) { throw 'Ambiguous ALN-Data-Server scheduled task' }
    if ($Tasks.Count -eq 1) {
        $Actions = @($Tasks[0].Actions)
        if ($Actions.Count -ne 1 -or $Actions[0].Execute -ine $Python -or
            $Actions[0].WorkingDirectory -ine $Root) {
            throw 'Scheduled task belongs to another or unknown installation'
        }
    }

    $FreshRaw = & $Python $Tool --root $Root --group-id $GroupId
    if ($LASTEXITCODE -ne 0) { throw 'Second read-only inspection failed; nothing was stopped' }
    $Fresh = ($FreshRaw -join "`n") | ConvertFrom-Json
    foreach ($Blocker in $Fresh.maintenance_blockers.PSObject.Properties) {
        if ($Blocker.Value -gt 0) { throw 'Other work appeared; nothing was stopped' }
    }
    if ($Fresh.eligible_count -eq 0) { Write-Host 'Draft finished meanwhile; no recovery needed'; exit 0 }

    $StopAttempted = $true
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'stop.ps1') -Elevated
    if ($LASTEXITCODE -ne 0) { throw 'Server stop failed; database was not repaired' }
    Write-Host 'Verifying original ZIP and backing up database; large ZIPs may take several minutes.'
    & $Python $Tool --root $Root --group-id $GroupId --apply
    if ($LASTEXITCODE -ne 0) { throw 'Recovery failed. Read the error above; do not delete the ZIP' }

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'start.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Recovery finished, but server startup did not pass health check' }
    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 5
    if ($Health.status -ne 'ok') { throw 'Server health check failed after restart' }
    Write-Host "Open http://localhost:$Port/upload?groupId=$GroupId"
    Write-Host 'Press Ctrl+F5, click recheck, then review the result before starting ingestion.'
    Write-Host 'The original ZIP is retained. A new upload is not required.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($StopAttempted) {
        Write-Host 'Automatic restart was not attempted after this failure.' -ForegroundColor Yellow
        Write-Host "Manual start command: powershell -NoProfile -ExecutionPolicy Bypass -File `"$Root\start.ps1`""
    }
    exit 1
}
