# 数据分析页 Z 字段「按值分图」模式设计

- 日期：2026-08-28
- 范围：前端 `frontend/src/pages/Explore.tsx`、`frontend/src/components/Charts.tsx`
- 状态：已获用户批准（2026-08-28）

## 背景与需求

数据分析页（Explore）目前选中 Z 字段时，把 Z 作为颜色编码画在一张图上（类别 Z → 多组彩色 trace 叠加；数值 Z → 连续色阶，用户称之为"三维图"）。所有 Z 值叠在一张图里太密，难以对比。

需求：

1. 新增「按 Z 值分图」模式：Z 的每个离散值（含 null 缺失组）单独画一张 Y-X 二维图。
   例：X=面积、Z=FL → 分别绘制 FL=0, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, null 的各一张图。
2. 原颜色编码模式保留，两种模式做成可切换选项。
3. 多个分图**竖向排列**，每张图宽度与现在单图一致（不被横向压缩），页面支持上下滚动。
4. 数值 Z 不分图（已确认）：分图仅对类别/工艺字段开放，数值 Z 保持颜色编码。

## 方案

方案 A（已选定）：Z 配置区内加「展示方式」切换 + 单个 Plotly 实例纵向扩展网格。

否决的替代方案：

- 方案 B（每 Z 值一个独立 Plotly 实例）：实例开销大，交互一致性差。
- 方案 C（顶部工具条新增图表类型）：与散点/箱型等 chartType 语义冲突，组合爆炸。

## 详细设计

### 1. 状态与 UI（Explore.tsx / Inspector）

- `EXPLORE_INITIAL_STATE` 增加 `zMode: 'facet'`，取值 `'facet' | 'color'`，随 `usePageState` 持久化。
- Inspector「颜色 / 分组（Z，单选）」区块：
  - 选中**类别 Z** 时，字段列表上方出现「展示方式」两个 chip：`按值分图`（默认）/ `颜色编码（原效果）`。
  - 选中**数值 Z** 时，显示提示「数值 Z 仅支持颜色编码」，渲染时按 color 处理（UI 上不出现 facet 选项；判定处带 `isCategorical` 条件做防御）。
- 网格提示 `gridText` 在分图模式下显示「将渲染 N(Z 值) × M(Y) × K(X) 个子图」。

### 2. UnifiedChartGrid 分面模式（Charts.tsx）

- 新增 prop：`facetZ?: boolean`。由 Explore 在 `zMode === 'facet' && zField?.isCategorical` 时传入。
- `facetZ` 为真时：
  - `zValues = distinctSortedValues(rows, zField.name, { includeNull: true })`
    —— 复用现有 helper：数值按数值排序、字符串自然序、null 以 `NULL_KEY`("∅") 排最后。
  - 上限 `MAX_Z_FACETS = 24`：超出截断为前 24 个，并经 `onWarn` 发 `{ kind: 'z_facets_clamped', total, shown }` 警告（沿用 `x_categories_clamped` 先例）。
  - 网格：`grid.rows = nZ × nY`，`grid.columns = cols`（**列数不变 → 每张图宽度不变**）；
    `layout.height = nZ × nY × height + 顶部余量`，外层 `chart-body` 已有 `overflow: auto`，天然支持竖向滚动。
  - 循环顺序：最外层 z（每个 Z 值），内层照旧 r(Y) × c(X)。每个 Z 值先 `filter` 行
    （`NULL_KEY` 组匹配 `isNullish(r[zName])`，其余按 `String(...) === String(...)` 比较，与现有 trace 分组逻辑一致）。
  - 每个 cell 调 builder 时 `zField` 传 `null`（块内不再颜色编码），`showlegend: false`。
  - 块级标题：每个 Z 块顶部居中注释 `FL = 0.5` / `FL = ∅（缺失）`（按该块首行 y 域定位）；
    cell 级注释保持 `yTitle / xTitle` 不变。
  - X 类目数组仍在**全量 rows** 上计算（现状如此），保证各分面 X 轴一致可比。

### 3. 交互继承

- 单 Plot 实例：lasso/框选隐藏、撤销/反选、点击 customdata → DeviceModal 等全部沿用现有 handler，无需改动。
- 聚合分支（Y 聚合 + 类别 X）：`group_by` 现状已含类别 Z，分面直接作用于聚合后的行，无需改后端。

### 4. 边界与错误处理

- Z 值全为 null → 渲染单个 `∅` 图。
- Z 仅 1 个 distinct 值 → 单图（效果等同不选 Z，标题仍标注 Z 值）。
- chartType 为 box / violin / swarm / line 时同样支持分图（builder 收到 `zField=null` 自然退化为单组）。
- 数值 Z + facet 的组合不会出现（UI 层禁用）；`facetZ` 判定含 `isCategorical` 作为防御。

### 5. 测试

- Vitest 单测：从 UnifiedChartGrid 的 useMemo 主体抽出纯函数
  `buildUnifiedSpec({ chartType, rows, xFields, yFields, zField, facetZ, ... })`
  返回 `{ traces, layout, warns }`，组件只做渲染。测试：
  - facet 模式 grid.rows == nZ × nY、columns == nX（宽度不变）；
  - 每 cell 的行过滤正确（含 ∅ 组）；
  - 24 上限截断 + `z_facets_clamped` 警告；
  - 数值 Z 不触发 facet（防御分支）。
- 手动验证：`npm run dev`，X=面积 + Z=FL → 9 张竖排等宽图，可上下滚动；
  切换回颜色编码模式效果与现状一致。

### 6. 落地位置

- 按 CLAUDE.md worktree 路由在 `/Users/jingbozuo/Projects/aln-data-frontend`（`feat/frontend`）实现。
  该 worktree 当前不存在，且 `origin/feat/frontend` 落后 main 233 个提交 —— 实现时基于最新 main 重建。
- 完成后回合 main；如需更新静态网页版，运行 `build_web.py` 重新构建部署（单独步骤，不在本设计范围内）。

## 非目标（YAGNI）

- 数值 Z 的分箱（binning）分图。
- 分图的横向/网格排布选项（仅竖排）。
- 每张分图独立导出/缩放控件。
