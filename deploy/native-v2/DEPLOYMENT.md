# 部署与验收方法

## 1. 准备

适用于 Windows 10/11 **64 位**。服务器无需虚拟化。服务器和个人电脑都使用同一份完整 ZIP，分别执行对应安装步骤。程序目录不要放网络共享、OneDrive 同步目录或中文路径。

准备本地磁盘空间：解压、安装依赖需要数 GB，另为原始 ZIP、解压 SNP、处理临时文件和备份预留空间。首次安装可能需要管理员安装随包 Microsoft Visual C++ 运行库；服务器注册后台任务、设置防火墙必须由管理员执行。没有管理员权限时，由 IT 执行这一步，不能保证无人登录运行。

让 IT 为服务器保留固定内网 IP 或固定主机名。若 Wi-Fi 与服务器不在同一网段，必须允许客户端到服务器 TCP 8000 的路由；Wi-Fi 的“访客隔离”可能阻止访问。

先解压到例如 `D:\ALN-Platform`，进入目录。ZIP 中有顶层目录，以下命令在包含 install.ps1 的目录执行。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\verify-package.ps1
```

校验应在安装前执行。运行时安装依赖、产生缓存后，包内部分文件会变化。

## 2. 安装服务器

默认数据目录可直接双击 `install-server.cmd`，同意管理员权限提示；它会安装并注册、验证开机任务。需要把数据放在独立磁盘时，使用下面的管理员 PowerShell 命令。

右键 PowerShell → **以管理员身份运行**：

```powershell
cd D:\ALN-Platform
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Role Server -DataDir D:\ALN-Data
powershell -NoProfile -ExecutionPolicy Bypass -File .\register-autostart.ps1
```

`install.ps1` 使用包内 Python 和 wheels，无需联网，不修改系统 PATH，不使用系统 Python。安装器比较系统与包内 Microsoft C++ 运行库版本；系统版本过旧时自动升级。若提示需要重启，请先重启 Windows，再重跑安装器。Torch 自检失败时，安装器会阻止模型预热和相关 ML 入口加载不兼容的 DLL；S11/Z11/相位/BodeQ 等标准原始数据绘图仍可使用。

`register-autostart.ps1` 创建 `ALN-Data-Server` 任务，以 LocalService 运行，为程序目录授予读取权限、数据目录授予写入权限，并配置防火墙。默认放行 LocalSubnet；若服务器和 Wi-Fi 分属不同网段，请用实际办公网段：

```powershell
.\register-autostart.ps1 -AllowedSubnet '192.168.10.0/24'
```

上面的网段只是格式示例，不能照抄。此步骤完成后不需要保持 PowerShell 窗口或远程桌面在线。

注册脚本会等待并确认健康接口、计划任务状态及监听进程的 LocalService 身份；失败会报错，不应把普通用户的后台进程当作注销后常驻。随后检查：

```powershell
.\healthcheck.ps1
Get-ScheduledTask -TaskName ALN-Data-Server
```

若健康检查显示 `TaskState=Ready`，代表任务已经退出。运行 `Get-ScheduledTaskInfo -TaskName ALN-Data-Server | Format-List LastRunTime,LastTaskResult`，查看数据目录下 `logs\server.log`，并在 Windows 应用程序事件中查找 `python.exe` 崩溃记录。曾有目标 Windows 10 机器因系统 `MSVCP140.dll` 为 14.34、包内运行库为 14.44，模型预热时进程崩溃；升级运行库后 `import torch` 自检恢复。不要仅因系统存在 `MSVCP140.dll` 就跳过版本检查。

服务器浏览器访问 `http://localhost:8000`；电脑访问 `http://服务器IP:8000`。
端口是 **8000**。地址、端口和数据目录保存在 `server-config.json`。改端口后重新注册任务及防火墙。

在服务器浏览器上传对照表，再上传原始 SNP/ZIP。也可以在个人电脑浏览器访问服务器地址并选择电脑文件，直接上传服务器；文件选择器不会删除电脑上的原文件。使用远程桌面操作时，文件选择器应选服务器磁盘上的文件。

## 3. 安装个人电脑软件

1. 将完整 ZIP 解压到本机英文路径，例如 `C:\ALN-Platform`。
2. 双击 `install-desktop.cmd`，或执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Role Desktop
```

3. 打开桌面的 **ALN Data Platform** 快捷方式。
4. 在批次页“内网服务器同步”填写 `http://服务器IP:8000` 及自己的上传人名称，保存配置。
5. 点击“立即双向同步”。后续启动、重新联网、回到前台及每分钟自动重试。
6. 自动同步有变化时，可点“刷新显示”更新已打开的数据页面，避免自动刷新打断当前绘图操作。

个人数据位于 `%USERPROFILE%\.aln-data`。首次换用本包桌面版前，关闭旧版并备份该目录。新版会原位补充同步元数据，不要让新旧桌面程序同时打开同一数据库。

