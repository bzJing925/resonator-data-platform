import type { AnalysisSpec } from '../../../types/platformContracts';
import { createDatasetRegistry } from '../model/dataset';
import { createSelectionState } from '../model/selection';
import { migrateLegacyExplore, serializeWorkspace, validateProjectBackup } from './projectCodec';
import { PROJECT_IMPORT_MAX_BYTES, ProjectError, type ProjectBackup, type ProjectHead, type ProjectKind, type ProjectVersionWithRows } from './projectTypes';

function forbidCurves(value: unknown, depth = 0): void {
  if (depth > 32) throw new ProjectError('CORRUPT', '项目嵌套超过上限');
  if (!value || typeof value !== 'object') return;
  for (const [key, item] of Object.entries(value)) {
    if (/^(raw_curve|raw_curves|true_curves|spectrum|sparam|s_parameters)$/i.test(key)) {
      throw new ProjectError('CORRUPT', '备份仅包含表格指标，不能携带原始曲线');
    }
    forbidCurves(item, depth + 1);
  }
}
export function exportProjectBackup(project: ProjectVersionWithRows): string {
  if (project.version.status !== 'complete') throw new ProjectError('CONFLICT', '请先保存完整项目版本');
  forbidCurves(project);
  const backup: ProjectBackup = { format: 'aln-analysis-project', schema_version: 1,
    exported_at: new Date().toISOString(), project };
  return JSON.stringify(validateProjectBackup(backup), null, 2);
}
export function importProjectBackup(input: unknown): ProjectVersionWithRows {
  const backup = validateProjectBackup(input);
  forbidCurves(backup);
  const project = structuredClone(backup.project);
  const importedFrom = { projectId: project.head.projectId, versionId: project.version.versionId };
  const now = new Date().toISOString(), projectId = crypto.randomUUID(), versionId = crypto.randomUUID();
  project.head = { ...project.head, projectId, currentVersionId: versionId, generation: 1,
    updatedAt: now, lastOpenedAt: now };
  project.version = { ...project.version, projectId, versionId, baseGeneration: 0, createdAt: now,
    extras: { ...project.version.extras, imported_from: importedFrom },
    chunkRefs: Object.fromEntries(Object.entries(project.version.chunkRefs).map(([id, refs]) =>
      [id, refs.map(() => crypto.randomUUID())])) };
  return project;
}
export async function readProjectFile(file: File) {
  if (file.size > PROJECT_IMPORT_MAX_BYTES) throw new ProjectError('CORRUPT', '项目文件超过100MiB');
  return importProjectBackup(await file.text());
}
export function forkProject(project: ProjectVersionWithRows, name: string, kind: ProjectKind): ProjectVersionWithRows {
  const copy = importProjectBackup(exportProjectBackup(project));
  copy.head.name = name; copy.head.kind = kind;
  return copy;
}
export function emptyProject(spec: AnalysisSpec = migrateLegacyExplore({}), name = '未命名项目'): ProjectVersionWithRows {
  const now = new Date().toISOString();
  const head: ProjectHead = { projectId: crypto.randomUUID(), currentVersionId: crypto.randomUUID(),
    generation: 1, name, kind: 'draft', schemaVersion: 1, updatedAt: now, lastOpenedAt: now };
  return serializeWorkspace({ registry: createDatasetRegistry(), activeTabId: 'chart',
    selection: createSelectionState(), pointStyles: {} }, spec, head);
}
export function templateSpec(spec: AnalysisSpec): AnalysisSpec {
  function removeSources(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(removeSources).filter((item) => item !== null);
    if (!value || typeof value !== 'object') return value;
    const row = Object.fromEntries(Object.entries(value));
    if (typeof row.field === 'string' && ['batch_no', 'batch_id', 'origin_id', 'source_batch_id', 'source_device_key'].includes(row.field)) return null;
    return Object.fromEntries(Object.entries(row)
      .filter(([key]) => !['batch_no', 'batch_id', 'source_ref', 'source_refs', 'origin_id', 'source_batch_id', 'source_device_key'].includes(key))
      .map(([key, item]) => [key, removeSources(item)]).filter(([, item]) => item !== null));
  }
  const cleaned = removeSources(spec.query_spec.filters);
  const filters = cleaned && typeof cleaned === 'object' && !Array.isArray(cleaned)
    ? Object.fromEntries(Object.entries(cleaned)) : {};
  return { ...structuredClone(spec), query_spec: { ...spec.query_spec, filters }, comparison: null };
}

export function validateTemplateFields(spec: AnalysisSpec, fields: { name: string; unit?: string }[] | null) {
  const names = new Set([...spec.query_spec.fields, ...spec.chart.x_fields,
    ...spec.chart.y_fields.map((f) => f.name), ...(spec.chart.z_field ? [spec.chart.z_field.name] : []),
    ...(spec.chart.wafer_z ? [spec.chart.wafer_z.name] : []), ...(spec.chart.wafer_facet ? [spec.chart.wafer_facet] : []),
    ...Object.keys(spec.units), ...(spec.quality_rule?.predicates.map((p) => p.field) ?? [])]);
  function filterFields(value: unknown) {
    if (!value || typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value)) {
      if (key === 'field' && typeof item === 'string') names.add(item);
      else if (!['field', 'op', 'children', 'value'].includes(key) && !Array.isArray(value)) names.add(key);
      if (key === 'children') filterFields(item);
      if (Array.isArray(value)) filterFields(item);
    }
  }
  filterFields(spec.query_spec.filters);
  if (names.size && !fields) throw new ProjectError('UNAVAILABLE', '字段元数据不可用，不能应用模板');
  for (const name of names) {
    const actual = fields?.find((f) => f.name === name);
    if (!actual) throw new ProjectError('CORRUPT', `模板字段不可用：${name}`);
    const unit = spec.units[name];
    if (unit !== undefined && unit !== (actual.unit ?? '')) throw new ProjectError('CORRUPT', `模板单位不一致：${name}（${unit} / ${actual.unit ?? '未知'}）`);
    for (const predicate of spec.quality_rule?.predicates ?? []) {
      if (predicate.field === name && predicate.unit !== (actual.unit ?? '')) throw new ProjectError('CORRUPT', `规则单位不一致：${name}`);
    }
  }
}
