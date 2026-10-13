import { ensureTable, getConn, configureSnapshotRoot, assertDeviceSnapshot, deviceSnapshotMetadata, normalizeRow } from './duckdb';
import { filtersToWhere } from './queryFilters';
import type { QuerySpec, SourceRef } from '../../types/platformContracts';
import { validateSourceRefs, sourceTuple } from '../../features/analysis/model/provenance';
import { unverifiedManifest } from '../../features/analysis/result/resultManifest';
export interface StaticExportInput { root: string; query_spec: QuerySpec; expected_sources: SourceRef[] }
function quote(field: string, columns: Set<string>) { if (!columns.has(field) || !/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(field)) throw new Error(`导出字段不允许：${field}`); return `"${field}"`; }
function value(input: unknown) { if (input == null || input === '' || (typeof input === 'number' && !Number.isFinite(input))) return '""'; let text = String(input); if (typeof input === 'string' && /^[=+\-@\t\r]/.test(text)) text = "'" + text; return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text; }
export async function runStaticFullExport(input: StaticExportInput, progress: (n: number) => void) {
  configureSnapshotRoot(input.root); await ensureTable('devices'); await assertDeviceSnapshot();
  const metadata = deviceSnapshotMetadata() as { comparison_sources?: { batch_id: number; source: SourceRef }[] } | null;
  if (!metadata?.comparison_sources || !input.expected_sources.every((source) => validateSourceRefs(source) && source.source_revision != null)) throw new Error('来源证据未知，不能完整源导出');
  const conn = await getConn();
  const columns = new Set((await conn.query('DESCRIBE devices')).toArray().map((row) => String(row.toJSON().column_name)));
  // Check every filter field against the actual published schema before producing SQL.
  const verify = (node: unknown): void => { if (!node || typeof node !== 'object') return; const obj = node as Record<string, unknown>; if (typeof obj.field === 'string') quote(obj.field, columns); else if (Array.isArray(obj.children)) obj.children.forEach(verify); else Object.keys(obj).forEach((key) => quote(key, columns)); };
  verify(input.query_spec.filters);
  const where = filtersToWhere(input.query_spec.filters);
  const ids = (await conn.query(`SELECT DISTINCT batch_id FROM devices WHERE ${where}`)).toArray().map((r) => Number(r.toJSON().batch_id));
  const sources = ids.map((id) => metadata.comparison_sources!.find((entry) => entry.batch_id === id)?.source);
  if (sources.some((source) => !source || !validateSourceRefs(source)) || sources.length !== input.expected_sources.length || sources.some((source) => !input.expected_sources.some((expected) => sourceTuple(expected) === sourceTuple(source!) && expected.source_revision === source!.source_revision))) throw new Error('SOURCE_CHANGED：来源集合或版本变化，请重新查询');
  const total = Number((await conn.query(`SELECT count(*) AS n FROM devices WHERE ${where}`)).toArray()[0].toJSON().n);
  if (!Number.isSafeInteger(total) || total < 0 || total > 200000) throw new Error('静态完整CSV最多200000行；请使用服务器/桌面，不截断导出');
  const fields = input.query_spec.fields.length ? input.query_spec.fields : [...columns].filter((field) => !['source_device_key', 'batch_id'].includes(field));
  fields.forEach((field) => quote(field, columns));
  const order = input.query_spec.order_by;
  const sort = order ? `${quote(order.replace(/^-/, ''), columns)} ${order.startsWith('-') ? 'DESC' : 'ASC'},id` : 'id';
  const chunks: Uint8Array[] = [], encoder = new TextEncoder(); let bytes = 0, returned = 0;
  const append = (text: string) => { const chunk = encoder.encode(text); bytes += chunk.byteLength; if (bytes > 100 * 1024 * 1024) throw new Error('静态完整CSV超过100MiB；请使用服务器/桌面'); chunks.push(chunk); };
  append('\ufeff' + fields.map(value).join(',') + '\r\n');
  for (let offset = 0; offset < total; offset += 2000) {
    const block = (await conn.query(`SELECT ${fields.map((field) => quote(field, columns)).join(',')} FROM devices WHERE ${where} ORDER BY ${sort} LIMIT 2000 OFFSET ${offset}`)).toArray().map((r) => normalizeRow(r.toJSON()));
    append(block.map((row) => fields.map((field) => value(row[field])).join(',')).join('\r\n') + '\r\n'); returned += block.length; progress(returned);
  }
  await assertDeviceSnapshot(); if (returned !== total) throw new Error('完整导出行数不一致');
  return { csv: new Blob(chunks as BlobPart[], { type: 'text/csv;charset=utf-8' }), manifest: { ...unverifiedManifest(input.query_spec, returned, total), source_refs: sources as SourceRef[], consistency: 'verified' as const } };
}
if (typeof document === 'undefined' && typeof self !== 'undefined') self.onmessage = async (event: MessageEvent<StaticExportInput>) => {
  try { const result = await runStaticFullExport(event.data, (rows) => self.postMessage({ type: 'progress', rows })); self.postMessage({ type: 'success', result }); }
  catch (failure) { self.postMessage({ type: 'error', message: String(failure) }); }
};
