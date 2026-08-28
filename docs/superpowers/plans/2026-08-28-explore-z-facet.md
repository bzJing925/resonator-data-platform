# 数据分析页 Z 字段「按值分图」实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 数据分析页（Explore）选中类别 Z 字段时，新增「按值分图」模式 —— 每个 Z 离散值（含 ∅ 缺失组）渲染一组竖排等宽的 2D 子图，原颜色编码模式保留为可切换选项。

**架构：** 方案 A（见规格 `docs/superpowers/specs/2026-08-28-explore-z-facet-design.md`）：把 `UnifiedChartGrid` 的 useMemo 主体抽成纯函数 `buildUnifiedSpec`（可单测），加 `facetZ` 参数；分面时 Plotly 网格从 `nY × nX` 扩展为 `(nZ × nY) × nX`（列数不变 → 宽度不变），单 Plotly 实例，交互全继承。Explore 侧加 `zMode: 'facet' | 'color'` 状态与切换 UI。

**技术栈：** React 18 + TypeScript + react-plotly.js + Vitest（jsdom，已验证 `Charts.tsx` 可在 vitest 下 import，无需 render）。

**前置阅读：**
- 规格：`docs/superpowers/specs/2026-08-28-explore-z-facet-design.md`
- 现状代码：`frontend/src/components/Charts.tsx`（`UnifiedChartGrid` 约 1519-1732 行；helper `distinctSortedValues` 822、`rowsForZ` 837、`axisSuffix` 843、`axisTitle` 848）
- `frontend/src/pages/Explore.tsx`（`EXPLORE_INITIAL_STATE` 176、`UnifiedChartGrid` 用法 746、Inspector Z 区块 905-916）

**关键事实（实现者无需重新调研）：**
- `usePageState` 用 `{...initial, ...persisted}` 浅合并 —— 旧持久化状态缺 `zMode` 时自动落到初始值，无需迁移代码。
- Plotly `layout.grid.ygap` 是归一化间距且要求 `(rows-1)*ygap < 1`；分面时行数常超 7，必须按总行数自适应收缩（任务 2 已给出公式）。
- 分面时 X 类目数组仍在**全量 rows** 上计算（保持各分面 X 轴一致可比），只有 builder 收到过滤后的 `cellRows`。
- 数值 Z 永不进入分面（`faceting` 判定含 `isCategorical`），UI 层也不给数值 Z 显示切换项。

---

### 任务 0：重建 frontend worktree 并验证基线

CLAUDE.md 路由规定前端工作在 `/Users/jingbozuo/Projects/aln-data-frontend`（`feat/frontend`）。该 worktree 不存在，且 `origin/feat/frontend` 落后 main 233 个提交 —— 基于最新 main 重建。

**文件：** 无（环境准备）

- [ ] **步骤 1：创建 worktree**

```bash
cd /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master
git branch feat/frontend main
git worktree add /Users/jingbozuo/Projects/aln-data-frontend feat/frontend
```

注意：不要推送/强推 `origin/feat/frontend`；合并方式在收尾时由 finishing-a-development-branch 技能决定。

- [ ] **步骤 2：安装依赖并跑基线测试**

```bash
cd /Users/jingbozuo/Projects/aln-data-frontend/frontend
npm install
npx vitest run
```

预期：全绿（现有 `Process.test.tsx` 3 个用例通过）。若基线就失败，停下来排查，不要带病开工。

**以下所有任务都在 `/Users/jingbozuo/Projects/aln-data-frontend` 中执行。**

---

### 任务 1：抽出 `buildUnifiedSpec` 纯函数（行为不变重构）

把 `UnifiedChartGrid` 的 useMemo 主体抽成模块级导出纯函数，组件只负责渲染。这是任务 2 可测试性的前提。本任务**不改变任何行为**。

**文件：**
- 修改：`frontend/src/components/Charts.tsx`（`UnifiedChartGrid` 区域，约 1502-1732 行）
- 测试：`frontend/src/components/Charts.test.tsx`（新建）

- [ ] **步骤 1：编写表征测试（锁定现状行为）**

创建 `frontend/src/components/Charts.test.tsx`：

