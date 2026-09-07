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
    expect(new Set(layers('swarm'))).toEqual(new Set(['raw-points']));
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
