内网 Windows 服务器部署说明（原生模式 · 免 Docker）
=====================================================

适用场景：服务器没开硬件虚拟化（装不了 Docker Desktop）、不能联网。
平台以"桌面模式"运行：单进程（API + 内嵌任务 worker + 前端托管），
SQLite 数据库，不需要 PostgreSQL / Redis / Docker / Nginx。

包内容
------
  python\                  Python 3.12 Windows 安装包（见第 0 步，可能需单独补拷）
  wheelhouse\              全部 Python 依赖的 Windows 64 位离线安装包
  requirements-server.txt  依赖清单（安装脚本用）
  backend\                 后端代码
  frontend\dist\           前端静态文件
  data\                    数据目录（首次安装时创建；数据库、上传文件都在这里）
  install.ps1 / start.ps1 / stop.ps1 / register-autostart.ps1


第 0 步：准备 Python（仅首次）
------------------------------
1. 先看服务器有没有 Python 3.12：PowerShell 里跑  python --version
   显示 Python 3.12.x 就跳过本步。
2. 没有的话：把 python-3.12.x-amd64.exe 放进 python\ 目录
   （python.org 下载页：https://www.python.org/downloads/release/python-31214/
   找 "Windows installer (64-bit)"；若包内已带则直接用）。
   ——安装包约 25MB，需从能联网且能下载 exe 的机器拷贝。


第 1 步：安装（仅首次）
-----------------------
解压本压缩包到英文路径（如 D:\aln-server-lite），在该目录打开 PowerShell：
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned     # 仅第一次需要
    .\install.ps1
等待 3-5 分钟，看到"安装完成"即可。


第 2 步：启动与访问
-------------------
    .\start.ps1
窗口会打印日志并一直挂着（这就是服务器进程，关窗口=停服务）。
本机浏览器：  http://localhost:8000
同事浏览器：  http://<服务器IP>:8000
（首次访问若 Windows 防火墙弹窗，选"允许"。）

停止：另开一个 PowerShell 跑  .\stop.ps1 ，或直接关掉启动窗口。
开机自启（可选）：跑一次  .\register-autostart.ps1 （登录该账户后自动启动）。


笔记本桌面版同步配置
--------------------
批次页「内网服务器同步」面板里，服务器地址填：
    http://<服务器IP>:8000
（注意原生模式端口是 8000，不是 Docker 方案的 8080。）


运维要点
--------
- 备份：停服后整个 data\ 目录拷走即可（SQLite 数据库 + 原始 snp 全在里面）。
  建议用任务计划程序做每周 robocopy 备份：
    robocopy D:\aln-server-lite\data E:\backup\aln-data /MIR
- 多人使用注意：SQLite 写入串行——请大家错开大批量上传（几百个文件的小批次随意），
  浏览器查询/看曲线/下载完全不受限。
- Deflate64 压缩的 zip 不支持（罕见格式；本包未含该组件，标准 zip 一切正常）。
- 升级：停服 → 用新包里的 backend\ 和 frontend\ 整个覆盖旧目录 → 启动。
  （data\ 和 venv\ 不要动。）
