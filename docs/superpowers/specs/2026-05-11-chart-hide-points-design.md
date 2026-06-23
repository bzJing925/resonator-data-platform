# 画图模块：图上框选 → 临时隐藏点

**日期**：2026-05-11
**作者**：qi.huang（with Claude）
**状态**：设计已确认，待实现

## 背景与目标

当前画图模块（`frontend/src/components/Charts.jsx` + `frontend/src/pages/Explore.jsx`）支持通过左侧 `FilterPanel` 做服务端筛选。但在画图过程中，用户经常想"临时甩掉几个明显异常的点"以看清主流分布——走 filter 太重（要构造条件、要重查、还会污染分析口径）。

目标：让用户在图上**直接框选一批点临时隐藏**，效果纯客户端、立即生效、可撤销，不动后端 filter。

### 非目标

- 不持久化（不写 URL、不存到 saved view）。刷新页面或关闭 tab 即丢失。
- 不动后端筛选 API。
- 不在 box/violin 的"箱体本身"上做框选语义（只有 outliers 点能被框中——这是 Plotly 的天然行为，符合预期）。
- 不在 line chart 的均值点上做框选（均值点没有 `customdata`，会被防御性 skip）。

## 决策汇总（来自 brainstorming）

| # | 问题 | 决策 |
|---|---|---|
| 设计基线 | 选哪条技术路线 | **路线 A**：复活 Plotly 自带 lasso/box select，监听 `onSelected` 拿 `customdata` |
| Q1 | hidden 集合作用域 | **全局**：切 chart type / 换 X / Y / Z 都共享一个 `hiddenIds` |
| Q2 | 何时自动清空 | **永不自动清空**，只能手动"清空"按钮；filters 变也不清 |
| Q3 | 框选后行为 | **修饰键区分**：默认拖 = 只看这些（hide 未选中），Shift+拖 = 隐藏选中 |
| Q4 | 默认 dragmode | **保持 pan**：用户必须先点 Plotly 工具条的 lasso/box 按钮进入选区模式 |
| 撤销 | 撤销栈深度 | **单步**（YAGNI） |

## 架构

```
rows  (Explore.jsx 从后端拿到的、filter 已应用的纯客户端数据)
   │
   ├── hiddenIds: Set<number>          ← 新增 state（Explore.jsx）
   ├── prevHiddenIds: Set<number> | null  ← 单步撤销栈
   ├── shiftHeldRef: { current: boolean } ← 全局 Shift 键状态
   │
   ▼
visibleRows = rows.filter(r => !hiddenIds.has(r.id))
   │
   ├── <HiddenBar> ............... 顶部小条（hiddenIds 非空才显示）
   │       撤销 / 清空 / 反选保留 + 当前命中计数
   │
   ▼
<UnifiedChartGrid rows={visibleRows} onSelection={handleSelection} />
<WaferMap        rows={visibleRows} onSelection={handleSelection} />
```

`Charts.jsx` 改动**轻**——只新增一个 `onSelection` 透传 prop 和把 modebar 上的 `select2d`/`lasso2d` 解禁。所有"隐藏"业务逻辑集中在 `Explore.jsx`。

## 组件 / 数据流

### Explore.jsx 新增

