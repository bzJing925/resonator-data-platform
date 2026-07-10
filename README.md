# 谐振器测试数据平台

## 启动桌面版

### 开发模式

需要 Node.js + Python 环境，并先安装好前端依赖（`cd frontend && npm install`）。

```bash
cd /.../aln-data-master
./scripts/dev-desktop.sh
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