```tsx
import { describe, expect, it } from 'vitest';
import { buildUnifiedSpec } from './Charts';

const X_NUM = { name: 'area_um2', label: '面积', isCategorical: false };
const X_CAT = { name: 'eg', label: 'EG', isCategorical: true };
const Y_QS = { name: 'qs', label: 'Qs', isCategorical: false };
const Z_CAT = { name: 'fl', label: 'FL', isCategorical: true };
const Z_NUM = { name: 'zp_ohm', label: 'Zp (Ω)', isCategorical: false };

const ROWS = [
  { area_um2: 100, eg: 'A', qs: 500, fl: 0, zp_ohm: 10 },
  { area_um2: 200, eg: 'A', qs: 600, fl: 0.5, zp_ohm: 20 },
  { area_um2: 300, eg: 'B', qs: 700, fl: 0.5, zp_ohm: 30 },
  { area_um2: 400, eg: 'B', qs: 800, fl: null, zp_ohm: 40 },
];

describe('buildUnifiedSpec 表征（重构须保持的现状行为）', () => {
  it('无 Z：grid = nY 行 × nX 列，每 cell 单条 trace，无图例', () => {
    const spec = buildUnifiedSpec({
      chartType: 'scatter', rows: ROWS, xFields: [X_NUM], yFields: [Y_QS],
    });
    expect(spec.placeholder).toBeNull();
    expect(spec.layout.grid.rows).toBe(1);
    expect(spec.layout.grid.columns).toBe(1);
    expect(spec.layout.height).toBe(1 * 350 + 100);
    expect(spec.allTraces).toHaveLength(1);
    expect(spec.layout.showlegend).toBe(false);
  });

  it('类别 Z 颜色编码：每 cell 每个 Z 值（含 ∅）一条 trace，图例开', () => {
    const spec = buildUnifiedSpec({
      chartType: 'scatter', rows: ROWS, xFields: [X_NUM], yFields: [Y_QS], zField: Z_CAT,
    });
    expect(spec.layout.grid.rows).toBe(1);
    expect(spec.allTraces).toHaveLength(3); // fl = 0 / 0.5 / ∅
    expect(spec.allTraces.map((t) => t.name)).toEqual(['0', '0.5', '∅']);
    expect(spec.layout.showlegend).toBe(true);
  });

  it('数值 Z + box：发 z_numeric_dropped 警告且忽略 Z', () => {
    const spec = buildUnifiedSpec({
      chartType: 'box', rows: ROWS, xFields: [X_CAT], yFields: [Y_QS], zField: Z_NUM,
    });
    expect(spec.pendingWarns.some((w) => w.kind === 'z_numeric_dropped')).toBe(true);
  });

  it('缺 X 或 Y：返回 placeholder 且不产出 trace', () => {
    const spec = buildUnifiedSpec({
      chartType: 'scatter', rows: ROWS, xFields: [], yFields: [Y_QS],
    });
    expect(spec.placeholder).toBeTruthy();
    expect(spec.allTraces).toHaveLength(0);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd /Users/jingbozuo/Projects/aln-data-frontend/frontend
npx vitest run src/components/Charts.test.tsx
```

预期：FAIL —— `buildUnifiedSpec is not exported`（import 报错或 `buildUnifiedSpec is not a function`）。

- [ ] **步骤 3：实施抽取**

在 `Charts.tsx` 中，于 `X_CATEGORY_TICK_LIMIT` 常量之后、`UnifiedChartGrid` 组件之前，新增导出函数。**函数体 = 现有 useMemo 回调（约 1556-1690 行）原样搬入**，仅做以下 3 处适配：

