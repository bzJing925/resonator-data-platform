import { describe, expect, it } from 'vitest';
import { createAnalysisDataset, duplicateAnalysisDataset } from './model/dataset';
import { pointStyleMetricKey } from './model/commands';
import { selectChartDataset, selectDatasets } from './model/selectors';
import { INITIAL_WORKSPACE_STATE, workspaceReducer } from './workspace';

const dataset = createAnalysisDataset({
  id: 'd1',
  name: '数据表1',
  kind: 'query',
  rows: [
    { id: 10, qs: 100, structure_type: 'Series', area_um2: 700 },
    { id: 20, qs: 200, structure_type: 'Parallel', area_um2: 900 },
  ],
});

function loadedState() {
  return workspaceReducer(INITIAL_WORKSPACE_STATE, {
    type: 'execute',
    command: { type: 'addDataset', dataset, activate: 'chart' },
  });
}

describe('analysis workspace reducer', () => {
  it('uses registry selectors for data tables and chart source references', () => {
    const state = loadedState();
    expect(selectDatasets(state)).toEqual([dataset]);
    expect(selectChartDataset(state)).toBe(dataset);
  });

  it('deletes selected rows and restores data and selection with undo/redo', () => {
    let state = loadedState();
    state = workspaceReducer(state, {
      type: 'execute',
      command: {
        type: 'updatePointStyle',
        datasetId: 'd1',
        rowIds: ['d1:device:10'],
        patch: { color: '#ff0000' },
      },
    });
    state = workspaceReducer(state, {
      type: 'execute',
      command: {
        type: 'selectRows',
        datasetId: 'd1',
        rowIds: ['d1:device:10'],
        mode: 'replace',
        activeSurface: 'worksheet',
      },
    });
    state = workspaceReducer(state, {
      type: 'execute',
      command: { type: 'deleteRows', datasetId: 'd1', rowIds: ['d1:device:10'] },
    });
    expect(state.registry.byId.d1.rows.map((row) => row.id)).toEqual([20]);
    expect(state.selection.rowIds.size).toBe(0);
    expect(state.pointStyles.d1['d1:device:10']).toBeUndefined();

    state = workspaceReducer(state, { type: 'undo' });
    expect(state.registry.byId.d1.rows.map((row) => row.id)).toEqual([10, 20]);
    expect(state.selection.rowIds).toEqual(new Set(['d1:device:10']));
    expect(state.pointStyles.d1['d1:device:10']).toEqual({ color: '#ff0000' });

    state = workspaceReducer(state, { type: 'redo' });
    expect(state.registry.byId.d1.rows.map((row) => row.id)).toEqual([20]);
  });

  it('updates multiple rows as one history entry and preserves untouched rows', () => {
    const before = loadedState();
    const untouched = before.registry.byId.d1.rows[1];
    const after = workspaceReducer(before, {
      type: 'execute',
      command: {
        type: 'batch',
        label: '批量编辑',
        commands: [
          {
            type: 'updateRows',
            datasetId: 'd1',
            updates: [{
              rowId: 'd1:device:10', changes: { qs: 123, area_um2: '1200' },
            }],
          },
          {
            type: 'updateRows',
            datasetId: 'd1',
            updates: [{ rowId: 'missing', changes: { qs: 999 } }],
          },
        ],
      },
    });
    expect(after.registry.byId.d1.rows[0].qs).toBe(123);
    expect(after.registry.byId.d1.rows[0].areaReality).toBe(1200);
    expect(after.registry.byId.d1.rows[1]).toBe(untouched);
    expect(after.registry.byId.d1.columns).toBe(before.registry.byId.d1.columns);
    expect(after.history.undoStack).toHaveLength(before.history.undoStack.length + 1);
    expect(workspaceReducer(after, { type: 'undo' }).registry.byId.d1.rows[0].qs).toBe(100);
  });

  it('guards referenced dataset close and restores a valid close with undo', () => {
    let state = loadedState();
    const second = createAnalysisDataset({
      id: 'd2', name: '数据表2', kind: 'derived', rows: [{ id: 30 }],
    });
    state = workspaceReducer(state, {
      type: 'execute', command: { type: 'addDataset', dataset: second, activate: false },
    });
    const guarded = workspaceReducer(state, {
      type: 'execute', command: { type: 'closeDataset', datasetId: 'd1' },
    });
    expect(guarded).toBe(state);
    const selfReplacement = workspaceReducer(state, {
      type: 'execute',
      command: { type: 'closeDataset', datasetId: 'd1', replacementDatasetId: 'd1' },
    });
    expect(selfReplacement).toBe(state);

    state = workspaceReducer(state, {
      type: 'execute',
      command: { type: 'closeDataset', datasetId: 'd1', replacementDatasetId: 'd2' },
    });
    expect(state.registry.byId.d1).toBeUndefined();
    expect(selectChartDataset(state)?.id).toBe('d2');

    state = workspaceReducer(state, { type: 'undo' });
    expect(state.registry.byId.d1).toBe(dataset);
    expect(selectChartDataset(state)?.id).toBe('d1');
  });

  it('can explicitly unlink the chart while closing its last dataset', () => {
    const before = loadedState();
    const closed = workspaceReducer(before, {
      type: 'execute',
      command: { type: 'closeDataset', datasetId: 'd1', replacementDatasetId: null },
    });
    expect(selectDatasets(closed)).toEqual([]);
    expect(selectChartDataset(closed)).toBeNull();
    expect(closed.registry.chartPageOrder).toEqual(['chart']);
    expect(workspaceReducer(closed, { type: 'undo' }).registry.byId.d1).toBe(dataset);
  });

  it('duplicates a dataset through one undoable command', () => {
    const before = loadedState();
    const copy = duplicateAnalysisDataset(dataset, 'd2', '数据表2');
    const after = workspaceReducer(before, {
      type: 'execute', command: { type: 'duplicateDataset', dataset: copy },
    });
    expect(selectDatasets(after).map((item) => item.id)).toEqual(['d1', 'd2']);
    expect(after.activeTabId).toBe('d2');
    expect(after.selection.activeSurface).toBe('worksheet');
    expect(workspaceReducer(after, { type: 'undo' }).registry.byId.d2).toBeUndefined();
  });

  it('renames a dataset through undoable metadata updates', () => {
    const before = loadedState();
    const renamed = workspaceReducer(before, {
      type: 'execute', command: { type: 'renameDataset', datasetId: 'd1', name: '  测量结果  ' },
    });
    expect(renamed.registry.byId.d1.name).toBe('测量结果');
    expect(renamed.registry.byId.d1.rows).toBe(before.registry.byId.d1.rows);
    expect(workspaceReducer(renamed, { type: 'undo' }).registry.byId.d1.name).toBe('数据表1');
    expect(workspaceReducer(before, {
      type: 'execute', command: { type: 'renameDataset', datasetId: 'd1', name: '   ' },
    })).toBe(before);
  });

  it('stores sparse point styles and restores them with undo', () => {
    let state = loadedState();
    state = workspaceReducer(state, {
      type: 'execute',
      command: {
        type: 'updatePointStyle',
        datasetId: 'd1',
        rowIds: ['d1:device:10'],
        patch: { color: '#ff0000', size: 8 },
      },
    });
    expect(state.pointStyles.d1['d1:device:10']).toEqual({ color: '#ff0000', size: 8 });
    state = workspaceReducer(state, { type: 'undo' });
    expect(state.pointStyles.d1).toBeUndefined();
  });

  it('stores metric styles sparsely without copying dataset rows', () => {
    const before = loadedState();
    const styled = workspaceReducer(before, {
      type: 'execute',
      command: {
        type: 'updatePointStyle',
        datasetId: 'd1',
        rowIds: [],
        metric: 'qs',
        patch: { symbol: 'diamond', opacity: 0.4 },
      },
    });
    expect(styled.registry.byId.d1).toBe(before.registry.byId.d1);
    expect(styled.pointStyles.d1[pointStyleMetricKey('qs')]).toEqual({
      symbol: 'diamond', opacity: 0.4,
    });
    expect(workspaceReducer(styled, { type: 'undo' }).pointStyles.d1).toBeUndefined();
  });
});


it('keeps current analysis configuration and notes through row undo', () => {
  let state = loadedState();
  state = workspaceReducer(state, { type: 'execute', command: { type: 'updateRows', datasetId: 'd1',
    updates: [{ rowId: 'd1:device:10', changes: { qs: 300 } }] } });
  state = workspaceReducer(state, { type: 'updateSpec', spec: { ...state.spec, chart: { ...state.spec.chart, type: 'box' } } });
  state = workspaceReducer(state, { type: 'updateNotes', notes: '保留备注' });
  const undone = workspaceReducer(state, { type: 'undo' });
  expect(undone.registry.byId.d1.rows[0].qs).toBe(100);
  expect(undone.spec.chart.type).toBe('box');
  expect(undone.notes).toBe('保留备注');
});
