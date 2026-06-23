# Windows 部署指南

**适用对象**：第一次在自己的 Windows 电脑上跑这套平台的同事。
**关键差异**：用 Docker Desktop 代替 podman，用 `bootstrap.ps1`（PowerShell）代替 `bootstrap.sh`（bash）。
**预计时间**：30–45 分钟（含下载 Docker 镜像）。

完整 Linux 文档见 [`deployment.md`](./deployment.md)；本指南只列 Windows 不一样的地方。

---

## 1. 先装好这些

### 1.1 Docker Desktop for Windows（必装）

- 下载：<https://www.docker.com/products/docker-desktop/>
- 安装时勾选「Use WSL 2 based engine」（默认就是）。
- 装完启动 Docker Desktop，等到右下角图标的 Engine 状态变成 **Running**。
- 验证：打开 PowerShell，运行：
  ```powershell
  docker --version
  docker compose version
  ```
  两条都有版本号输出就 OK。

> Docker Desktop 是免费的（个人使用 / 小公司 < 250 员工 < 1000 万美元营收免费；超出按 Docker 商业条款付费）。如果有合规顾虑，可改用 Rancher Desktop 或 Podman Desktop，命令兼容。

### 1.2 Git for Windows（必装）

- 下载：<https://git-scm.com/download/win>
- 装完会带一个「Git Bash」，本指南里用 PowerShell，但 Git Bash 也能跑 `bootstrap.sh`。

### 1.3 Node.js 18+（首次必装，之后不用）

- 下载 LTS 版：<https://nodejs.org/>
- 验证：
  ```powershell
  node --version    # v18 或更高
  npm --version
  ```

只在**第一次部署**或**前端代码更新后**需要，用来跑 `npm run build` 生成 `frontend/dist/`。
如果同事不打算改前端，也可以让别人构建好 `dist/` 之后整个目录拷过来。

### 1.4 允许 PowerShell 跑本地脚本（一次性）

默认 Windows 不让跑未签名的 `.ps1`。**以管理员身份**打开 PowerShell，执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

这条只影响当前用户，不会改全局策略。

---

## 2. 首次部署（5 步）

> 假设仓库已 clone 到 `D:\code\aln-data`。下文 PowerShell 命令默认在仓库根目录执行。

### 2.1 复制 .env

```powershell
cd D:\code\aln-data
Copy-Item .env.example .env
notepad .env
```

至少改这两行：

```
POSTGRES_PASSWORD=改成一个足够长的密码
DATA_ROOT=D:/aln-data
```

`DATA_ROOT` 三种写法（任选）：

| 写法 | 适合谁 | 数据落在哪 |
|---|---|---|
| `D:/aln-data` | 有独立数据盘 | `D:\aln-data\` |
| `./data` | 嫌麻烦，跟代码放一起 | `D:\code\aln-data\data\` |
| `C:/Users/张三/aln-data` | C 盘够大 | `C:\Users\张三\aln-data\` |

**注意**：路径里用正斜杠 `/` 或双反斜杠 `\\`，不要写单个 `\`（会被解析吃掉）。
**别**用包含中文 / 空格的路径，docker volume 偶尔不认。

### 2.2 构建前端

```powershell
cd frontend
npm install     # 第一次几分钟，下载 ~300MB node_modules
npm run build   # 输出到 frontend\dist\
cd ..
```

完成后 `frontend\dist\index.html` 应该存在。

### 2.3 启动平台

```powershell
.\bootstrap.ps1 up
```

脚本会：
1. 校验 `.env` 与 Docker Desktop
2. 在 `DATA_ROOT` 下创建 `pgdata / redis / uploads / files / exports / logs` 等子目录
3. 拉镜像（首次 ~1.5GB）
4. 起 postgres + redis，等它们 healthy
5. 在 api 容器里跑 `alembic upgrade head` 建表
6. 起 api + worker + nginx
7. curl `/api/health` 验证

跑完会打印三个入口：

```
本机访问： http://localhost:8080
局域网入口：http://192.168.x.x:8080    <- 同事打开这个
API 直连： http://localhost:8001
```

浏览器打开本机访问的那个 URL，应该看到上传页。

### 2.4 防火墙放行（让同部门的同事也能访问）

Docker Desktop 一般会自动放行，但如果同事在另一台机器上打开「局域网入口」打不开，手动放行 8080：

```powershell
# 管理员 PowerShell
New-NetFirewallRule -DisplayName "ALN Data 8080" -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow
```

### 2.5 日常使用

```powershell
.\bootstrap.ps1 status    # 看跑得怎样
.\bootstrap.ps1 down      # 关机前停掉，数据保留
.\bootstrap.ps1 up        # 下次开机再启
```

---

## 3. 不用 bootstrap.ps1，纯手工跑 docker compose

如果对 PowerShell 脚本不放心，可以拆成原始命令（行为一致）：

```powershell
# 1. 确保 .env 里 DATA_ROOT 是【绝对路径】，例如 D:/aln-data
#    （docker compose 把相对路径解析到 deploy/ 下，不是项目根，会坑）

# 2. 创建数据目录
$dataRoot = "D:\aln-data"
"pgdata","redis","uploads","files","mappings","exports","logs\api","logs\worker" |
    ForEach-Object { New-Item -ItemType Directory -Force -Path "$dataRoot\$_" } | Out-Null

# 3. 起 db
docker compose -f deploy\docker-compose.yml --env-file .env up -d postgres redis

# 4. 等 postgres healthy（约 10 秒）
docker inspect -f '{{.State.Health.Status}}' aln-postgres

