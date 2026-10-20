# 可选：注册登录后自动启动（任务计划程序，当前用户级，免管理员）
$Root = $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Root\start.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "ALN-Data-Server" -Action $action -Trigger $trigger `
    -Description "谐振器数据平台（原生模式）登录后自启" -Force
Write-Host "已注册开机自启任务 ALN-Data-Server（登录本账户后自动启动）。卸载：Unregister-ScheduledTask -TaskName ALN-Data-Server"
