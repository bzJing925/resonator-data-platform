import type { AnalysisSpec, QuerySpec, ResultManifest, SourceRef } from '../../../types/platformContracts';
import { validateSourceRefs, type JsonValue } from './provenance';

type SpecErrorCode = 'CORRUPT' | 'UNSUPPORTED_VERSION';
class SpecError extends Error {
  constructor(public code: SpecErrorCode, message: string) { super(message); }
}

function fail(message: string): never { throw new SpecError('CORRUPT', message); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail('需要对象');
  if (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) {
    return fail('仅接受普通JSON对象');
  }
  return Object.fromEntries(Object.entries(value));
}
function string(value: unknown): string { return typeof value === 'string' ? value : fail('需要字符串'); }
function number(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fail('需要有限数值');
}
function integer(value: unknown): number {
  const n = number(value); return Number.isSafeInteger(n) && n >= 0 ? n : fail('需要非负整数');
}
function array(value: unknown): unknown[] { return Array.isArray(value) ? value : fail('需要数组'); }
function boolean(value: unknown): boolean { return typeof value === 'boolean' ? value : fail('需要布尔值'); }
function date(value: unknown): string {
  const text = string(value); return Number.isFinite(Date.parse(text)) ? new Date(text).toISOString() : fail('日期无效');
}
function choice<T extends string>(value: unknown, values: readonly T[]): T {
  return values.find((item) => item === value) ?? fail('字段值不在契约范围');
}
function strings(value: unknown): string[] { return array(value).map(string); }
function unique(values: string[]) { if (new Set(values).size !== values.length) fail('标识重复'); }
function source(value: unknown): SourceRef | null {
  if (value === null) return null;
  if (!validateSourceRefs(value)) return fail('来源身份无效');
  return { ...value };
}
const forbidden = /^(token|apikey|password|authorization|secret|credentials|html|script|expression|graphdiv|history|selection|hover|cache)$/i;
function safeJson(value: unknown, warnings: string[], depth = 0): JsonValue {
  if (depth > 32) return fail('JSON嵌套超过上限');
  if (value === null) return null;
  if (typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) { warnings.push('非有限数值已转为null'); return null; }
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => safeJson(item, warnings, depth + 1));
  const object = record(value);
  return Object.fromEntries(Object.entries(object).map(([key, item]) => {
    if (forbidden.test(key.replace(/[_-]/g, '')) || ['__proto__', 'constructor', 'prototype'].includes(key)) {
      return fail(`禁止项目字段：${key}`);
    }
    return [key, safeJson(item, warnings, depth + 1)];
  }));
}
function jsonRecord(value: unknown) { return record(safeJson(value, [])); }

