@echo off
chcp 65001 >nul
echo ===========================================
echo  ALN 数据平台 - 管理员清理脚本
echo  用途：启动 PostgreSQL 17，卸载 PostgreSQL 18，禁用 WSL
echo ===========================================
echo.

:: 1. 启动 PostgreSQL 17 服务
echo [1/3] 启动 PostgreSQL 17 服务...
net start postgresql-x64-17
if %errorlevel% neq 0 (
    echo 提示：PostgreSQL 17 服务可能已经在运行或启动失败。
) else (
    echo PostgreSQL 17 服务已启动。
)
echo.

:: 2. 卸载 PostgreSQL 18
echo [2/3] 卸载 PostgreSQL 18...
if exist "C:\Program Files\PostgreSQL\18\uninstall-postgresql.exe" (
    "C:\Program Files\PostgreSQL\18\uninstall-postgresql.exe" --mode unattended
    echo PostgreSQL 18 卸载程序已执行。
) else (
    echo 未找到 PostgreSQL 18 卸载程序，可能已卸载。
)
echo.

:: 3. 禁用 WSL
echo [3/3] 禁用 WSL...
dism.exe /online /disable-feature /featurename:Microsoft-Windows-Subsystem-Linux /norestart
dism.exe /online /disable-feature /featurename:VirtualMachinePlatform /norestart
echo WSL 已禁用（重启后生效）。
echo.

echo ===========================================
echo  清理完成。请重启电脑以完全生效。
echo ===========================================
pause
