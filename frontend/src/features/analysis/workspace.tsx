import React, { createContext, useCallback, useContext, useMemo, useReducer } from 'react';
import {
  commandLabel,
  executeAnalysisCommand,
  isUndoableCommand,
  type AnalysisCommand,
  type PointStylePatch,
  type PointStyleState,
  type WorkspacePresent,
} from './model/commands';
import {
  CHART_PAGE_ID,
  createAnalysisDataset,
  createDatasetRegistry,
  duplicateAnalysisDataset,
  normalizeAnalysisRows,
  type AnalysisDataset,
  type AnalysisRow,
  type DatasetId,
  type RowId,
} from './model/dataset';
import {
  createHistoryState,
  pushHistory,
  redoHistory,
  undoHistory,
  type HistoryState,
} from './model/history';
import {
  selectActiveDataset,
  selectCanRedo,
  selectCanUndo,
  selectChartDataset,
  selectDatasets,
} from './model/selectors';
import {
  createSelectionState,
  type ActiveSurface,
  type SelectionMode,
  type SelectionState,
} from './model/selection';

export type {
  AnalysisDataset,
  AnalysisRow,
  DatasetId,
  RowId,
  ActiveSurface,
  SelectionMode,
  SelectionState,
};
export { normalizeAnalysisRows };

export interface WorkspaceState extends WorkspacePresent {
  history: HistoryState<WorkspacePresent>;
}

export const INITIAL_WORKSPACE_STATE: WorkspaceState = {
  registry: createDatasetRegistry(),
  activeTabId: CHART_PAGE_ID,
  selection: createSelectionState(),
  pointStyles: {},
  history: createHistoryState<WorkspacePresent>(),
};

export type WorkspaceAction =
  | { type: 'execute'; command: AnalysisCommand }
  | { type: 'undo' }
  | { type: 'redo' };

function workspacePresent(state: WorkspaceState): WorkspacePresent {
  const { history: _history, ...present } = state;
  return present;
}

export function workspaceReducer(state: WorkspaceState, action: WorkspaceAction): WorkspaceState {
  if (action.type === 'execute') {
    const before = workspacePresent(state);
    const after = executeAnalysisCommand(before, action.command);
    if (after === before) return state;
    const history = isUndoableCommand(action.command)
      ? pushHistory(state.history, { label: commandLabel(action.command), before, after })
      : state.history;
    return { ...after, history };
  }
  const before = workspacePresent(state);
  if (action.type === 'undo') {
    const result = undoHistory(before, state.history);
    return result.present === before ? state : { ...result.present, history: result.history };
  }
  const result = redoHistory(before, state.history);
  return result.present === before ? state : { ...result.present, history: result.history };
}

let datasetSequence = 1;

interface WorkspaceApi {
  state: WorkspaceState;
  datasets: AnalysisDataset[];
  chartDataset: AnalysisDataset | null;
  chartDatasetId: DatasetId | null;
  activeDataset: AnalysisDataset | null;
  selection: SelectionState;
  pointStyles: PointStyleState;
  canUndo: boolean;
  canRedo: boolean;
  loadQueryRows: (rows: Record<string, unknown>[], stats?: Record<string, unknown> | null) => void;
  addDataset: (name?: string) => DatasetId;
  closeDataset: (datasetId: DatasetId, replacementDatasetId?: DatasetId | null) => void;
  duplicateDataset: (datasetId?: DatasetId) => void;
  renameDataset: (datasetId: DatasetId, name: string) => void;
  setActiveTab: (tabId: string) => void;
  setChartDataset: (datasetId: DatasetId) => void;
  selectRows: (
    datasetId: DatasetId,
    rowIds: RowId[],
    mode?: SelectionMode,
    activeSurface?: ActiveSurface,
  ) => void;
  updateRows: (
    datasetId: DatasetId,
    updates: Array<{ rowId: RowId; changes: Record<string, unknown> }>,
  ) => void;
  editCell: (datasetId: DatasetId, rowId: RowId, column: string, value: unknown) => void;
  deleteRows: (datasetId: DatasetId, rowIds: RowId[]) => void;
  deleteSelection: () => void;
  updatePointStyle: (
    datasetId: DatasetId,
    rowIds: RowId[],
    patch: PointStylePatch,
    metric?: string,
  ) => void;
  undo: () => void;
  redo: () => void;
}

const WorkspaceContext = createContext<WorkspaceApi | null>(null);

function nextDatasetIdentity() {
  const number = datasetSequence;
  datasetSequence += 1;
  return { id: `analysis-${number}`, name: `数据表${number}` };
}

