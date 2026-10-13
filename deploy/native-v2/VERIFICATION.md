# 验证报告 — 原生模式 Windows 部署包

日期：2026-09-28（两轮会话接力完成）
范围：`aln-platform-win64-*.zip`（服务器原生模式 + 桌面版离线包）

## 一、实际通过项（本机 macOS 实证）

**后端测试**
- 同步/副本/远程访问相关套件（tests/services、tests/api、tests/core、桌面模式、tests/models）：232 passed
- `tests/services/test_replication_v2.py`（18 个用例）：同步更新/删除、处理中排除、表格幂等回执、关联更新、跨客户端表格传播、重提取同路径新 ID、v1 回传批次收养、revision 自愈、并发 409——全通过
- `tests/test_native_pair.py`（两套真实 HTTP 原生服务互连，独立 SQLite/文件目录）：无本地 SNP 的 5 种频谱、BodeQ、文件列表/曲线、文件树、单文件与 ZIP 下载、指标更新；另验证电脑独立对照表上行、原始批次自动回传、服务器提参、本地原文件保留及重复同步不重传。`-m integration` 显式运行 **2 passed**。单元测试另验证独立对照表下行到另一台电脑及服务器删除后的清理。
- Ruff 全库通过
- 本轮后端非 PostgreSQL 套件：391 passed、11 skipped；全量运行中另有 11 failed + 6 errors，均依赖本机未启动的 PostgreSQL（localhost:5432），涉及 test_export/test_query_safety/test_reprocess/test_integration/test_deembed_path/test_pipeline_batch，与原生 SQLite 方案无关

**前端**
- vitest 130 passed / 1 skipped（含在线重连、本地上传完成后立即自动同步、独立对照表下行后刷新提示用例）
- tsc --noEmit 通过；vite build + electron 主进程编译通过

**打包与资源**
- wheelhouse 77 个 Windows amd64 wheel：按 uv.lock 的 Windows markers 独立重放选包逐字一致；torch/sqlalchemy 的 METADATA 依赖闭包抽查通过；linux-only nvidia/cuda/triton 正确排除；zipfile-deflate64 按设计排除（代码已可选导入）
- 运行时资源：electron-v42.4.0-win32-x64.zip 的 SHA256 与 Electron 官方 SHASUMS256.txt 一致；python embed zip / electron zip CRC 校验通过；vc_redist.x64.exe 为真实 Windows 可执行文件
- ZIP 产物：manifest 文件哈希全对；zip CRC 全过；单一顶层目录；包内 frontend/dist、electron-dist、backend/app 与当前源码逐字节相同；无 .env/用户数据库/用户 SNP/venv/node_modules/Mac 二进制
- embedded Python 启动链：python312._pth 含 Lib/site-packages 与 ../backend；bootstrap 先解 pip wheel 再离线安装再 `pip check` 再 import 冒烟

**独立代码评审（两轮 + 修复复审）**
- 第一轮（同步/副本/远程访问核心）：发现 1 项关键 + 6 项重要问题，全部修复并经独立复审（含变异实证）：
  v1"回传"过的批次在 v2 同步中收养为副本（原先每次同步永久报"批次名冲突"）；表格关联 revision 过期自愈 + 上传时校验目标必须是服务器副本；未映射的远程器件 ID 不再透传（避免与本地 ID 碰撞显示错误曲线）；副本批次远程重算/重提取的任务进度与按钮状态修复；表格回执的版本检查纳入写事务（并发不再静默覆盖）；删除副本批次的确认文案明确"将同时删除服务器上的原始数据"
- 第二轮（打包/部署脚本）：wheelhouse 选包逻辑、清单自洽、ZIP 洁净度经独立复算通过；开机任务注册脚本的容错已修；无构建阻塞项
- 复审遗留的次要项见下文"跟进项"

**架构验证**
- 桌面模式（SQLite + 内嵌 worker 线程）作为服务器对外服务的可行性在 Mac 上冒烟通过：health / 同步端点 / 前端托管全通
- 双原生服务 HTTP 互连（一台当服务器一台当客户端）在测试中实证

## 二、环境阻塞项（本机无法执行，非缺陷）

- 办公网过滤 Windows 可执行文件下载：python.org / 华为云 / npmmirror 的 .exe 与 embed zip 均 404。运行时资源最终经其他渠道取得并已做哈希校验（见上）
- Docker Hub 被办公网封锁：备用 Docker 部署包未产出（原生模式为主交付，Docker 包非必需）
- 本机无 localhost:5432 PostgreSQL：依赖 PG 的 11+6 个测试为预存环境失败（该平台原生模式使用 SQLite，不受其影响）

## 三、Windows 实机验证（2026-09-28 Parallels Windows 11 VM 实测）

