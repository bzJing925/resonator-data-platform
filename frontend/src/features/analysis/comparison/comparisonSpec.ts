import type { AnalysisComparison, AnalysisComparisonEndpoint, QuerySpec, SourceRef } from '../../../types/platformContracts';
import { validateSourceRefs, sourceTuple } from '../model/provenance';
import { parseQuerySpec } from '../project/projectCodec';
export const COMPARISON_METRICS = ['fs_ghz', 'fp_ghz', 'qs', 'qp', 'k2eff_pct', 'zs_ohm', 'zp_ohm'];
export type Shape = [number | string | null, number | string | null, number | string | null, string | null];
export interface ProcessingConditions {
  frequency_ghz: [number, number]; deembedded: boolean; deembed_method: string; process_type: string;
  config_digest: string; algorithm_digest: string; mapping_digest: string;
}
export interface ConditionEndpoint {
  source: SourceRef; query_spec: QuerySpec; conditions: ProcessingConditions | null;
  structures: Shape[]; matched_total: number | null; issues: { code: string; message: string }[];
}
export interface ConditionInspection {
  schema_version: 1; endpoints: { a: ConditionEndpoint; b: ConditionEndpoint }; units: Record<string, string>;
}
function object(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('条件元数据格式无效');
  return Object.fromEntries(Object.entries(value));
}
function text(value: unknown) { if (typeof value !== 'string') throw new Error('条件字段无效'); return value; }
function conditions(value: unknown): ProcessingConditions | null {
  if (value === null) return null;
  const v = object(value), f = v.frequency_ghz;
  if (!Array.isArray(f) || f.length !== 2 || f.some((n) => typeof n !== 'number' || !Number.isFinite(n)) || f[0] >= f[1] || typeof v.deembedded !== 'boolean') throw new Error('处理频段或去嵌条件无效');
  const digest = (key: string) => { const result = text(v[key]); if (!/^[a-f0-9]{64}$/i.test(result)) throw new Error('处理摘要未知'); return result; };
  return { frequency_ghz: [f[0], f[1]], deembedded: v.deembedded,
    deembed_method: text(v.deembed_method), process_type: text(v.process_type),
    config_digest: digest('config_digest'), algorithm_digest: digest('algorithm_digest'), mapping_digest: digest('mapping_digest') };
}
export function parseConditionEndpoint(value: unknown): ConditionEndpoint {
  const v = object(value);
  if (!validateSourceRefs(v.source) || v.source.source_device_key !== null || v.source.source_revision === null) throw new Error('条件来源版本未知');
  if (!Array.isArray(v.structures) || !Array.isArray(v.issues)) throw new Error('结构或问题列表无效');
  const structures: Shape[] = v.structures.map((item) => {
    if (!Array.isArray(item) || item.length !== 4 || item.slice(0, 3).some((x) => x !== null && typeof x !== 'string' && (typeof x !== 'number' || !Number.isFinite(x))) || (item[3] !== null && typeof item[3] !== 'string')) throw new Error('结构元组无效');
    return [item[0], item[1], item[2], item[3]];
  });
  const matched = v.matched_total;
  if (matched !== null && (typeof matched !== 'number' || !Number.isSafeInteger(matched) || matched < 0)) throw new Error('条件计数无效');
  return { source: v.source, query_spec: parseQuerySpec(v.query_spec), conditions: conditions(v.conditions), structures,
    matched_total: matched, issues: v.issues.map((issue) => { const i = object(issue); return { code: text(i.code), message: text(i.message) }; }) };
}
export function validateConditionInspection(value: unknown, comparison: AnalysisComparison): ConditionInspection {
  const v = object(value), e = object(v.endpoints);
  if (v.schema_version !== 1) throw new Error('条件协议版本不支持');
  const endpoints = { a: parseConditionEndpoint(e.a), b: parseConditionEndpoint(e.b) };
  for (const side of ['a', 'b'] as const) {
    const expected = comparison[side].source, actual = endpoints[side].source;
    if (!expected || sourceTuple(actual) !== sourceTuple(expected) || actual.source_revision !== expected.source_revision) throw new Error('条件来源版本已变化');
    if (JSON.stringify(endpoints[side].query_spec) !== JSON.stringify(comparison[side].query_spec)) throw new Error('条件查询规格不一致');
  }
  return { schema_version: 1, endpoints, units: Object.fromEntries(Object.entries(object(v.units)).map(([key, unit]) => [key, text(unit)])) };
}
export function makeComparison(a: AnalysisComparisonEndpoint, b: AnalysisComparisonEndpoint, scope: AnalysisComparison['scope'], metrics: string[]): AnalysisComparison {
  if (!metrics.length || metrics.some((m) => !COMPARISON_METRICS.includes(m))) throw new Error('请选择可核验指标');
  if (scope === 'source' && [a, b].some((e) => !e.source || !validateSourceRefs(e.source) || e.source.source_revision === null || e.source.source_device_key !== null || e.dataset_id !== null)) throw new Error('源比较必须绑定已知批次版本');
  if (scope === 'workspace_snapshot' && [a, b].some((e) => !e.dataset_id || e.source !== null)) throw new Error('快照比较必须选择工作表');
  return { scope, a, b, metrics, group_fields: scope === 'source' ? ['eg', 'fl', 'ag', 's_param_port'] : ['structureType', 's_param_port'],
    common_frequency_ghz: null, condition_policy: 'require_equal', wafer_metric: null, unified_color_scale: true };
}
