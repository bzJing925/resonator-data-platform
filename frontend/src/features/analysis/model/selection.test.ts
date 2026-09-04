import { describe, expect, it } from 'vitest';
import { createSelectionState, selectRows } from './selection';

describe('analysis selection model', () => {
  it('supports replace, add and toggle in one dataset', () => {
    let selection = selectRows(createSelectionState(), {
      datasetId: 'd1', rowIds: ['r1'], activeSurface: 'chart',
    });
    selection = selectRows(selection, { datasetId: 'd1', rowIds: ['r2'], mode: 'add' });
    selection = selectRows(selection, { datasetId: 'd1', rowIds: ['r1'], mode: 'toggle' });
    expect(selection.rowIds).toEqual(new Set(['r2']));
    expect(selection.anchorRowId).toBe('r1');
    expect(selection.activeSurface).toBe('chart');
  });

  it('replaces the selection when the dataset changes', () => {
    const first = selectRows(createSelectionState(), { datasetId: 'd1', rowIds: ['r1'] });
    const second = selectRows(first, { datasetId: 'd2', rowIds: ['r2'], mode: 'add' });
    expect(second.datasetId).toBe('d2');
    expect(second.rowIds).toEqual(new Set(['r2']));
  });

  it('clears the anchor when replacement selection is empty', () => {
    const selected = selectRows(createSelectionState(), {
      datasetId: 'd1', rowIds: ['r1'], activeSurface: 'chart',
    });
    const cleared = selectRows(selected, { datasetId: 'd1', rowIds: [], mode: 'replace' });
    expect(cleared.rowIds.size).toBe(0);
    expect(cleared.anchorRowId).toBeNull();
  });
});
