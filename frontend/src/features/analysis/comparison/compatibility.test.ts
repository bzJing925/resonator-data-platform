import { expect, it } from 'vitest';
import { sourceA, sourceB } from '../__fixtures__/platformFixtures';
import { applyCommonSubset, inspectCompatibility } from './compatibility';
import { makeComparison, validateConditionInspection, type ConditionInspection } from './comparisonSpec';
const query = { filters: {}, fields: ['qs'], order_by: null };
function inspection(): ConditionInspection {
  const conditions = { frequency_ghz: [1, 3] as [number, number], deembedded: false, deembed_method: 'default', process_type: 'AUTO',
    config_digest: 'a'.repeat(64), algorithm_digest: 'b'.repeat(64), mapping_digest: 'c'.repeat(64) };
  return { schema_version: 1, units: { qs: '1' }, endpoints: {
    a: { source: { ...sourceA, source_device_key: null }, query_spec: query, conditions, structures: [[1, 2, null, 'S11']], matched_total: 4, issues: [] },
    b: { source: { ...sourceB, source_device_key: null }, query_spec: query, conditions: structuredClone(conditions), structures: [[1, 2, null, 'S11']], matched_total: 4, issues: [] },
  } };
}
it('selects same-named batches by UUID tuple and permits a self-comparison', () => {
  const input = inspection(), comparison = makeComparison({ source: input.endpoints.a.source, dataset_id: null, query_spec: query },
    { source: input.endpoints.b.source, dataset_id: null, query_spec: query }, 'source', ['qs']);
  expect(comparison.a.source?.source_batch_id).not.toBe(comparison.b.source?.source_batch_id);
  expect(validateConditionInspection(input, comparison)).toEqual(input);
  expect(inspectCompatibility(input, ['qs'], { qs: '1' }).blocked).toBe(false);
  input.endpoints.b = input.endpoints.a;
  expect(inspectCompatibility(input, ['qs'], { qs: '1' }).issues).toEqual([]);
});
it('unknown source revisions and conditions cannot masquerade as equal', () => {
  const input = inspection();
  expect(() => makeComparison({ source: { ...input.endpoints.a.source, source_revision: null }, dataset_id: null, query_spec: query },
    { source: input.endpoints.b.source, dataset_id: null, query_spec: query }, 'source', ['qs'])).toThrow('批次版本');
  input.endpoints.a.conditions = null; input.endpoints.b.conditions = null;
  expect(inspectCompatibility(input, ['qs'], { qs: '1' }).blocked).toBe(true);
});
it('frequency, unit, algorithm/config/mapping differences block and keep concrete values', () => {
  const input = inspection();
  input.endpoints.b.conditions!.frequency_ghz = [2, 4];
  input.endpoints.b.conditions!.algorithm_digest = 'd'.repeat(64);
  const review = inspectCompatibility(input, ['qs'], { qs: 'MHz' });
  expect(review.blocked).toBe(true);
  expect(review.issues.find((i) => i.field === 'frequency_ghz')).toMatchObject({ a: [1, 3], b: [2, 4], blocking: true });
  expect(review.issues.some((i) => i.field === 'qs')).toBe(true);
  expect(input.endpoints.b.conditions!.frequency_ghz).toEqual([2, 4]);
});
it('intersects complete tuples, requiring explicit subset; never forms a false Cartesian shape', () => {
  const input = inspection();
  input.endpoints.a.structures = [[1, 2, null, 'S11'], [3, 4, null, 'S22']];
  input.endpoints.b.structures = [[1, 2, null, 'S11'], [1, 4, null, 'S22']];
  const review = inspectCompatibility(input, ['qs'], { qs: '1' });
  expect(review.requiresSubset).toBe(true); expect(review.blocked).toBe(false);
  expect(review.common).toEqual([[1, 2, null, 'S11']]);
  expect(() => applyCommonSubset(query, [])).toThrow();
  const subset = applyCommonSubset(query, review.common);
  expect(JSON.stringify(subset.filters)).toContain('is_null');
  expect(query.filters).toEqual({});
  input.endpoints.b.structures = [[1, 4, null, 'S22']];
  expect(inspectCompatibility(input, ['qs'], { qs: '1' }).blocked).toBe(true);
});
it('stale responses and malformed digests are rejected before applying a spec', () => {
  const input = inspection(), comparison = makeComparison({ source: input.endpoints.a.source, dataset_id: null, query_spec: query },
    { source: input.endpoints.b.source, dataset_id: null, query_spec: query }, 'source', ['qs']);
  const stale = structuredClone(input); stale.endpoints.a.source.source_revision!++;
  expect(() => validateConditionInspection(stale, comparison)).toThrow('版本');
  input.endpoints.a.conditions!.mapping_digest = 'unknown';
  expect(() => validateConditionInspection(input, comparison)).toThrow('摘要');
});
