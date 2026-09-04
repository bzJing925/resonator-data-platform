import { describe, expect, it } from 'vitest';
import { createAnalysisDataset } from './model/dataset';
import { rowIdsInRange, rowIndexAtPointer, visibleRowRange } from './WorksheetGrid';

const dataset = createAnalysisDataset({
  id: 'd1',
  name: '数据表1',
  kind: 'query',
  rows: Array.from({ length: 20 }, (_, index) => ({ id: index + 1 })),
});

describe('WorksheetGrid geometry', () => {
  it('only renders the viewport plus a small overscan window', () => {
    expect(visibleRowRange(2800, 560, 100_000)).toEqual({ start: 92, end: 128 });
  });

  it('clamps the window at dataset boundaries', () => {
    expect(visibleRowRange(0, 280, 12)).toEqual({ start: 0, end: 12 });
    expect(visibleRowRange(280, 280, 20)).toEqual({ start: 2, end: 20 });
  });

  it('maps pointer position from the live virtual row-area origin', () => {
    expect(rowIndexAtPointer(156, 114, 20)).toBe(1);
    expect(rowIndexAtPointer(156, -166, 20)).toBe(11);
    expect(rowIndexAtPointer(9999, 114, 20)).toBe(19);
  });

  it('creates stable contiguous ranges in either drag direction', () => {
    expect(rowIdsInRange(dataset, 1, 3)).toEqual([
      'd1:device:2', 'd1:device:3', 'd1:device:4',
    ]);
    expect(rowIdsInRange(dataset, 3, 1)).toEqual([
      'd1:device:2', 'd1:device:3', 'd1:device:4',
    ]);
  });
});