export function parseQuerySpec(value: unknown): QuerySpec {
  const v = record(value);
  return { filters: jsonRecord(v.filters), fields: strings(v.fields),
    order_by: v.order_by === null ? null : string(v.order_by) };
}
const aggregations = ['all', 'mean', 'min', 'max', 'p50', 'p25', 'p75'] as const;
function field(value: unknown) {
  const v = record(value); return { name: string(v.name), aggregation: choice(v.aggregation, aggregations) };
}
export function parseAnalysisSpec(value: unknown): AnalysisSpec {
  const v = record(value);
  if (v.schema_version !== 1) throw new SpecError('UNSUPPORTED_VERSION', '不支持此分析规格版本');
  const chart = record(v.chart);
  const quality = v.quality_rule === null ? null : record(v.quality_rule);
  const comparison = v.comparison === null ? null : record(v.comparison);
  function endpoint(value: unknown) {
    const item = record(value);
    return { source: source(item.source), dataset_id: item.dataset_id === null ? null : string(item.dataset_id),
      query_spec: parseQuerySpec(item.query_spec) };
  }
  let frequency: [number, number] | null = null;
  if (comparison && comparison.common_frequency_ghz !== null) {
    const pair = array(comparison.common_frequency_ghz);
    if (pair.length !== 2 || number(pair[0]) >= number(pair[1])) fail('比较频段无效');
    frequency = [number(pair[0]), number(pair[1])];
  }
  const spec: AnalysisSpec = {
    schema_version: 1, query_spec: parseQuerySpec(v.query_spec), plot_limit: integer(v.plot_limit),
    chart: { type: choice(chart.type, ['scatter', 'box', 'violin', 'swarm', 'line', 'wafer']),
      x_fields: strings(chart.x_fields), y_fields: array(chart.y_fields).map(field),
      z_field: chart.z_field === null ? null : field(chart.z_field),
      z_mode: choice(chart.z_mode, ['facet', 'color']), wafer_z: chart.wafer_z === null ? null : field(chart.wafer_z),
      wafer_facet: chart.wafer_facet === null ? null : string(chart.wafer_facet),
      show_data_points: boolean(chart.show_data_points) },
    units: Object.fromEntries(Object.entries(record(v.units)).map(([key, unit]) => [key, string(unit)])),
    quality_rule: quality === null ? null : { id: string(quality.id), version: integer(quality.version),
      label: string(quality.label), combine: choice(quality.combine, ['and']),
      predicates: array(quality.predicates).map((predicate) => {
        const p = record(predicate); return { field: string(p.field), op: choice(p.op, ['gte', 'lte']),
          value: number(p.value), unit: string(p.unit) };
      }) },
    comparison: comparison === null ? null : {
      scope: choice(comparison.scope, ['source', 'workspace_snapshot']), a: endpoint(comparison.a),
      b: endpoint(comparison.b), metrics: strings(comparison.metrics), group_fields: strings(comparison.group_fields),
      common_frequency_ghz: frequency, condition_policy: choice(comparison.condition_policy, ['require_equal', 'common_subset']),
      wafer_metric: comparison.wafer_metric === null ? null : string(comparison.wafer_metric),
      unified_color_scale: boolean(comparison.unified_color_scale) },
  };
  if (spec.plot_limit < 1 || spec.plot_limit > 200000) fail('图表上限无效');
  if (spec.comparison) {
    if (!spec.comparison.metrics.length) fail('比较指标为空');
    unique(spec.comparison.metrics); unique(spec.comparison.group_fields);
    for (const endpoint of [spec.comparison.a, spec.comparison.b]) {
      if (spec.comparison.scope === 'source' && (!endpoint.source || endpoint.source.source_revision === null)) fail('源比较版本未知');
      if (spec.comparison.scope === 'workspace_snapshot' && !endpoint.dataset_id) fail('快照比较缺数据表');
    }
  }
  return spec;
}
export function parseManifest(value: unknown): ResultManifest {
  const v = record(value), sampling = record(v.sampling);
  if (v.schema_version !== 1) throw new SpecError('UNSUPPORTED_VERSION', '结果说明版本不支持');
  const rate = sampling.rate === null ? null : number(sampling.rate);
  if (rate !== null && (rate <= 0 || rate > 1)) fail('采样率无效');
  const refs = array(v.source_refs).map((item) => source(item) ?? fail('来源列表不可为null'));
  const result: ResultManifest = { schema_version: 1, query_spec: parseQuerySpec(v.query_spec),
    source_refs: refs, matched_total: v.matched_total === null ? null : integer(v.matched_total),
    total_relation: choice(v.total_relation, ['exact', 'lower_bound', 'unknown']), returned: integer(v.returned),
    truncated: boolean(v.truncated), sampling: { method: choice(sampling.method, ['none', 'systematic']), rate },
    local_edits: integer(v.local_edits), excluded_rows: integer(v.excluded_rows), generated_at: string(v.generated_at),
    consistency: choice(v.consistency, ['verified', 'unverified']) };
  date(result.generated_at); // Validate without losing server microsecond precision.
  if (result.total_relation === 'unknown' && result.matched_total !== null) fail('未知总数必须null');
  if (result.total_relation === 'exact' && (result.matched_total === null || result.returned > result.matched_total)) fail('精确计数无效');
  if (result.consistency === 'verified' && refs.some((ref) => ref.source_revision === null)) fail('未证实来源不能verified');
  return result;
}

export function migrateLegacyExplore(input: unknown): AnalysisSpec {
  const v = record(input);
  function legacyField(value: unknown) {
    if (value === undefined || value === null) return null;
    if (typeof value === 'string') return { name: value, aggregation: choice('all', aggregations) };
    const entry = record(value);
    return { name: string(entry.name), aggregation: choice(entry.aggregation ?? entry.agg ?? 'all', aggregations) };
  }
  return { schema_version: 1, query_spec: { filters: v.filters ? jsonRecord(v.filters) : {}, fields: [], order_by: null },
    plot_limit: typeof v.limit === 'number' && v.limit > 0 ? Math.min(200000, Math.floor(v.limit)) : 200000,
    chart: { type: choice(v.chartType ?? 'scatter', ['scatter', 'box', 'violin', 'swarm', 'line', 'wafer']),
      x_fields: Array.isArray(v.xFields) ? strings(v.xFields) : [],
      y_fields: Array.isArray(v.yFields) ? v.yFields.map((value) => {
        if (typeof value === 'string') return { name: value, aggregation: choice('all', aggregations) };
        const entry = record(value); return { name: string(entry.name), aggregation: choice(entry.aggregation ?? entry.agg ?? 'all', aggregations) };
      }) : [],
      z_field: legacyField(v.zField), z_mode: v.zMode === 'color' ? 'color' : 'facet',
      wafer_z: legacyField(v.waferZ), wafer_facet: typeof v.waferFacet === 'string' && v.waferFacet !== '__none__' ? v.waferFacet : null,
      show_data_points: typeof v.showDataPoints === 'boolean' ? v.showDataPoints : true },
    units: {}, quality_rule: null, comparison: null };
}