```ts
export function buildUnifiedSpec({
  chartType,
  rows,
  xFields,
  yFields,
  zField,
  facetZ = false, // 任务 2 才在函数体中使用；此处先声明，保证组件透传后 typecheck 干净
  height = 350,
  width = null,
}: {
  chartType: string;
  rows: Record<string, unknown>[];
  xFields: ChartField[];
  yFields: ChartField[];
  zField?: ChartField | null;
  facetZ?: boolean;
  height?: number;
  width?: number | null;
}) {
  // 适配 1：cols / nRows / builder / placeholder 从组件挪入（原 1544-1551 行）
  const cols = (xFields || []).length;
  const nRows = (yFields || []).length;
  const builder = BUILDER_MAP[chartType];
  const placeholder = cols === 0 || nRows === 0
    ? '请至少选一个 X / Y 字段'
    : !builder
      ? `未知图表类型：${String(chartType)}`
      : null;

  // 适配 2：原 useMemo 回调主体（const pw = []; const effZ = ...; 一直到
  //         if (!placeholder) for (...) 循环结束）原样放在这里，一字不改。

  // 适配 3：返回值多出 placeholder 字段
  return { allTraces: outTraces, layout: outLayout, pendingWarns: pw, perfWarn: pWarn, placeholder };
}
```

然后把 `UnifiedChartGrid` 组件改为薄壳（完整替换现有组件实现）：

```tsx
export function UnifiedChartGrid({
  chartType,
  rows,
  xFields,
  yFields,
  zField,
  facetZ = false,
  height = 350,
  width = null,
  onPerformanceWarn,
  onWarn,
  onPointClick,
  onSelection,
}: {
  chartType: string;
  rows: Record<string, unknown>[];
  xFields: ChartField[];
  yFields: ChartField[];
  zField?: ChartField | null;
  facetZ?: boolean;
  height?: number;
  width?: number | null;
  onPerformanceWarn?: (warn: { rowCount: number; threshold: number }) => void;
  onWarn?: (warn: unknown) => void;
  onPointClick?: (d: unknown) => void;
  onSelection?: (e: unknown) => void;
}) {
  const { allTraces, layout, pendingWarns, perfWarn, placeholder } = useMemo(
    () => buildUnifiedSpec({ chartType, rows, xFields, yFields, zField, facetZ, height, width }),
    [chartType, rows, xFields, yFields, zField, facetZ, height, width],
  );

  // 以下 warns dispatch / handleClick / placeholder 早退 / PlotAny 渲染
  // 与现状（约 1694-1730 行）完全一致，原样保留：
  //   const warnsKey = JSON.stringify(pendingWarns);
  //   useEffect(... onWarn ...); useEffect(... onPerformanceWarn ...);
  //   const handleClick = useCallback(...);
  //   if (placeholder) return (<div ...>{placeholder}</div>);
  //   return (<PlotAny divId="chart-unified" data={allTraces} layout={layout} ... />);
}
```

`facetZ` 在任务 1 就已声明在 `buildUnifiedSpec` 与组件的签名里，但函数体暂不使用（`tsconfig.json` 关闭了 `noUnusedParameters`，typecheck 不受影响）；任务 2 才实现它。

- [ ] **步骤 4：运行测试验证通过**

```bash
npx vitest run src/components/Charts.test.tsx
npm run typecheck
```

预期：4 个用例全 PASS；typecheck 0 error。同时跑全量 `npx vitest run` 确认 `Process.test.tsx` 不受影响。

- [ ] **步骤 5：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-frontend
git add frontend/src/components/Charts.tsx frontend/src/components/Charts.test.tsx
git commit -m "refactor(frontend): 抽出 buildUnifiedSpec 纯函数（行为不变，加分面做准备）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 2：`facetZ` 分面逻辑（TDD）

**文件：**
- 修改：`frontend/src/components/Charts.tsx`（`buildUnifiedSpec`）
- 测试：`frontend/src/components/Charts.test.tsx`（追加 describe）

- [ ] **步骤 1：编写失败的测试**

在 `Charts.test.tsx` 末尾追加：

