import type { QualityRuleV1 } from '../../../types/platformContracts';
export type Row = Record<string, unknown>;
export interface MetricSummary {
  valid_n: number; missing_n: number; min: number | null; max: number | null; mean: number | null;
  median: number | null; p25: number | null; p75: number | null; std: number | null;
}
export const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
function sum(values: number[]) {
  let result = 0, correction = 0;
  for (const value of values) { const y = value - correction, next = result + y; correction = (next - result) - y; result = next; }
  return result;
}
export function computeMetricSummary(rows: Row[], metric: string): MetricSummary {
  const values = rows.map((row) => row[metric]).filter(finite).sort((a, b) => a - b), n = values.length;
  let scale = 1;
  for (const value of values) scale = Math.max(scale, Math.abs(value));
  const scaled = values.map((v) => v / scale), mean = n ? sum(scaled) / n : 0;
  const rescale = (v: number) => Number.isFinite(v * scale) ? v * scale : null;
  const quantile = (p: number) => {
    if (!n) return null;
    const index = (n - 1) * p, low = Math.floor(index), high = Math.ceil(index), ratio = index - low;
    return rescale(scaled[low] * (1 - ratio) + scaled[high] * ratio);
  };
  return { valid_n: n, missing_n: rows.length - n, min: n ? values[0] : null, max: n ? values[n - 1] : null,
    mean: n ? rescale(mean) : null, median: quantile(0.5), p25: quantile(0.25), p75: quantile(0.75),
    std: n >= 2 ? rescale(Math.sqrt(sum(scaled.map((v) => (v - mean) ** 2)) / (n - 1))) : null };
}
export function evaluateQualityRule(row: Row, rule: QualityRuleV1 | null): 'pass' | 'fail' | 'unknown' | null {
  if (!rule) return null;
  if (!rule.predicates.length || rule.predicates.some((p) => !finite(row[p.field]) || !finite(p.value))) return 'unknown';
  return rule.predicates.every((p) => {
    const value = row[p.field];
    return finite(value) && (p.op === 'gte' ? value >= p.value : value <= p.value);
  }) ? 'pass' : 'fail';
}
export function summarizeQuality(rows: Row[], rule: QualityRuleV1 | null) {
  let pass = 0, fail = 0, unknown = 0;
  const pf = { Y: 0, N: 0, unknown: 0 };
  for (const row of rows) {
    const status = evaluateQualityRule(row, rule);
    if (status === 'pass') pass++; else if (status === 'fail') fail++; else if (status === 'unknown') unknown++;
    if (row.pf === 'Y') pf.Y++; else if (row.pf === 'N') pf.N++; else pf.unknown++;
  }
  return { quality: { rule, pass, fail, unknown, evaluated: pass + fail, pass_rate: pass + fail ? pass / (pass + fail) : null }, pf };
}
export function metricGroups(rows: Row[], metrics: string[], fields: string[]) {
  const groups = new Map<string, { group: Row; rows: Row[] }>();
  for (const row of rows) {
    const group = Object.fromEntries(fields.map((field) => [field, row[field] ?? null])), key = JSON.stringify(group);
    const existing = groups.get(key) ?? { group, rows: [] }; existing.rows.push(row); groups.set(key, existing);
  }
  return [...groups].map(([key, value]) => ({ key, ...value, metrics: Object.fromEntries(metrics.map((metric) => [metric, computeMetricSummary(value.rows, metric)])) }));
}
export function computeMeanDifferences(a: Row[], b: Row[], metrics: string[], groupFields: string[]) {
  const aGroups = metricGroups(a, metrics, groupFields), bGroups = new Map(metricGroups(b, metrics, groupFields).map((group) => [group.key, group]));
  return aGroups.flatMap((left) => {
    const right = bGroups.get(left.key);
    if (!right) return [];
    return metrics.map((metric) => {
      const sa = left.metrics[metric], sb = right.metrics[metric], delta = sa.mean !== null && sb.mean !== null ? sb.mean - sa.mean : null;
      return { group: left.group, metric, a_valid_n: sa.valid_n, b_valid_n: sb.valid_n,
        mean_delta: delta !== null && Number.isFinite(delta) ? delta : null };
    });
  });
}
