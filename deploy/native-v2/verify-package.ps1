$ErrorActionPreference = 'Stop'
$Manifest = Get-Content "$PSScriptRoot\SHA256SUMS.json" -Raw | ConvertFrom-Json
foreach ($Entry in $Manifest.PSObject.Properties) {
    $File = Join-Path $PSScriptRoot $Entry.Name
    if (-not (Test-Path -LiteralPath $File)) { throw "Missing: $($Entry.Name)" }
    if ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Entry.Value) {
        throw "Checksum mismatch: $($Entry.Name)"
    }
}
Write-Host 'Package checksums OK. Run before installation, not after upgrades.'
