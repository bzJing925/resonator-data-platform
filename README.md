# 谐振器测试数据平台

## 启动桌面版

### 开发模式

需要 Node.js + Python 环境。

```bash
# 1. 安装前端依赖
cd frontend
npm install

# 2. 安装后端依赖（桌面版用 SQLite，无需 Docker）
cd ../backend
uv sync

# 3. 启动桌面版（Electron 会自动拉起后端）
cd ../frontend
npm run electron:dev
```

### 构建安装包

```bash
# 一键构建前端 + PyInstaller 后端 + Electron 安装包
python build.py

# 安装包输出目录
frontend/release/
```

构建完成后，按平台安装：

- **macOS**：打开 `frontend/release/ALN Resonator Data Platform-x.x.x.dmg`，把应用拖到 Applications。
- **Windows**：运行 `frontend/release/ALN Resonator Data Platform x.x.x.exe`。

安装后双击应用图标即可启动。
