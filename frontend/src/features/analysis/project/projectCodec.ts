import type { AnalysisSpec, QuerySpec, ResultManifest, SourceRef } from '../../../types/platformContracts';
import { createAnalysisDataset, type AnalysisDataset, type DatasetRegistry } from '../model/dataset';
import { createHistoryState } from '../model/history';
import { createSelectionState } from '../model/selection';
import type { PointStyleState, WorkspacePresent } from '../model/commands';
import type { WorkspaceState } from '../workspace';
import { jsonCell, validateSourceRefs, type ExcludedRow, type JsonValue, type LocalEdit } from '../model/provenance';
import {
  PROJECT_CHUNK_ROWS, PROJECT_IMPORT_MAX_BYTES, PROJECT_IMPORT_MAX_ROWS, ProjectError,
  type ProjectBackup, type ProjectHead, type ProjectVersionWithRows, type RegistryMetadata,
} from './projectTypes';

function fail(message: string): never { throw new ProjectError('CORRUPT', message); }
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
function canonical(value: unknown): string {
  function sorted(item: JsonValue): JsonValue {
    if (Array.isArray(item)) return item.map(sorted);
    if (item !== null && typeof item === 'object') return Object.fromEntries(
      Object.entries(item).sort(([a], [b]) => a.localeCompare(b)).map(([key, cell]) => [key, sorted(cell)]));
    return item;
  }
  return JSON.stringify(sorted(jsonCell(value)));
}

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
  if (v.schema_version !== 1) throw new ProjectError('UNSUPPORTED_VERSION', '不支持此分析规格版本');
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
  if (v.schema_version !== 1) throw new ProjectError('UNSUPPORTED_VERSION', '结果说明版本不支持');
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

function edits(value: unknown): LocalEdit[] {
  return array(value).map((item) => {
    const v = record(item); return { datasetId: string(v.datasetId), rowId: string(v.rowId),
      column: string(v.column), before: jsonCell(v.before), after: jsonCell(v.after), source: source(v.source) };
  });
}
function exclusions(value: unknown): ExcludedRow[] {
  return array(value).map((item) => {
    const v = record(item); return { datasetId: string(v.datasetId), rowId: string(v.rowId),
      reason: string(v.reason), source: source(v.source),
      originalRow: Object.fromEntries(Object.entries(record(v.originalRow)).map(([key, cell]) => [key, jsonCell(cell)])) };
  });
}
function dataset(value: unknown): AnalysisDataset {
  const v = record(value), id = string(v.id), rawRows = array(v.rows).map(record);
  const ids = rawRows.map((row) => string(row.rowId)); unique(ids);
  for (const row of rawRows) {
    if (!string(row.rowId).trim() || row.__rowId !== row.rowId || row.datasetId !== id) fail('行身份归属无效');
  }
  const d = createAnalysisDataset({ id, name: string(v.name), kind: choice(v.kind, ['query', 'derived', 'empty']),
    rows: rawRows, stats: v.stats === null ? null : record(v.stats),
    manifest: v.manifest === null ? null : parseManifest(v.manifest) });
  d.revision = integer(v.revision);
  if (d.revision < 1) fail('工作表revision无效');
  const map = record(v.fieldMap);
  d.fieldMap = { structureType: map.structureType === null ? null : string(map.structureType),
    areaReality: map.areaReality === null ? null : string(map.areaReality) };
  d.columns = array(v.columns).map((item) => {
    const c = record(item); return { key: string(c.key), sourceField: c.sourceField === null ? null : string(c.sourceField) };
  });
  unique(d.columns.map((c) => c.key));
  d.provenance = Object.fromEntries(Object.entries(record(v.provenance)).map(([rowId, ref]) => [rowId, source(ref)]));
  d.edits = edits(v.edits); d.exclusions = exclusions(v.exclusions);
  unique(d.exclusions.map((item) => item.rowId));
  const rowIds = new Set(ids.concat(d.exclusions.map((e) => e.rowId)));
  if (rowIds.size !== ids.length + d.exclusions.length) fail('排除行仍在工作表');
  for (const item of [...d.edits, ...d.exclusions]) {
    if (item.datasetId !== id || !rowIds.has(item.rowId)) fail('账本引用无效');
  }
  for (const rowId of rowIds) if (!(rowId in d.provenance)) fail('行缺来源登记');
  for (const row of rawRows) {
    const ref = row._source_ref === undefined ? null : source(row._source_ref);
    if (canonical(ref) !== canonical(d.provenance[string(row.rowId)])) fail('行来源与来源登记不一致');
  }
  for (const excluded of d.exclusions) {
    if (excluded.originalRow.rowId !== excluded.rowId || excluded.originalRow.datasetId !== id
      || canonical(excluded.source) !== canonical(d.provenance[excluded.rowId])) fail('排除行身份无效');
  }
  for (const edit of d.edits) if (canonical(edit.source) !== canonical(d.provenance[edit.rowId])) fail('编辑来源无效');
  for (const rowId of Object.keys(d.provenance)) if (!rowIds.has(rowId)) fail('来源登记行不存在');
  if (d.manifest && (d.manifest.returned !== d.rows.length || d.manifest.excluded_rows !== d.exclusions.length
      || d.manifest.local_edits !== d.edits.length)) fail('结果说明与账本不一致');
  return d;
}
function styles(value: unknown): PointStyleState {
  return Object.fromEntries(Object.entries(record(value)).map(([datasetId, rows]) => [datasetId,
    Object.fromEntries(Object.entries(record(rows)).map(([rowId, item]) => {
      const s = record(item);
      return [rowId, { ...(s.color === undefined ? {} : { color: string(s.color) }),
        ...(s.symbol === undefined ? {} : { symbol: string(s.symbol) }),
        ...(s.size === undefined ? {} : { size: number(s.size) }),
        ...(s.opacity === undefined ? {} : { opacity: number(s.opacity) }),
        ...(s.outlineColor === undefined ? {} : { outlineColor: string(s.outlineColor) }),
        ...(s.outlineWidth === undefined ? {} : { outlineWidth: number(s.outlineWidth) }) }];
    }))]));
}

