' ALN 内网服务器静默启动器（wscript 版）
' 不弹任何 PowerShell/控制台窗口；启动器立即退出，后端独立运行（关窗口不影响服务）
Option Explicit

Dim fso, sh, root, logDir, exe, args, cmd, q
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root & "\backend"

logDir = root & "\data\logs"
If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)

sh.Environment("Process")("ALN_DESKTOP_MODE") = "true"
sh.Environment("Process")("ALN_DESKTOP_DIR") = root & "\data"
sh.Environment("Process")("DEBUG") = "false"
sh.Environment("Process")("ALN_SERVER_MODE") = "true"

exe = root & "\venv\Scripts\pythonw.exe"
args = "-m uvicorn app.main:app --host 0.0.0.0 --port 8000"
q = Chr(34)
cmd = "cmd /c " & q & exe & q & " " & args & " >> " & q & logDir & "\server.log" & q & " 2>&1"
sh.Run cmd, 0, False