```tsx
describe('buildUnifiedSpec facetZ 分面模式', () => {
  const FACET_ROWS = [
    { area_um2: 100, qs: 500, qp: 50, fl: 0 },
    { area_um2: 200, qs: 600, qp: 60, fl: 0.5 },
    { area_um2: 300, qs: 700, qp: 70, fl: 0.5 },
    { area_um2: 400, qs: 800, qp: 80, fl: null },
  ];
  const facetCall = (over = {}) => buildUnifiedSpec({
    chartType: 'scatter', rows: FACET_ROWS,
    xFields: [X_NUM], yFields: [Y_QS], zField: Z_CAT, facetZ: true,
    ...over,
  });

  it('每个 Z 值（含 ∅）一块：grid.rows = nZ × nY，columns = nX 不变', () => {
    const spec = facetCall();
    expect(spec.layout.grid.rows).toBe(3); // fl = 0 / 0.5 / ∅
    expect(spec.layout.grid.columns).toBe(1);
    expect(spec.layout.height).toBe(3 * 350 + 100);
  });

  it('块内行过滤正确：每块一条 trace，只含该 Z 值的行', () => {
    const spec = facetCall();
    expect(spec.allTraces).toHaveLength(3);
    expect(spec.allTraces[0].x).toEqual([100]);       // fl = 0
    expect(spec.allTraces[1].x).toEqual([200, 300]);  // fl = 0.5
    expect(spec.allTraces[2].x).toEqual([400]);       // fl = ∅
  });

  it('块标题注释含 "FL = 0.5" 与 ∅（缺失）', () => {
    const spec = facetCall();
    const texts = spec.layout.annotations.map((a) => a.text);
    expect(texts).toContain('FL = 0.5');
    expect(texts).toContain('FL = 0');
    expect(texts.some((t) => String(t).includes('∅'))).toBe(true);
  });

  it('分面时块内不再颜色编码：无图例、无 legendgroup', () => {
    const spec = facetCall();
    expect(spec.layout.showlegend).toBe(false);
    expect(spec.allTraces.every((t) => !t.legendgroup)).toBe(true);
  });

  it('多 Y：grid.rows = nZ × nY（Z 为最外层）', () => {
    const spec = facetCall({ yFields: [Y_QS, { name: 'qp', label: 'Qp', isCategorical: false }] });
    expect(spec.layout.grid.rows).toBe(6); // 3 个 Z 值 × 2 个 Y
    expect(spec.allTraces).toHaveLength(6);
  });

  it('超过 24 个 Z 值：截断为 24 块并发 z_facets_clamped 警告', () => {
    const many = Array.from({ length: 26 }, (_, i) => ({ area_um2: i, qs: i, fl: i }));
    const spec = facetCall({ rows: many });
    expect(spec.layout.grid.rows).toBe(24);
    expect(spec.pendingWarns.some(
      (w) => w.kind === 'z_facets_clamped' && w.total === 26 && w.shown === 24,
    )).toBe(true);
  });

  it('数值 Z + facetZ=true：防御回退为颜色编码，不分面', () => {
    const spec = facetCall({ zField: Z_NUM });
    expect(spec.layout.grid.rows).toBe(1);
    expect(spec.allTraces[0].marker.colorscale).toBe('Viridis');
  });

  it('Z 全为 null：单个 ∅ 块', () => {
    const spec = facetCall({ rows: [{ area_um2: 1, qs: 2, fl: null }] });
    expect(spec.layout.grid.rows).toBe(1);
    expect(spec.layout.annotations.some((a) => String(a.text).includes('∅'))).toBe(true);
  });

  it('facetZ=false 时行为与现状一致（不分面）', () => {
    const spec = facetCall({ facetZ: false });
    expect(spec.layout.grid.rows).toBe(1);
    expect(spec.allTraces).toHaveLength(3); // 颜色编码：3 条 Z trace
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

```bash
npx vitest run src/components/Charts.test.tsx
```

预期：新 describe 下用例 FAIL（`facetZ` 被忽略，`grid.rows` 仍为 1；或 `buildUnifiedSpec` 不识别参数导致断言失败）。任务 1 的 4 个表征用例必须仍 PASS。

- [ ] **步骤 3：实现分面逻辑**

在 `Charts.tsx` 的 `X_CATEGORY_TICK_LIMIT` 旁新增常量：

```ts
// 分面模式（facetZ）下 Z 值个数上限：超出截断 + 警告，
// 防止几十个竖排子图把单张 Plotly 图撑到不可用。
const MAX_Z_FACETS = 24;
```

在函数体内做以下修改（其余行保持不变；`facetZ` 参数任务 1 已声明，这里开始使用它）：

**(a)** `effZ`/`z_numeric_dropped` 逻辑之后，插入分面准备：

```ts
  // ── 按 Z 值分面：仅类别 Z；数值 Z 或未开 facetZ 时 nZ=1，退化为现状 ──
  const faceting = !!facetZ && !!effZ && !!effZ.isCategorical;
  let zFacetValues: any[] = [null];
  if (faceting && effZ) {
    const all = distinctSortedValues(rows, effZ.name, { includeNull: true });
    if (all.length > MAX_Z_FACETS) {
      zFacetValues = all.slice(0, MAX_Z_FACETS);
      pw.push({ kind: 'z_facets_clamped', zField: effZ.name, total: all.length, shown: MAX_Z_FACETS });
    } else {
      zFacetValues = all;
    }
  }
  const nZ = faceting ? zFacetValues.length : 1;
  const totalRows = nRows * nZ;
