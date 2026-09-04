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
    expect(spec.layout.grid.roworder).toBe('top to bottom');
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

  it('多面板预计点数超过阈值时，类别 X 映射为 WebGL 数值刻度', () => {
    const rows = Array.from({ length: 2000 }, (_, i) => ({
      __rowId: `device:${i}`, eg: i % 2 ? 'Series' : 'Parallel', qs: i, qp: i + 1, dbqs: i + 2,
    }));
    const spec = buildUnifiedSpec({
      chartType: 'scatter', rows, xFields: [X_CAT],
      yFields: [Y_QS, { ...Y_QS, name: 'qp' }, { ...Y_QS, name: 'dbqs' }],
    });
    expect(spec.allTraces.every((trace) => trace.type === 'scattergl')).toBe(true);
    expect(spec.layout.xaxis.type).toBe('linear');
    expect(spec.perfWarn?.pointCount).toBe(6000);
  });

  it('折线聚合点保留其底层 rowId，供选择和删除使用', () => {
    const rows = [
      { __rowId: 'device:1', eg: 'A', qs: 10 },
      { __rowId: 'device:2', eg: 'A', qs: 20 },
    ];
    const spec = buildUnifiedSpec({ chartType: 'line', rows, xFields: [X_CAT], yFields: [Y_QS] });
    expect(spec.allTraces[0].customdata[0].__memberRowIds).toEqual(['device:1', 'device:2']);
  });
});

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

  it('空数据 + facetZ：退化为非分面（grid.rows = nY），不产出 0 行网格', () => {
    const spec = facetCall({ rows: [] });
    expect(spec.layout.grid.rows).toBe(1);
    expect(spec.layout.height).toBe(1 * 350 + 100);
  });

  it('多 X × 分面：cellIdx 跨列唯一（轴键不碰撞）', () => {
    const spec = facetCall({ xFields: [X_NUM, { name: 'qp', label: 'Qp', isCategorical: false }] });
    expect(spec.layout.grid.rows).toBe(3);
    expect(spec.layout.grid.columns).toBe(2);
    const xAxisKeys = Object.keys(spec.layout).filter((k) => /^xaxis\d*$/.test(k));
    expect(xAxisKeys).toHaveLength(6); // 3 Z × 1 Y × 2 X
    expect(spec.allTraces).toHaveLength(6);
  });

  it('箱型图 + 分面：每块一条 trace，offsetgroup 互异', () => {
    const rows = FACET_ROWS.map((r, i) => ({ ...r, eg: i % 2 ? 'A' : 'B' }));
    const spec = facetCall({ chartType: 'box', xFields: [X_CAT], rows });
    expect(spec.allTraces).toHaveLength(3);
    expect(new Set(spec.allTraces.map((t) => t.offsetgroup)).size).toBe(3);
  });

  it('块标题锚定各块首 cell 的 y 域（yref 回归锁）', () => {
    const spec = facetCall();
    const blockTitles = spec.layout.annotations.filter((a) => String(a.text).startsWith('FL = '));
    expect(blockTitles.map((a) => a.yref)).toEqual(['y domain', 'y2 domain', 'y3 domain']);
  });

  it('swarm 分面：GL/SVG 阈值按全量行数判定，块内行数不触发 SVG 退化', () => {
    // 全量 6000 ≥ 5000 阈值；分 3 块后每块仅 2000 行 —— 若按块内行数
    // 判定会错误退化为 SVG scatter（审查修复 1 的回归锁）。
    const big = Array.from({ length: 6000 }, (_, i) => ({
      eg: i % 2 ? 'A' : 'B',
      qs: i,
      fl: i % 3,
    }));
    const spec = buildUnifiedSpec({
      chartType: 'swarm', rows: big,
      xFields: [X_CAT], yFields: [Y_QS], zField: Z_CAT, facetZ: true,
    });
    expect(spec.layout.grid.rows).toBe(3);
    expect(spec.allTraces).toHaveLength(3);
    expect(spec.allTraces.every((t) => t.type === 'scattergl')).toBe(true);
  });

  it('分面时重复警告按内容去重（3 块只发 1 条）', () => {
    // X 类别值 81 个（> X_CATEGORY_TICK_LIMIT 80）触发 x_categories_clamped；
    // fl 3 个值 → 3 个分面块 → 循环内 push 3 次，去重后应只剩 1 条
    const rows = Array.from({ length: 81 }, (_, i) => ({ eg: `E${i}`, qs: i, fl: i % 3 }));
    const spec = buildUnifiedSpec({
      chartType: 'box', rows, xFields: [X_CAT], yFields: [Y_QS], zField: Z_CAT, facetZ: true,
    });
    expect(spec.pendingWarns.filter((w) => w.kind === 'x_categories_clamped')).toHaveLength(1);
  });

  it('分面数值 X：同列各块显式共享量程，仅底块显示刻度与轴标题', () => {
    const spec = facetCall(); // FACET_ROWS：area_um2 = 100,200,300,400
    expect(spec.layout.grid.roworder).toBe('top to bottom');
    // 非底块：隐藏刻度与标题；底块：显示
    expect(spec.layout.xaxis.showticklabels).toBe(false);
    expect(spec.layout.xaxis.title).toBeUndefined();
    expect(spec.layout.xaxis2.showticklabels).toBe(false);
    expect(spec.layout.xaxis3.showticklabels).toBeUndefined();
    expect(spec.layout.xaxis3.title?.text).toContain('面积');
    // 同列各块 range 一致且覆盖全量数据（3% 边距）
    expect(spec.layout.xaxis.range).toEqual(spec.layout.xaxis3.range);
    expect(spec.layout.xaxis.range[0]).toBeLessThan(100);
    expect(spec.layout.xaxis.range[1]).toBeGreaterThan(400);
    expect(spec.layout.xaxis.matches).toBeUndefined();
  });

  it('分面类目 X：categoryarray 各块共享，仅底块显示刻度', () => {
    const rows = FACET_ROWS.map((r, i) => ({ ...r, eg: i % 2 ? 'A' : 'B' }));
    const spec = facetCall({ chartType: 'box', xFields: [X_CAT], rows });
    expect(spec.layout.xaxis.showticklabels).toBe(false);
    expect(spec.layout.xaxis3.showticklabels).toBeUndefined();
    expect(spec.layout.xaxis.categoryarray).toEqual(spec.layout.xaxis3.categoryarray);
    expect(spec.layout.xaxis.matches).toBeUndefined();
  });

  it('分面共享 X（多 X 列）：每列 range 各自一致', () => {
    const spec = facetCall({ xFields: [X_NUM, { name: 'qp', label: 'Qp', isCategorical: false }] });
    expect(spec.layout.xaxis6.range).toEqual(spec.layout.xaxis2.range); // qp 列
    expect(spec.layout.xaxis5.range).toEqual(spec.layout.xaxis.range);  // 面积列
    expect(spec.layout.xaxis6.showticklabels).toBeUndefined(); // 底行显示
    expect(spec.layout.xaxis6.matches).toBeUndefined();
  });
});