已在真实 Windows 11 虚拟机（内网隔离网络）完整实测通过：
1. ✅ 整包解压 + `install.ps1` 离线安装（embedded Python + 77 个 wheel + pip check + import 冒烟；未触发 UAC）
2. ✅ `start.ps1`/`stop.ps1` 一键启停循环（后台启动、健康等待、打印访问地址；关停找到并结束服务进程）
3. ✅ `healthcheck.ps1` 健康检查（含 catalog 协议校验）
4. ✅ 内网访问：另一台机器（macOS）经网络访问页面与 API
5. ✅ 浏览器上传对照表 + SNP（合成谐振器 s1p），提参入库，fs/fp 提取正确
6. ✅ S11/阻抗/相位/BodeQ 曲线与原始文件下载
7. ✅ 桌面客户端同步：另一台机器的桌面模式实例拉取副本成功；本地无文件时曲线透明代理到服务器（server-proxy）与远程下载均实证
8. ✅ 双击启停包装 start-server.cmd / stop-server.cmd：cp936 实机复现"UTF-8 中文注释被 cmd.exe DBCS 扫描粘连成乱码命令"故障（powershell 行根本不执行），改纯 ASCII + CRLF 后一键启停循环实证通过；构建器已加 .cmd/.bat 纯 ASCII 守卫（.ps1 的 BOM 守卫同类）
9. ✅ start.ps1 局域网地址探测加固：真服务器只打印 localhost（启动时未联网/NetTCPIP 枚举为空）后，增加 WMI 兜底枚举与"未检测到内网 IP"明示提示；VM 实测主路径打印同事地址、WMI 兜底返回正确 IP
10. ✅ stop.ps1 改端口反查：真服务器"找不到进程但网站还在跑"——根因是 LocalService 运行的实例读不到命令行，WMI 匹配为空且提权分支不触发。改为 TCP 表端口反查 PID（对所有用户可见）为主、命令行匹配为兜底，杀不掉时自动提权；先停禁计划任务再杀进程（防自动拉起竞争）。VM 实证：后台模式启停回归、重复关停提示、非 python 占端口不误杀且给出黄色提示；LocalService 实杀路径待真机验收（见下节）
11. ✅ 新包在 Windows 11 VM 上离线安装成功（77 wheels、pip check、后端导入、torch 导入）；`register-autostart.ps1` 已实机确认计划任务运行、健康接口正常且监听进程属于 LocalService。注销唯一登录用户后接口仍可用；重启且无人登录后任务自动恢复，另一台 Mac 可通过内网请求 `/api/health` 得到 `ok`。
12. ✅ 新增 `install-server.ps1` 一键安装与注册入口在 Windows 11 VM 的管理员上下文中重跑成功；`install-server.cmd` 是其 ASCII/CRLF 双击包装，仍应在目标机确认 UAC 提权交互。

**仍未实机验证（首次正式部署时按 DEPLOYMENT.md 第 7 节执行）：**
- 目标 Windows 10 服务器上的 LocalService 权限、注销/重启无人登录后的访问（Windows 11 VM 已验证，仍须在目标机器复测）
- vc_redist 缺失时的提权安装路径（测试 VM 已预装运行库）
- Electron 桌面壳在 Windows 的实机运行（测试走浏览器通道）
- 真实办公 Wi-Fi 网段（测试用 Parallels host-only 网络代替）
- 大数据量与多客户端并发下的 SQLite 表现
- `install-server.cmd` 从普通用户双击后的 UAC 提权交互（VM 上已验证管理员上下文的安装与注册路径）

## 四、已知限制（如实声明）

- **老 CPU（无 AVX 指令集）上 torch 不可用**：安装时显示黄色警告但不中止；ML 预测（PINN 快速曲线/稀疏重建）自动禁用，数据管理、全部绘图（skrf 路径）、同步不受影响。VC++ 运行库不完整同理（包内 prerequisites 有 vc_redist 安装器）
- sparse/reconstructor.pt 权重不在仓库中：Z 稀疏重建功能不可用（入口保留）；S11/阻抗/相位/BodeQ 等全部现有绘图不依赖该模型
- Deflate64 压缩的 zip 不支持（罕见格式；请重压为标准 zip 后上传）
- 指标副本为 SQLite 数据库（而非字面 Excel 文件）；可经导出功能生成 Excel/CSV
- 同步 v2 面向原生 SQLite 部署；Docker/PostgreSQL 模式未配同步表迁移
- 无账号权限系统：仅限可信内网；防火墙规则限定指定网段
- 一机一实例：同一台服务器不要装两份（开机任务名/防火墙规则名为全局固定值）

## 五、跟进项（不阻塞交付，后续版本处理）

- upgrade.ps1 对程序目录改 robocopy /MIR 并覆盖 prerequisites/；bootstrap_runtime.py 按 pinned 版本选 pip wheel
- 上游运行时资源在构建脚本内锚定 sha256 常量
- install-server.cmd 与文档流程对齐或移除
- 桌面僵尸 python.exe 清理（按 run_desktop_backend.py 路径 taskkill 过滤）
- stop.ps1 路径匹配大小写不敏感化

## 六、2026-10-08 目标服务器故障修复

目标 Windows 10 机器上，计划任务启动约两秒后退出（`LastTaskResult=255`），健康检查无法连接。应用程序事件记录 `python.exe` 在 `MSVCP140.dll` 中以 `0xc0000005` 崩溃。系统 DLL 版本为 14.34，包内运行库安装器为 14.44；单独执行 `import torch` 报 `c10.dll` 初始化失败。手动安装包内运行库后，系统 DLL 升至 14.44，`import torch` 返回 0。

修复：安装器按版本升级运行库，并在需要重启时明确中止；torch 自检失败时写入禁用标志，阻止后台模型预热及 ML 入口再次加载 DLL，保留标准原始数据绘图。健康检查现在显示任务状态、退出码和日志位置。目标机器上的任务常驻与注销后访问仍需重新验收，不能仅凭 torch 导入成功断言部署已完成。