```

**(b)** `outLayout.grid` 的 `rows` 与 `ygap` 改为：

```ts
    grid: {
      rows: totalRows,
      columns: cols,
      pattern: 'independent',
      xgap: 0.08,
      // plotly 要求 (rows-1)*ygap < 1；分面时行数常超 7，
      // 按总行数收缩使像素间距近似恒定（≈84px/行高350px）。
      ygap: faceting ? Math.min(0.16, 0.24 / totalRows) : 0.16,
      roworder: 'top to bottom',
    },
```

**(c)** `outLayout.height` 与 `showlegend` 改为：

```ts
    height: totalRows * height + 100,
```

```ts
    showlegend: !!effZ && effZ.isCategorical && !faceting,
```

**(d)** 单元格循环从双层改为三层（Z 为最外层）。把现有的

```ts
    if (!placeholder) for (let r = 0; r < nRows; r++) {
      const yField = yFields[r];
      for (let c = 0; c < cols; c++) {
        const xField = xFields[c];
        const cellIdx = r * cols + c + 1;
```

替换为：

```ts
    if (!placeholder) for (let zi = 0; zi < nZ; zi++) {
      const zv = faceting ? zFacetValues[zi] : null;
      const cellRows = faceting && effZ ? rowsForZ(rows, effZ.name, zv) : rows;

      for (let r = 0; r < nRows; r++) {
        const yField = yFields[r];
        for (let c = 0; c < cols; c++) {
          const xField = xFields[c];
          const cellIdx = (zi * nRows + r) * cols + c + 1;
```

循环体内部（xLayout/yLayout/单元注释/swarm 警告）**原样保留**，注意两点：
- X 类目数组（`xCategoryArray`）仍用全量 `rows` 计算 —— 不要改成 `cellRows`；
- 原循环体因为多包了一层，注意补齐缩进与闭合大括号。

**(e)** builder 调用的参数改 3 处：

```ts
          const cellTraces = builder({
            rows: cellRows,
            xField,
            yField,
            zField: faceting ? null : effZ,
            axisRef: sfx,
            useGl,
            zCategoryValues,
            showLegend: !faceting && cellIdx === COLOR_OWNER_CELL,
            cellGroup: `cell${cellIdx}`,
            xCategoryArray,
          });
```

**(f)** 在 zi 循环体的末尾（r/c 双层循环结束之后）追加块级标题注释：

```ts
      // 块级标题：每个 Z 值块顶部居中。锚定该块首 cell 的 y 域，
      // y>1 表示在域上方（y=1.14 为经验起始值，目视偏了再调）。
      if (faceting && effZ) {
        const sfx0 = axisSuffix(zi * nRows * cols + 1);
        outLayout.annotations.push({
          xref: 'paper',
          x: 0.5,
          xanchor: 'center',
          yref: `y${sfx0} domain`,
          y: 1.14,
          yanchor: 'bottom',
          text: `${axisTitle(effZ)} = ${zv === NULL_KEY ? '∅（缺失）' : String(zv)}`,
          showarrow: false,
          font: { size: 13, color: '#E7E9ED' },
        });
      }
```

- [ ] **步骤 4：运行测试验证通过**

```bash
npx vitest run src/components/Charts.test.tsx
npm run typecheck
```

预期：测试 13 个用例（4 表征 + 9 分面）全 PASS；typecheck 0 error。

- [ ] **步骤 5：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-frontend
git add frontend/src/components/Charts.tsx frontend/src/components/Charts.test.tsx
git commit -m "feat(frontend): UnifiedChartGrid 支持按类别 Z 值分面（竖排等宽子图）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 3：Explore 页 UI 接线

**文件：**
- 修改：`frontend/src/pages/Explore.tsx`

说明：本任务是纯 UI 接线。Explore 组件级测试需要 jsdom 渲染 Plotly（jsdom 无 canvas，无法渲染），故不为本任务写组件测试 —— 逻辑核心已在任务 2 单测覆盖，本任务靠 typecheck + 任务 4 手动验证兜底。

- [ ] **步骤 1：加 `zMode` 状态**

`EXPLORE_INITIAL_STATE`（约 176-189 行）中，`zField: null,` 之后加一行：

```ts
  zMode: 'facet',
```

`state` 解构（约 215-228 行）加 `zMode,`；`setZField` 定义（约 233 行）之后加：

```ts
  const setZMode = useCallback((v) => setRawState((s) => ({ ...s, zMode: typeof v === 'function' ? v(s.zMode) : v })), [setRawState]);
```

`zMeta` 的 useMemo（约 286-290 行）之后加派生：

```ts
  // 分面生效条件：用户选了「按值分图」且 Z 是类别字段（数值 Z 永不进入分面）。
  const faceting = zMode === 'facet' && !!zMeta?.isCategorical;
```

- [ ] **步骤 2：透传 facetZ 并让警告随 zMode 复位**

`UnifiedChartGrid` 用法（约 746-757 行）加 prop：

```tsx
                    <UnifiedChartGrid
                      chartType={chartType}
                      rows={visibleRows}
                      xFields={xMeta}
                      yFields={yMeta}
                      zField={zMeta}
                      facetZ={faceting}
                      onPerformanceWarn={onChartWarn}
                      onWarn={onChartWarn}
                      onPointClick={handlePointClick}
                      onSelection={handleSelection}
                    />
```

警告复位 useEffect（约 507 行）的依赖数组加 `zMode`：

```ts
  useEffect(() => { setChartWarnings([]); }, [rows, chartType, xFields, yFields, zField, zMode]);
```

`formatChartWarning`（约 516-527 行）在 `z_numeric_dropped` 分支后加：

```ts
    if (w.kind === 'z_facets_clamped') {
      return `Z 字段 "${w.zField}" 共 ${w.total} 个不同值，分图模式仅显示前 ${w.shown} 个 — 建议改用颜色编码或加筛选条件缩小范围`;
    }
```

- [ ] **步骤 3：Inspector 加「展示方式」切换**

模块级常量（放在 `CHART_TYPES` 定义附近）：

```ts
// Z 展示方式：facet = 每个 Z 值一组竖排子图；color = 原颜色编码。
const Z_MODE_OPTIONS = [
  { key: 'facet', label: '按值分图' },
  { key: 'color', label: '颜色编码（原效果）' },
];
```

`Inspector` 调用处（约 777-798 行）加 3 个 prop：

```tsx
        <Inspector
          fields={fields}
          chartType={chartType}
          xFields={xFields}
          setXFields={setXFields}
          yFields={yFields}
          setYFields={setYFields}
          zField={zField}
          setZField={setZField}
          zMode={zMode}
          setZMode={setZMode}
          zMeta={zMeta}
          waferZ={waferZ}
          ...
```

`Inspector` 函数签名（约 813-822 行）对应加 `zMode, setZMode, zMeta,`。

`Inspector` 内 `gridText`（约 828 行）改为：

```ts
  // Grid preview text (X cols × Y rows; facet mode adds a Z-block outer loop).
  const zFacetActive = !!zField && zMode === 'facet' && !!zMeta?.isCategorical;
  const gridText = zFacetActive
    ? `按 Z 值分图：每个 Z 值渲染 ${yMeta.length || '?'} 行 × ${xMeta.length || '?'} 列子图，竖向排列`
    : `将渲染 ${yMeta.length || '?'} 行 × ${xMeta.length || '?'} 列 = ${(yMeta.length || 0) * (xMeta.length || 0) || '?'} 个子图`;
```

Z 区块（`<div className="section-title">颜色 / 分组（Z，单选）</div>` 之后、`<FieldRadioWithAgg` 之前）插入：

```tsx
              {zField && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                  <span style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>展示方式：</span>
                  {zMeta?.isCategorical ? (
                    Z_MODE_OPTIONS.map((o) => (
                      <label
                        key={o.key}
                        className={`explore-fieldchip${zMode === o.key ? ' checked' : ''} radio`}
                      >
                        <input
                          type="radio"
                          checked={zMode === o.key}
                          onChange={() => setZMode(o.key)}
                          style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                        />
                        <span className="explore-fieldchip-radio" aria-hidden>
                          {zMode === o.key && <span className="dot" />}
                        </span>
                        <span className="explore-fieldchip-label">{o.label}</span>
                      </label>
                    ))
                  ) : (
                    <span className="hint" style={{ fontSize: 10.5 }}>数值 Z 仅支持颜色编码</span>
                  )}
                </div>
              )}
```

- [ ] **步骤 4：typecheck**

```bash
cd /Users/jingbozuo/Projects/aln-data-frontend/frontend
npm run typecheck
npx vitest run
```

预期：typecheck 0 error；测试全绿（本任务未动逻辑，不应有测试变化）。

- [ ] **步骤 5：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-frontend
git add frontend/src/pages/Explore.tsx
git commit -m "feat(frontend): 数据分析页 Z 字段加「按值分图/颜色编码」切换

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 任务 4：整体验证与手动验收

**文件：** 无

- [ ] **步骤 1：自动化验证**

```bash
cd /Users/jingbozuo/Projects/aln-data-frontend/frontend
npx vitest run
npm run typecheck
npm run build
```

预期：测试全绿；typecheck 0 error；`vite build` 成功（含 electron 主进程编译）。

- [ ] **步骤 2：手动验收（由用户执行，GUI 操作）**

起全栈（`frontend npm run build` 已在步骤 1 完成，仓库根 `./bootstrap.sh up`，开 http://localhost:8080；或 `npm run dev` + 后端 dev server），在数据分析页逐项确认：

1. X=面积、Z=FL、任意 Y → 运行查询 → 每个 FL 值一块（含 `FL = ∅（缺失）`），竖向排列、每块宽度与原来单图一致、页面可上下滚动；块顶部有 `FL = …` 标题。
2. 切到「颜色编码（原效果）」→ 回到原来的叠加图，与改动前一致。
3. Z 选数值字段（如 Qs）→ 显示「数值 Z 仅支持颜色编码」，图保持颜色编码。
4. 分图模式下框选隐藏 / 撤销 / 点详情弹窗仍可用。
5. 若某类别 Z 超过 24 个值 → 顶部黄条警告且只画前 24 块。

- [ ] **步骤 3：收尾**

调用 superpowers:finishing-a-development-branch 技能决定合并方式（回合 main / 是否处理落后的 `origin/feat/frontend` / 是否用 `build_web.py` 更新静态网页版 —— 静态网页版更新是独立步骤，需用户确认后再做）。

---

## 自检记录

- **规格覆盖：** 规格 §1（zMode/UI/gridText）→ 任务 3；§2（facetZ、24 上限、网格扩展、块标题、全量 X 类目）→ 任务 2；§3（交互继承）→ 无代码改动，任务 4 验收清单第 4 条覆盖；§4（边界：全 null / 单值 / 其他图表类型 / 数值防御）→ 任务 2 测试 + 实现分支；§5（测试/手动验证）→ 任务 1/2/4；§6（worktree）→ 任务 0。
- **占位符：** 无 TODO/待定；唯一留白是块标题 y=1.14 的目视微调，已在注释中说明。
- **类型一致：** `buildUnifiedSpec` 返回 `{ allTraces, layout, pendingWarns, perfWarn, placeholder }`，组件解构同名；警告 kind `z_facets_clamped` 在 Charts.tsx（push）、Charts.test.tsx（断言）、Explore.tsx（formatChartWarning）三处拼写一致；`facetZ` 在组件 prop、`buildUnifiedSpec` 参数、Explore 透传三处同名。