export function AnalysisWorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(workspaceReducer, INITIAL_WORKSPACE_STATE);
  const datasets = useMemo(() => selectDatasets(state), [state]);
  const chartDataset = selectChartDataset(state);
  const activeDataset = selectActiveDataset(state);
  const chartDatasetId = state.registry.chartPages[CHART_PAGE_ID]?.datasetId || null;

  const execute = useCallback((command: AnalysisCommand) => {
    dispatch({ type: 'execute', command });
  }, []);

  const loadQueryRows = useCallback((
    rows: Record<string, unknown>[],
    stats: Record<string, unknown> | null = null,
  ) => {
    const identity = nextDatasetIdentity();
    execute({
      type: 'addDataset',
      dataset: createAnalysisDataset({ ...identity, kind: 'query', rows, stats }),
      activate: 'chart',
    });
  }, [execute]);

  const addDataset = useCallback((name?: string) => {
    const identity = nextDatasetIdentity();
    execute({
      type: 'addDataset',
      dataset: createAnalysisDataset({
        ...identity,
        name: name || identity.name,
        kind: 'empty',
        rows: [],
      }),
      activate: 'dataset',
    });
    return identity.id;
  }, [execute]);

  const closeDataset = useCallback((
    datasetId: DatasetId,
    replacementDatasetId?: DatasetId | null,
  ) => execute({ type: 'closeDataset', datasetId, replacementDatasetId }), [execute]);

  const duplicateDataset = useCallback((datasetId?: DatasetId) => {
    const source = state.registry.byId[datasetId || chartDatasetId || ''];
    if (!source) return;
    const identity = nextDatasetIdentity();
    execute({
      type: 'duplicateDataset',
      dataset: duplicateAnalysisDataset(source, identity.id, identity.name),
    });
  }, [chartDatasetId, execute, state.registry.byId]);
  const renameDataset = useCallback(
    (datasetId: DatasetId, name: string) => execute({ type: 'renameDataset', datasetId, name }),
    [execute],
  );

  const setActiveTab = useCallback((tabId: string) => execute({
    type: 'activateTab',
    tabId,
    activeSurface: tabId === CHART_PAGE_ID ? 'chart' : 'worksheet',
  }), [execute]);
  const setChartDataset = useCallback(
    (datasetId: DatasetId) => execute({ type: 'setChartDataset', datasetId }),
    [execute],
  );
  const selectRows = useCallback((
    datasetId: DatasetId,
    rowIds: RowId[],
    mode: SelectionMode = 'replace',
    activeSurface?: ActiveSurface,
  ) => execute({ type: 'selectRows', datasetId, rowIds, mode, activeSurface }), [execute]);
  const updateRows = useCallback((
    datasetId: DatasetId,
    updates: Array<{ rowId: RowId; changes: Record<string, unknown> }>,
  ) => execute({ type: 'updateRows', datasetId, updates }), [execute]);
  const editCell = useCallback((
    datasetId: DatasetId,
    rowId: RowId,
    column: string,
    value: unknown,
  ) => updateRows(datasetId, [{ rowId, changes: { [column]: value } }]), [updateRows]);
  const deleteRows = useCallback(
    (datasetId: DatasetId, rowIds: RowId[]) => execute({ type: 'deleteRows', datasetId, rowIds }),
    [execute],
  );
  const deleteSelection = useCallback(() => {
    if (!state.selection.datasetId || state.selection.rowIds.size === 0) return;
    deleteRows(state.selection.datasetId, [...state.selection.rowIds]);
  }, [deleteRows, state.selection]);
  const updatePointStyle = useCallback((
    datasetId: DatasetId,
    rowIds: RowId[],
    patch: PointStylePatch,
    metric?: string,
  ) => execute({ type: 'updatePointStyle', datasetId, rowIds, patch, metric }), [execute]);
  const undo = useCallback(() => dispatch({ type: 'undo' }), []);
  const redo = useCallback(() => dispatch({ type: 'redo' }), []);

  const api = useMemo<WorkspaceApi>(() => ({
    state,
    datasets,
    chartDataset,
    chartDatasetId,
    activeDataset,
    selection: state.selection,
    pointStyles: state.pointStyles,
    canUndo: selectCanUndo(state),
    canRedo: selectCanRedo(state),
    loadQueryRows,
    addDataset,
    closeDataset,
    duplicateDataset,
    renameDataset,
    setActiveTab,
    setChartDataset,
    selectRows,
    updateRows,
    editCell,
    deleteRows,
    deleteSelection,
    updatePointStyle,
    undo,
    redo,
  }), [
    state, datasets, chartDataset, chartDatasetId, activeDataset, loadQueryRows,
    addDataset, closeDataset, duplicateDataset, renameDataset, setActiveTab, setChartDataset,
    selectRows, updateRows, editCell, deleteRows, deleteSelection, updatePointStyle,
    undo, redo,
  ]);

  return <WorkspaceContext.Provider value={api}>{children}</WorkspaceContext.Provider>;
}

export function useAnalysisWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error('useAnalysisWorkspace must be used within AnalysisWorkspaceProvider');
  return value;
}
