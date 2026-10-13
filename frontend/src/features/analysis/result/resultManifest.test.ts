import { expect, it } from 'vitest';
import { RequestSequence, unverifiedManifest, validateQueryResult } from './resultManifest';
const spec = { filters: {}, fields: ['qs'], order_by: null };
it('unknown totals remain null; exact totals and truncation are validated', () => {
  const legacy = validateQueryResult({ rows: [{ qs: 1 }], returned: 1, total: 2, truncated: true }, spec);
  expect(legacy.manifest.matched_total).toBeNull();
  expect(legacy.manifest.consistency).toBe('unverified');
  const manifest = unverifiedManifest(spec, 1, 300001, true);
  expect(validateQueryResult({ rows: [{ qs: 1 }], manifest, truncated: true }, spec).manifest.matched_total).toBe(300001);
  expect(() => validateQueryResult({ rows: [], manifest }, spec)).toThrow('计数');
  expect(() => validateQueryResult({ rows: [{ qs: 1 }], manifest: { ...manifest, returned: 1, matched_total: 0 } }, spec)).toThrow();
});
it('aggregate count is matched devices and returned is groups, never pretends equal', () => {
  const manifest = unverifiedManifest(spec, 1, 500);
  expect(validateQueryResult({ groups: [{ qs: { avg: 1 } }], manifest }, spec, true).manifest.matched_total).toBe(500);
});
it('verified results reject missing identity and mismatched specifications', () => {
  const manifest = { ...unverifiedManifest(spec, 1, 1), consistency: 'verified' };
  expect(() => validateQueryResult({ rows: [{ qs: 1 }], manifest }, spec)).toThrow('来源');
  expect(() => validateQueryResult({ rows: [{ qs: 1 }], manifest: unverifiedManifest({ ...spec, fields: ['qp'] }, 1, 1) }, spec)).toThrow('规格');
});
it('A slow / B fast installs only B and stale A errors cannot change state', async () => {
  const sequence = new RequestSequence(), installed: string[] = [];
  let resolveA!: (value: string) => void;
  const a = new Promise<string>((resolve) => resolveA = resolve);
  const first = sequence.begin();
  const loadA = a.then((value) => { if (sequence.current(first)) installed.push(value); });
  const second = sequence.begin();
  if (sequence.current(second)) installed.push(await Promise.resolve('B'));
  resolveA('A'); await loadA;
  expect(installed).toEqual(['B']); sequence.invalidate(); expect(sequence.current(second)).toBe(false);
});

it('legacy sampling remains visible; a full exact raw result cannot have missing rows', () => {
  const result = validateQueryResult({ rows: [{ qs: 1 }], sampled: true, sample_rate: 0.5 }, spec);
  expect(result.manifest.sampling).toEqual({ method: 'systematic', rate: 0.5 });
  expect(() => validateQueryResult({ rows: [{ qs: 1 }], manifest: unverifiedManifest(spec, 1, 2) }, spec)).toThrow('完整结果');
});
