# ALN 谐振器数据平台 — 桌面版零基础启动指南

本文档面向**未配置任何开发环境**的新电脑，说明如何在 macOS、Windows 和 Linux 上从零安装依赖并启动桌面版。

> 桌面版 = Electron 原生窗口 + FastAPI 后端 + React 前端，双击图标即可启动，无需手动运行后端或打开浏览器。

---

## 目录

- [系统要求](#系统要求)
- [macOS 启动步骤](#macos-启动步骤)
- [Windows 启动步骤](#windows-启动步骤)
- [Linux 启动步骤](#linux-启动步骤)
- [数据库初始化（首次运行）](#数据库初始化首次运行)
- [常见问题](#常见问题)

---

## 系统要求

| 组件 | 版本要求 | 用途 |
|------|---------|------|
| Python | 3.12+ | 后端运行环境 |
| Node.js | 18+ | 前端 + Electron 构建 |
| PostgreSQL | 15+ | 业务数据库 |
| Redis | 6+ | 任务队列缓存 |

---

## macOS 启动步骤

### 1. 安装 Homebrew（包管理器）

打开「终端」，执行：

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

> 安装完成后，按提示运行 `(echo; eval "$(/opt/homebrew/bin/brew shellenv)") >> ~/.zprofile` 将 brew 加入环境变量。

### 2. 安装 Python 3.12

```bash
brew install python@3.12
```

验证：

```bash
python3.12 --version  # 应输出 Python 3.12.x
```

### 3. 安装 Node.js

```bash
brew install node
```

验证：

```bash
node --version   # 应输出 v18.x 或更高
npm --version    # 应输出 9.x 或更高
```

### 4. 安装 PostgreSQL 和 Redis

```bash
brew install postgresql@15 redis
```

启动服务：

```bash
brew services start postgresql@15
brew services start redis
```

验证：

```bash
pg_isready  # 应输出 "accepting connections"
redis-cli ping  # 应输出 "PONG"
```

### 5. 安装 uv（Python 包管理器）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

关闭并重新打开终端，验证：

```bash
uv --version
```

### 6. 克隆项目并进入目录

```bash
git clone https://github.com/bzJing925/resonator-data-platform.git
cd resonator-data-platform
```

### 7. 配置环境变量

```bash
cp .env.example .env
```

用任意编辑器打开 `.env` 文件，修改 `POSTGRES_PASSWORD` 为安全密码（至少 16 位，避免特殊字符）。

### 8. 初始化数据库（首次运行）

```bash
createdb aln  # 创建数据库（用户名为当前系统用户）
cd backend
uv sync       # 安装 Python 依赖
uv run alembic upgrade head  # 运行数据库迁移
```

### 9. 安装前端依赖

```bash
cd ../frontend
npm install
```

### 10. 启动桌面版

```bash
cd ..
./scripts/dev-desktop.sh
```

桌面窗口会自动打开。首次启动会编译 Electron 主进程，可能需要 1–2 分钟。

---

## Windows 启动步骤

### 1. 安装 Python 3.12

1. 访问 https://www.python.org/downloads/windows/
2. 下载 **Python 3.12.x (64-bit)** 安装包
3. 运行安装程序，**勾选「Add Python to PATH」**
4. 点击「Install Now」

验证（打开 PowerShell）：

```powershell
python --version  # 应输出 Python 3.12.x
```

### 2. 安装 Node.js

1. 访问 https://nodejs.org/
2. 下载 **LTS 版本**（v18+）
3. 运行安装程序，保持默认选项

验证：

```powershell
node --version   # 应输出 v18.x 或更高
npm --version    # 应输出 9.x 或更高
```

### 3. 安装 PostgreSQL 和 Redis

**PostgreSQL：**

1. 访问 https://www.postgresql.org/download/windows/
2. 下载 PostgreSQL 15+ 安装程序
3. 安装时记住设置的密码（后面 `.env` 要用）
4. 保持默认端口 `5432`

**Redis（Windows 推荐用 Memurai）：**

1. 访问 https://www.memurai.com/ 下载并安装
2. 或安装 WSL2 后使用 Docker 运行 Redis

验证 PostgreSQL：

```powershell
psql -U postgres -c "SELECT 1;"
```

验证 Redis：

```powershell
redis-cli ping  # 应输出 PONG
```

### 4. 安装 uv

以**管理员身份**打开 PowerShell：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

关闭并重新打开 PowerShell，验证：

```powershell
uv --version
```

### 5. 克隆项目并进入目录

```powershell
git clone https://github.com/bzJing925/resonator-data-platform.git
cd resonator-data-platform
```

### 6. 配置环境变量

```powershell
Copy-Item .env.example .env
```

用记事本或 VS Code 打开 `.env` 文件，修改 `POSTGRES_PASSWORD` 为安装 PostgreSQL 时设置的密码。

### 7. 初始化数据库（首次运行）

```powershell
psql -U postgres -c "CREATE DATABASE aln;"
cd backend
uv sync
uv run alembic upgrade head
```

### 8. 安装前端依赖

```powershell
cd ..\frontend
npm install
```

### 9. 启动桌面版

```powershell
cd ..\scripts
.\dev-desktop.ps1
```

或在项目根目录：

```powershell
.\scripts\dev-desktop.ps1
```

桌面窗口会自动打开。首次启动会编译 Electron 主进程，可能需要 1–2 分钟。

---

## Linux 启动步骤

> 以下以 Ubuntu/Debian 为例，其他发行版使用对应的包管理器（如 Fedora 用 `dnf`，Arch 用 `pacman`）。

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip nodejs npm postgresql-15 redis-server git curl
```

验证：

```bash
python3.12 --version  # 应输出 Python 3.12.x
node --version          # 应输出 v18.x 或更高
```

### 2. 启动 PostgreSQL 和 Redis

```bash
sudo systemctl enable --now postgresql redis-server
```

验证：

```bash
sudo -u postgres pg_isready  # 应输出 "accepting connections"
redis-cli ping                # 应输出 PONG"
```

### 3. 安装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

重新登录或执行 `source ~/.bashrc` 后验证：

```bash
uv --version
```

### 4. 克隆项目并进入目录

```bash
git clone https://github.com/bzJing925/resonator-data-platform.git
cd resonator-data-platform
```

### 5. 配置环境变量

```bash
cp .env.example .env
```

用任意编辑器打开 `.env` 文件，修改 `POSTGRES_PASSWORD` 为安全密码。

### 6. 初始化数据库（首次运行）

```bash
sudo -u postgres createdb aln
cd backend
uv sync
uv run alembic upgrade head
```

### 7. 安装前端依赖

```bash
cd ../frontend
npm install
```

### 8. 启动桌面版

```bash
cd ..
./scripts/dev-desktop.sh
```

桌面窗口会自动打开。首次启动会编译 Electron 主进程，可能需要 1–2 分钟。

---

## 数据库初始化（首次运行）

首次运行桌面版前，必须完成数据库迁移。上述步骤已包含，此处单独说明：

```bash
cd backend
uv sync
uv run alembic upgrade head
```

此命令会在 PostgreSQL 中创建所有表结构。后续更新代码后，如果提示数据库结构不匹配，重新执行此命令即可。

---

## 常见问题

### Q: 提示 `uv: command not found`

A: uv 安装后可能没有自动加入 PATH。关闭终端重新打开，或手动执行：

```bash
# macOS/Linux
export PATH="$HOME/.local/bin:$PATH"

# Windows (PowerShell)
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
```

### Q: `npm install` 报错或卡住

A: 切换为国内 npm 镜像：

```bash
npm config set registry https://registry.npmmirror.com
npm install
```

### Q: `uv sync` 提示 Python 版本不匹配

A: 确保安装了 Python 3.12，并指定版本：

```bash
uv python install 3.12
uv sync --python 3.12
```

### Q: 启动桌面版后窗口白屏 / 无法连接后端

A: 检查：

1. PostgreSQL 是否运行：`pg_isready`
2. Redis 是否运行：`redis-cli ping`
3. `.env` 中的 `POSTGRES_PASSWORD` 是否正确
4. 数据库是否已初始化：`cd backend && uv run alembic upgrade head`

### Q: 提示 `ELECTRON_RUN_AS_NODE` 相关错误

A: 如果在 VS Code 或其他 Electron 编辑器的终端中运行，脚本已自动处理此环境变量。如果仍报错，尝试在外部系统终端中运行：

```bash
./scripts/dev-desktop.sh
```

### Q: 如何停止桌面版？

A: 关闭 Electron 窗口后，在终端中按 `Ctrl+C` 停止。如果后台仍有残留进程，可以：

```bash
# macOS/Linux
pkill -f electron
pkill -f uvicorn

# Windows (PowerShell)
Get-Process electron,node,python | Stop-Process -Force
```

### Q: 如何重新安装所有依赖？

A: 删除依赖缓存后重新安装：

```bash
# 后端
cd backend
rm -rf .venv uv.lock
uv sync

# 前端
cd ../frontend
rm -rf node_modules package-lock.json
npm install
```

---

## 附录：一键启动命令速查

| 操作 | macOS/Linux | Windows (PowerShell) |
|------|------------|---------------------|
| 启动桌面版 | `./scripts/dev-desktop.sh` | `.\scripts\dev-desktop.ps1` |
| 启动 Web 开发版 | `./scripts/dev-start.sh` | `.\scripts\dev-start.ps1` |
| 后端数据库迁移 | `cd backend && uv run alembic upgrade head` | `cd backend; uv run alembic upgrade head` |
| 构建安装包 | `python build.py` | `python build.py` |