```js
const [hiddenIds, setHiddenIds] = useState(() => new Set());
const [prevHiddenIds, setPrevHiddenIds] = useState(null); // 单步 undo
const shiftHeldRef = useRef(false);

// 全局 Shift 监听
useEffect(() => {
  const down = (e) => { if (e.key === 'Shift') shiftHeldRef.current = true; };
  const up   = (e) => { if (e.key === 'Shift') shiftHeldRef.current = false; };
  window.addEventListener('keydown', down);
  window.addEventListener('keyup', up);
  return () => {
    window.removeEventListener('keydown', down);
    window.removeEventListener('keyup', up);
  };
}, []);

// 派生
const visibleRows = useMemo(
  () => (hiddenIds.size === 0 ? rows : rows.filter(r => !hiddenIds.has(r.id))),
  [rows, hiddenIds],
);
const hiddenInCurrentRows = useMemo(
  () => rows.reduce((n, r) => n + (hiddenIds.has(r.id) ? 1 : 0), 0),
  [rows, hiddenIds],
);

function handleSelection(e) {
  if (!e || !Array.isArray(e.points)) return;
  const selectedIds = new Set();
  for (const p of e.points) {
    const row = p.customdata;
    if (row && row.id != null) selectedIds.add(row.id);
  }
  if (selectedIds.size === 0) return; // 空选择忽略

  const shift = shiftHeldRef.current;
  let next;
  if (shift) {
    // 隐藏这些
    next = new Set(hiddenIds);
    for (const id of selectedIds) next.add(id);
  } else {
    // 只看这些：把当前 visible 中没被选中的全加入 hidden
    // 防御：如果会导致 visible 变空（即 visible 中没有一个 id 命中 selectedIds），放弃
    const remainingVisible = visibleRows.reduce(
      (n, r) => n + (selectedIds.has(r.id) ? 1 : 0),
      0,
    );
    if (remainingVisible === 0) return;
    next = new Set(hiddenIds);
    for (const r of visibleRows) {
      if (!selectedIds.has(r.id)) next.add(r.id);
    }
  }

  if (next.size === hiddenIds.size) {
    // no-op，比如"只看这些"选中等于当前全集
    let same = true;
    for (const id of next) if (!hiddenIds.has(id)) { same = false; break; }
    if (same) return;
  }

  setPrevHiddenIds(hiddenIds);
  setHiddenIds(next);
}

function handleUndo()   { if (prevHiddenIds == null) return; setHiddenIds(prevHiddenIds); setPrevHiddenIds(null); }
function handleClear()  { if (hiddenIds.size === 0) return; setPrevHiddenIds(hiddenIds); setHiddenIds(new Set()); }
function handleInvert() {
  // 反选保留：当前可见 → 隐藏；当前隐藏（且在 rows 中）→ 可见
  const next = new Set();
  for (const r of visibleRows) next.add(r.id);
  // hiddenIds 里不在 rows 的 stale id 一并丢弃（反选语义里把它们"放出来"）
  setPrevHiddenIds(hiddenIds);
  setHiddenIds(next);
}
```

### HiddenBar 组件（新增，写在 Explore.jsx 内部即可，~30 行）

只渲染条件：`hiddenIds.size > 0`。

```jsx
<div className="hidden-bar">
  <span>已隐藏 {hiddenInCurrentRows} 个点</span>
  <button onClick={handleInvert} disabled={hiddenIds.size === 0}>反选保留</button>
  <button onClick={handleUndo}  disabled={prevHiddenIds == null}>撤销</button>
  <button onClick={handleClear}>清空</button>
  <span className="hint">提示：先点工具条 lasso/框选；拖动 = 只看选中，Shift+拖 = 隐藏选中</span>
</div>
```

样式参考现有 `FilterPanel` 的细条风格，CSS 加到 `frontend/src/pages/Explore.css`（或同文件的样式表）。

### Charts.jsx 改动

1. `baseConfig`（`Charts.jsx:21`）：
   ```js
   const baseConfig = {
     displaylogo: false,
     responsive: true,
     modeBarButtonsToRemove: [], // 之前是 ['select2d', 'lasso2d']，去掉以启用
   };
   ```
   `dragmode` 不设置（默认 pan，保持现有行为）。

2. `UnifiedChartGrid` 函数签名加 `onSelection`：
   ```js
   export function UnifiedChartGrid({ ..., onSelection }) {
     ...
     return (
       <Plot
         ...
         onSelected={onSelection}
         ...
       />
     );
   }
   ```

3. `WaferMap` 同样加 `onSelection` 透传给 `<Plot onSelected={...} />`。

4. 散点/wafer/swarm 已经在所有 trace 上挂 `customdata`，**不需要新增 customdata**。box/violin 的 outliers 默认也带 customdata（Plotly 行为），不需要改。

### Explore.jsx 接线

```jsx
<UnifiedChartGrid
  rows={visibleRows}
  ...
  onPointClick={(d) => setActiveDevice(d)}
  onSelection={handleSelection}
/>
<WaferMap
  rows={visibleRows}
  ...
  onPointClick={useAggregate ? undefined : (d) => setActiveDevice(d)}
  onSelection={handleSelection}
/>
```

