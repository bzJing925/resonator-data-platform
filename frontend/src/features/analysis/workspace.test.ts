import { describe, expect, it } from 'vitest';
import { normalizeAnalysisRows, workspaceReducer } from './workspace';

const dataset = {
  id: 'd1', name: '数据表1', kind: 'query' as const,
  rows: normalizeAnalysisRows([{ id: 10, qs: 100 }, { id: 20, qs: 200 }], 'd1'),
  stats: null, revision: 1,
};

function loadedState() {
  return workspaceReducer({
    datasets: [], activeTabId: 'chart' as const, chartDatasetId: null,
    selectedRowIds: new Set<string>(), selectionDatasetId: null,
    undoStack: [], redoStack: [],
  }, { type: 'load', dataset });
}

describe('analysis workspace reducer', () => {
  it('assigns stable device row ids and a derived structureType column', () => {
    expect(dataset.rows.map((row) => row.__rowId)).toEqual(['device:10', 'device:20']);
    expect(dataset.rows[0].structureType).toBe('');
  });

  it('deletes selected dataset rows and restores them with undo', () => {
    let state = loadedState();
    state = workspaceReducer(state, {
      type: 'select', datasetId: 'd1', rowIds: ['device:10'], mode: 'replace',
    });
    state = workspaceReducer(state, { type: 'deleteSelection' });
    expect(state.datasets[0].rows.map((row) => row.id)).toEqual([20]);
    state = workspaceReducer(state, { type: 'undo' });
    expect(state.datasets[0].rows.map((row) => row.id)).toEqual([10, 20]);
    state = workspaceReducer(state, { type: 'redo' });
    expect(state.datasets[0].rows.map((row) => row.id)).toEqual([20]);
  });

  it('edits one row without replacing untouched row objects', () => {
    const before = loadedState();
    const untouched = before.datasets[0].rows[1];
    const after = workspaceReducer(before, {
      type: 'edit', datasetId: 'd1', rowId: 'device:10', column: 'qs', value: 123,
    });
    expect(after.datasets[0].rows[0].qs).toBe(123);
    expect(after.datasets[0].rows[1]).toBe(untouched);
  });
});
