import type { ResultManifest, SourceRef } from '../../../types/platformContracts';
import { attachSourceRefs, type ExcludedRow, type LocalEdit } from './provenance';

export type DatasetId = string;
export type RowId = string;

interface AnalysisRow extends Record<string, unknown> {
  rowId: RowId;
  /** Compatibility alias while Charts and Worksheet migrate to rowId. */
  __rowId: RowId;
  datasetId: DatasetId;
  structureType: string;
  areaReality: number | null;
}

export interface WorksheetColumn {
  key: string;
  sourceField: string | null;
}

export interface AnalysisFieldMap {
  structureType: string | null;
  areaReality: string | null;
}

export interface AnalysisDataset {
  id: DatasetId;
  name: string;
  kind: 'query' | 'derived' | 'empty';
  rows: AnalysisRow[];
  columns: WorksheetColumn[];
  fieldMap: AnalysisFieldMap;
  stats: Record<string, unknown> | null;
  revision: number;
  manifest: ResultManifest | null;
  provenance: Record<RowId, SourceRef | null>;
  edits: LocalEdit[];
  exclusions: ExcludedRow[];
}

interface AnalysisChartPage {
  id: string;
  name: string;
  kind: 'chart';
  datasetId: DatasetId | null;
}

export interface DatasetRegistry {
  byId: Record<DatasetId, AnalysisDataset>;
  order: DatasetId[];
  chartPages: Record<string, AnalysisChartPage>;
  chartPageOrder: string[];
}

export const CHART_PAGE_ID = 'chart';

const STRUCTURE_TYPE_ALIASES = ['structureType', 'structure_type', 'StructureType'];
const AREA_REALITY_ALIASES = ['areaReality', 'area_um2', 'Area_reality'];
const INTERNAL_ROW_FIELDS = new Set(['rowId', '__rowId', 'datasetId', '_source_ref']);

function sourceField(rows: Record<string, unknown>[], aliases: string[]) {
  return aliases.find((alias) => rows.some((row) =>
    Object.prototype.hasOwnProperty.call(row, alias))) || null;
}

function inferAnalysisFieldMap(rows: Record<string, unknown>[]): AnalysisFieldMap {
  return {
    structureType: sourceField(rows, STRUCTURE_TYPE_ALIASES),
    areaReality: sourceField(rows, AREA_REALITY_ALIASES),
  };
}

export function normalizeStructureType(value: unknown) {
  return typeof value === 'string' ? value.trim() : '';
}

export function normalizeAreaReality(value: unknown) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function sourceValue(row: Record<string, unknown>, source: string | null) {
  return source ? row[source] : undefined;
}

function stableRowId(row: Record<string, unknown>, datasetId: DatasetId, index: number) {
  const existing = row.rowId ?? row.__rowId;
  if (typeof existing === 'string' && existing.trim()) return existing;
  if (row.id !== null && row.id !== undefined) return `${datasetId}:device:${String(row.id)}`;
  return `${datasetId}:row:${index}`;
}

function normalizeAnalysisRows(
  rows: Record<string, unknown>[],
  datasetId: DatasetId,
  fieldMap = inferAnalysisFieldMap(rows),
): AnalysisRow[] {
  const usedIds = new Set<RowId>();
  return rows.map((row, index) => {
    const baseId = stableRowId(row, datasetId, index);
    let rowId = baseId;
    let suffix = 1;
    while (usedIds.has(rowId)) {
      rowId = `${baseId}:${suffix}`;
      suffix += 1;
    }
    usedIds.add(rowId);
    return {
      ...row,
      rowId,
      __rowId: rowId,
      datasetId,
      structureType: normalizeStructureType(sourceValue(row, fieldMap.structureType)),
      areaReality: normalizeAreaReality(sourceValue(row, fieldMap.areaReality)),
    };
  });
}

export function buildWorksheetColumns(
  rows: Record<string, unknown>[],
  fieldMap: AnalysisFieldMap,
): WorksheetColumn[] {
  const keys = new Set<string>(['structureType', 'areaReality']);
  for (const row of rows) {
    Object.keys(row).forEach((key) => {
      if (!INTERNAL_ROW_FIELDS.has(key)) keys.add(key);
    });
  }
  return [...keys].map((key) => ({
    key,
    sourceField: key === 'structureType'
      ? fieldMap.structureType
      : key === 'areaReality' ? fieldMap.areaReality : key,
  }));
}

