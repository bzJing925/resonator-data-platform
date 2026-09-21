# 停止数据平台（原生模式）
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*uvicorn*app.main*" }
if ($procs) {
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "已停止 $($procs.Count) 个平台进程"
} else {
    Write-Host "没有找到运行中的平台进程"
}