export function serializeWorkspace(state: WorkspacePresent, spec: AnalysisSpec,
  head: ProjectHead, notes = ''): ProjectVersionWithRows {
  const byId: RegistryMetadata['byId'] = {};
  const datasets: Record<string, AnalysisDataset> = {};
  const manifests: Record<string, ResultManifest> = {}, provenance: Record<string, SourceRef | null> = {};
  const chunkRefs: Record<string, string[]> = {};
  const allEdits: LocalEdit[] = [], allExclusions: ExcludedRow[] = [];
  for (const id of state.registry.order) {
    const d = state.registry.byId[id];
    const { rows: _rows, provenance: sourceRegistry, ...metadata } = d;
    // Reducer datasets/provenance are immutable, just like the captured rows below.
    // Avoid cloning the entire source registry for every single-cell autosave.
    byId[id] = { ...structuredClone(metadata), provenance: sourceRegistry }; datasets[id] = d;
    chunkRefs[id] = Array.from({ length: Math.ceil(d.rows.length / PROJECT_CHUNK_ROWS) }, () => crypto.randomUUID());
    if (d.manifest) manifests[id] = structuredClone(d.manifest);
    for (const [rowId, ref] of Object.entries(d.provenance)) provenance[JSON.stringify([id, rowId])] = ref;
    allEdits.push(...d.edits); allExclusions.push(...d.exclusions);
  }
  return { head: { ...head }, datasets, version: { projectId: head.projectId, versionId: head.currentVersionId,
    schemaVersion: 1, baseGeneration: Math.max(0, head.generation - 1), status: 'complete',
    createdAt: new Date().toISOString(), spec: structuredClone(spec),
    registryMetadata: { ...state.registry, byId }, pointStyles: structuredClone(state.pointStyles),
    activeTabId: state.activeTabId, manifests, chunkRefs, provenance, edits: allEdits,
    exclusions: allExclusions, notes } };
}
export function restoreWorkspace(project: ProjectVersionWithRows): WorkspaceState {
  const registry: DatasetRegistry = { ...structuredClone(project.version.registryMetadata),
    byId: Object.fromEntries(Object.entries(project.datasets).map(([id, d]) => [id, { ...d, rows: [...d.rows] }])) };
  if (project.version.status !== 'complete') fail('不能恢复草稿版本');
  return { registry, spec: project.version.spec, notes: project.version.notes, readOnly: project.head.kind === 'snapshot',
    projectExtras: project.version.extras, activeTabId: project.version.activeTabId, pointStyles: structuredClone(project.version.pointStyles),
    selection: createSelectionState(), history: createHistoryState() };
}

