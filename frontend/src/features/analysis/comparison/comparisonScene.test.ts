import { expect, it } from 'vitest';
import { createAnalysisDataset } from '../model/dataset';
import { comparisonColorDomain, comparisonWaferCells, distributionScene, differenceRows, groupMemberRows } from './comparisonScene';
const dataset = createAnalysisDataset({ id: 'a', name: 'A', kind: 'query', rows: [
  { qs: 10, wafer: 'W1', x: 1, y: 1, s_param_port: 'S11' },
  { qs: 30, wafer: 'W1', x: 1, y: 1, s_param_port: 'S11' },
  { qs: null, wafer: 'W1', x: 1, y: 1, s_param_port: 'S11' },
  { qs: 100, wafer: 'W2', x: 1, y: 1, s_param_port: 'S11' },
] });
it('same-wafer cells aggregate mean and n without merging a different wafer', () => {
  const cells = comparisonWaferCells(dataset, 'qs');
  expect(cells).toHaveLength(2);
  expect(cells[0]).toMatchObject({ qs: 20, _cell_n: 3, _cell_valid_n: 2, wafer: 'W1' });
  expect(cells[0].__memberRowIds).toHaveLength(3);
  expect(cells[1].qs).toBe(100);
});
it('color domain is shared, equal-value padded and all-missing has no color scale', () => {
  expect(comparisonColorDomain([{ qs: 10 }], [{ qs: 100 }], 'qs')).toEqual([10, 100]);
  const equal = comparisonColorDomain([{ qs: 10 }], [{ qs: 10 }], 'qs');
  expect(equal![0]).toBeLessThan(10); expect(equal![1]).toBeGreaterThan(10);
  expect(comparisonColorDomain([{ qs: null }], [{ qs: Infinity }], 'qs')).toBeNull();
});
it('real scene raw customdata retains dataset and row identity; self delta zero selects only valid members', () => {
  const scene = distributionScene(dataset, 'qs', '1', 'box');
  const raw = scene.allTraces.find((trace) => trace.meta?.analysisLayer === 'raw-points');
  expect(raw.customdata[0]).toMatchObject({ datasetId: 'a', rowId: dataset.rows[0].rowId });
  expect(differenceRows(dataset, dataset, 'qs', ['s_param_port'])[0].mean_delta).toBe(0);
  expect(groupMemberRows(dataset, { s_param_port: 'S11' }, 'qs')).toHaveLength(3);
});