export function createAnalysisDataset({
  id,
  name,
  kind,
  rows,
  stats = null,
  manifest = null,
}: {
  id: DatasetId;
  name: string;
  kind: AnalysisDataset['kind'];
  rows: Record<string, unknown>[];
  stats?: Record<string, unknown> | null;
  manifest?: ResultManifest | null;
}): AnalysisDataset {
  const fieldMap = inferAnalysisFieldMap(rows);
  const normalizedRows = normalizeAnalysisRows(rows, id, fieldMap);
  const references = attachSourceRefs(rows, manifest);
  return {
    id,
    name,
    kind,
    rows: normalizedRows,
    columns: buildWorksheetColumns(normalizedRows, fieldMap),
    fieldMap,
    stats,
    revision: 1,
    manifest: manifest ? { ...manifest, consistency: references.every((ref) => ref !== null
      && ref.source_revision !== null && ref.source_device_key !== null)
      ? manifest.consistency : 'unverified' } : null,
    provenance: Object.fromEntries(normalizedRows.map((row, index) => [row.rowId, references[index]])),
    edits: [],
    exclusions: [],
  };
}

export function duplicateAnalysisDataset(
  source: AnalysisDataset,
  id: DatasetId,
  name: string,
): AnalysisDataset {
  const rowIds = new Map([...source.rows.map((row) => row.rowId),
    ...source.exclusions.map((row) => row.rowId)].map((rowId, index) => [rowId, `${id}:row:${index}`]));
  return {
    ...source,
    id,
    name,
    kind: 'derived',
    rows: source.rows.map((row) => {
      const rowId = rowIds.get(row.rowId)!;
      return { ...row, rowId, __rowId: rowId, datasetId: id };
    }),
    manifest: source.manifest ? structuredClone(source.manifest) : null,
    provenance: Object.fromEntries(Object.entries(source.provenance).map(([rowId, ref]) =>
      [rowIds.get(rowId) ?? rowId, ref ? { ...ref } : null])),
    edits: source.edits.map((edit) => ({ ...structuredClone(edit), datasetId: id,
      rowId: rowIds.get(edit.rowId) ?? edit.rowId })),
    exclusions: source.exclusions.map((excluded) => {
      const rowId = rowIds.get(excluded.rowId)!;
      return { ...structuredClone(excluded), datasetId: id, rowId,
        originalRow: { ...structuredClone(excluded.originalRow), datasetId: id, rowId, __rowId: rowId } };
    }),
    columns: source.columns.map((column) => ({ ...column })),
    fieldMap: { ...source.fieldMap },
    revision: 1,
  };
}

export function createDatasetRegistry(): DatasetRegistry {
  return {
    byId: {},
    order: [],
    chartPages: {
      [CHART_PAGE_ID]: {
        id: CHART_PAGE_ID,
        name: '图表',
        kind: 'chart',
        datasetId: null,
      },
    },
    chartPageOrder: [CHART_PAGE_ID],
  };
}

export function addDatasetToRegistry(
  registry: DatasetRegistry,
  dataset: AnalysisDataset,
): DatasetRegistry {
  if (registry.byId[dataset.id]) return registry;
  return {
    ...registry,
    byId: { ...registry.byId, [dataset.id]: dataset },
    order: [...registry.order, dataset.id],
  };
}

export function updateDatasetInRegistry(
  registry: DatasetRegistry,
  dataset: AnalysisDataset,
): DatasetRegistry {
  if (!registry.byId[dataset.id] || registry.byId[dataset.id] === dataset) return registry;
  return { ...registry, byId: { ...registry.byId, [dataset.id]: dataset } };
}

export function removeDatasetFromRegistry(
  registry: DatasetRegistry,
  datasetId: DatasetId,
): DatasetRegistry {
  if (!registry.byId[datasetId]) return registry;
  const { [datasetId]: _removed, ...byId } = registry.byId;
  return { ...registry, byId, order: registry.order.filter((id) => id !== datasetId) };
}
