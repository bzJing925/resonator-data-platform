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

describe('JMP analysis chart scene', () => {
  it('builds structure/area hierarchy with vertically stacked shared-X metrics', () => {
    const scene = buildJmpChartScene({
      chartType: 'scatter',
      rows: ANALYSIS_ROWS,
      yFields: ANALYSIS_Y_FIELDS,
      datasetRevision: 7,
    });

    expect(scene.layout.meta).toEqual({
      analysisLayout: {
        outerX: 'structureType',
        innerX: 'areaReality',
        yMetrics: ['qs', 'qp', 'k2eff_pct'],
      },
      datasetRevision: 7,
    });
    expect(scene.layout.grid).toMatchObject({ rows: 3, columns: 1, roworder: 'top to bottom' });
    expect(scene.layout.xaxis2.matches).toBe('x');
    expect(scene.layout.xaxis3.matches).toBe('x');
    expect(scene.layout.xaxis.showticklabels).toBe(false);
    expect(scene.layout.xaxis3.ticktext).toEqual([700, 900, 1200, 700, 900, 1200].map(String));
    expect(scene.layout.annotations.map((annotation) => annotation.text)).toEqual(['Parallel', 'Series']);
  });

  for (const chartType of ANALYSIS_CHART_TYPES) {
    it(`${chartType} exposes one selectable raw point for every metric value`, () => {
      const scene = buildJmpChartScene({ chartType, rows: ANALYSIS_ROWS, yFields: ANALYSIS_Y_FIELDS });
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
    const layers = (chartType: string) => buildJmpChartScene({
      chartType,
      rows: ANALYSIS_ROWS,
      yFields: ANALYSIS_Y_FIELDS,
    }).allTraces.map((trace) => trace.meta.analysisLayer);

    expect(new Set(layers('scatter'))).toEqual(new Set(['raw-points']));
    expect(new Set(layers('swarm'))).toEqual(new Set(['raw-points']));
    expect(new Set(layers('box'))).toEqual(new Set(['distribution', 'raw-points']));
    expect(new Set(layers('violin'))).toEqual(new Set(['distribution', 'raw-points']));
    expect(new Set(layers('line'))).toEqual(new Set(['summary', 'raw-points']));
    const lineScene = buildJmpChartScene({
      chartType: 'line', rows: ANALYSIS_ROWS, yFields: ANALYSIS_Y_FIELDS,
    });
    expect(lineScene.allTraces.filter((trace) => trace.meta.analysisLayer === 'summary')).toHaveLength(6);
    expect(lineScene.allTraces.filter((trace) => trace.meta.analysisLayer === 'summary')
      .every((trace) => Math.max(...trace.x) - Math.min(...trace.x) <= 2)).toBe(true);
  });

  it('keeps trace identities stable when only the dataset revision changes', () => {
    const build = (datasetRevision: number) => buildJmpChartScene({
      chartType: 'line',
      rows: ANALYSIS_ROWS.map((row) => ({ ...row })),
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

  it('preserves missing structure and area as explicit hierarchy buckets', () => {
    const scene = buildJmpChartScene({
      chartType: 'scatter',
      rows: [{ rowId: 'r1', structureType: '', areaReality: null, qs: 1 }],
      yFields: [ANALYSIS_Y_FIELDS[0]],
    });
    expect(scene.layout.xaxis.ticktext).toEqual(['∅']);
    expect(scene.layout.annotations[0].text).toBe('∅');
    expect(scene.allTraces[0].customdata).toEqual([{ rowId: 'r1' }]);
  });

  it('normalizes numeric structure buckets consistently', () => {
    const scene = buildJmpChartScene({
      chartType: 'scatter',
      rows: [{ rowId: 'r1', structureType: 42, areaReality: 700, qs: 1 }],
      yFields: [ANALYSIS_Y_FIELDS[0]],
    });
    expect(scene.layout.annotations[0].text).toBe('42');
    expect(scene.allTraces[0].customdata).toHaveLength(1);
  });

  it('downsamples only the visual raw layer while retaining every source row mapping', () => {
    const rows = buildPerformanceRows(50_001);
    const scene = buildJmpChartScene({
      chartType: 'scatter',
      rows,
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
      rows: ANALYSIS_ROWS,
      yFields: ANALYSIS_Y_FIELDS,
      datasetRevision: 3,
    };
    const first = buildCachedJmpChartScene('dataset-1', args);

    expect(buildCachedJmpChartScene('dataset-1', args)).toBe(first);
    expect(buildCachedJmpChartScene('dataset-1', { ...args, datasetRevision: 4 })).not.toBe(first);
    expect(buildCachedJmpChartScene('dataset-1', {
      ...args,
      rows: [...ANALYSIS_ROWS],
    })).not.toBe(first);
  });
});