注意：`rows` 改成 `visibleRows`。

**aggregated 模式（`useAggregate === true`）的行为**：已确认 `Explore.jsx:198-206 / 245-264` 的聚合分支产出的 row 不带 `id` 字段（只有 group_by 列如 `x`、`y`、`eg` 等）。`handleSelection` 里的 `if (!row || row.id == null) continue` 防御性 skip 会让 aggregated 行不会被加入 `selectedIds`，因此**aggregated 模式下框选自然变 no-op，无需特殊代码**。这是可接受的——aggregated 是统计视图，"隐藏一个聚合 cell"本身语义就模糊。

## 视觉与交互细节

- **选区视觉**：用户框选后，Plotly 短暂高亮选中点+灰化未选中点，约 100ms 后我们 setState → rows 变 → Plot 重画 → 选区自然消失。不需要手动 `Plotly.relayout`。
- **多 cell（grid 模式）**：Plotly lasso 是单 subplot 内的。用户在 cell A 框选 → `e.points` 只来自 cell A → 但 hide 按 id 算，**所有 cell 同步更新**。这正是想要的。
- **box / violin**：箱体本身没 customdata → 框选无效，只有 outliers 点能被框中。符合预期。
- **line chart**：均值点没 customdata 或 customdata 不是原始 row → 防御性 skip（`if (!row || row.id == null) continue`）。

## 边界情况

| 场景 | 行为 |
|---|---|
| 框选 0 点 | no-op |
| "只看这些"会让 visible 变空 | 跳过（避免误操作清屏） |
| "只看这些"选中 = 全集 | no-op |
| 框选时 row.id 缺失 | 单点 skip，其余照常 |
| rows 变化（重查询） | hiddenIds 不动；stale id 自动不命中；计数自动反映新 rows |
| chart type / X / Y / Z 改变 | hiddenIds 不动 |
| 撤销已经是空栈 | 按钮 disabled，点不到 |
| Shift 按住时窗口失焦 | 下次 keyup 不会触发 → ref 残留 true。可接受：用户重新按一次 Shift 即可纠正。**可选改进**：监听 `window.blur` 也复位 |

## 错误处理

无真正错误路径（纯客户端 state）。所有 user-facing 异常通过"按钮禁用"和 "no-op" 表达，不弹 alert / toast。

## 测试计划（手工）

人手过一遍即可，画图模块本身没有自动测：

1. **scatter**：lasso 圈 5 个点 → 消失，计数=5；Shift+lasso 圈另 3 个 → 计数=8；撤销 → 回到 5。
2. **多 cell grid**：在 cell A 圈一片 → 所有 cell 同步消失。
3. **swarm**：Shift+lasso 一群 → 消失；切到 scatter（同 X/Y）→ 同样不可见。
4. **wafer map**：lasso 一块区域 → 那些 die 消失（如果是非 aggregated 模式）。
5. **chart 切换不丢**：scatter → box → violin → 回 scatter，hiddenIds 一直生效。
6. **filters 改 → 重查**：hiddenIds 保留；计数自动反映命中数；点"清空"恢复全集。
7. **反选保留 → 撤销**：能精确回到上一态。
8. **空选择保护**：拉一个 0 像素框 → 啥都不动，撤销栈也不变。
9. **"只看"清屏保护**：选 visible 的全集 → no-op，不会全图清空。

## 文件清单

- `frontend/src/components/Charts.jsx`：
  - 改 `baseConfig`（启用 select2d / lasso2d）
  - `UnifiedChartGrid` / `WaferMap` 加 `onSelection` prop 透传到 `<Plot onSelected={...}>`
- `frontend/src/pages/Explore.jsx`：
  - 新增 `hiddenIds` / `prevHiddenIds` / `shiftHeldRef`
  - Shift 全局监听 `useEffect`
  - `handleSelection` / `handleUndo` / `handleClear` / `handleInvert`
  - `visibleRows` / `hiddenInCurrentRows` 派生
  - `HiddenBar` 内联组件
  - 把 `<UnifiedChartGrid rows>` 和 `<WaferMap rows>` 改成 `visibleRows`
- `frontend/src/pages/Explore.css`（或同处样式）：`HiddenBar` 样式

预计代码增量：~80 行。
