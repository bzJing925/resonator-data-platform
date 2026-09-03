import React, { createContext, useCallback, useContext, useMemo, useReducer } from 'react';

export type AnalysisRow = Record<string, any> & { __rowId: string };

export interface AnalysisDataset {
  id: string;
  name: string;
  kind: 'query' | 'derived';
  rows: AnalysisRow[];
  stats: Record<string, any> | null;
  revision: number;
}

type SelectionMode = 'replace' | 'toggle' | 'add';

interface EditCommand {
  type: 'edit';
  datasetId: string;
  rowId: string;
  column: string;
  before: unknown;
  after: unknown;
}

interface DeleteCommand {
  type: 'delete';
  datasetId: string;
  removed: Array<{ index: number; row: AnalysisRow }>;
}

type Command = EditCommand | DeleteCommand;

interface WorkspaceState {
  datasets: AnalysisDataset[];
  activeTabId: 'chart' | string;
  chartDatasetId: string | null;
  selectedRowIds: Set<string>;
  selectionDatasetId: string | null;
  undoStack: Command[];
  redoStack: Command[];
}

const INITIAL_STATE: WorkspaceState = {
  datasets: [],
  activeTabId: 'chart',
  chartDatasetId: null,
  selectedRowIds: new Set(),
  selectionDatasetId: null,
  undoStack: [],
  redoStack: [],
};

type Action =
  | { type: 'load'; dataset: AnalysisDataset }
  | { type: 'duplicate'; dataset: AnalysisDataset }
  | { type: 'tab'; tabId: string }
  | { type: 'chartDataset'; datasetId: string }
  | { type: 'select'; datasetId: string; rowIds: string[]; mode: SelectionMode }
  | { type: 'edit'; datasetId: string; rowId: string; column: string; value: unknown }
  | { type: 'deleteSelection' }
  | { type: 'undo' }
  | { type: 'redo' };

function replaceDataset(
  state: WorkspaceState,
  datasetId: string,
  update: (dataset: AnalysisDataset) => AnalysisDataset,
) {
  return state.datasets.map((dataset) => dataset.id === datasetId ? update(dataset) : dataset);
}

function applyCommand(state: WorkspaceState, command: Command, reverse: boolean): WorkspaceState {
  if (command.type === 'edit') {
    const value = reverse ? command.before : command.after;
    const datasets = replaceDataset(state, command.datasetId, (dataset) => ({
      ...dataset,
      revision: dataset.revision + 1,
      rows: dataset.rows.map((row) => row.__rowId === command.rowId
        ? { ...row, [command.column]: value }
        : row),
    }));
    return { ...state, datasets };
  }

  const datasets = replaceDataset(state, command.datasetId, (dataset) => {
    if (!reverse) {
      const removedIds = new Set(command.removed.map(({ row }) => row.__rowId));
      return {
        ...dataset,
        revision: dataset.revision + 1,
        rows: dataset.rows.filter((row) => !removedIds.has(row.__rowId)),
      };
    }
    const rows = dataset.rows.slice();
    for (const { index, row } of [...command.removed].sort((a, b) => a.index - b.index)) {
      rows.splice(Math.min(index, rows.length), 0, row);
    }
    return { ...dataset, revision: dataset.revision + 1, rows };
  });
  return { ...state, datasets };
}

export function workspaceReducer(state: WorkspaceState, action: Action): WorkspaceState {
  switch (action.type) {
    case 'load':
      return {
        ...state,
        datasets: [...state.datasets, action.dataset],
        chartDatasetId: action.dataset.id,
        activeTabId: 'chart',
        selectedRowIds: new Set(),
        selectionDatasetId: action.dataset.id,
        undoStack: [],
        redoStack: [],
      };
    case 'duplicate':
      return {
        ...state,
        datasets: [...state.datasets, action.dataset],
        activeTabId: action.dataset.id,
        selectedRowIds: new Set(),
        selectionDatasetId: action.dataset.id,
      };
    case 'tab':
      return { ...state, activeTabId: action.tabId };
    case 'chartDataset':
      return {
        ...state,
        chartDatasetId: action.datasetId,
        selectedRowIds: new Set(),
        selectionDatasetId: action.datasetId,
      };
    case 'select': {
      const next = action.mode === 'replace' || state.selectionDatasetId !== action.datasetId
        ? new Set<string>()
        : new Set(state.selectedRowIds);
      for (const id of action.rowIds) {
        if (action.mode === 'toggle' && next.has(id)) next.delete(id);
        else next.add(id);
      }
      return { ...state, selectedRowIds: next, selectionDatasetId: action.datasetId };
    }
    case 'edit': {
      const dataset = state.datasets.find((item) => item.id === action.datasetId);
      const row = dataset?.rows.find((item) => item.__rowId === action.rowId);
      if (!row || row[action.column] === action.value) return state;
      const command: EditCommand = {
        type: 'edit', datasetId: action.datasetId, rowId: action.rowId,
        column: action.column, before: row[action.column], after: action.value,
      };
      const next = applyCommand(state, command, false);
      return { ...next, undoStack: [...state.undoStack, command], redoStack: [] };
    }
    case 'deleteSelection': {
      if (!state.selectionDatasetId || state.selectedRowIds.size === 0) return state;
      const dataset = state.datasets.find((item) => item.id === state.selectionDatasetId);
      if (!dataset) return state;
      const removed = dataset.rows.flatMap((row, index) =>
        state.selectedRowIds.has(row.__rowId) ? [{ index, row }] : []);
      if (removed.length === 0) return state;
      const command: DeleteCommand = { type: 'delete', datasetId: dataset.id, removed };
      const next = applyCommand(state, command, false);
      return {
        ...next,
        selectedRowIds: new Set(),
        undoStack: [...state.undoStack, command],
        redoStack: [],
      };
    }
    case 'undo': {
      const command = state.undoStack.at(-1);
      if (!command) return state;
      const next = applyCommand(state, command, true);
      return {
        ...next,
        undoStack: state.undoStack.slice(0, -1),
        redoStack: [...state.redoStack, command],
      };
    }
    case 'redo': {
      const command = state.redoStack.at(-1);
      if (!command) return state;
      const next = applyCommand(state, command, false);
      return {
        ...next,
        undoStack: [...state.undoStack, command],
        redoStack: state.redoStack.slice(0, -1),
      };
    }
    default:
      return state;
  }
}

