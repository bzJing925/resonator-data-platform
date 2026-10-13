import { describe, expect, it } from 'vitest';
import { buildSelectionRestyle, buildYAxisAutorangeUpdate, buildYDataRanges, buildYAxisLayoutRanges, datumRowIds } from './chartInteraction';

describe('analysis chart interaction helpers', () => {
  it('maps formal row ids and preserves compatibility inputs', () => {
    expect(datumRowIds({ rowId: 'r1' })).toEqual(['r1']);
    expect(datumRowIds({ __rowId: 'legacy' })).toEqual(['legacy']);
    expect(datumRowIds({ __memberRowIds: ['r2', 'r3'] })).toEqual(['r2', 'r3']);
  });

  it('restyles only selectable raw traces', () => {
    const result = buildSelectionRestyle([
      { meta: { analysisLayer: 'summary' }, customdata: [{ count: 2 }] },
      { meta: { analysisLayer: 'raw-points' }, customdata: [{ rowId: 'r1' }, { rowId: 'r2' }] },
      { meta: { analysisLayer: 'distribution' } },
      { customdata: [{ __memberRowIds: ['r2', 'r3'] }] },
    ], new Set(['r2']));
    expect(result).toEqual({ traceIndices: [1, 3], selectedpoints: [[1], [0]] });
  });

  it('clears selectedpoints without touching statistical traces', () => {
    expect(buildSelectionRestyle([
      { meta: { analysisLayer: 'raw-points' }, customdata: [{ rowId: 'r1' }] },
      { meta: { analysisLayer: 'summary' }, customdata: [{ count: 1 }] },
    ], new Set())).toEqual({ traceIndices: [0], selectedpoints: [null] });
  });

  it('uses all finite source rows, including extremes outside rendered samples', () => {
    expect(buildYDataRanges([{ v: 10 }, { v: 25 }, { v: null }, { v: Infinity }, { v: '99' }], [{ name: 'v' }]))
      .toEqual({ v: [5, 30] });
    expect(buildYDataRanges([{ v: 7 }], [{ name: 'v' }, { name: 'missing' }]))
      .toEqual({ v: [2, 12] });
  });

  it('maps each metric range to its axes across columns and facets', () => {
    expect(buildYAxisLayoutRanges({ grid: { columns: 2 }, yaxis: {}, yaxis2: {}, yaxis3: {}, yaxis4: {}, yaxis5: {} },
      [{ name: 'a' }, { name: 'b' }], { a: [0, 10], b: [20, 30] }))
      .toEqual({ yaxis: [0, 10], yaxis2: [0, 10], yaxis3: [20, 30], yaxis4: [20, 30], yaxis5: [0, 10] });
  });

  it('reset uses source ranges and falls back to finite trace data', () => {
    expect(buildYAxisAutorangeUpdate({ yaxis: {}, yaxis2: {}, yaxis3: {}, meta: { analysisYRanges: { yaxis: [-5, 105] } } },
      [{ y: [20, 30, NaN] }, { yaxis: 'y2', y: [7, null] }]))
      .toEqual({ 'yaxis.autorange': false, 'yaxis.range': [-5, 105],
        'yaxis2.autorange': false, 'yaxis2.range': [2, 12], 'yaxis3.autorange': true });
  });

  it('builds an autorange patch for Y axes only', () => {
    expect(buildYAxisAutorangeUpdate({
      xaxis: {}, yaxis: {}, xaxis2: {}, yaxis2: {}, yaxis3: {}, annotations: [],
    })).toEqual({
      'yaxis.autorange': true,
      'yaxis2.autorange': true,
      'yaxis3.autorange': true,
    });
  });
});
