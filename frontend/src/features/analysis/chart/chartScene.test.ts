import { describe, expect, it } from 'vitest';
import {
  ANALYSIS_CHART_TYPES,
  ANALYSIS_ROWS,
  ANALYSIS_Y_FIELDS,
  buildPerformanceRows,
} from '../__fixtures__/analysisRows';
import { datumRowIds } from './chartInteraction';
import { buildJmpChartScene } from './chartScene';
import { buildCachedJmpChartScene, clearJmpChartSceneCache } from './chartSceneCache';
import { formatMedianLabel } from './buildSwarmMedianTraces';
import type { ChartSceneArgs } from './types';

const USER_X_FIELDS = [{ name: 'process_mode', label: '工艺模式', isCategorical: true }];
const USER_Z_FIELD = { name: 'device_area', label: '器件面积', isCategorical: false };
const USER_ROWS = ANALYSIS_ROWS.map((row) => ({
  ...row,
  process_mode: row.structureType,
  device_area: row.areaReality,
}));

function buildScene(args: Omit<ChartSceneArgs, 'xFields' | 'zField'> & {
  xFields?: ChartSceneArgs['xFields'];
  zField?: ChartSceneArgs['zField'];
}) {
  return buildJmpChartScene({
    xFields: USER_X_FIELDS,
    zField: USER_Z_FIELD,
    ...args,
  });
}

