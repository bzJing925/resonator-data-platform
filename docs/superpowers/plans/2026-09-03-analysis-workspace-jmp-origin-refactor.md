# 数据分析工作区 JMP / Origin 交互改造计划

- 日期：2026-09-03
- 范围：数据分析页面、图表渲染、Worksheet、Electron 桌面交互
- 状态：待实施
- 预计工程量：20–28 人日

## 1. 目标

把当前数据分析页面升级为接近 JMP / Origin 使用体验的交互式工作区，建立以下统一工作流：

```text
底层 Dataset
  ↕
数据表 ↔ Selection State ↔ 图表
  ↕             ↕
编辑/删除      样式/筛选
  ↘             ↙
       Undo / Redo
```

本轮重点解决：

1. 补齐 Electron 桌面右键菜单和键盘快捷键。
2. 为散点图、箱形图、小提琴图、蜂群图、折线图实现真实可用的框选、联动和删除。
3. 明确数据表 `+` 的语义，并支持表格创建、复制、重命名、关闭和恢复。
4. 修复 Worksheet 列对齐，增加鼠标拖动和键盘范围选择。
5. 按参考图实现“结构类型 X + 面积 Z + 多个 Y 指标纵向排列”的 JMP 式图表布局。
6. 增加与现有产品风格一致的 Origin 式数据点样式编辑器。
7. 在大数据量下保证缩放、平移、框选和表格滚动流畅。

## 2. 当前架构判断

### 2.1 无需整体重写前端

当前产品已经使用 Electron 封装 React 前端，本质上已经是桌面应用。体验仍像网页的主要原因不是 Electron 能力不足，而是：

- Electron 主进程只提供基础编辑菜单，没有数据分析上下文菜单和命令 IPC。
- Delete/Backspace 等事件绑定在局部 React 容器上，依赖焦点且行为不稳定。
- 图表、表格和桌面菜单没有共享统一命令。
- Plotly 的默认交互模式及当前 trace 组织方式无法覆盖五类图表的原始点选择。

因此保留 Electron、React 和现阶段的 Plotly，集中重构数据分析模块。只有性能基准证明 Plotly 在目标数据规模下无法满足要求时，才评估替换原始点渲染层。

### 2.2 已确认的现状问题

- `frontend/electron/main.ts` 只有基础剪切、复制、粘贴菜单。
- `frontend/electron/preload.ts` 未暴露数据分析命令相关 IPC。
- `frontend/src/pages/Explore.tsx` 的键盘删除依赖工作区焦点。
- `frontend/src/components/Charts.tsx` 没有为所有图形建立独立的原始点选择层。
- 箱形图和小提琴图的统计几何本身不能可靠映射每条原始数据。
- 折线图会把多条数据聚合为均值点，一个 marker 可能对应多个底层行。
- Plotly 局部更新错误目前存在被静默忽略的路径，不利于发现选择失效。
- 删除后没有显式触发相关 Y 轴重新计算范围。
- Worksheet 表头和内容分别使用独立 Grid 计算列宽，容易发生错位。
- Worksheet 目前支持点击、组合键和 Shift 范围选择，但没有 pointer drag selection。
- Workspace Tab 的 `+` 当前更接近复制数据集，缺少关闭、删除、重命名等生命周期。
- 当前图表仍是通用 X/Y/Z 组合，没有完整实现参考图中的结构类型、面积和多指标层级。
- 数据点样式仍主要由固定调色板决定，没有稳定的 style override 模型。

## 3. 功能交叉关系与依赖

### 3.1 核心依赖链

```text
规范化 structureType / Area_reality
                ↓
Dataset Registry + 稳定 rowId
                ↓
Selection State + Command + Undo/Redo
                ↓
┌────────────────┼─────────────────┐
Worksheet        Chart Scene       Electron Commands
拖选/删除        原始点选择层       菜单/快捷键
└────────────────┼─────────────────┘
                ↓
图表 ↔ 表格联动 / 删除 / Y 轴 autorange
                ↓
Origin 式样式覆盖
```

### 3.2 交叉程度

| 问题 | 交叉程度 | 关键依赖 |
|---|---:|---|
| Electron 右键和快捷键 | 高 | 统一 Command、Selection、activeSurface |
| 五类图表框选和删除 | 最高 | rowId、原始点选择层、Chart Scene、Command |
| 数据表新增和关闭 | 中高 | Dataset Registry、图表数据源引用、Undo |
| 表格对齐和拖动选择 | 高 | ColumnModel、Selection、虚拟滚动 |
| JMP 式绘图 | 最高 | 规范字段、分组规则、Chart Scene |
| Origin 式样式编辑 | 高 | Selection、稳定 traceKey、Undo、局部更新 |

