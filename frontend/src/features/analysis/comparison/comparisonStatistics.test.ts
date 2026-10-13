import { expect, it } from 'vitest';
import fixture from '../__fixtures__/platformComparison.json';
import { computeMetricSummary, computeMeanDifferences, evaluateQualityRule, summarizeQuality } from './comparisonStatistics';
const a = fixture.platform.a.map((qs) => ({ qs, structureType: 'X', s_param_port: 'S11', pf: qs !== null && qs >= 20 ? 'N' : 'Y' }));
const b = fixture.platform.b.map((qs) => ({ qs, structureType: 'X', s_param_port: 'S11' }));
const rule = { id: 'qs-min', version: 1, label: 'qs>=20', predicates: [{ field: 'qs', op: 'gte' as const, value: 20, unit: '1' }], combine: 'and' as const };
it('shared P2 counts/mean/linear quartiles/deltas match backend fixture tolerance', () => {
  const sa = computeMetricSummary(a, 'qs'), sb = computeMetricSummary(b, 'qs');
  expect(sa.valid_n).toBe(3); expect(sa.missing_n).toBe(1);
  expect(sa.mean).toBeCloseTo(fixture.platform.a_mean, 9); expect(sb.mean).toBeCloseTo(fixture.platform.b_mean, 9);
  expect(sa.p25).toBe(fixture.platform.a_p25); expect(sa.median).toBe(fixture.platform.a_median); expect(sa.p75).toBe(fixture.platform.a_p75);
  expect(computeMeanDifferences(a, b, ['qs'], ['structureType', 's_param_port'])[0].mean_delta).toBeCloseTo(fixture.platform.delta, 9);
  expect(computeMeanDifferences(a, a, ['qs'], ['structureType'])[0].mean_delta).toBe(0);
});
it('null/nonfinite are missing, empty/singleton std and stable overflow handling', () => {
  expect(computeMetricSummary([{ qs: null }, { qs: NaN }, { qs: Infinity }, { qs: '3' }], 'qs')).toMatchObject({ valid_n: 0, missing_n: 4, mean: null, std: null });
  expect(computeMetricSummary([{ qs: 1 }], 'qs').std).toBeNull();
  expect(computeMetricSummary([{ qs: 1e308 }, { qs: 1e308 }], 'qs').mean).toBe(1e308);
  expect(computeMetricSummary([{ qs: -1e308 }, { qs: 1e308 }], 'qs').mean).toBe(0);
  expect(computeMetricSummary(fixture.simple.a.map((qs) => ({ qs })), 'qs').std).toBeCloseTo(fixture.simple.std, 9);
});
it('custom quality unknown is excluded from rate; raw pf remains separately opposite', () => {
  expect(evaluateQualityRule({ qs: null }, rule)).toBe('unknown');
  const result = summarizeQuality(a, rule);
  expect(result.quality).toMatchObject({ pass: 2, fail: 1, unknown: 1, evaluated: 3 });
  expect(result.quality.pass_rate).toBeCloseTo(2 / 3, 9);
  expect(result.pf).toEqual({ Y: 2, N: 2, unknown: 0 });
  expect(summarizeQuality(a, null).quality.pass_rate).toBeNull();
});
it('different groups never create falsely paired device deltas', () => {
  expect(computeMeanDifferences([{ qs: 1, s_param_port: 'S11' }], [{ qs: 2, s_param_port: 'S22' }], ['qs'], ['s_param_port'])).toEqual([]);
});
