import type { DatasetId, RowId } from './dataset';

export type ActiveSurface = 'chart' | 'worksheet' | null;
export type SelectionMode = 'replace' | 'toggle' | 'add';

export interface SelectionState {
  datasetId: DatasetId | null;
  rowIds: Set<RowId>;
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
  const next = mode === 'replace' || selection.datasetId !== datasetId
    ? new Set<RowId>()
    : new Set(selection.rowIds);
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

export function removeRowsFromSelection(selection: SelectionState, removed: Set<RowId>) {
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
