import { describe, expect, it } from 'vitest';
import { sourceA, sourceB } from '../__fixtures__/platformFixtures';
import { createAnalysisDataset, duplicateAnalysisDataset } from './dataset';
import { executeAnalysisCommand } from './commands';
import { INITIAL_WORKSPACE_STATE, workspaceReducer } from '../workspace';
import { attachSourceRefs, sourceTuple, validateSourceRefs } from './provenance';

function loaded() {
  const dataset = createAnalysisDataset({ id: 'a', name: '同名', kind: 'query', rows: [
    { id: 1, qs: 10, _source_ref: sourceA }, { id: 2, qs: 20, _source_ref: sourceB },
  ] });
  const state = workspaceReducer(INITIAL_WORKSPACE_STATE, {
    type: 'execute', command: { type: 'addDataset', dataset, activate: 'dataset' },
  });
  return { state, dataset };
}

describe('stable provenance and local ledgers', () => {
  it('keeps same-name sources distinct and path moves independent', () => {
    expect(sourceTuple(sourceA)).not.toEqual(sourceTuple(sourceB));
    expect(attachSourceRefs([{ _source_ref: sourceA, s_param_path: '/old' }]))
      .toEqual(attachSourceRefs([{ _source_ref: sourceA, s_param_path: '/new' }]));
    expect(validateSourceRefs({ ...sourceA, source_revision: -1 })).toBe(false);
    expect(attachSourceRefs([{ id: 99 }, { _source_ref: {} }])).toEqual([null, null]);
  });
  it('records edits and exclusions with original values; undo restores the ledger', () => {
    const { state, dataset } = loaded();
    const rowId = dataset.rows[0].rowId;
    const edited = workspaceReducer(state, { type: 'execute', command: {
      type: 'updateRows', datasetId: 'a', updates: [{ rowId, changes: { qs: 30 } }],
    } });
    expect(edited.registry.byId.a.edits[0]).toMatchObject({ before: 10, after: 30, source: sourceA });
    const removed = workspaceReducer(edited, { type: 'execute', command: {
      type: 'deleteRows', datasetId: 'a', rowIds: [rowId],
    } });
    expect(removed.registry.byId.a.exclusions[0].originalRow.qs).toBe(30);
    expect(removed.registry.byId.a.exclusions[0].source).toEqual(sourceA);
    expect(workspaceReducer(removed, { type: 'undo' }).registry.byId.a.exclusions).toEqual([]);
    expect(workspaceReducer(edited, { type: 'undo' }).registry.byId.a.edits).toEqual([]);
  });
  it('copies workspace identities and edit ownership but retains source IDs', () => {
    const { state, dataset } = loaded();
    const edited = executeAnalysisCommand(state, { type: 'updateRows', datasetId: 'a',
      updates: [{ rowId: dataset.rows[0].rowId, changes: { qs: 30 } }] }).registry.byId.a;
    const copy = duplicateAnalysisDataset(edited, 'b', '副本');
    expect(copy.rows[0].rowId).not.toBe(edited.rows[0].rowId);
    expect(copy.provenance[copy.rows[0].rowId]).toEqual(sourceA);
    expect(copy.edits[0]).toMatchObject({ datasetId: 'b', rowId: copy.rows[0].rowId });
    expect(copy.edits[0]).not.toBe(edited.edits[0]);
  });
  it('cannot edit source identity cells', () => {
    const { state, dataset } = loaded();
    const after = executeAnalysisCommand(state, { type: 'updateRows', datasetId: 'a',
      updates: [{ rowId: dataset.rows[0].rowId, changes: { _source_ref: sourceB } }] });
    expect(after.registry.byId.a.rows[0]._source_ref).toEqual(sourceA);
    expect(after.registry.byId.a.edits).toEqual([]);
  });
});