### 3.3 不能独立先做的功能

- 不能先给五类图表分别添加临时框选逻辑，否则会产生多套 rowId 映射。
- 不能先增加样式面板，否则样式无法稳定关联选择、数据行和图表 trace。
- 不能只给 Electron 增加快捷键，否则按钮、右键和键盘会走不同删除路径。
- 不能只修 Tab UI，否则关闭数据表时无法正确处理图表引用和撤销。
- 不能单纯增加缓存解决性能问题，必须先拆分底层数据、派生图表和高频交互状态。

## 4. 目标数据与命令模型

```ts
type DatasetId = string;
type RowId = string;

interface AnalysisRow {
  rowId: RowId;
  datasetId: DatasetId;
  structureType: string;
  areaReality: number | null;
  values: Record<string, unknown>;
}

interface AnalysisDataset {
  id: DatasetId;
  name: string;
  revision: number;
  rows: AnalysisRow[];
  columns: WorksheetColumn[];
}

interface SelectionState {
  datasetId: DatasetId | null;
  rowIds: Set<RowId>;
  anchorRowId: RowId | null;
  activeSurface: "chart" | "worksheet" | null;
}

interface PointStyleOverride {
  color?: string;
  size?: number;
  symbol?: string;
  opacity?: number;
  outlineColor?: string;
  outlineWidth?: number;
}

interface AnalysisCommand {
  execute(): void;
  undo(): void;
  redo(): void;
}
```

设计约束：

- `rowId` 创建后保持稳定，筛选、排序、切换 Tab 和重新绘图不得改变。
- 原始数据和 view state 分离；选择、缩放、面板开关不得修改 Dataset。
- 图表点通过 `customdata.rowId` 反查底层行。
- 删除通过 Command 修改 Dataset，不允许仅隐藏 marker。
- Undo/Redo 优先记录补丁或受影响行，禁止为每次操作深拷贝大型 Dataset。
- 高频鼠标移动和缩放状态保留在图表/表格渲染层，手势完成后再提交共享状态。

## 5. 分阶段实施计划

### Phase 0：建立真实交互验收基线

**目的：** 先稳定复现问题，防止 mock Plotly 的单元测试掩盖真实交互失败。

**主要文件：**

- 修改：`frontend/src/components/Charts.test.tsx`
- 新增：`frontend/e2e/analysis/*.spec.ts`
- 检查：分析查询接口及其 schema

**任务：**

- [ ] 准备覆盖五种图形、多个 Y 指标、多个结构类型和面积的固定 fixture。
- [ ] 添加真实 Plotly 集成测试，减少关键路径上的全量 mock。
- [ ] 建立 Playwright Electron 测试入口。
- [ ] 记录 1 万、5 万、10 万行数据下的缩放、平移、框选、刷新耗时和帧率。
- [ ] 明确 `structureType`、`Area_reality` 的权威数据来源、类型和缺失值规则。

**完成标准：** 六个问题均可稳定复现，后续阶段有可执行的回归测试。

---

### Phase 1：统一 Dataset、Selection 和 Command

**目的：** 为表格、图表、Electron 菜单、样式编辑和 Undo/Redo 建立共同基础。

**主要文件：**

- 修改：`frontend/src/features/analysis/workspace.tsx`
- 建议新增：
  - `frontend/src/features/analysis/model/dataset.ts`
  - `frontend/src/features/analysis/model/selection.ts`
  - `frontend/src/features/analysis/model/commands.ts`
  - `frontend/src/features/analysis/model/history.ts`
  - `frontend/src/features/analysis/model/selectors.ts`

**任务：**

- [ ] 建立 Dataset Registry，区分数据表实例、图表页面和数据源引用。
- [ ] 规范化 `structureType`、`Area_reality`，保留原始字段名映射。
- [ ] 确保所有行有稳定且唯一的 `rowId`。
- [ ] 抽离单一 Selection State，移除图表和表格各自维护的选择集合。
- [ ] 实现 `selectRows`、`deleteRows`、`updateRows`、`addDataset`、`closeDataset`、`duplicateDataset`、`updatePointStyle` 命令。
- [ ] 建立 Undo/Redo 历史栈和批量命令边界。
- [ ] 添加 reducer、selector、删除和撤销的单元测试。

**完成标准：** 所有选择和数据修改都从统一模型与命令入口执行。

---

### Phase 2：重构 Chart Scene，实现 JMP 式数据组织

**目的：** 先修正图表数据语义和层级，再增加选择交互。

**目标布局：**

