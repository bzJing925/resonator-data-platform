# Windows 桌面安装包（可自定义安装）设计与交接文档

> **交接说明（先读这段）**：本文档是跨机器交接产物。任务在 2026-07-10 的会话中完成调研，
> 尚未进入实现。在另一台电脑上继续时，新会话从本文档恢复全部上下文：
> 先读「任务目标」「关键发现」「设计方案」，确认设计无误后，
> 按 Superpowers 流程调用 writing-plans 写实现计划并执行。
> 已完成的前置任务（Explore X 轴结构参数功能、README 简化）已合入 main 并推送，无需重做。

## 任务目标

将 ALN 谐振器数据平台的 **Windows 桌面版**打包成安装包，使其可以在**其他 Windows 电脑上自定义安装**（选择安装路径、创建快捷方式等），安装后双击图标即可运行（桌面模式：SQLite + 本地队列，无需 PostgreSQL / Redis / Docker）。

## 用户已确认的决定

1. **打包机器：在 Windows 电脑上打包**（头脑风暴问题 1，用户选 A）。
   备选：GitHub Actions 的 `windows-latest` runner（见「方案 B」）。

## 关键发现（调研结论，2026-07-10）

### 1. NSIS 自定义安装配置已就绪，无需改代码

[frontend/package.json](../../frontend/package.json) 的 `build.win` / `build.nsis` 段已经满足需求：

```json
"win": { "target": [{ "target": "nsis", "arch": ["x64"] }, { "target": "portable", "arch": ["x64"] }] },
"nsis": {
  "oneClick": false,                              // 向导式安装（非一键静默）
  "allowToChangeInstallationDirectory": true,     // 用户可选安装路径 ← 核心需求
  "createDesktopShortcut": true,
  "createStartMenuShortcut": true,
  "shortcutName": "ALN Data Platform"
}
```

`oneClick: false` 时 electron-builder 生成 NSIS 向导安装器，安装过程中用户可改安装目录、选择是否创建快捷方式。同时会额外产出一个便携版 exe（免安装直接运行）。若不需要便携版，可从 `win.target` 中删除 `portable` 项（可选优化，非必须）。

### 2. 构建入口已存在

根目录 [build.py](../../build.py)：`python build.py --target win` 依次执行

1. `npm run build`（frontend，产出 `dist/` + `electron-dist/`）
2. `backend/build_backend.py`（PyInstaller 打包后端为 `frontend/build/backend/aln-backend.exe`，onefile 模式）
3. `npx electron-builder --win`（产出 NSIS 安装包 + 便携版到 `frontend/release/`）

当前版本号：`frontend/package.json` = **0.2.0**（注意：`backend/pyproject.toml` 和根 `pyproject.toml` 仍为 0.1.1，版本不一致，发布前建议统一）。

### 3. feat/deploy 分支有一个 Windows 打包的关键修复未合入 main

`feat/deploy` 相对 main：**ahead 2 / behind 10**（merge-base = `7be6cd9`）。

必须保留的 2 个提交：

- `2c5420b fix(deploy): build.py 使用 backend/.venv 的 Python 运行 PyInstaller`
  **Windows 打包必须**。否则 `build.py` 用系统 Python 跑 PyInstaller，缺项目依赖导致 hidden import 失败。
- `e8407b3 ci(deploy): 添加 GitHub Actions 桌面安装包构建工作流`
  新增 `.github/workflows/build-desktop.yml`，可在 `windows-latest` / `macos-latest` 上云端构建（当前触发条件是 push 到 `feat/deploy` 或手动 `workflow_dispatch`）。

main 领先的 10 个提交（README 简化、Explore X 轴功能、desktop-quickstart.md 等）是文档/前端变更，**合并 feat/deploy 时预期冲突点**：

- `README.md`：deploy 上是旧的全文版本，main 上是精简桌面版——**保留 main 版本**。
- `frontend/src/pages/Explore.tsx`：deploy 上是旧的字段列表，main 上是新的 mapping 结构参数版本——**保留 main 版本**。
- `docs/desktop-quickstart.md`：main 上新增，deploy 上不存在——**保留 main 版本**。
- `build.py`：两边都改过（main 仅格式化，deploy 加了 venv Python 逻辑）——**取 deploy 的 venv 逻辑 + main 的格式化**。

> 注：本机 feat/deploy worktree 在 `/Users/jingbozuo/Projects/aln-data-deploy`（路径是本机的，换机后按需要重建 worktree 或直接在 main 上工作）。

### 4. 桌面模式架构（目标机器零依赖）

