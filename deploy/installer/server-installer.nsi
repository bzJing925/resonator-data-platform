; ALN 谐振器数据平台 · 内网服务器（免 Docker 原生模式）一键安装包
; 构建：make_server_installer.sh 调用 makensis 编译本脚本
; 产物：dist-server-pack/ALN-Server-Setup-<版本>.exe
!define APP_NAME      "ALN 谐振器数据平台·内网服务器"
!define APP_VERSION   "1.0.3"
!define APP_DIR       "aln-server"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ALN-Server"

!include "MUI2.nsh"

Name "${APP_NAME}"
OutFile "ALN-Server-Setup-${APP_VERSION}.exe"
Unicode true
InstallDir "C:\${APP_DIR}"
RequestExecutionLevel admin
ShowInstDetails show
SetCompressor /SOLID lzma
BrandingText "${APP_NAME} v${APP_VERSION}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "立即启动服务器（弹出服务窗口）"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchServer
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "安装平台（后端 + 前端 + 离线依赖）"
  SetShellVarContext all
  SetOutPath "$INSTDIR"
  File /r "pack\*"

  SetOutPath "$INSTDIR\python"
  File "assets\python-3.12.10-amd64.exe"
  Rename "$INSTDIR\python\python-3.12.10-amd64.exe" "$INSTDIR\python\python-3.12-amd64.exe"

  DetailPrint "运行安装脚本：检查/安装 Python、创建虚拟环境、离线安装依赖（约 3-5 分钟）..."
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\install.ps1"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONEXCLAMATION|MB_OK "安装脚本返回码 $0（非 0）。$\n可在 $INSTDIR 里用 PowerShell 手动跑 install.ps1 查看原因后重试。"
  ${EndIf}

  CreateDirectory "$SMPROGRAMS\ALN 内网服务器"
  CreateShortcut "$SMPROGRAMS\ALN 内网服务器\启动服务器.lnk" \
    "wscript.exe" "$INSTDIR\start.vbs"
  CreateShortcut "$SMPROGRAMS\ALN 内网服务器\停止服务器.lnk" \
    "powershell.exe" "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File \"$INSTDIR\stop.ps1\""
  CreateShortcut "$SMPROGRAMS\ALN 内网服务器\注册开机自启.lnk" \
    "powershell.exe" "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File \"$INSTDIR\register-autostart.ps1\""
  CreateShortcut "$SMPROGRAMS\ALN 内网服务器\卸载.lnk" "$INSTDIR\uninstall.exe"
  CreateShortcut "$DESKTOP\ALN 内网服务器.lnk" \
    "wscript.exe" "$INSTDIR\start.vbs"

  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME} v${APP_VERSION}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Function LaunchServer
  Exec '"powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\start.ps1"'
FunctionEnd

Section "Uninstall"
  DetailPrint "停止服务进程..."
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\stop.ps1"'
  Pop $0
  Sleep 1500
  RMDir /r "$INSTDIR"
  Delete "$SMPROGRAMS\ALN 内网服务器\*.*"
  RMDir "$SMPROGRAMS\ALN 内网服务器"
  Delete "$DESKTOP\ALN 内网服务器.lnk"
  DeleteRegKey HKLM "${UNINSTALL_KEY}"
SectionEnd
