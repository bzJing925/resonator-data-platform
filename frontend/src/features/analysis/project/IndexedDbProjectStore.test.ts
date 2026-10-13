import { expect, it } from 'vitest';
import { createAnalysisDataset } from '../model/dataset';
import { platformRows } from '../__fixtures__/platformFixtures';
import { openProjectDb, unchangedChunk } from './IndexedDbProjectStore';

it('reuses unchanged chunks; an edit in the second block rewrites only that block', () => {
  const before = createAnalysisDataset({ id: 'rows', name: 'rows', kind: 'query', rows: platformRows(2001) }).rows;
  const after = before.map((row, i) => i === 2000 ? { ...row, qs: 99 } : row);
  expect(unchangedChunk(before, after, 0)).toBe(true);
  expect(unchangedChunk(before, after, 2000)).toBe(false);
  expect(unchangedChunk(before, after.slice(1), 0)).toBe(false);
});
it('missing IndexedDB has an explicit unavailable result', async () => {
  await expect(openProjectDb(undefined)).rejects.toMatchObject({ code: 'UNAVAILABLE' });
});
