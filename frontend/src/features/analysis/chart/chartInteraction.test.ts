import { describe, expect, it } from 'vitest';
import { buildSelectionRestyle, buildYAxisAutorangeUpdate, datumRowIds } from './chartInteraction';

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
