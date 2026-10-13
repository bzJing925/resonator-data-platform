$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $ElevatedArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    $Elevated = Start-Process powershell.exe -ArgumentList $ElevatedArguments -Verb RunAs -Wait -PassThru
    if ($Elevated.ExitCode -ne 0) { throw 'Administrator installation failed or was cancelled.' }
    exit
}
& (Join-Path $Root 'install.ps1') -Role Server
& (Join-Path $Root 'register-autostart.ps1')