```text
Y 指标 1：原始点 + 分布 + 汇总连线
Y 指标 2：原始点 + 分布 + 汇总连线
Y 指标 3：原始点 + 分布 + 汇总连线
────────────────────────────────
X 外层：structureType
X 内层/Z：Area_reality
```

**主要文件：**

- 修改：`frontend/src/components/Charts.tsx`
- 建议新增：
  - `frontend/src/features/analysis/chart/chartScene.ts`
  - `frontend/src/features/analysis/chart/buildRawPointTraces.ts`
  - `frontend/src/features/analysis/chart/buildDistributionTraces.ts`
  - `frontend/src/features/analysis/chart/buildSummaryTraces.ts`
  - `frontend/src/features/analysis/chart/axisLayout.ts`

**任务：**

- [ ] 建立纯函数 `Chart Scene Model`，把数据转换与 React/Plotly 渲染分开。
- [ ] 使用稳定数字槽位或 Plotly multicategory 轴表示结构类型和面积层级。
- [ ] 多个 Y 指标纵向排列并共享 X 轴。
- [ ] 将图形拆分为原始点层、统计图形层、汇总连线层。
- [ ] 为每个原始点写入 `customdata.rowId`。
- [ ] 建立稳定 `traceKey`，选择或样式变化时不重建无关 trace。
- [ ] 保持筛选后的数据、表格数据和图表 Scene 使用同一 Dataset revision。

**五类图形策略：**

| 图形 | 展示层 | 选择层 |
|---|---|---|
| 散点图 | 原始散点 | 散点本身 |
| 箱形图 | 箱体、须、异常点 | 独立原始点覆盖层 |
| 小提琴图 | 密度轮廓 | 独立原始点覆盖层 |
| 蜂群图 | 排布后的原始点 | 蜂群点本身 |
| 折线图 | 汇总线和汇总点 | 独立原始点覆盖层 |

**完成标准：** 实现参考图的 X/Z/Y 组织方式，且每个原始点都能唯一映射到底层行。

---

### Phase 3：实现图表框选、联动删除和纵轴自动调整

**主要文件：**

- 修改：`frontend/src/pages/Explore.tsx`
- 修改：`frontend/src/components/Charts.tsx`
- 修改：Phase 1/2 新增的 selection、command、scene 文件

**任务：**

- [ ] 提供框选、套索、平移、缩放工具并设置明确的默认模式。
- [ ] 处理 `plotly_selected`、`plotly_click`、`plotly_deselect`。
- [ ] 根据 `customdata.rowId` 更新统一 Selection State。
- [ ] 使用专门高亮 trace 或局部 `restyle`，避免选择一个点时重建全图。
- [ ] 移除静默吞掉关键 Plotly 更新错误的逻辑，开发环境输出可诊断错误。
- [ ] Delete/Backspace 调用统一 `deleteRows` 命令。
- [ ] 删除后清理失效选择，只重建受影响的 trace 和指标。
- [ ] 删除后显式执行相关 Y 轴 autorange，保留 X/Z 分组结构。
- [ ] 验证 Undo 后数据、图表、表格和选择状态同时恢复。

**完成标准：** 五类图形均可框选原始点，图表和表格双向联动，删除作用于底层 Dataset。

---

### Phase 4：修复 Worksheet 与 Tab 生命周期

#### 4.1 Worksheet 列布局

- [ ] 新增共享 `ColumnModel`，表头和数据行使用同一列宽数组。
- [ ] 同步横向滚动位置。
- [ ] 行号、选择列和数据列使用同一布局定义。
- [ ] 保留行虚拟化；按实测决定是否增加列虚拟化。

#### 4.2 鼠标和键盘选择

- [ ] 支持单击、Shift 连续范围、Ctrl/Cmd 增减选择。
- [ ] 支持 pointer down/move/up 拖动选择连续多行。
- [ ] 拖出可视区时自动滚动。
- [ ] 支持方向键、Page Up/Down、Home/End。
- [ ] 支持 Ctrl/Cmd+A 和 Delete/Backspace。
- [ ] 将拖动中的临时选择保留在表格组件内部，手势结束后再提交共享状态。

#### 4.3 数据表 Tab 生命周期

- [ ] 将 `+` 定义为“新建空白数据表”或打开新建菜单。
- [ ] 把“复制数据表”拆成独立命令。
- [ ] 增加重命名、复制、关闭和右键菜单。
- [ ] 关闭被图表引用的数据表时，要求切换数据源或解除引用。
- [ ] 关闭数据表进入 Undo 历史，可恢复。
- [ ] 至少保留一个图表 Tab。

**主要文件：**

