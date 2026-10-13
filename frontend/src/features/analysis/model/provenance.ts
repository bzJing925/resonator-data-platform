import type { ResultManifest, SourceRef } from '../../../types/platformContracts';

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export interface LocalEdit {
  datasetId: string;
  rowId: string;
  column: string;
  before: JsonValue;
  after: JsonValue;
  source: SourceRef | null;
}
export interface ExcludedRow {
  datasetId: string;
  rowId: string;
  source: SourceRef | null;
  reason: string;
  originalRow: Record<string, JsonValue>;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function validateSourceRefs(value: unknown): value is SourceRef {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = Object.fromEntries(Object.entries(value));
  return typeof record.origin_id === 'string' && UUID.test(record.origin_id)
    && typeof record.source_batch_id === 'string' && UUID.test(record.source_batch_id)
    && (record.source_device_key === null || (typeof record.source_device_key === 'string'
      && UUID.test(record.source_device_key)))
    && (record.source_revision === null || (typeof record.source_revision === 'number'
      && Number.isSafeInteger(record.source_revision) && record.source_revision >= 0));
}
export function sourceTuple(source: SourceRef) {
  return JSON.stringify([source.origin_id.toLowerCase(), source.source_batch_id.toLowerCase(),
    source.source_device_key?.toLowerCase() ?? null]);
}
export function attachSourceRefs(rows: Record<string, unknown>[], manifest?: ResultManifest | null) {
  return rows.map((row) => {
    const ref = row._source_ref;
    if (!validateSourceRefs(ref)) return null;
    if (manifest && !manifest.source_refs.some((batch) => validateSourceRefs(batch)
      && batch.origin_id.toLowerCase() === ref.origin_id.toLowerCase()
      && batch.source_batch_id.toLowerCase() === ref.source_batch_id.toLowerCase()
      && batch.source_revision === ref.source_revision)) return null;
    return { ...ref };
  });
}
export function jsonCell(value: unknown): JsonValue {
  if (value === null) return null;
  if (typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (Array.isArray(value)) return value.map(jsonCell);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, jsonCell(item)]));
  }
  return null;
}
