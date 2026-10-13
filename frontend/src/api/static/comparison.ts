import type { AnalysisComparison, AnalysisComparisonEndpoint, QualityRuleV1 } from '../../types/platformContracts';
import { validateSourceRefs, sourceTuple } from '../../features/analysis/model/provenance';
import { parseConditionEndpoint, type Shape } from '../../features/analysis/comparison/comparisonSpec';
import { inspectCompatibility } from '../../features/analysis/comparison/compatibility';
import { comparisonResult, summarizeCohort } from '../../features/analysis/comparison/comparisonData';
import { unverifiedManifest } from '../../features/analysis/result/resultManifest';
import { ensureTable, assertDeviceSnapshot, deviceSnapshotMetadata, getConn, normalizeRow } from './duckdb';
import { filtersToWhere } from './index';

function object(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('静态条件证据缺失');
  return Object.fromEntries(Object.entries(value));
}
export function publishedUnits(metrics: string[]) {
  const metadata = object(deviceSnapshotMetadata()), units = object(metadata.comparison_units);
  return Object.fromEntries(metrics.map((metric) => {
    if (typeof units[metric] !== 'string' || !units[metric]) throw new Error(`静态指标单位未知：${metric}`);
    return [metric, units[metric]];
  }));
}
export function publishedSources() {
  const raw = deviceSnapshotMetadata();
  if (!raw) return [];
  const metadata = object(raw);
  if (typeof metadata.devices_sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(metadata.devices_sha256) || !Array.isArray(metadata.comparison_sources)) return [];
  return metadata.comparison_sources.map((item) => {
    const record = object(item);
    if (!validateSourceRefs(record.source) || record.source.source_device_key !== null || record.source.source_revision === null
      || typeof record.batch_id !== 'number' || !Number.isSafeInteger(record.batch_id) || record.batch_id < 1
      || !Array.isArray(record.proven_metrics) || record.proven_metrics.some((m) => typeof m !== 'string')) throw new Error('静态来源证据无效');
    const endpoint = parseConditionEndpoint({ source: record.source, query_spec: { filters: {}, fields: [], order_by: null },
      conditions: record.conditions, structures: [], matched_total: 0, issues: [] });
    if (!endpoint.conditions) throw new Error('静态处理证据未知');
    return { ...endpoint, batch_id: record.batch_id, proven_metrics: record.proven_metrics };
  });
}
function quote(name: string) {
  if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(name)) throw new Error('静态比较字段无效');
  return `"${name}"`;
}
async function readCohort(endpoint: AnalysisComparisonEndpoint, comparison: AnalysisComparison, rule: QualityRuleV1 | null) {
  const source = endpoint.source;
  if (!source) throw new Error('比较来源缺失');
  const record = publishedSources().find((item) => sourceTuple(item.source) === sourceTuple(source));
  if (!record || record.source.source_revision !== source.source_revision) throw new Error('SOURCE_CHANGED：静态来源版本未知或已变化');
  const needed = [...comparison.metrics, ...(rule?.predicates.map((p) => p.field) ?? [])];
  if (needed.some((field) => !record.proven_metrics.includes(field))) throw new Error('CONDITIONS_UNKNOWN：当前指标缺少已发布证据');
  const conn = await getConn(), where = `(${filtersToWhere(endpoint.query_spec.filters)}) AND batch_id=${record.batch_id}`;
  const count = (await conn.query(`SELECT COUNT(*) AS n FROM devices WHERE ${where}`)).toArray()[0].toJSON();
  const total = Number(count.n);
  if (!Number.isSafeInteger(total) || total > 200000) throw new Error('静态完整比较最多200000行；请使用服务器或桌面，不能以截断结果代替');
  const columns = [...new Set(['id', 'batch_id', 'source_device_key', 'batch_no', 'wafer', 'x', 'y', 'pf', 'eg', 'fl', 'ag', 's_param_port', ...needed])];
  const order = endpoint.query_spec.order_by;
  const sort = order ? `${quote(order.replace(/^-/, ''))} ${order.startsWith('-') ? 'DESC' : 'ASC'},id` : 'id';
  const rows: Record<string, unknown>[] = []; let bytes = 0;
  for (let offset = 0; offset < total; offset += 2000) {
    const block = (await conn.query(`SELECT ${columns.map(quote).join(',')} FROM devices WHERE ${where} ORDER BY ${sort} LIMIT 2000 OFFSET ${offset}`))
      .toArray().map((r) => ({ ...normalizeRow(r.toJSON()), _source_ref: { ...source, source_device_key: r.toJSON().source_device_key } }));
    bytes += new TextEncoder().encode(JSON.stringify(block)).byteLength;
    if (bytes > 100 * 1024 * 1024) throw new Error('静态完整比较超过100MiB；请使用服务器或桌面');
    rows.push(...block);
  }
  if (rows.length !== total || rows.some((row) => !validateSourceRefs(row._source_ref) || row._source_ref.source_device_key === null)) throw new Error('静态完整比较行数或来源身份无效');
  const manifest = { ...unverifiedManifest(endpoint.query_spec, total, total), source_refs: [source], consistency: 'verified' as const };
  const shapes = new Map<string, Shape>();
  for (const row of rows) {
    const shape = [row.eg ?? null, row.fl ?? null, row.ag ?? null, row.s_param_port ?? null];
    const shapeKey = JSON.stringify(shape);
    if (!shapes.has(shapeKey)) {
      const checked = parseConditionEndpoint({ ...record, query_spec: endpoint.query_spec, structures: [shape], matched_total: total });
      shapes.set(shapeKey, checked.structures[0]);
    }
  }
  return { rows, manifest, endpoint: { source, query_spec: endpoint.query_spec, conditions: record.conditions,
    structures: [...shapes.values()], matched_total: total, issues: [] } };
}
export async function inspectStaticConditions(comparison: AnalysisComparison, rule: QualityRuleV1 | null) {
  await ensureTable('devices'); await assertDeviceSnapshot();
  const ruleUnits = publishedUnits(rule?.predicates.map((p) => p.field) ?? []);
  for (const predicate of rule?.predicates ?? []) if (predicate.unit !== ruleUnits[predicate.field] || !Number.isFinite(predicate.value)) throw new Error('规则单位未知或不同');
  const a = await readCohort(comparison.a, comparison, rule), b = await readCohort(comparison.b, comparison, rule);
  await assertDeviceSnapshot();
  return { schema_version: 1 as const, endpoints: { a: a.endpoint, b: b.endpoint }, units: publishedUnits(comparison.metrics) };
}
export async function loadStaticComparison(comparison: AnalysisComparison, rule: QualityRuleV1 | null, units: Record<string, string>) {
  await ensureTable('devices'); await assertDeviceSnapshot();
  const ruleUnits = publishedUnits(rule?.predicates.map((p) => p.field) ?? []);
  for (const predicate of rule?.predicates ?? []) if (predicate.unit !== ruleUnits[predicate.field] || !Number.isFinite(predicate.value)) throw new Error('规则单位未知或不同');
  const a = await readCohort(comparison.a, comparison, rule), b = await readCohort(comparison.b, comparison, rule);
  const review = inspectCompatibility({ schema_version: 1, endpoints: { a: a.endpoint, b: b.endpoint }, units: publishedUnits(comparison.metrics) }, comparison.metrics, units);
  if (comparison.common_frequency_ghz && JSON.stringify(comparison.common_frequency_ghz) !== JSON.stringify(a.endpoint.conditions?.frequency_ghz)) throw new Error('提取频段不同，不能裁剪标量统一频段');
  if (review.blocked || review.requiresSubset) throw new Error('比较条件不一致；请先明确选择共同子集重新审查');
  await assertDeviceSnapshot();
  return comparisonResult(summarizeCohort(a.rows, a.manifest, comparison, rule), summarizeCohort(b.rows, b.manifest, comparison, rule), comparison, units);
}