- `frontend/src/features/analysis/WorksheetGrid.tsx`
- `frontend/src/features/analysis/WorkspaceTabs.tsx`
- `frontend/src/features/analysis/workspace.tsx`
- `frontend/src/styles.css`

**完成标准：** 表格严格对齐，支持连续拖选和批量删除，数据表可新建、复制、重命名、关闭和恢复。

---

### Phase 5：增加 Origin 式数据点样式编辑器

**主要文件：**

- 新增：`frontend/src/features/analysis/PointStyleInspector.tsx`
- 修改：`frontend/src/components/Charts.tsx`
- 修改：`frontend/src/features/analysis/workspace.tsx`
- 修改：`frontend/src/styles.css`

**任务：**

- [ ] 在现有右侧面板风格中增加“数据点样式”Inspector。
- [ ] 支持填充颜色、点大小、形状、透明度、描边颜色、描边宽度。
- [ ] 支持作用范围：选中点、当前分组、当前 Series、当前指标。
- [ ] 使用稀疏 style override，不复制 Dataset。
- [ ] 样式修改接入 Undo/Redo。
- [ ] 使用 Plotly 局部更新同步样式。
- [ ] 在筛选、切换 Tab、全屏切换后保留样式。

**完成标准：** 可对选中点或分组批量编辑样式，且不会触发整张图表重建。

---

### Phase 6：补齐 Electron 桌面菜单和快捷键

**目的：** 让应用具备稳定的桌面操作体验，但不重写前端。

**主要文件：**

- `frontend/electron/main.ts`
- `frontend/electron/preload.ts`
- Electron API 类型声明
- `frontend/src/pages/Explore.tsx`
- 建议新增：`frontend/src/features/analysis/commandBridge.ts`

**任务：**

- [ ] 在主进程注册分析工作区菜单和快捷键。
- [ ] 通过受限 preload IPC 暴露命令，不开放 Node 直通。
- [ ] 为图表和表格增加桌面上下文菜单。
- [ ] 提供删除所选数据、撤销、重做、全选、复制、导出所选数据、编辑样式、坐标轴自动调整。
- [ ] 支持 Delete/Backspace、Cmd/Ctrl+A、Cmd/Ctrl+Z、Cmd/Ctrl+Shift+Z、Cmd/Ctrl+C。
- [ ] 使用 `activeSurface` 判断命令作用于图表还是 Worksheet。
- [ ] 保证按钮、右键菜单、应用菜单和快捷键调用同一 Command。

**完成标准：** 快捷键不再依赖局部 DOM 焦点，图表和表格均能调用上下文相关的桌面菜单。

---

### Phase 7：性能优化、全屏验证和发布验收

**性能原则：** 只针对基准测试确认的瓶颈优化，不使用 loading 或 debounce 掩盖问题。

**任务：**

- [ ] 按 dataset revision、字段、分组和图形类型缓存派生 Scene。
- [ ] 缩放和平移保持在 Plotly 内部，不写入 React 全局状态。
- [ ] 选择变化只更新选择/高亮层。
- [ ] 删除只重算受影响的指标和分组。
- [ ] Worksheet 使用虚拟滚动和行级订阅，避免全表刷新。
- [ ] 大数据散点优先使用 `ScatterGL`。
- [ ] 超大数据使用 viewport LOD 或视觉层降采样，同时保留原始 rowId 选择映射。
- [ ] 仅在数据转换被确认是 CPU 瓶颈时引入 Web Worker。
- [ ] 全屏切换使用 `ResizeObserver` 和 Plotly resize，不重建 Dataset 或全部 trace。
- [ ] 验证全屏退出后筛选、当前 Tab、选择、样式和图表配置不丢失。
- [ ] 运行 1 万、5 万、10 万行的缩放、平移、框选、批量删除基准。

**完成标准：**

- 缩放和平移不触发整页 React 重渲染。
- 目标硬件和验收数据集下，连续交互尽量稳定在 60 FPS。
- 全屏时隐藏左侧主导航，保留底部 Tab，配置和筛选使用可收起面板。

## 6. 技术栈建议

### 保留

- **Electron：** 已具备桌面容器能力，补充菜单、快捷键、上下文菜单和 IPC 即可。
- **React：** 当前问题集中在分析域状态和渲染链路，无需整体迁移框架。
- **Plotly：** 先重构 trace 和 Scene Model，并使用 `ScatterGL`、局部更新和稳定 trace key。

### 暂不采用

- 不立即整体迁移 Canvas/WebGL 图表库。
- 不先引入重量级全局状态库。
- 不为选择和拖动过程频繁写入全局响应式状态。
- 不依赖深拷贝实现 Undo/Redo。