电脑上传参数表格后，软件会自动把指标记录上传服务器；原 Excel/CSV 文件保留在上传电脑。独立上传的对照表也会自动上传解析后的条目，即使尚无批次引用，其他电脑会从服务器同步这些条目。表格必须符合平台现有列契约；XLSX/XLS/CSV 均支持。同名但内容不同的对照表会提示冲突，不会覆盖服务器数据。

电脑也可在软件端上传 SNP/ZIP：软件先保留和处理本地文件，批次处理成功后自动回传服务器；离线时排队，恢复内网连接后重试。服务器处理成功才标记为已同步，本地原文件和本地原始批次继续保留。同步面板的“回传”按钮可用于手动重试。请确认同步提示没有错误并在服务器批次页看到该批次，不能只以电脑本地上传成功作为服务器已收到的证据。

如果表格要关联服务器已有原始数据：先同步该批次，再上传表格，并填写“关联服务器原始批次”。文件名/端口/目录必须可唯一匹配；遇到冲突时检查同步面板错误信息，先同步再重新导入。不要用任意改名来绕过关联错误。

离线时可分析已同步指标及本机已有原始批次。只有服务器持有的原始频谱，在离线时不能读取。下载文件需用户主动选择保存；查看曲线不会把整个 SNP 批次同步到电脑。

## 4. 日常启动、停止、日志

管理员 PowerShell：

```powershell
.\stop.ps1
.\start.ps1
.\healthcheck.ps1
```

停止脚本会关闭本安装实例及计算子进程，并禁用自动重启；启动脚本会重新启用后台任务。没有注册任务时，start.ps1 前台运行，关闭窗口即停止，这仅用于排查。

服务器日志：`D:\ALN-Data\logs\server.log`，轮转保留最近 10 个文件。
桌面日志：`%USERPROFILE%\.aln-data\logs`。

中断的上传/重算任务会标记为失败；查看错误，再从批次页重新提取或重算。服务器保留原始 ZIP 以供恢复。

## 5. 备份与恢复

在无人上传/计算时运行。备份会短暂停服；用独立磁盘或备份盘，不要放回数据目录内部。

```powershell
.\backup.ps1 -Destination E:\ALN-Backups
```

生成带时间戳的新目录，不使用 `/MIR` 覆盖历史备份。包含完整数据库、原始文件、上传文件、对照表等，以及 server-config.json。源任务原先正在运行时会恢复启动。

恢复：停止服务；保留当前数据目录作为回退副本；把所选备份的整个 `data` 目录复制到新的本地目录；修改 server-config.json 的 data_dir；重新执行 register-autostart.ps1 授权并启动；执行健康检查及抽样绘图。不要在服务运行时只拷贝 SQLite 主文件，遗漏 WAL 会丢失最近写入。

## 6. 升级

### 从旧 lite 包迁移

1. 关闭旧服务器 PowerShell 进程和旧登录自启任务。
2. 停服备份旧包的整个 data 目录。
3. 新包解压到新目录，安装时 `-DataDir` 指向旧数据目录的副本，先验证再切换。
4. 注册新开机任务，健康检查；个人电脑全部使用本包配套桌面端。

不要覆盖正在运行的旧目录，也不要让两个服务器进程同时使用同一个数据库。

### v2 后续升级

新包先解压到另一个目录，例如 D:\ALN-New。管理员进入旧安装目录执行：

```powershell
.\upgrade.ps1 -NewPackagePath D:\ALN-New -BackupDir E:\ALN-Backups
```

脚本先备份数据和旧程序，停服更新程序/依赖，保留 server-config.json 和数据目录，再启动。升级失败时保留停服状态，按备份恢复。数据库降级被拒绝；回退程序时应同时恢复升级前数据库备份。

桌面升级：关闭软件，保留 `%USERPROFILE%\.aln-data`，完整解压新包到新目录，重新安装桌面端并使用新的快捷方式。

### E:\JBZ 现有服务器升级（20260928 → 20261008）

这台服务器现有程序目录为 `E:\JBZ\aln-platform-win64-20260928`，现有数据目录为 `E:\JBZ\aln-platform-win64-20260928\data`。**仅把 ZIP 拷到服务器不能运行；也不要直接双击新版目录的 `install-server.cmd`**，否则会使用新版目录下新建的空 `data`。按以下顺序操作：

1. 把 `aln-platform-win64-20261008.zip` 拷到 `E:\JBZ`。在 PowerShell 中完整解压；ZIP 自带顶层目录，解压后应存在 `E:\JBZ\aln-platform-win64-20261008\install.ps1`：

   ```powershell
   Expand-Archive -LiteralPath 'E:\JBZ\aln-platform-win64-20261008.zip' -DestinationPath 'E:\JBZ'
   Test-Path 'E:\JBZ\aln-platform-win64-20261008\install.ps1'
   powershell -NoProfile -ExecutionPolicy Bypass -File 'E:\JBZ\aln-platform-win64-20261008\verify-package.ps1'
   ```

   `Test-Path` 应返回 `True`，校验应显示 `Package checksums OK`。若已存在同名新版解压目录，先核对其内容，不要用 `-Force` 将文件混合覆盖。

