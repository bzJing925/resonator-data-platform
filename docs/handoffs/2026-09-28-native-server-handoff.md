# Windows 原生部署改造交接（暂停点）

用户要求：满足原始 SNP 只保存在 Windows 10 服务器、无需 Docker/虚拟化；个人电脑使用软件桌面版只缓存指标，表格在电脑上传后内网同步；服务器原始文件可远程浏览、完整 S11/Z11/相位/BodeQ 绘图和主动下载；本地有原始数据时也可绘图；服务器无须远程桌面用户登录就能常驻。最终交付新方案、部署步骤和完整 ZIP。

用户因 5 小时用量将尽要求停止并换模型继续。所有修改尚未提交。不要把当前状态宣称为完成交付。

## 已写的实现

- backend/app/models/replica.py：副本映射、服务器 UUID、批次 revision、表格幂等回执。
- backend/app/services/replica_service.py：SQLite 触发器版本跟踪、只同步已完成批次、增量更新/删除、稳定本地器件 ID、表格上行、关联原始批次时的匹配和版本冲突保护。
- backend/app/api/replication.py：/api/sync/v2/catalog、batches/{id}、tables。
- backend/app/api/desktop_sync.py：新增 /synchronize，互斥锁，服务器侧隐藏桌面同步入口。
- backend/app/services/remote_access.py：客户端将服务器副本的文件/器件曲线/BodeQ/下载/批次操作转发服务器。远程文件树节点、任务 ID 使用负数；器件 ID 翻译回本地；检查服务器身份。最后补了写操作前身份预检查。
- backend/app/api/devices.py 与 raw_download.py：历史 gzip SNP 读取/解压下载；最后补了曲线缓存 mtime+size 失效。
- desktop_setup.py：补建同步表、兼容缺少 synced_at 的旧 SQLite；禁止高版本数据库降级删表。
- local_worker.py：启动时将中断任务标失败并提示重试，保留原始输入。
- pyproject.toml/uv.lock：pyarrow 移至运行依赖，补 xlrd。
- ServerClient 禁用环境代理。旧原始数据回传不再仅按条数删除本地文件。
- 前端自动同步：启动/online/focus/60 秒，支持表格上行，保留手动同步；上传页可填关联服务器批次；同步有变化后可手动刷新显示。
- frontend/electron/main.ts：识别 native-bundle.json，使用随包 Python 启动，保留原 Electron 菜单/完整前端，禁用该离线包的在线更新。
- deploy/native-v2/：安装、开机任务 LocalService、防火墙、启动停止、备份升级、健康检查、包哈希校验、独立 Python 启动器、README.txt、DESIGN.md、DEPLOYMENT.md。
- scripts/build_native_bundle.py：跨平台生成完整 Windows 服务器+桌面共用离线包，包含 Windows CPython embedded、Electron、wheelhouse、现有模型、文档及 SHA256 清单。

## 已下载资源（不需要重新下载）

在 dist-server-pack/runtime-downloads/：
- python-3.12.10-embed-amd64.zip（实际用于新包）
- python-3.12.10-amd64.exe（前期下载备用，新包不用安装器）
- electron-v42.4.0-win32-x64.zip
- vc_redist.x64.exe
- SHASUMS256.txt 是否完整需检查（构建脚本最后会从 Electron 官方下载校验）。

Windows wheels 在 dist-server-pack/aln-server-lite/wheelhouse/，新增 pyarrow、xlrd、pip 已下载。新脚本按 uv.lock 的 Windows markers 精确选择 77 个包，并检查 METADATA 的依赖闭包。不要把旧压缩包当新交付。

## 已执行验证

- 后端 Ruff 全库通过（最后一轮）。
- 相关后端测试最后一轮 **150 passed**：services、sync APIs、devices proxy/cache、batch sync、core、desktop mode。
- 新增 tests/services/test_replication_v2.py：同步更新/删除、处理中排除、表格幂等、关联更新、跨客户端表格传播、重提取同路径新 ID 等。
- 新增 tests/test_native_pair.py：两套真实 HTTP 原生服务、独立 SQLite/文件目录，测试无本地 SNP 的 5 种频谱、BodeQ、文件列表/曲线、文件树、单文件和 ZIP 下载及指标更新。**1 passed**，最近一次在最终缓存失效及写身份预检查修改之前；建议最终重跑一次。
- 前端 typecheck/build 已通过；完整前端测试 **127 passed, 1 skipped**，随后新增 online 重连测试，相关文件 **6 passed**（前端完整测试最终应变为 128 passed, 1 skipped，需实际确认）。
- 后端全量曾执行：387 passed, 15 skipped, 11 failed, 6 errors；失败/错误均为未启动 localhost:5432 PostgreSQL，最后失败重跑日志 dist-server-pack/backend-full-failures.txt。不是 Windows 原生 SQLite 联调失败，不要伪称全量绿。
- 尚无真实 Windows 运行环境，不能声称已验证 embedded Python 安装、Windows DLL、Electron exe、LocalService 开机任务、注销/重启、真实 Wi-Fi 与大数据并发。

