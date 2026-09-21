# 数据平台（原生模式）一键安装 —— 免 Docker、免联网、免管理员
# 用法：解压后在包根目录右键用 PowerShell 运行，或 powershell -File install.ps1
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

Write-Host "== 1/3 检查 Python 3.12 ==" -ForegroundColor Cyan
$pyOk = $false
try {
    $v = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($v -eq '3.12') { $pyOk = $true }
} catch {}
if (-not $pyOk) {
    $installer = Join-Path $Root 'python\python-3.12-amd64.exe'
    if (-not (Test-Path $installer)) {
        Write-Host "缺少 Python 安装包：$installer" -ForegroundColor Red
        Write-Host "请把 python-3.12.x-amd64.exe 放到 python\ 目录后重试（见 README.txt 第 0 步）"
        exit 1
    }
    Write-Host "安装 Python 3.12（免管理员，装到当前用户目录）..."
    & $installer /passive InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0 | Out-Null
    # 安装器改的是注册表 PATH，当前会话手动刷新
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','User') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','Machine')
}
python --version
if ($LASTEXITCODE -ne 0) { Write-Host "Python 不可用，请检查" -ForegroundColor Red; exit 1 }

Write-Host "== 2/3 创建虚拟环境并离线安装依赖（约 3-5 分钟）==" -ForegroundColor Cyan
if (-not (Test-Path "$Root\venv\Scripts\python.exe")) {
    python -m venv "$Root\venv"
}
& "$Root\venv\Scripts\python.exe" -m pip install --no-index --find-links "$Root\wheelhouse" `
    -r "$Root\requirements-server.txt" --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "依赖安装失败" -ForegroundColor Red; exit 1 }

Write-Host "== 3/3 初始化数据目录 ==" -ForegroundColor Cyan
New-Item -ItemType Directory -Force "$Root\data" | Out-Null

Write-Host ""
Write-Host "安装完成！双击 start.ps1 即可运行平台。" -ForegroundColor Green
Write-Host "浏览器访问 http://localhost:8000 ，同事访问 http://<本机IP>:8000"