# 5. 跑迁移
docker compose -f deploy\docker-compose.yml --env-file .env run --rm api alembic upgrade head

# 6. 起余下
docker compose -f deploy\docker-compose.yml --env-file .env up -d api worker nginx

# 7. 验证
Invoke-WebRequest http://localhost:8080/api/health
```

---

## 4. 常见坑

### 4.1 `bootstrap.ps1 : 无法加载文件，未对文件进行数字签名`

跳过 §1.4，执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

如果是**从 GitHub 下载 zip 解压**得到的代码，所有文件会带「来自网络」标记，即便策略放行了还会被拦。先解掉：

```powershell
Get-ChildItem -Recurse | Unblock-File
```

更推荐用 `git clone` 而不是下载 zip，clone 出来的文件没这个标记。

### 4.1.5 `.ps1` 报「参数列表中缺少参量」/ 中文显示成 `鐩锛` `鎬?` 乱码

Windows PowerShell 5.1（系统自带的 `powershell.exe`）默认按系统 ANSI 编码（中文系统是 GBK）读 `.ps1`，遇到 UTF-8 文件就乱码报错。本仓库的 `bootstrap.ps1` 已经带 UTF-8 BOM，正常不会触发。
如果你拿到的版本没 BOM（比如老 zip），就地转一下：

```powershell
$txt = [IO.File]::ReadAllText("$PWD\bootstrap.ps1", [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText("$PWD\bootstrap.ps1", $txt, [Text.UTF8Encoding]::new($true))
```

或者直接装 PowerShell 7（`winget install Microsoft.PowerShell`），用 `pwsh` 代替 `powershell`，pwsh 默认就按 UTF-8 读。

### 4.2 启动后浏览器打不开 `localhost:8080`

按顺序排查：

```powershell
.\bootstrap.ps1 status            # 看哪个容器没起来
docker compose -f deploy\docker-compose.yml logs nginx --tail 30
docker compose -f deploy\docker-compose.yml logs api --tail 50
```

最常见原因：
- Docker Desktop 没启动（图标右下角是橘色而不是绿色）
- 8080 被别的程序占用：`Get-NetTCPConnection -LocalPort 8080`；改 `.env` 里 `NGINX_PORT=18080` 再 `.\bootstrap.ps1 up` 重启
- 前端 `frontend\dist\` 没构建：回到 §2.2

### 4.3 `alembic upgrade head` 报 `connection refused`

postgres 容器没 ready 就跑了迁移。`bootstrap.ps1` 已经有 90 秒等待，超时通常是因为：
- 数据目录权限：Windows 下 Docker Desktop 一般直接给容器全权限，不太会出问题；如果出，删了 `DATA_ROOT\pgdata\` 重来
- `.env` 里 `POSTGRES_PASSWORD` 含 `$`、`#`、空格、引号 — docker compose 解析坑，换成纯字母数字

### 4.4 上传大 zip 卡住

`deploy\nginx\default.conf` 已经把超时调到 10 分钟、body 限制 50GB。如果还卡：
- 看 worker 日志：`docker compose -f deploy\docker-compose.yml logs worker --tail 100`
- Docker Desktop 默认给 2 CPU / 2GB 内存，处理大 zip 会很慢。打开 Docker Desktop → Settings → Resources，调到 4 CPU / 8GB+

### 4.5 想完全清空重来

```powershell
.\bootstrap.ps1 reset    # 输入 RESET 确认；会删 DATA_ROOT 下的业务数据
```

---

## 5. 数据备份

### 5.1 数据库

```powershell
$ts = Get-Date -Format "yyyy-MM-dd"
$backupDir = "D:\aln-backups"
New-Item -ItemType Directory -Force $backupDir | Out-Null
docker exec aln-postgres pg_dump -U aln aln | Out-File -Encoding utf8 "$backupDir\aln_$ts.sql"
```

放进 Windows 任务计划程序，每日凌晨 2 点跑：
- 任务计划程序 → 创建任务 → 触发器：每日 02:00
- 操作：`powershell.exe`，参数：`-File "D:\code\aln-data\backup.ps1"`（自己包装上面三行）

### 5.2 恢复

```powershell
Get-Content "D:\aln-backups\aln_2026-05-12.sql" | docker exec -i aln-postgres psql -U aln aln
```

### 5.3 上传的原始文件

`DATA_ROOT\files\` 是用户上传的 zip 解压后的源文件。可以用 robocopy 同步到 NAS：

```powershell
robocopy "D:\aln-data\files" "\\nas\aln-files" /MIR /MT:8 /R:2 /W:5
```

---

## 6. 升级代码

```powershell
cd D:\code\aln-data
git pull

# 后端有改动 -> 重建镜像
docker compose -f deploy\docker-compose.yml build api worker
.\bootstrap.ps1 up    # up 会跑 alembic 迁移，并重启容器到最新镜像

# 仅前端有改动 -> 重建 dist 然后重启 nginx
cd frontend
npm install   # 如果 package.json 变了
npm run build
cd ..
docker compose -f deploy\docker-compose.yml restart nginx
```

---

## 7. 我要给同事提供整套的话，最少要发哪些

最简版「拎包入住」：

1. 让同事装好 §1 的三样工具（Docker Desktop / Git / Node.js）。
2. 同事自己 `git clone` 仓库，然后照 §2 走。

也可以你这边 `npm run build` 完后，把 `frontend\dist\` 一起打包发给同事（省掉同事装 Node.js）。

绝对**不要**把 `.env` 发给同事——里面有密码。让他自己 `Copy-Item .env.example .env` 改。
