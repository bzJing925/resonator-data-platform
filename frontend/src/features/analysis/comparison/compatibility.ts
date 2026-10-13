import type { QuerySpec } from '../../../types/platformContracts';
import type { ConditionInspection, Shape } from './comparisonSpec';
export interface CompatibilityIssue { field: string; a: unknown; b: unknown; message: string; blocking: boolean }
const key = (shape: Shape) => JSON.stringify(shape);
export function inspectCompatibility(input: ConditionInspection, metrics: string[], expectedUnits: Record<string, string>) {
  const { a, b } = input.endpoints, issues: CompatibilityIssue[] = [];
  for (const side of [a, b]) for (const issue of side.issues) issues.push({ field: issue.code, a: a.issues, b: b.issues, message: issue.message, blocking: true });
  if (!a.conditions || !b.conditions) issues.push({ field: 'conditions', a: a.conditions, b: b.conditions, message: '处理证据未知，不能视为相同', blocking: true });
  if (a.conditions && b.conditions) for (const field of Object.keys(a.conditions) as (keyof typeof a.conditions)[]) {
    if (JSON.stringify(a.conditions[field]) !== JSON.stringify(b.conditions[field])) issues.push({ field, a: a.conditions[field], b: b.conditions[field], message: field === 'frequency_ghz' ? '提取频段不同；裁剪标量不能统一频段' : '处理条件不同，需取得相同条件的重算结果', blocking: true });
  }
  for (const metric of metrics) {
    if (!input.units[metric] || !expectedUnits[metric] || input.units[metric] !== expectedUnits[metric]) issues.push({ field: metric, a: input.units[metric] ?? null, b: expectedUnits[metric] ?? null, message: '指标单位未知或不一致', blocking: true });
  }
  const aSet = new Set(a.structures.map(key)), bSet = new Set(b.structures.map(key));
  const common = a.structures.filter((shape) => bSet.has(key(shape)));
  const differ = aSet.size !== bSet.size || [...aSet].some((shape) => !bSet.has(shape));
  if (differ) issues.push({ field: 'structure/s_param_port', a: a.structures, b: b.structures, message: common.length ? '端口/结构不同，必须明确选择共同元组子集' : '没有共同端口/结构子集', blocking: !common.length });
  if (a.structures.some((shape) => !shape[3]) || b.structures.some((shape) => !shape[3])) issues.push({ field: 's_param_port', a: a.structures, b: b.structures, message: '端口未知，不能视为相同', blocking: true });
  return { issues, common, requiresSubset: differ, blocked: issues.some((issue) => issue.blocking) };
}
export function applyCommonSubset(spec: QuerySpec, selected: Shape[]): QuerySpec {
  if (!selected.length) throw new Error('请选择非空共同子集');
  const subset = { op: 'or', children: selected.map((shape) => ({ op: 'and', children:
    ['eg', 'fl', 'ag', 's_param_port'].map((field, i) => shape[i] === null
      ? { field, op: 'is_null' } : { field, op: 'eq', value: shape[i] }) })) };
  return { ...spec, filters: { op: 'and', children: [spec.filters, subset] } };
}