- `ALN_DESKTOP_MODE=true` 时后端强制使用 SQLite（`~/.aln-data/aln-data.db`），数据目录 `~/.aln-data/`。
- 任务队列用本地线程队列（`app/workers/local_queue.py`），无需 Redis/Celery。
- `backend/app/desktop_setup.py` 启动时自动建表 + schema 版本迁移（当前 v3）。
- Electron 主进程 [frontend/electron/main.ts](../../frontend/electron/main.ts)：动态找空闲端口 → 拉起 PyInstaller 后端 → 轮询 health → 加载前端；崩溃自动重启，退出时清理子进程。
- **README-desktop.md 第 5 节「数据库说明」已过时**（还写着需要用户预装 PostgreSQL + Redis），实现计划里应顺手修正。

### 5. Git push 的坑（换机后也会遇到）

全局 `~/.gitconfig` 有 URL 重写规则：

```
url.https://ghproxy.net/https://github.com/.insteadof https://github.com/
```

导致 push 时实际走 ghproxy 镜像 → GitHub 凭据不生效 → `No anonymous write access`。
**绕过方法**（本会话验证过有效）：

```bash
git config --global --unset url.https://ghproxy.net/https://github.com/.insteadof \
  && git push origin <branch>; \
git config --global url.https://ghproxy.net/https://github.com/.insteadof https://github.com/
```

（临时取消重写 → push → 恢复规则。Windows 上若在 Git Bash/PowerShell 执行，分号换行即可。）

## 设计方案

### 方案 A（用户已选）：Windows 机器本地打包

在 Windows 开发机上执行完整构建链。

- 前置：Python 3.12+、Node 18+、uv；`backend` 下 `uv sync`，`frontend` 下 `npm install`。
- 构建：仓库根目录 `python build.py --target win`（依赖第 3 条的 venv 修复已合入）。
- 产出：`frontend/release/ALN Resonator Data Platform Setup 0.2.0.exe`（NSIS 向导安装包）+ 便携版 exe。
- 优点：最稳、产物可直接本机先验证；缺点：需要一台装好工具链的 Windows 机。

### 方案 B（备选）：GitHub Actions 云端打包

利用 `e8407b3` 已加好的 `.github/workflows/build-desktop.yml`：

- 需要先把 workflow 合入 main（或调整触发分支）并推送。
- Actions 页面手动触发或 push 触发，windows-latest runner 执行 `python build.py --target win`，产物以 artifact 上传，下载即可分发。
- 优点：不依赖本地 Windows 机；缺点：首次跑通可能要调试 runner 环境（uv/npm 缓存、镜像下载），无法即时交互排错。

### 方案 C（不推荐）：只分发便携版 exe

`win.target` 中已有 `portable`，免安装双击即用。但无安装向导、无路径选择、无快捷方式注册，**不满足"自定义安装"需求**，仅可作为附带产物。

### 推荐结论

用户已选方案 A。实现内容（无新代码，主要是集成与验证）：

1. 将 `feat/deploy` 的 2 个提交合入 main（按第 3 条的冲突策略）。
2. 修正 `README-desktop.md` 过时的数据库章节（SQLite，无需 PostgreSQL/Redis）。
3. （可选）统一版本号到 0.2.0；可选删除 portable target。
4. 在 Windows 机上跑 `python build.py --target win`。
5. 在干净 Windows 机上验证（见下）。

## 验证清单（干净 Windows 机）

1. 运行 `... Setup 0.2.0.exe` → 出现安装向导 → **能修改安装路径**（如 `D:\ALN`）→ 安装完成。
2. 桌面与开始菜单生成快捷方式，双击启动。
3. 首次启动：splash → 后端就绪（约 10–20s）→ 主界面打开。
4. 数据目录 `%USERPROFILE%\.aln-data\` 生成（SQLite db + files/ + logs/）。
5. 上传一个测试 zip → 任务跑完 → 数据分析页出图。
6. 控制面板/设置里可正常卸载。
7. 预期坑：安装包**未签名**，Windows SmartScreen 会拦截 → 点「更多信息 → 仍要运行」即可（文档里需告知最终用户）。

## 参考文件

- [frontend/package.json](../../frontend/package.json) — electron-builder / NSIS 配置
- [build.py](../../build.py) — 一键构建入口
- [backend/build_backend.py](../../backend/build_backend.py) — PyInstaller 后端打包
- [frontend/electron/main.ts](../../frontend/electron/main.ts) — 主进程（后端生命周期）
- [README-desktop.md](../../README-desktop.md) — 桌面版说明（第 5 节待修正）
- `.github/workflows/build-desktop.yml` — 云端构建（在 feat/deploy 上，待合入）