2. 确认 `E:` 盘有足够空间容纳旧数据的一份备份、旧程序的一份备份和解压后的新程序。以**管理员身份**打开 PowerShell，进入旧程序目录，运行旧目录的升级脚本：

   ```powershell
   cd 'E:\JBZ\aln-platform-win64-20260928'
   powershell -NoProfile -ExecutionPolicy Bypass -File .\upgrade.ps1 -NewPackagePath 'E:\JBZ\aln-platform-win64-20261008' -BackupDir 'E:\JBZ\ALN-Backups'
   ```

   脚本会备份旧 `data` 和程序，停止旧进程，把新版程序复制到**原安装目录**，保留原 `server-config.json` 和 `data`，再启动计划任务。因此升级后运行目录名仍是 `aln-platform-win64-20260928`，但程序内容是新版；不要同时启动新版解压目录中的另一套服务器。

3. 在旧程序目录验收，确认计划任务是 `Running`、健康检查通过，并核对原有批次仍存在：

   ```powershell
   Get-ScheduledTask -TaskName ALN-Data-Server | Format-List TaskName,State
   .\healthcheck.ps1
   Get-Content .\server-config.json
   ```

   配置里的 `data_dir` 应仍是 `E:\JBZ\aln-platform-win64-20260928\data`。从同事电脑访问 `http://服务器IP:8000`，抽样查看原始曲线和指标；随后注销远程桌面登录，再从电脑访问一次。升级失败时不要删除旧目录或备份，记录错误和 `data\logs\server.log` 后再恢复。

## 7. 目标 Windows 必做验收

1. 断开外网但保留办公内网，确认离线安装成功，health 状态为 ok。
2. 服务器上传一批真实 SNP，完成提取；电脑同步指标，检查本机 files 目录未出现该批次原始文件。
3. 电脑点击器件和原始文件分别查看 S11、Z11、相位、BodeQ；检查多曲线比较、筛选、缩放、图像/指标导出。
4. 下载单个 SNP 和整批 ZIP，对比内容/哈希与服务器源文件。
5. 电脑 A 在软件端上传 SNP/ZIP，处理后等待自动回传；服务器和电脑 B 能看到批次、绘制原始曲线，电脑 A 原文件仍在。再用电脑浏览器直接上传另一批文件，确认服务器收到且电脑原文件仍在。
6. 电脑 A 单独上传未关联批次的对照表，服务器和电脑 B 能看到全部条目；上传参数表格后，服务器和电脑 B 同步后指标一致；重复同步不重复入库。
7. 修改/重算服务器指标，再同步，确认已有批次更新；处理途中同步不能留下永久空批次。
8. 删除测试批次后，电脑副本同步删除；断网时不能误删副本。本机独立批次不受影响。
9. 软件先打开、后连接办公 Wi-Fi，确认自动同步恢复。断网后已有指标图表仍可用，远程曲线明确提示需要内网。
10. 关闭远程桌面窗口、注销登录、重启服务器且不登录，分别从电脑访问平台。
11. 上传中模拟服务中断，重启后任务显示中断失败，原始输入仍在，可以重新处理。
12. 用实际批次规模和预期同时使用人数测试上传、查询、绘图耗时与磁盘增长。
13. 实际完成一次备份恢复，并核对原始文件与指标数量。

本次构建环境是 macOS，已跑原生双服务 HTTP 联调；以上 Windows 行为、网络和实际负载仍须实机验收。本包不把这些尚未执行的步骤写成“已通过”。

## 8. 常见问题

- 远程桌面出现选择用户：这是 Windows 登录，不是平台登录；正确注册后台任务后，无需登录就能访问平台。
- 电脑连了 Wi-Fi 仍不可达：检查服务器 IP、8000 端口、防火墙网段和网络隔离。在电脑 PowerShell 执行 `Test-NetConnection 服务器IP -Port 8000`。
- 安装报 DLL load failed：安装随包的 Microsoft C++ x64 运行库，重启后重试；查看安装输出中具体失败的模块。
- Python 被公司策略拦截：本包没有绕过公司应用白名单的能力，需要 IT 放行包内 runtime\python.exe 与 desktop\ALN-Desktop.exe。
- 提示服务器身份改变：不要把不同服务器的同名数据混在同一副本。保留旧数据库身份迁移，或备份后换用新的本地数据目录。
- 独立表格没有曲线：指标数值无法恢复真实频谱。使用关联原始批次模式，或者在服务器上传对应原始文件。
- ZIP 使用 Deflate64：离线包未包含该可选解码组件；请重新压缩为普通 Deflate ZIP 后上传，或上传单独 SNP。标准 ZIP 不受影响。
- 实验性稀疏重建提示模型未加载：仓库没有其训练权重；现有真实频谱和 BodeQ 绘图仍可使用，详见 DESIGN.md。
