import { expect, it } from 'vitest';
import { platformRows, sourceA } from '../__fixtures__/platformFixtures';
import { INITIAL_WORKSPACE_STATE, workspaceReducer, type WorkspaceAction } from '../workspace';
import { createAnalysisDataset } from '../model/dataset';
import { restoreWorkspace, serializeWorkspace } from './projectCodec';
import { emptyProject, exportProjectBackup, forkProject, importProjectBackup, templateSpec, validateTemplateFields } from './projectBackup';

function populated() {
  const project = emptyProject();
  const dataset = createAnalysisDataset({ id: 'data', name: 'data', kind: 'query',
    rows: platformRows(2).map((row) => ({ ...row, _source_ref: sourceA })) });
  const state = workspaceReducer(INITIAL_WORKSPACE_STATE, { type: 'execute', command: { type: 'addDataset', dataset, activate: 'dataset' } });
  return serializeWorkspace(state, state.spec, project.head);
}
it('file roundtrip keeps rows/config/source but creates independent project/version/chunk IDs', () => {
  const original = populated(), loaded = importProjectBackup(exportProjectBackup(original));
  expect(loaded.datasets).toEqual(original.datasets);
  expect(loaded.version.spec).toEqual(original.version.spec);
  expect(loaded.head.projectId).not.toBe(original.head.projectId);
  expect(loaded.version.versionId).not.toBe(original.version.versionId);
  expect(loaded.version.chunkRefs).not.toEqual(original.version.chunkRefs);
  expect(loaded.version.baseGeneration).toBe(0);
});
it('rejects future, credentials, raw curve and incomplete files before a write is possible', () => {
  const p = populated();
  expect(() => importProjectBackup({ format: 'aln-analysis-project', schema_version: 99, project: p })).toThrow();
  p.version.extras = { token: 'secret' };
  expect(() => exportProjectBackup(p)).toThrow();
  p.version.extras = { raw_curves: [1, 2] };
  expect(() => exportProjectBackup(p)).toThrow('原始曲线');
  p.version.extras = {}; p.version.status = 'draft';
  expect(() => exportProjectBackup(p)).toThrow('保存完整');
});
it('snapshot reducer rejects edits/config/undo while a named fork is editable', () => {
  const frozen = restoreWorkspace(forkProject(populated(), '快照', 'snapshot'));
  expect(frozen.readOnly).toBe(true);
  const edit: WorkspaceAction = { type: 'execute', command: { type: 'updateRows', datasetId: 'data', updates: [{ rowId: frozen.registry.byId.data.rows[0].rowId, changes: { qs: 99 } }] } } as const;
  expect(workspaceReducer(frozen, edit)).toBe(frozen);
  expect(workspaceReducer(frozen, { type: 'updateNotes', notes: 'changed' })).toBe(frozen);
  const copy = restoreWorkspace(forkProject(populated(), '副本', 'named'));
  expect(copy.readOnly).toBe(false);
  expect(workspaceReducer(copy, edit).registry.byId.data.rows[0].qs).toBe(99);
});
it('templates strip fixed batch/source selectors and comparisons without removing metric filters', () => {
  const spec = populated().version.spec;
  spec.query_spec.filters = { batch_no: 'same', qs: 120, origin_id: sourceA.origin_id };
  const result = templateSpec(spec);
  expect(result.query_spec.filters).toEqual({ qs: 120 });
  spec.query_spec.filters = { batch_no: 'same', qs: 120 };
  expect(templateSpec(spec).query_spec.filters).toEqual({ qs: 120 });
  expect(result.comparison).toBeNull();
});

it('templates reject unavailable fields and different units before creating a head', () => {
  const spec = emptyProject().version.spec;
  spec.chart.y_fields = [{ name: 'fs_ghz', aggregation: 'all' }]; spec.units = { fs_ghz: 'GHz' };
  expect(() => validateTemplateFields(spec, null)).toThrow('元数据');
  expect(() => validateTemplateFields(spec, [{ name: 'fs_ghz', unit: 'MHz' }])).toThrow('单位');
  expect(() => validateTemplateFields(spec, [{ name: 'fs_ghz', unit: 'GHz' }])).not.toThrow();
});
