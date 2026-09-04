import { describe, expect, it } from 'vitest';
import { createAnalysisDataset } from './model/dataset';
import { pointStyleMetricKey } from './model/commands';
import {
  buildPointStyleRestyles,
  resolvePointStyleTarget,
  resolvedPointStyle,
} from './pointStyle';

const dataset = createAnalysisDataset({
  id: 'd1',
  name: '数据表1',
  kind: 'query',
  rows: [
    { rowId: 'r1', structureType: 'Series', areaReality: 700 },
    { rowId: 'r2', structureType: 'Series', areaReality: 700 },
    { rowId: 'r3', structureType: 'Series', areaReality: 900 },
    { rowId: 'r4', structureType: 'Parallel', areaReality: 700 },
  ],
});

describe('point style scopes', () => {
  it('resolves selection, inner group, outer series and metric targets', () => {
    const common = {
      dataset,
      selectedRowIds: new Set(['r1', 'r4']),
      anchorRowId: 'r1',
      metric: 'qs',
    };
    expect(resolvePointStyleTarget({ ...common, scope: 'selected' }).rowIds).toEqual(['r1', 'r4']);
    expect(resolvePointStyleTarget({ ...common, scope: 'group' }).rowIds).toEqual(['r1', 'r2']);
    expect(resolvePointStyleTarget({ ...common, scope: 'series' }).rowIds).toEqual(['r1', 'r2', 'r3']);
    expect(resolvePointStyleTarget({ ...common, scope: 'metric' })).toEqual({
      rowIds: [], metric: 'qs',
    });
  });

  it('lets row overrides win over metric overrides', () => {
    const styles = {
      [pointStyleMetricKey('qs')]: { color: '#00ff00', size: 9 },
      r1: { color: '#ff0000' },
    };
    expect(resolvedPointStyle(styles, 'r1', 'qs')).toEqual({ color: '#ff0000', size: 9 });
    expect(resolvedPointStyle(styles, 'r2', 'qs')).toEqual({ color: '#00ff00', size: 9 });
  });
});

describe('buildPointStyleRestyles', () => {
  it('builds per-point marker arrays only for raw traces', () => {
    const traces = [
      {
        marker: { color: '#111111', size: 6, opacity: 0.8, line: { color: '#ffffff', width: 0.5 } },
        customdata: [{ rowId: 'r1' }, { rowId: 'r2' }],
        meta: { analysisLayer: 'raw-points', metric: 'qs' },
      },
      { meta: { analysisLayer: 'summary', metric: 'qs' } },
    ];
    const [restyle] = buildPointStyleRestyles(traces, {
      [pointStyleMetricKey('qs')]: { symbol: 'diamond', opacity: 0.4 },
      r1: { color: '#ff0000', outlineWidth: 2 },
    });
    expect(restyle.traceIndex).toBe(0);
    expect(restyle.update['marker.color']).toEqual([['#ff0000', '#111111']]);
    expect(restyle.update['marker.symbol']).toEqual([['diamond', 'diamond']]);
    expect(restyle.update['marker.opacity']).toEqual([[0.4, 0.4]]);
    expect(restyle.update['marker.line.width']).toEqual([[2, 0.5]]);
  });

  it('uses immutable scene defaults after Plotly mutates marker arrays', () => {
    const [restyle] = buildPointStyleRestyles([{
      marker: {
        color: ['#ff0000'],
        size: [12],
        symbol: ['square'],
        opacity: [0.4],
        line: { color: ['#00ffff'], width: [2] },
      },
      customdata: [{ rowId: 'r1' }],
      meta: {
        analysisLayer: 'raw-points',
        metric: 'qs',
        pointStyleDefaults: {
          color: '#5FA8D3',
          size: 6,
          symbol: 'circle',
          opacity: 0.82,
          outlineColor: 'rgba(255,255,255,0.35)',
          outlineWidth: 0.5,
        },
      },
    }], {});

    expect(restyle.update['marker.color']).toEqual([['#5FA8D3']]);
    expect(restyle.update['marker.symbol']).toEqual([['circle']]);
    expect(restyle.update['marker.line.width']).toEqual([[0.5]]);
  });

  it('resolves a row override inside a downsampled point bucket', () => {
    const [restyle] = buildPointStyleRestyles([{
      marker: { color: '#111111' },
      customdata: [{ rowId: 'r1', __memberRowIds: ['r1', 'r2'] }],
      meta: { analysisLayer: 'raw-points', metric: 'qs' },
    }], { r2: { color: '#ff0000' } });

    expect(restyle.update['marker.color']).toEqual([['#ff0000']]);
  });
});