describe('JMP analysis chart scene', () => {
  it('builds selected X/Z hierarchy with vertically stacked shared-X metrics', () => {
    const scene = buildScene({
      chartType: 'scatter',
      rows: USER_ROWS,
      yFields: ANALYSIS_Y_FIELDS,
      datasetRevision: 7,
    });

    expect(scene.layout.meta).toEqual({
      analysisLayout: {
        xFields: ['process_mode'],
        zField: 'device_area',
        yMetrics: ['qs', 'qp', 'k2eff_pct'],
      },
      datasetRevision: 7,
    });
    expect(scene.layout.grid).toMatchObject({ rows: 3, columns: 1, roworder: 'top to bottom' });
    expect(scene.layout.xaxis2.matches).toBe('x');
    expect(scene.layout.xaxis3.matches).toBe('x');
    expect(scene.layout.xaxis.showticklabels).toBe(false);
    expect(scene.layout.xaxis3.ticktext).toEqual([
      'Parallel', 'Series', 'Parallel', 'Series', 'Parallel', 'Series',
    ]);
    expect(scene.layout.annotations.map((annotation) => annotation.text)).toEqual(['700', '900', '1200']);
    expect(scene.layout.xaxis3.title.text).toBe('器件面积 / 工艺模式');
  });

  for (const chartType of ANALYSIS_CHART_TYPES) {
    it(`${chartType} exposes one selectable raw point for every metric value`, () => {
      const scene = buildScene({ chartType, rows: USER_ROWS, yFields: ANALYSIS_Y_FIELDS });
      const rawLayers = scene.allTraces.filter((trace) => trace.meta.analysisLayer === 'raw-points');
      expect(rawLayers).toHaveLength(ANALYSIS_Y_FIELDS.length);
      expect(rawLayers.flatMap((trace) => trace.customdata)).toHaveLength(
        ANALYSIS_ROWS.length * ANALYSIS_Y_FIELDS.length,
      );
      expect(rawLayers.flatMap((trace) => trace.customdata).every((datum) =>
        typeof datum.rowId === 'string' && !('__memberRowIds' in datum))).toBe(true);
    });
  }

  it('keeps raw, distribution, and summary responsibilities separate', () => {
    const layers = (chartType: string) => buildScene({
      chartType,
      rows: USER_ROWS,
      yFields: ANALYSIS_Y_FIELDS,
    }).allTraces.map((trace) => trace.meta.analysisLayer);

    expect(new Set(layers('scatter'))).toEqual(new Set(['raw-points']));
    expect(new Set(layers('swarm'))).toEqual(new Set(['raw-points', 'swarm-median']));
    expect(new Set(layers('box'))).toEqual(new Set(['distribution', 'raw-points']));
    expect(new Set(layers('violin'))).toEqual(new Set(['distribution', 'raw-points']));
    expect(new Set(layers('line'))).toEqual(new Set(['summary', 'raw-points']));
    const lineScene = buildScene({
      chartType: 'line', rows: USER_ROWS, yFields: ANALYSIS_Y_FIELDS,
    });
    expect(lineScene.allTraces.filter((trace) => trace.meta.analysisLayer === 'summary')).toHaveLength(9);
    expect(lineScene.allTraces.filter((trace) => trace.meta.analysisLayer === 'summary')
      .every((trace) => Math.max(...trace.x) - Math.min(...trace.x) <= 1)).toBe(true);
  });

  it('can hide raw data-point overlays while retaining the summary', () => {
    const scene = buildScene({
      chartType: 'line', rows: USER_ROWS, yFields: ANALYSIS_Y_FIELDS, showDataPoints: false,
    });
    expect(scene.allTraces.some((trace) => trace.meta.analysisLayer === 'raw-points')).toBe(false);
    expect(scene.allTraces.some((trace) => trace.meta.analysisLayer === 'summary')).toBe(true);
  });

  it('keeps trace identities stable when only the dataset revision changes', () => {
    const build = (datasetRevision: number) => buildScene({
      chartType: 'line',
      rows: USER_ROWS.map((row) => ({ ...row })),
      yFields: ANALYSIS_Y_FIELDS,
      datasetRevision,
    });
    const before = build(1);
    const after = build(2);

    expect(after.allTraces.map((trace) => trace.uid)).toEqual(before.allTraces.map((trace) => trace.uid));
    expect(after.allTraces.map((trace) => trace.meta.traceKey)).toEqual(
      before.allTraces.map((trace) => trace.meta.traceKey),
    );
    expect(after.allTraces.every((trace) => /^[a-zA-Z0-9_-]+$/.test(trace.uid))).toBe(true);
    expect(before.layout.meta.datasetRevision).toBe(1);
    expect(after.layout.meta.datasetRevision).toBe(2);
  });

  it('preserves missing selected X and Z values as explicit hierarchy buckets', () => {
    const scene = buildScene({
      chartType: 'scatter',
      rows: [{ rowId: 'r1', process_mode: '', device_area: null, qs: 1 }],
      yFields: [ANALYSIS_Y_FIELDS[0]],
    });
    expect(scene.layout.xaxis.ticktext).toEqual(['∅']);
    expect(scene.layout.annotations[0].text).toBe('∅');
    expect(scene.allTraces[0].customdata).toEqual([{ rowId: 'r1' }]);
  });

  it('does not create a Z hierarchy when no Z field is selected', () => {
    const scene = buildScene({
      chartType: 'scatter',
      rows: [
        { rowId: 'r1', process_mode: 'Mode A', qs: 1 },
        { rowId: 'r2', process_mode: 'Mode B', qs: 2 },
      ],
      yFields: [ANALYSIS_Y_FIELDS[0]],
      zField: null,
    });
    expect(scene.layout.xaxis.ticktext).toEqual(['Mode A', 'Mode B']);
    expect(scene.layout.annotations).toEqual([]);
    expect(scene.layout.meta.analysisLayout.zField).toBeNull();
    expect(scene.layout.xaxis.title.text).toBe('工艺模式');
    expect(scene.allTraces[0].customdata).toHaveLength(2);
  });

  it('uses every selected X field as a composite category without fixed field names', () => {
    const scene = buildScene({
      chartType: 'scatter',
      rows: [
        { rowId: 'r1', batch: 'B1', mode: 'M1', temperature: 25, qs: 1 },
        { rowId: 'r2', batch: 'B1', mode: 'M2', temperature: 25, qs: 2 },
      ],
      xFields: [
        { name: 'batch', label: '批次', isCategorical: true },
        { name: 'mode', label: '模式', isCategorical: true },
      ],
      zField: { name: 'temperature', label: '温度', isCategorical: false },
      yFields: [ANALYSIS_Y_FIELDS[0]],
    });

    expect(scene.layout.xaxis.ticktext).toEqual(['B1 / M1', 'B1 / M2']);
    expect(scene.layout.annotations.map((annotation) => annotation.text)).toEqual(['25']);
    expect(scene.layout.xaxis.title.text).toBe('温度 / 批次 / 模式');
  });

  it('downsamples only the visual raw layer while retaining every source row mapping', () => {
    const rows = buildPerformanceRows(50_001);
    const scene = buildScene({
      chartType: 'scatter',
      rows: rows.map((row) => ({
        ...row,
        process_mode: row.structureType,
        device_area: row.areaReality,
      })),
      yFields: [ANALYSIS_Y_FIELDS[0]],
    });
    const raw = scene.allTraces[0];
    expect(raw.hovertemplate).not.toContain('%{customdata.__memberCount}');
    expect(raw.customdata.every((datum) => datum.memberCount === datumRowIds(datum).length)).toBe(true);
    const mappedRowIds = raw.customdata.flatMap(datumRowIds);

    expect(raw.x.length).toBeLessThanOrEqual(50_000);
    expect(mappedRowIds).toHaveLength(rows.length);
    expect(new Set(mappedRowIds).size).toBe(rows.length);
    expect(raw.meta).toMatchObject({
      sourcePointCount: rows.length,
      renderedPointCount: raw.x.length,
    });
  });

  it('reuses a scene only for the same dataset revision, fields, and row reference', () => {
    clearJmpChartSceneCache();
    const args = {
      chartType: 'scatter',
      rows: USER_ROWS,
      xFields: USER_X_FIELDS,
      yFields: ANALYSIS_Y_FIELDS,
      zField: USER_Z_FIELD,
      datasetRevision: 3,
    };
    const first = buildCachedJmpChartScene('dataset-1', args);

    expect(buildCachedJmpChartScene('dataset-1', args)).toBe(first);
    expect(buildCachedJmpChartScene('dataset-1', { ...args, datasetRevision: 4 })).not.toBe(first);
    expect(buildCachedJmpChartScene('dataset-1', {
      ...args,
      rows: [...USER_ROWS],
    })).not.toBe(first);
    expect(buildCachedJmpChartScene('dataset-1', {
      ...args,
      xFields: [{ name: 'other_x' }],
    })).not.toBe(first);
    expect(buildCachedJmpChartScene('dataset-1', {
      ...args,
      zField: { name: 'other_z' },
    })).not.toBe(first);
  });
});

