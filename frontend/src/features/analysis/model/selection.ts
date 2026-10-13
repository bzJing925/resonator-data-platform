import type { DatasetId, RowId } from './dataset';

export type ActiveSurface = 'chart' | 'worksheet' | null;
export type SelectionMode = 'replace' | 'toggle' | 'add' | 'addAcross';

export interface SelectionState {
  datasetId: DatasetId | null;
  rowIds: Set<RowId>;
  /** Only explicit cross-cohort selection adds this map; ordinary selection is unchanged. */
  byDataset?: Record<DatasetId, Set<RowId>>;
  anchorRowId: RowId | null;
  activeSurface: ActiveSurface;
}

export function createSelectionState(): SelectionState {
  return {
    datasetId: null,
    rowIds: new Set(),
    anchorRowId: null,
    activeSurface: null,
  };
}

export function selectRows(
  selection: SelectionState,
  {
    datasetId,
    rowIds,
    mode = 'replace',
    anchorRowId,
    activeSurface = selection.activeSurface,
  }: {
    datasetId: DatasetId;
    rowIds: Iterable<RowId>;
    mode?: SelectionMode;
    anchorRowId?: RowId | null;
    activeSurface?: ActiveSurface;
  },
): SelectionState {
  const incoming = [...rowIds];
  if (mode === 'addAcross') {
    const byDataset = { ...(selection.byDataset ?? (selection.datasetId ? { [selection.datasetId]: selection.rowIds } : {})) };
    byDataset[datasetId] = new Set([...(byDataset[datasetId] ?? []), ...incoming]);
    return { datasetId, byDataset, rowIds: new Set(Object.values(byDataset).flatMap((ids) => [...ids])),
      anchorRowId: anchorRowId ?? incoming.at(-1) ?? null, activeSurface };
  }
  const next = mode === 'replace' || selection.datasetId !== datasetId
    ? new Set<RowId>()
    : new Set(selectedRowsForDataset(selection, datasetId));
  for (const rowId of incoming) {
    if (mode === 'toggle' && next.has(rowId)) next.delete(rowId);
    else next.add(rowId);
  }
  return {
    datasetId,
    rowIds: next,
    anchorRowId: anchorRowId === undefined
      ? incoming.at(-1) || (mode === 'replace' ? null : selection.anchorRowId)
      : anchorRowId,
    activeSurface,
  };
}

export function selectedRowsForDataset(selection: SelectionState, datasetId: DatasetId) {
  return selection.byDataset?.[datasetId] ?? (selection.datasetId === datasetId ? selection.rowIds : new Set<RowId>());
}
export function removeRowsFromSelection(selection: SelectionState, removed: Set<RowId>, datasetId = selection.datasetId) {
  if (selection.byDataset && datasetId) {
    const byDataset = { ...selection.byDataset,
      [datasetId]: new Set([...selectedRowsForDataset(selection, datasetId)].filter((id) => !removed.has(id))) };
    return { ...selection, byDataset, rowIds: new Set(Object.values(byDataset).flatMap((ids) => [...ids])),
      anchorRowId: selection.datasetId === datasetId && selection.anchorRowId && removed.has(selection.anchorRowId) ? null : selection.anchorRowId };
  }
  if (selection.rowIds.size === 0) return selection;
  const rowIds = new Set([...selection.rowIds].filter((rowId) => !removed.has(rowId)));
  if (rowIds.size === selection.rowIds.size) return selection;
  return {
    ...selection,
    rowIds,
    anchorRowId: selection.anchorRowId && removed.has(selection.anchorRowId)
      ? null
      : selection.anchorRowId,
  };
}

export function removeDatasetSelection(selection: SelectionState, datasetId: DatasetId) {
  if (!selection.byDataset) return selection.datasetId === datasetId ? createSelectionState() : selection;
  const { [datasetId]: _removed, ...byDataset } = selection.byDataset;
  const nextId = selection.datasetId === datasetId ? Object.keys(byDataset)[0] ?? null : selection.datasetId;
  return { ...selection, byDataset, datasetId: nextId, anchorRowId: null,
    rowIds: new Set(Object.values(byDataset).flatMap((ids) => [...ids])) };
}