## 未完成（按优先级）

1. **仔细复查新代码与脚本**，尤其 remote_access.py 的代理读写路由/负数 node 与 task ID、表格关联和同步事务/重试逻辑；当前没有独立代码评审。避免局部实现被文档夸大。
2. **补足关键验收**：最终双服务 HTTP 测试；真正的服务器文件上传及 worker 子进程处理（当前双服务测试通过 SQL seed 原始批次，未覆盖上传/去嵌流水线）；表格上传 API 到服务器和第二客户端的完整 HTTP 流；远程文件树修改、批次重算、任务状态/SSE；断网重试和服务器身份不符时不得发出写操作。
3. **检查 Windows 部署脚本实际可行性**：embedded Python 的 python312._pth 包含 Lib/site-packages 与 ../backend；bootstrap_runtime.py 从 pip wheel 解包，然后 pip 离线安装；C++ redist 缺失时提权安装。LocalService 访问权限、程序路径引用、Task Scheduler 停止子进程/重启、备份/升级恢复都未实机验证。可用 PowerShell parser 静态检查作为最低补充，但不能冒充 Windows 实机测试。
4. **完成 ZIP 构建**：在 backend/ 运行 `uv run --no-sync python ../scripts/build_native_bundle.py --skip-build`。如改动前端/电子主进程，先 npm run build 或去掉 --skip-build。构建日志 dist-server-pack/build-native.log。原构建进程 uv PID 6228/python PID 6230 在暂停时已发送 TERM；可能留下部分目录或 ZIP，必须重建并做 CRC/完整清单校验，不能直接交付残件。
5. **校验最终包**：要求 Python/Electron/Windows wheels/模型/完整绘图前端/文档都在；没有 .env、用户数据库、用户 SNP、venv 或 Mac 二进制；全部 manifest 哈希一致；zip CRC 通过；核实前端源码与构建产物无过期。
6. **写最终验证报告**：实际通过项/环境阻塞项/Windows 未验收项分开。将报告加进 deploy/native-v2 后重新打包，让包内报告与交付一致。
7. **最终交付**：链接 ZIP、SHA256、DESIGN.md、DEPLOYMENT.md，简述服务器和电脑安装命令。明确首次服务器后台任务注册需要管理员，而不是以前的“绝对免管理员”。

## 已知边界/风险，继续时必须正视

- 当前仓库不存在实验性 Z-稀疏重建所需 sparse/reconstructor.pt 权重。入口与远程路径保留，现有真实频谱/BodeQ不依赖该模型。DESIGN/DEPLOYMENT 已明确此限制；用户要求“绘图不能阉割”，最终不能把缺失模型的功能声称已可用。现有 surrogate 模型权重和相关资源存在并会打包。
- Deflate64 可选解码组件仍未打包，标准 ZIP 支持，文档要求重压为普通 ZIP；用户原始文件若必须是 Deflate64 则需要进一步解决。
- 指标副本采用 SQLite，不是字面上只保存 Excel；文档已解释。
- 自动同步有变化时提示刷新显示，不自动强制刷新并打断图表操作。
- 服务器仍是可信内网共享平台，没有完整账号权限系统；防火墙限制指定网段，不公开互联网。
- 同步 v2 当前定位原生 SQLite，未给 Docker/PostgreSQL 模式补同步表的 Alembic migration；不要扩展宣称兼容所有部署模式。
- server side catalog ready() 为每批次查询任务，规模较大时需实测；单批次 JSON/ORM 导入可能需性能优化，但不要未经测试强行重构。
- 同步批次重命名若恰好复用另一本地批次名会报冲突，保护本地数据。
- upgrade.ps1 对程序文件使用覆盖复制，没有清理旧版本删除的文件；需评估是否改善。不要未经备份删除用户文件。

## 用户原有文件保护

进入本轮前已有未跟踪的 dist-server-pack.zip 和 tmp/，不要删改。旧压缩包 dist-server-pack/aln-server-lite-20260924.zip 保留。所有更改未提交，git status 可看到清单。用户要求的是最终部署包，不是 PR；无需擅自推送或提交。
