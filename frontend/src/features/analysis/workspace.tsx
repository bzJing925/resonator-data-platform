import { migrateLegacyExplore, parseAnalysisSpec } from './model/specCodec';
import type { AnalysisSpec, ResultManifest } from '../../types/platformContracts';
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
  type AnalysisDataset,
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
  DatasetId,
  RowId,
  ActiveSurface,
  SelectionMode,
};

export interface WorkspaceState extends WorkspacePresent {
  spec: AnalysisSpec;
  readOnly: boolean;
  history: HistoryState<WorkspacePresent>;
}

export const INITIAL_WORKSPACE_STATE: WorkspaceState = {
  readOnly: false,
  spec: migrateLegacyExplore({ xFields: ['eg'], yFields: ['qs'], limit: 50000 }),
  registry: createDatasetRegistry(),
  activeTabId: CHART_PAGE_ID,
  selection: createSelectionState(),
  pointStyles: {},
  history: createHistoryState<WorkspacePresent>(),
};

export type WorkspaceAction =
  | { type: 'updateSpec'; spec: AnalysisSpec | ((current: AnalysisSpec) => AnalysisSpec) }
  | { type: 'execute'; command: AnalysisCommand }
  | { type: 'undo' }
  | { type: 'redo' };

function workspacePresent(state: WorkspaceState): WorkspacePresent {
  const { history: _history, spec: _spec, readOnly: _readOnly, ...present } = state;
  return present;
}

export function workspaceReducer(state: WorkspaceState, action: WorkspaceAction): WorkspaceState {
  if (state.readOnly && (action.type === 'updateSpec' || action.type === 'undo' || action.type === 'redo'
    || (action.type === 'execute' && isUndoableCommand(action.command)))) return state;
  if (action.type === 'updateSpec') return { ...state, spec: parseAnalysisSpec(typeof action.spec === 'function' ? action.spec(state.spec) : action.spec) };
  if (action.type === 'execute') {
    const before = workspacePresent(state);
    const after = executeAnalysisCommand(before, action.command);
    if (after === before) return state;
    const history = isUndoableCommand(action.command)
      ? pushHistory(state.history, { label: commandLabel(action.command), before, after })
      : state.history;
    return { ...after, spec: after.spec ?? state.spec, readOnly: state.readOnly, history };
  }
  const before = workspacePresent(state);
  if (action.type === 'undo') {
    const result = undoHistory(before, state.history);
    return result.present === before ? state : { ...result.present, spec: result.present.spec ?? state.spec, readOnly: state.readOnly, history: result.history };
  }
  const result = redoHistory(before, state.history);
  return result.present === before ? state : { ...result.present, spec: result.present.spec ?? state.spec, readOnly: state.readOnly, history: result.history };
}

let datasetSequence = 1;

interface WorkspaceApi {
  state: WorkspaceState;
  updateSpec: (spec: AnalysisSpec | ((current: AnalysisSpec) => AnalysisSpec)) => void;
  datasets: AnalysisDataset[];
  chartDataset: AnalysisDataset | null;
  chartDatasetId: DatasetId | null;
  activeDataset: AnalysisDataset | null;
  selection: SelectionState;
  pointStyles: PointStyleState;
  canUndo: boolean;
  canRedo: boolean;
  loadQueryRows: (rows: Record<string, unknown>[], stats?: Record<string, unknown> | null,
    manifest?: ResultManifest | null) => DatasetId;
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
  return { id: crypto.randomUUID(), name: `数据表${number}` };
}

export function AnalysisWorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(workspaceReducer, INITIAL_WORKSPACE_STATE, (initial) => {
    try {
      const legacy = JSON.parse(localStorage.getItem('aln_page_state_v1') ?? '{}');
      if (legacy?.explore) return { ...initial, spec: migrateLegacyExplore(legacy.explore) };
    } catch { /* Invalid or unavailable legacy preferences do not block a new session. */ }
    return initial;
  });
  const updateSpec = useCallback((next: AnalysisSpec | ((current: AnalysisSpec) => AnalysisSpec)) => {
    dispatch({ type: 'updateSpec', spec: next });
  }, []);
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
    manifest: ResultManifest | null = null,
  ) => {
    const identity = nextDatasetIdentity();
    execute({
      type: 'addDataset',
      dataset: createAnalysisDataset({ ...identity, kind: 'query', rows, stats, manifest }),
      activate: 'chart',
    });
    return identity.id;
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
    if (state.selection.byDataset) {
      execute({ type: 'batch', label: '排除两组所选器件', commands: Object.entries(state.selection.byDataset)
        .filter(([, ids]) => ids.size).map(([datasetId, ids]) => ({ type: 'deleteRows', datasetId, rowIds: [...ids] })) });
    } else deleteRows(state.selection.datasetId, [...state.selection.rowIds]);
  }, [deleteRows, execute, state.selection]);
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
    updateSpec,
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
    state, updateSpec, datasets, chartDataset, chartDatasetId, activeDataset, loadQueryRows,
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