### 重新评估条件

若在完成 Scene Model、ScatterGL、局部更新和 LOD 后，目标硬件上的 10 万行基准仍无法满足交互要求，再评估：

- 将原始点选择层迁移到 regl/deck.gl 等 WebGL 渲染器。
- 保留 Plotly 负责坐标轴、布局、统计图形和导出。
- 通过统一 Chart Scene 同时驱动 Plotly 与 WebGL 图层。

## 7. 风险与控制措施

| 风险 | 影响 | 控制措施 |
|---|---|---|
| `structureType` 或 `Area_reality` 数据源不统一 | JMP 布局分组错误 | Phase 0 明确权威字段和回退规则 |
| 聚合点与原始行一对多 | 删除错误数据 | 统计层与原始选择层彻底分离 |
| Plotly mixed trace 局部更新不一致 | 高亮失效 | 稳定 traceKey、真实 Plotly 集成测试、错误不静默吞掉 |
| Dataset 删除导致大数组复制 | 大数据卡顿 | 补丁、结构共享、revision 和局部派生 |
| 表格拖选触发大量 React 更新 | 拖动卡顿 | 手势状态局部保存，完成后批量提交 |
| 关闭数据表破坏图表引用 | 图表失效 | Dataset Registry 引用检查和 Undo |
| Electron 快捷键与输入框冲突 | 误删数据 | activeSurface、可编辑元素判断和统一 Command Guard |
| 样式覆盖数量过大 | 内存和渲染开销上升 | 稀疏覆盖、分组规则优先、批量局部更新 |
| 全屏 resize 触发图表重建 | 状态丢失和卡顿 | 布局状态独立、ResizeObserver、只调用 resize |

## 8. 测试与验收矩阵

### 自动测试

- Dataset、rowId、selection、commands、history 的单元测试。
- Chart Scene 的 X/Z 分组、多 Y 布局和 rowId 映射测试。
- 五种图形选择事件的真实 Plotly 集成测试。
- Worksheet 对齐、拖选、键盘操作和虚拟滚动测试。
- Electron 上下文菜单、快捷键、全屏和状态保持 E2E。
- 删除、样式编辑、关闭表格的 Undo/Redo 回归测试。

### 手动验收

- [ ] 不导出文件即可查看绘图所用原始数据。
- [ ] 表格在大数据量下可以平滑滚动。
- [ ] 五种图形均可框选多个原始点。
- [ ] 图表选择后表格对应行同步高亮。
- [ ] 表格选择后图中对应点同步高亮。
- [ ] Delete/Backspace 删除底层 Dataset 数据。
- [ ] 删除后相关纵轴自动调整。
- [ ] Undo/Redo 能恢复删除、样式和关闭的数据表。
- [ ] 表格可拖选多行且列严格对齐。
- [ ] `+`、复制、重命名、关闭的语义明确。
- [ ] 图表符合结构类型 X、面积 Z、多指标 Y 的 JMP 式布局。
- [ ] 可以编辑选中点、分组、Series 或指标的颜色和点样式。
- [ ] 图表和表格均提供桌面右键菜单和常用快捷键。
- [ ] 缩放和平移不触发整页重新渲染。
- [ ] 全屏隐藏主导航但保留 Workspace Tab。
- [ ] 退出全屏后筛选、选择、Tab 和样式不丢失。

## 9. 推荐提交顺序

1. `test(analysis): add real interaction acceptance harness`
2. `refactor(analysis): introduce dataset registry and stable row ids`
3. `refactor(analysis): centralize selection and command history`
4. `feat(analysis): build JMP multi-metric chart scene`
5. `feat(analysis): add raw point selection overlays`
6. `feat(analysis): link chart selection deletion and autorange`
7. `fix(analysis): align worksheet columns and scrolling`
8. `feat(analysis): support worksheet drag selection`
9. `feat(analysis): add worksheet tab lifecycle`
10. `feat(analysis): add point style inspector`
11. `feat(electron): add analysis context menus and shortcuts`
12. `perf(analysis): optimize chart and worksheet interaction`
13. `test(analysis): cover desktop linked-analysis workflow`

## 10. 最终执行顺序

```text
验收基线
  → 数据与命令模型
  → JMP Chart Scene
  → 五类图表框选、删除和 autorange
  → Worksheet 与 Tab
  → Origin 式样式编辑
  → Electron 桌面命令
  → 性能、全屏和发布验收
```

该顺序优先消除共享根因，并确保每个后续 UI 功能建立在同一 Dataset、Selection 和 Command 模型之上。
