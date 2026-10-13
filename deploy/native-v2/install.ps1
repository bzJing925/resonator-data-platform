param([ValidateSet('Server','Desktop')][string]$Role = 'Server', [string]$DataDir = '')
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
if ($Root -match '[^\x00-\x7F]') { throw 'Extract to an ASCII path, e.g. D:\ALN-Platform' }
$Redist = Join-Path $Root 'prerequisites\vc_redist.x64.exe'
$RuntimeDll = Join-Path $env:WINDIR 'System32\msvcp140.dll'
if (-not (Test-Path $Redist)) { throw 'Microsoft C++ runtime installer missing' }
$BundledVersion = [version]((Get-Item $Redist).VersionInfo.ProductVersion)
$InstalledVersion = if (Test-Path $RuntimeDll) {
    [version]((Get-Item $RuntimeDll).VersionInfo.FileVersion)
} else { [version]'0.0' }
if ($InstalledVersion -lt $BundledVersion) {
    Write-Host "Updating Microsoft C++ runtime: $InstalledVersion -> $BundledVersion"
    $Proc = Start-Process -FilePath $Redist -ArgumentList '/install /quiet /norestart' -Verb RunAs -Wait -PassThru
    if ($Proc.ExitCode -notin @(0,3010,1638)) { throw "C++ runtime installation failed: $($Proc.ExitCode)" }
    if ($Proc.ExitCode -eq 3010) { throw 'C++ runtime installed. Restart Windows, then run install-server.cmd again.' }
    $InstalledVersion = if (Test-Path $RuntimeDll) {
        [version]((Get-Item $RuntimeDll).VersionInfo.FileVersion)
    } else { [version]'0.0' }
    if ($InstalledVersion -lt $BundledVersion) {
        throw "C++ runtime is still $InstalledVersion; restart Windows, then run install-server.cmd again."
    }
}
$Python = Join-Path $Root 'runtime\python.exe' 
if (-not (Test-Path $Python)) { throw 'Bundled Python runtime missing. Re-extract the package.' }
& $Python "$Root\bootstrap_runtime.py"
if ($LASTEXITCODE -ne 0) { throw 'Runtime bootstrap failed' }
$Venv = $Python
& $Venv -m pip install --no-index --find-links "$Root\wheelhouse" -r "$Root\requirements-server.txt" --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw 'Offline dependency installation failed' }
& $Venv -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed' }
$env:DOTENV_PATH = Join-Path $Root 'no-dotenv'
$env:ALN_DESKTOP_MODE = 'true'
$env:DEBUG = 'false'
Push-Location "$Root\backend"
try {
    & $Venv -c "import app.main, pyarrow, skrf, openpyxl, xlrd; print('Runtime imports OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Runtime import check failed' }
    # torch 只对可选的 ML 预测功能有用；老 CPU（无 AVX）/运行库不全时降级而非中止安装
    & $Venv -c "import torch; print('torch OK')"
    if ($LASTEXITCODE -ne 0) {
        '1' | Set-Content (Join-Path $Root 'torch-disabled.flag') -Encoding Ascii
        Write-Host '警告：torch 不可用（CPU 可能不支持 AVX 指令集）——ML 预测功能禁用，数据管理/曲线/同步不受影响。' -ForegroundColor Yellow
    } else {
        Remove-Item (Join-Path $Root 'torch-disabled.flag') -ErrorAction SilentlyContinue
    }
} finally { Pop-Location }
if ($Role -eq 'Server') {
    if (-not (Test-Path "$Root\server-config.json")) {
        if (-not $DataDir) { $DataDir = Join-Path $Root 'data' }
        $DataDir = [System.IO.Path]::GetFullPath($DataDir)
        New-Item -ItemType Directory -Force $DataDir | Out-Null
        @{data_dir=$DataDir; host='0.0.0.0'; port=8000} | ConvertTo-Json | Set-Content "$Root\server-config.json" -Encoding UTF8
    }
    Write-Host 'Installed. Run register-autostart.ps1 as Administrator to start the server at boot.'
} else {
    if (-not (Test-Path "$Root\desktop\ALN-Desktop.exe")) { throw 'Desktop runtime missing' }
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'ALN Data Platform.lnk'))
    $Shortcut.TargetPath = Join-Path $Root 'desktop\ALN-Desktop.exe'
    $Shortcut.WorkingDirectory = Join-Path $Root 'desktop'
    $Shortcut.Save()
    Write-Host 'Installed. Start ALN Data Platform from the desktop shortcut.'
}
