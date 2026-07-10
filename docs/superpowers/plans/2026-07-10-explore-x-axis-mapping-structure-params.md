# Explore 页 X 轴使用 Mapping 结构参数 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 Explore 页的 X 轴可选标签改为仅展示由 mapping 解析到 devices 表的器件结构参数。

**架构：** 仅修改 `frontend/src/pages/Explore.jsx` 中的 `X_AXIS_FIELD_NAMES` 常量与 `AXIS_FIELD_LABELS` 标签；选择器、状态归一化、图表渲染逻辑全部复用现有实现。

**技术栈：** React 18 + Vite 5；本改动纯前端，无需后端变更。

---

## 文件清单

| 文件 | 职责 |
|------|------|
| `frontend/src/pages/Explore.jsx` | 包含 X 轴字段白名单 `X_AXIS_FIELD_NAMES`、字段显示标签 `AXIS_FIELD_LABELS`、选择器与状态管理。本次唯一修改文件。 |
| `docs/superpowers/specs/2026-07-10-explore-x-axis-mapping-structure-params-design.md` | 设计规格（已存在，实施前阅读）。 |

## 任务 1：确认当前 X 轴字段列表

**文件：**
- 读取：`frontend/src/pages/Explore.jsx:72-100`

- [ ] **步骤 1：读取现有常量**

  打开 `frontend/src/pages/Explore.jsx`，确认当前存在以下常量：

  ```js
  const X_AXIS_FIELD_NAMES = [
    'eg', 'fl', 'ag', 'batch_no', 'wafer', 'pf', 'x', 'y', 'fs_ghz',
  ];
  const X_AXIS_FIELD_SET = new Set(X_AXIS_FIELD_NAMES);
  const AXIS_FIELD_LABELS = {
    eg: 'EG',
    fl: 'FL',
    ag: 'AG',
    batch_no: '批次',
    wafer: 'Wafer',
    pf: 'P/F',
    x: 'X',
    y: 'Y',
    fs_ghz: 'fs',
    // ...
  };
  ```

- [ ] **步骤 2：确认 `EXPLORE_INITIAL_STATE.xFields`**

  在同文件第 169-182 行确认初始状态：

  ```js
  const EXPLORE_INITIAL_STATE = {
    chartType: 'scatter',
    xFields: ['eg'],
    // ...
  };
  ```

  `eg` 仍在新的结构参数字段列表中，因此初始状态无需修改。

## 任务 2：替换 X 轴字段白名单

**文件：**
- 修改：`frontend/src/pages/Explore.jsx:72-80`

- [ ] **步骤 1：修改 `X_AXIS_FIELD_NAMES`**

  将第 74-76 行：

  ```js
  const X_AXIS_FIELD_NAMES = [
    'eg', 'fl', 'ag', 'batch_no', 'wafer', 'pf', 'x', 'y', 'fs_ghz',
  ];
  ```

  替换为：

  ```js
  // Device structure parameters derived from the mapping (对照表).
  // These are the only fields users may select as the X axis in Explore.
  const X_AXIS_FIELD_NAMES = [
    'eg', 'fl', 'ag', 'pf', 'area_um2', 'area_n',
  ];
  ```

- [ ] **步骤 2：确认 `X_AXIS_FIELD_SET` 自动更新**

  第 80 行 `const X_AXIS_FIELD_SET = new Set(X_AXIS_FIELD_NAMES);` 不需要修改，它会自动基于新的列表生成。

- [ ] **步骤 3：Commit**

  ```bash
  git add frontend/src/pages/Explore.jsx
  git commit -m "feat(frontend): Explore X-axis uses only mapping structure params

  Replace hardcoded X_AXIS_FIELD_NAMES with the device structure
  parameters derived from the mapping table.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
  ```

## 任务 3：补充结构参数字段显示标签

**文件：**
- 修改：`frontend/src/pages/Explore.jsx:82-100`

- [ ] **步骤 1：添加 `area_um2` 与 `area_n` 标签**

  在 `AXIS_FIELD_LABELS` 对象中，为新增字段补充中文/英文标签。例如将第 82-100 行扩展为：

  ```js
  const AXIS_FIELD_LABELS = {
    eg: 'EG',
    fl: 'FL',
    ag: 'AG',
    pf: 'P/F',
    area_um2: '面积 (μm²)',
    area_n: '区域编号',
    batch_no: '批次',
    wafer: 'Wafer',
    x: 'X',
    y: 'Y',
    fs_ghz: 'fs',
    fp_ghz: 'fp',
    qs: 'Qs',
    qp: 'Qp',
    k2eff_pct: 'k²eff',
    zs_ohm: 'Zs',
    zp_ohm: 'Zp',
    dbqs: 'dBQs',
    dbqp: 'dBQp',
  };
  ```

  > 若 `area_um2` 与 `area_n` 已存在，则跳过本步骤。

- [ ] **步骤 2：Commit**

  ```bash
  git add frontend/src/pages/Explore.jsx
  git commit -m "feat(frontend): add display labels for area_um2 and area_n

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
  ```

## 任务 4：验证前端构建无错误

**文件：**
- 运行：`frontend/package.json` 脚本

- [ ] **步骤 1：安装依赖**

  ```bash
  cd frontend
  npm install
  ```

  预期：依赖安装完成，无报错。

- [ ] **步骤 2：运行生产构建**

  ```bash
  npm run build
  ```

  预期：Vite 构建成功，终端输出类似：

  ```
  dist/                     0.05 kB │ gzip: 0.07 kB
  dist/assets/index-xxx.js  xxx kB │ gzip: xxx kB
  ✓ built in x.xx
  ```

  无 TypeScript/ESLint 错误（项目未配置类型检查，以 Vite 能成功构建为准）。

- [ ] **步骤 3：Commit（如有构建产物更新）**

  本改动不产生新的 `dist/` 提交目标，通常无需额外 commit。

## 任务 5：手动验证 X 轴选项

**文件：**
- 运行：`frontend/src/pages/Explore.jsx`

- [ ] **步骤 1：启动开发服务器**

  ```bash
  cd frontend
  npm run dev
  ```

  预期：Vite dev server 在 `http://localhost:5173` 启动。

- [ ] **步骤 2：打开 Explore 页**

  浏览器访问 `http://localhost:5173/explore`（或对应路由），确保后端 API 可访问（参考 `CLAUDE.md` 启动后端）。

- [ ] **步骤 3：检查 X 轴下拉框**

  在 X 轴字段选择器中，确认选项仅包含：

  - EG
  - FL
  - AG
  - P/F
  - 面积 (μm²)
  - 区域编号

  不应再出现：批次、Wafer、X、Y、fs。

- [ ] **步骤 4：验证图表渲染**

  1. 选择 X 轴为 `EG`，Y 轴为 `Qs`，确认散点图正常渲染。
  2. 切换 X 轴为 `面积 (μm²)`，确认图表更新且无报错。
  3. 多选 X 轴字段（如 EG + 面积），确认多子图视图工作正常。

- [ ] **步骤 5：清理验证环境**

  停止 `npm run dev`（Ctrl+C）。

## 自检

- [ ] 规格覆盖度：所有需求（结构参数字段、仅 Explore 页、完全替换、schema 判定）均已在任务 2-3 中实现。
- [ ] 占位符扫描：计划无 TODO、无"适当错误处理"等模糊描述。
- [ ] 类型一致性：未新增类型；字段名与 `devices` 表、`EXPORT_FIELDS`、设计规格一致。
