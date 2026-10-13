import { expect, it, vi } from 'vitest';
import { sourceA, sourceB } from '../__fixtures__/platformFixtures';
import { createAnalysisDataset } from '../model/dataset';
import { unverifiedManifest } from '../result/resultManifest';
import { makeComparison } from './comparisonSpec';
import { loadComparisonCohorts, summarizeCohort } from './comparisonData';
const post = vi.hoisted(() => vi.fn());
vi.mock('../../../api/client', () => ({ default: { post } }));
vi.mock('../../../config', () => ({ IS_STATIC: false }));
const query = { filters: {}, fields: ['qs'], order_by: null };
it('snapshot consumes captured edited/excluded rows and keeps their manifest, without API', async () => {
  const dataset = createAnalysisDataset({ id: 'a', name: 'partial', kind: 'query', rows: [{ qs: 10 }, { qs: null }],
    manifest: unverifiedManifest(query, 2, 300001, true) });
  dataset.edits = [{ datasetId: 'a', rowId: dataset.rows[0].rowId, column: 'qs', before: 1, after: 10, source: null }];
  const comparison = makeComparison({ source: null, dataset_id: 'a', query_spec: query }, { source: null, dataset_id: 'a', query_spec: query }, 'workspace_snapshot', ['qs']);
  const result = await loadComparisonCohorts(comparison, null, [dataset], { qs: '1' });
  expect(result.cohorts.a.manifest).toMatchObject({ truncated: true, matched_total: 300001, local_edits: 1, returned: 2 });
  expect(result.cohorts.a.statistics.qs.mean).toBe(10); expect(result.deltas[0].mean_delta).toBe(0);
  expect(post).not.toHaveBeenCalled(); dataset.rows[0].qs = 999;
  expect(result.cohorts.a.rows[0].qs).toBe(10);
});
it('source uses one fixed-cohort endpoint and rejects stale, truncated or disagreeing statistics', async () => {
  const refs = [sourceA, sourceB].map((ref) => ({ ...ref, source_device_key: null }));
  const comparison = makeComparison({ source: refs[0], dataset_id: null, query_spec: query }, { source: refs[1], dataset_id: null, query_spec: query }, 'source', ['qs']);
  const cohorts = refs.map((source, i) => {
    const rows = [{ qs: 10 + i, _source_ref: { ...source, source_device_key: i ? sourceB.source_device_key : sourceA.source_device_key } }];
    const manifest = { ...unverifiedManifest(query, 1, 1), source_refs: [source], consistency: 'verified' as const };
    return summarizeCohort(rows, manifest, comparison, null);
  });
  const response = { schema_version: 1, cohorts: { a: cohorts[0], b: cohorts[1] }, units: { qs: '1' } };
  post.mockResolvedValue({ data: response });
  const result = await loadComparisonCohorts(comparison, null, [], { qs: '1' });
  expect(result.deltas[0].mean_delta).toBe(1);
  expect(post.mock.calls[0][0]).toBe('/analysis/compare');
  const wrong = structuredClone(response); wrong.cohorts.a.statistics.qs.mean = 99;
  post.mockResolvedValue({ data: wrong });
  await expect(loadComparisonCohorts(comparison, null, [], { qs: '1' })).rejects.toThrow('对账');
  const stale = structuredClone(response); stale.cohorts.b.manifest.source_refs[0].source_revision!++;
  post.mockResolvedValue({ data: stale });
  await expect(loadComparisonCohorts(comparison, null, [], { qs: '1' })).rejects.toThrow('来源版本');
  const partial = structuredClone(response); partial.cohorts.a.manifest.truncated = true;
  post.mockResolvedValue({ data: partial });
  await expect(loadComparisonCohorts(comparison, null, [], { qs: '1' })).rejects.toThrow('完整');
});