export function validateProjectBackup(input: unknown): ProjectBackup {
  const warnings: string[] = [];
  if (typeof input === 'string' && new TextEncoder().encode(input).byteLength > PROJECT_IMPORT_MAX_BYTES) fail('项目文件超过100MiB');
  let raw: unknown = input;
  if (typeof input === 'string') { try { raw = JSON.parse(input); } catch { fail('项目JSON无效'); } }
  const envelope = record(raw);
  if (envelope.schema_version !== 1) throw new ProjectError('UNSUPPORTED_VERSION', '项目格式版本不支持');
  if (envelope.format !== 'aln-analysis-project') fail('未知项目格式');
  const cleaned = record(safeJson(raw, warnings));
  if (new TextEncoder().encode(JSON.stringify(cleaned)).byteLength > PROJECT_IMPORT_MAX_BYTES) fail('项目超过100MiB');
  const p = record(cleaned.project), h = record(p.head), v = record(p.version), registry = record(v.registryMetadata);
  if (h.schemaVersion !== 1 || v.schemaVersion !== 1) throw new ProjectError('UNSUPPORTED_VERSION', '项目结构版本不支持');
  const head: ProjectHead = { projectId: string(h.projectId), currentVersionId: string(h.currentVersionId),
    generation: integer(h.generation), name: string(h.name), kind: choice(h.kind, ['draft', 'named', 'snapshot']),
    updatedAt: date(h.updatedAt), lastOpenedAt: date(h.lastOpenedAt), schemaVersion: 1 };
  if (!head.projectId.trim() || !head.currentVersionId.trim() || head.generation < 1 || v.projectId !== head.projectId || v.versionId !== head.currentVersionId || v.status !== 'complete') fail('版本指针无效');
  const datasets = Object.fromEntries(Object.entries(record(p.datasets)).map(([id, item]) => {
    const d = dataset(item); if (d.id !== id) fail('工作表键无效'); return [id, d];
  }));
  const order = strings(registry.order); unique(order);
  if (order.length !== Object.keys(datasets).length || order.some((id) => !datasets[id])) fail('工作表顺序无效');
  if (Object.values(datasets).reduce((n, d) => n + d.rows.length + d.exclusions.length, 0) > PROJECT_IMPORT_MAX_ROWS) fail('项目超过500000行');
  const byId: RegistryMetadata['byId'] = {};
  for (const id of order) {
    const { rows: _rows, ...metadata } = datasets[id];
    if (!record(registry.byId)[id]) fail('工作表元数据缺失');
    const incoming = record(record(registry.byId)[id]);
    if (canonical(incoming) !== canonical(metadata)) fail('工作表元数据与行存储不一致');
    byId[id] = metadata;
  }
  const chartPages = Object.fromEntries(Object.entries(record(registry.chartPages)).map(([id, item]) => {
    const c = record(item), datasetId = c.datasetId === null ? null : string(c.datasetId);
    if (c.id !== id || c.kind !== 'chart' || (datasetId && !datasets[datasetId])) fail('图表引用无效');
    return [id, { id, name: string(c.name), kind: 'chart' as const, datasetId }];
  }));
  const chartPageOrder = strings(registry.chartPageOrder); unique(chartPageOrder);
  if (chartPageOrder.length !== Object.keys(chartPages).length || chartPageOrder.some((id) => !chartPages[id])) fail('图表顺序无效');
  const activeTabId = string(v.activeTabId);
  if (!datasets[activeTabId] && !chartPages[activeTabId]) fail('活动页不存在');
  const chunkRefs = Object.fromEntries(Object.entries(record(v.chunkRefs)).map(([id, refs]) => [id, strings(refs)]));
  const allRefs: string[] = [];
  for (const id of order) {
    if (chunkRefs[id]?.length !== Math.ceil(datasets[id].rows.length / PROJECT_CHUNK_ROWS)) fail('行块引用数量无效');
    allRefs.push(...chunkRefs[id]);
  }
  unique(allRefs);
  if (Object.keys(chunkRefs).length !== order.length) fail('多余块引用');
  const manifests = Object.fromEntries(Object.entries(record(v.manifests)).map(([id, m]) => [id, parseManifest(m)]));
  const provenance = Object.fromEntries(Object.entries(record(v.provenance)).map(([id, ref]) => [id, source(ref)]));
  const parsedEdits = edits(v.edits), parsedExclusions = exclusions(v.exclusions);
  const expectedEdits = order.flatMap((id) => datasets[id].edits), expectedExclusions = order.flatMap((id) => datasets[id].exclusions);
  if (canonical(parsedEdits) !== canonical(expectedEdits) || canonical(parsedExclusions) !== canonical(expectedExclusions)) fail('项目账本不一致');
  const expectedProvenance = Object.fromEntries(order.flatMap((id) => Object.entries(datasets[id].provenance).map(([rowId, ref]) => [JSON.stringify([id, rowId]), ref])));
  if (canonical(provenance) !== canonical(expectedProvenance)) fail('项目来源登记不一致');
  const expectedManifests = Object.fromEntries(order.filter((id) => datasets[id].manifest).map((id) => [id, datasets[id].manifest]));
  if (canonical(manifests) !== canonical(expectedManifests)) fail('项目结果说明不一致');
  const knownVersionKeys = new Set(['projectId', 'versionId', 'schemaVersion', 'baseGeneration', 'status', 'createdAt', 'spec', 'registryMetadata', 'pointStyles', 'activeTabId', 'manifests', 'chunkRefs', 'provenance', 'edits', 'exclusions', 'notes', 'extras']);
  const extras: Record<string, JsonValue> = Object.fromEntries(Object.entries(v).filter(([key]) => !knownVersionKeys.has(key)).map(([key, value]) => [key, jsonCell(value)]));
  Object.assign(extras, v.extras === undefined ? {} : record(v.extras));
  if (warnings.length) extras.validation_warnings = warnings;
  const version = { projectId: head.projectId, versionId: head.currentVersionId, schemaVersion: 1 as const,
    baseGeneration: integer(v.baseGeneration), status: 'complete' as const, createdAt: date(v.createdAt),
    spec: parseAnalysisSpec(v.spec), registryMetadata: { byId, order, chartPages, chartPageOrder },
    pointStyles: styles(v.pointStyles), activeTabId, manifests, chunkRefs, provenance,
    edits: parsedEdits, exclusions: parsedExclusions, notes: string(v.notes), extras };
  if (version.baseGeneration !== head.generation - 1) fail('generation关系无效');
  for (const [id, rowStyles] of Object.entries(version.pointStyles)) {
    if (!datasets[id]) fail('点样式工作表不存在');
    const rowIds = new Set(datasets[id].rows.map((row) => row.rowId));
    for (const rowId of Object.keys(rowStyles)) if (!rowIds.has(rowId) && !rowId.startsWith('@@analysis-metric/')) fail('点样式行不存在');
  }
  if (version.spec.comparison?.scope === 'workspace_snapshot') {
    for (const endpoint of [version.spec.comparison.a, version.spec.comparison.b]) if (!datasets[endpoint.dataset_id ?? '']) fail('比较工作表不存在');
  }
  return { format: 'aln-analysis-project', schema_version: 1, exported_at: date(cleaned.exported_at),
    project: { head, version, datasets } };
}

export function migrateProjectBackup(input: unknown) { return validateProjectBackup(input); }
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
