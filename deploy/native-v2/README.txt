ALN 数据平台：Windows 原生内网部署包 v0.3.0
============================================================

本包同时用于服务器和个人电脑，不需要 Docker、WSL、虚拟化或联网下载。
包含独立 Python 3.12 x64、Windows 依赖 wheel、Electron 桌面程序、完整前端、
后端、现有代理模型资源及 Microsoft C++ 运行库安装器。

先阅读 DEPLOYMENT.md（完整部署/升级/备份/验收方法）和 DESIGN.md（新方案）。

你当前的 E:\JBZ 服务器已有 20260928 版和数据：
  把 aln-platform-win64-20261008.zip 拷到 E:\JBZ 后，先完整解压；
  确认 E:\JBZ\aln-platform-win64-20261008\install.ps1 存在。
  不要在 ZIP 内运行脚本，也不要在新目录直接双击 install-server.cmd：
  它会创建一套空数据。请按 DEPLOYMENT.md 第 6 节“E:\JBZ 现有服务器升级”操作，
  用旧目录的 upgrade.ps1 备份并升级，保留旧 data 和 server-config.json。

服务器（Windows 10 x64）：
1. 首次安装时，完整解压到 E:\JBZ\aln-platform-win64-20261008
   （或其他英文路径、本地磁盘）。已有旧版数据时请用上面的升级流程。
2. 双击 install-server.cmd；同意管理员权限提示后，安装器会离线安装依赖、
   注册 LocalService 开机任务、设置防火墙，并确认监听进程确属 LocalService。
   默认数据目录是程序目录下的 data。需要自定义数据目录时，管理员 PowerShell 执行：
   powershell -NoProfile -ExecutionPolicy Bypass -File .\verify-package.ps1
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Role Server -DataDir D:\ALN-Data
   powershell -NoProfile -ExecutionPolicy Bypass -File .\register-autostart.ps1
3. 等待启动后执行 .\healthcheck.ps1，再访问 http://localhost:8000 。
   其他电脑访问 http://服务器IP:8000 。服务器上传 SNP 时，在服务器上打开浏览器选择文件。

日常使用（免管理员）：
  启动：双击 start-server.cmd（已注册开机任务时走任务通道，否则后台进程启动；
        启动后自动打印访问地址）。
  关停：双击 stop-server.cmd（实例属于系统服务账户时会自动请求一次提权）。
  备份：双击运行 backup.ps1（详见 DEPLOYMENT.md）。

个人电脑（Windows 10/11 x64）：
1. 完整解压同一个包到本地英文路径，双击 install-desktop.cmd。
2. 双击桌面 ALN Data Platform 快捷方式。
3. 批次页“内网服务器同步”填 http://服务器IP:8000，保存后“立即双向同步”。
4. 此后软件启动、重新联网、回到前台、每隔一分钟自动尝试同步。
   个人电脑上传参数表格、对照表会自动同步其数据；软件上传 SNP/ZIP
   处理完成后自动回传服务器，电脑原文件保留。断网时排队，联网后重试。

Windows 登录页面不影响后台服务器；任务按开机触发，以 LocalService 运行。
服务器重启且无人登录时，也应能从个人电脑访问。请按验收清单实机验证。

本包不含用户原始 SNP 或数据库。不要只复制 exe、backend 或 frontend 子目录。
旧桌面版需换用本包配套桌面程序，旧同步协议不具备全部新功能。