let datasetSequence = 1;

export function normalizeAnalysisRows(rows: Record<string, any>[], datasetId: string): AnalysisRow[] {
  return rows.map((row, index) => ({
    ...row,
    structureType: row.structureType ?? '',
    __rowId: row.id != null ? `device:${row.id}` : `${datasetId}:row:${index}`,
  }));
}

interface WorkspaceApi {
  state: WorkspaceState;
  chartDataset: AnalysisDataset | null;
  activeDataset: AnalysisDataset | null;
  loadQueryRows: (rows: Record<string, any>[], stats?: Record<string, any> | null) => void;
  duplicateDataset: (datasetId?: string) => void;
  setActiveTab: (tabId: string) => void;
  setChartDataset: (datasetId: string) => void;
  setSelection: (datasetId: string, rowIds: string[], mode?: SelectionMode) => void;
  editCell: (datasetId: string, rowId: string, column: string, value: unknown) => void;
  deleteSelection: () => void;
  undo: () => void;
  redo: () => void;
}

const WorkspaceContext = createContext<WorkspaceApi | null>(null);

export function AnalysisWorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(workspaceReducer, INITIAL_STATE);

  const loadQueryRows = useCallback((rows: Record<string, any>[], stats = null) => {
    const number = datasetSequence++;
    const id = `analysis-${number}`;
    dispatch({
      type: 'load',
      dataset: {
        id,
        name: `数据表${number}`,
        kind: 'query',
        rows: normalizeAnalysisRows(rows, id),
        stats,
        revision: 1,
      },
    });
  }, []);

  const duplicateDataset = useCallback((datasetId?: string) => {
    const source = state.datasets.find((item) => item.id === (datasetId || state.chartDatasetId));
    if (!source) return;
    const number = datasetSequence++;
    const id = `analysis-${number}`;
    dispatch({
      type: 'duplicate',
      dataset: {
        ...source,
        id,
        name: `数据表${number}`,
        kind: 'derived',
        rows: source.rows.map((row, index) => ({ ...row, __rowId: `${id}:row:${index}` })),
        revision: 1,
      },
    });
  }, [state.chartDatasetId, state.datasets]);

  const setActiveTab = useCallback((tabId: string) => dispatch({ type: 'tab', tabId }), []);
  const setChartDataset = useCallback(
    (datasetId: string) => dispatch({ type: 'chartDataset', datasetId }),
    [],
  );
  const setSelection = useCallback(
    (datasetId: string, rowIds: string[], mode: SelectionMode = 'replace') =>
      dispatch({ type: 'select', datasetId, rowIds, mode }),
    [],
  );
  const editCell = useCallback(
    (datasetId: string, rowId: string, column: string, value: unknown) =>
      dispatch({ type: 'edit', datasetId, rowId, column, value }),
    [],
  );
  const deleteSelection = useCallback(() => dispatch({ type: 'deleteSelection' }), []);
  const undo = useCallback(() => dispatch({ type: 'undo' }), []);
  const redo = useCallback(() => dispatch({ type: 'redo' }), []);

  const api = useMemo<WorkspaceApi>(() => ({
    state,
    chartDataset: state.datasets.find((item) => item.id === state.chartDatasetId) || null,
    activeDataset: state.datasets.find((item) => item.id === state.activeTabId) || null,
    loadQueryRows,
    duplicateDataset,
    setActiveTab,
    setChartDataset,
    setSelection,
    editCell,
    deleteSelection,
    undo,
    redo,
  }), [
    state, loadQueryRows, duplicateDataset, setActiveTab, setChartDataset,
    setSelection, editCell, deleteSelection, undo, redo,
  ]);

  return <WorkspaceContext.Provider value={api}>{children}</WorkspaceContext.Provider>;
}

export function useAnalysisWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error('useAnalysisWorkspace must be used within AnalysisWorkspaceProvider');
  return value;
}
