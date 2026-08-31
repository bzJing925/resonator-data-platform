# ALN Resonator Data Platform — 桌面版

将 FastAPI + React 平台打包为原生桌面应用：双击 `.exe` / `.dmg` 即可启动，无需手动运行后端或打开浏览器。

## 1. 架构

- **Electron**：提供原生窗口、菜单、生命周期管理。
- **FastAPI 后端**：PyInstaller 打包为独立可执行文件，随 Electron 资源一起分发。
- **前端**：Vite 构建为静态文件，由后端 `StaticFiles` 在 `http://127.0.0.1:8000` 提供。
- **启动流程**：
  1. Electron 主进程显示 splash 画面；
  2. 检查 `127.0.0.1:8000`，若未运行则启动后端可执行文件；
  3. 等待 health 接口就绪；
  4. 窗口加载本地页面。

## 2. 环境要求

- Node.js 18+ + npm
- Python 3.11+（仅构建时需要）
- PostgreSQL 15 + Redis（运行时需要，桌面包不自带数据库）
- macOS 12+ / Windows 10+ / Linux

## 3. 一键构建

```bash
python build.py
```

输出：

- macOS：`frontend/release/ALN Resonator Data Platform-0.1.1-arm64.dmg`
- Windows：`frontend/release/ALN Resonator Data Platform Setup 0.1.1.exe`
- Windows 便携版：`frontend/release/ALN Resonator Data Platform 0.1.1.exe`

仅构建 macOS：

```bash
python build.py --target mac
```

仅构建 Windows：

```bash
python build.py --target win
```

跳过 PyInstaller（用于前端调试）：

```bash
python build.py --skip-backend
```

## 4. 开发调试

启动 Vite 开发服务器 + Electron（开发模式直接调用项目根目录 `python main.py`）：

```bash
cd frontend
npm run electron:dev
```

预览生产构建（不打包）：

```bash
cd frontend
npm run electron:preview
```

## 5. 数据库说明

当前桌面包**不包含 PostgreSQL 与 Redis**。用户需要预先安装并确保服务运行：

- macOS：`brew install postgresql@15 redis`
- Windows：下载 PostgreSQL 安装包 + Memurai/Redis
- 或使用 Docker：`docker-compose up -d postgres redis`

数据库初始化（首次运行）：

```bash
cd backend
alembic upgrade head
```

## 6. 跨平台兼容性

| 平台 | 构建机 | 输出 | 签名 |
|---|---|---|---|
| macOS | macOS | `.dmg` (arm64/x64) | 未签名，用户首次需右键“打开” |
| Windows | macOS/Windows | `.exe` (nsis/portable) | 未签名，可能触发 SmartScreen |
| Linux | Linux | `.AppImage` | 未签名 |

> 若需 macOS 公证或 Windows 代码签名，请在 `frontend/package.json` 的 `build` 字段中配置证书信息。

## 7. 项目结构变更

```
frontend/
  electron/
    main.js          # Electron 主进程
    preload.js       # 安全桥接
    splash.html      # 启动画面
  build-resources/
    make_icons.py    # 图标生成脚本
    icon.{png,ico,icns}
  build/backend/
    aln-backend      # PyInstaller 后端可执行文件
backend/
  build_backend.py   # PyInstaller 打包脚本
build.py             # 一键构建入口
```

## 8. 常见问题

**Q: Electron-builder 下载二进制超时？**
A: `build.py` 已设置国内镜像；如仍失败，可手动设置：

```bash
export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
export ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/
```

**Q: 后端启动失败？**
A: 检查 `frontend/release/mac-arm64/ALN Resonator Data Platform.app/Contents/Resources/backend/aln-backend` 是否存在；在终端运行该文件查看错误。

**Q: 如何关闭后端调试日志？**
A: 生产环境下后端 stdout/stderr 已重定向到 Electron 主进程控制台；可在 `electron/main.js` 中调整 `stdio` 参数。