describe('蜂群图（swarm）绘制规约', () => {
  const SWARM_X_FIELDS = [{ name: 'tier', label: '面积档位', isCategorical: true }];
  const SWARM_Y_FIELDS = [{ name: 'v', label: 'Vs', isCategorical: false }];
  // T1 档：a/b/c 同 y=1（应横向堆叠），d 在 y=2（独占一层居中）；T2 档：e 单点
  const SWARM_ROWS = [
    { rowId: 'a', tier: 'T1', v: 1 },
    { rowId: 'b', tier: 'T1', v: 1 },
    { rowId: 'c', tier: 'T1', v: 1 },
    { rowId: 'd', tier: 'T1', v: 2 },
    { rowId: 'e', tier: 'T2', v: 1 },
  ];
  const swarmScene = () => buildJmpChartScene({
    chartType: 'swarm',
    rows: SWARM_ROWS,
    xFields: SWARM_X_FIELDS,
    yFields: SWARM_Y_FIELDS,
    zField: null,
  });

  it('同一 y 值附近的点在档位内横向对称堆叠，密度决定蜂群宽度', () => {
    const raw = swarmScene().allTraces.find((trace) => trace.meta.analysisLayer === 'raw-points');
    expect(raw.y).toEqual([1, 1, 1, 2, 1]);
    expect(raw.x[0]).toBeCloseTo(-0.1, 10);
    expect(raw.x[1]).toBeCloseTo(0, 10);
    expect(raw.x[2]).toBeCloseTo(0.1, 10);
    expect(raw.x[3]).toBeCloseTo(0, 10);
    expect(raw.x[4]).toBeCloseTo(1, 10);
  });

  it('散点小而圆润、不透明（交叠不加深颜色），各指标纯色互不重复', () => {
    const scene = buildScene({ chartType: 'swarm', rows: USER_ROWS, yFields: ANALYSIS_Y_FIELDS });
    const raws = scene.allTraces.filter((trace) => trace.meta.analysisLayer === 'raw-points');
    expect(raws.map((trace) => [trace.marker.size, trace.marker.opacity, trace.marker.line.width]))
      .toEqual([[5, 1, 0], [5, 1, 0], [5, 1, 0]]);
    const colors = raws.map((trace) => trace.marker.color);
    expect(new Set(colors).size).toBe(ANALYSIS_Y_FIELDS.length);
  });

  it('各档位中位数以同色系深色折线贯穿子图，数值标注在子图顶部', () => {
    const scene = swarmScene();
    const medians = scene.allTraces.filter((trace) => trace.meta.analysisLayer === 'swarm-median');
    const main = medians.find((trace) => trace.mode === 'lines+markers');
    const halo = medians.find((trace) => trace.mode === 'lines');
    expect(main).toBeDefined();
    expect(main.x).toEqual([0, 1]);
    expect(main.y).toEqual([1, 1]);
    expect(main.connectgaps).toBe(true);
    const pointColor = scene.allTraces.find((t) => t.meta.analysisLayer === 'raw-points').marker.color;
    expect(main.line.color).not.toBe(pointColor);
    // 深色主线 + 亮色描边：把趋势线从密集点云里分离出来
    expect(halo).toBeDefined();
    expect(halo.line.width).toBeGreaterThan(main.line.width);
    expect(medians.indexOf(halo)).toBeLessThan(medians.indexOf(main));

    const notes = scene.layout.annotations.filter((annotation) => annotation.yanchor === 'bottom');
    expect(notes.map((annotation) => [annotation.x, annotation.text]))
      .toEqual([[0, '1.00'], [1, '1.00']]);
    expect(notes.every((annotation) =>
      annotation.yref === 'y domain' && annotation.y === 1 && annotation.showarrow === false))
      .toBe(true);
    expect(notes[0].font.color).toBe(pointColor);
    expect(notes[0].font.size).toBeLessThanOrEqual(10);
  });

  it('多指标时每条中位数折线锚定各自子图轴', () => {
    const scene = buildScene({ chartType: 'swarm', rows: USER_ROWS, yFields: ANALYSIS_Y_FIELDS });
    const medians = scene.allTraces.filter((trace) =>
      trace.meta.analysisLayer === 'swarm-median' && trace.mode === 'lines+markers');
    expect(medians.map((trace) => [trace.xaxis, trace.yaxis])).toEqual([
      ['x', 'y'], ['x2', 'y2'], ['x3', 'y3'],
    ]);
    const notes = scene.layout.annotations.filter((annotation) => annotation.yanchor === 'bottom');
    expect(notes).toHaveLength(6 * ANALYSIS_Y_FIELDS.length);
    expect(new Set(notes.map((annotation) => annotation.yref)))
      .toEqual(new Set(['y domain', 'y2 domain', 'y3 domain']));
    // 已知中位数：档(700, Parallel) 的 qs = [851, 953, 1055, 1157] → 1004
    const first = notes.find((annotation) => annotation.xref === 'x' && annotation.x === 0);
    expect(first.text).toBe('1004');
  });

  it('相邻档位之间以竖向实线框线分隔，子图紧密堆叠，组标签保留在轴下方', () => {
    const scene = buildScene({ chartType: 'swarm', rows: USER_ROWS, yFields: ANALYSIS_Y_FIELDS });
    expect(scene.layout.grid.ygap).toBe(0.03);
    // 档位少（6 档 × 50px + 边距 = 400px < 假定容器宽度）时不强制图宽
    expect(scene.layout.width).toBeUndefined();
    const shapes = scene.layout.shapes ?? [];
    expect(shapes).toHaveLength(5); // 6 个档位 → 5 条分界框线
    expect(shapes.every((shape) =>
      shape.type === 'line' && shape.line.dash === 'solid' && shape.yref === 'paper'
      && shape.y0 === 0 && shape.y1 === 1 && shape.x0 === shape.x1)).toBe(true);
    expect(shapes.map((shape) => shape.x0)).toEqual([0.5, 1.5, 2.5, 3.5, 4.5]);
    const groupLabels = scene.layout.annotations.filter((annotation) => annotation.yref === 'paper');
    expect(groupLabels.map((annotation) => annotation.text)).toEqual(['700', '900', '1200']);
  });

  it('中位数折线绘制在散点层之上，避免被密集点云淹没', () => {
    const scene = swarmScene();
    expect(scene.allTraces.map((trace) => trace.meta.analysisLayer))
      .toEqual(['raw-points', 'swarm-median', 'swarm-median']);
    expect(scene.allTraces[2].mode).toBe('lines+markers');
  });

  it('档位过密时中位数标注转为竖排并收进顶边内侧，图宽按每档 50px 加宽', () => {
    const rows = Array.from({ length: 45 }, (_, index) => ({
      rowId: `r${index}`, tier: `T${index}`, v: index,
    }));
    const scene = buildJmpChartScene({
      chartType: 'swarm', rows, xFields: SWARM_X_FIELDS, yFields: SWARM_Y_FIELDS, zField: null,
    });
    const notes = scene.layout.annotations.filter((annotation) => annotation.textangle === -90);
    expect(notes).toHaveLength(45);
    expect(notes.every((annotation) => annotation.yanchor === 'top' && annotation.y < 1)).toBe(true);
    // 45 档 × 50px + 左右边距 100px = 2350px，超出假定容器宽度 → 固定图宽（容器滚动）
    expect(scene.layout.width).toBe(2350);
  });

  it('中位数标注按量级选择定点精度', () => {
    expect(formatMedianLabel(1004)).toBe('1004');
    expect(formatMedianLabel(123.456)).toBe('123.5');
    expect(formatMedianLabel(6.789)).toBe('6.79');
    expect(formatMedianLabel(0.456)).toBe('0.46');
  });
});
