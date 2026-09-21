import { CHART_PAGE_ID, type AnalysisDataset, type DatasetId } from './dataset';
import type { WorkspaceState } from '../workspace';

export function selectDatasets(state: WorkspaceState): AnalysisDataset[] {
  return state.registry.order.flatMap((id) => {
    const dataset = state.registry.byId[id];
    return dataset ? [dataset] : [];
  });
}

function selectDatasetById(state: WorkspaceState, datasetId: DatasetId | null) {
  return datasetId ? state.registry.byId[datasetId] || null : null;
}

export function selectChartDataset(state: WorkspaceState) {
  const datasetId = state.registry.chartPages[CHART_PAGE_ID]?.datasetId || null;
  return selectDatasetById(state, datasetId);
}

export function selectActiveDataset(state: WorkspaceState) {
  return selectDatasetById(state, state.activeTabId);
}

export function selectCanUndo(state: WorkspaceState) {
  return state.history.undoStack.length > 0;
}

export function selectCanRedo(state: WorkspaceState) {
  return state.history.redoStack.length > 0;
}
