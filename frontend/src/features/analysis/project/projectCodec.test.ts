import { describe, expect, it } from 'vitest';
import { platformRows, sourceA } from '../__fixtures__/platformFixtures';
import { createAnalysisDataset } from '../model/dataset';
import { INITIAL_WORKSPACE_STATE, workspaceReducer } from '../workspace';
import { migrateLegacyExplore, migrateProjectBackup, restoreWorkspace, serializeWorkspace, validateProjectBackup } from './projectCodec';
import type { ProjectBackup, ProjectHead } from './projectTypes';

function backup(count = 2001): ProjectBackup {
  const rows = platformRows(count).map((row) => ({ ...row, _source_ref: sourceA }));
  const dataset = createAnalysisDataset({ id: 'dataset-a', name: '表格A', kind: 'query', rows });
  let state = workspaceReducer(INITIAL_WORKSPACE_STATE, { type: 'execute', command: {
    type: 'addDataset', dataset, activate: 'dataset',
  } });
  state = workspaceReducer(state, { type: 'execute', command: {
    type: 'updateRows', datasetId: dataset.id, updates: [{ rowId: dataset.rows[count - 1].rowId, changes: { qs: 301 } }],
  } });
  const head: ProjectHead = { projectId: crypto.randomUUID(), currentVersionId: crypto.randomUUID(),
    generation: 1, name: '测试项目', kind: 'named', updatedAt: new Date().toISOString(),
    lastOpenedAt: new Date().toISOString(), schemaVersion: 1 };
  return { format: 'aln-analysis-project', schema_version: 1, exported_at: new Date().toISOString(),
    project: serializeWorkspace(state, migrateLegacyExplore({ chartType: 'box', xFields: ['eg'], yFields: ['qs'] }), head) };
}

describe('complete project codec', () => {
  it('round-trips 2001 rows, edit and source identity but clears session history/selection', () => {
    const original = backup();
    const checked = validateProjectBackup(JSON.stringify(original));
    expect(checked.project.version.chunkRefs['dataset-a']).toHaveLength(2);
    const restored = restoreWorkspace(checked.project);
    expect(restored.registry.byId['dataset-a'].rows[2000].qs).toBe(301);
    expect(restored.registry.byId['dataset-a'].edits).toHaveLength(1);
    expect(restored.registry.byId['dataset-a'].provenance).toEqual(original.project.datasets['dataset-a'].provenance);
    expect(restored.history.undoStack).toEqual([]);
    expect(restored.selection.rowIds.size).toBe(0);
    expect(checked.project.version.spec.chart.type).toBe('box');
  });
  it('retains the original excluded row and coherent ledger', () => {
    const value = backup(2);
    const loaded = restoreWorkspace(value.project);
    const after = workspaceReducer(loaded, { type: 'execute', command: {
      type: 'deleteRows', datasetId: 'dataset-a', rowIds: [loaded.registry.byId['dataset-a'].rows[1].rowId],
    } });
    value.project = serializeWorkspace(after, value.project.version.spec, value.project.head);
    const checked = validateProjectBackup(value);
    expect(checked.project.datasets['dataset-a'].rows).toHaveLength(1);
    expect(checked.project.version.exclusions[0].originalRow.qs).toBe(301);
  });
  it('rejects future schemas, dangling identities, duplicate rows and draft pointer', () => {
    const value = backup(2);
    expect(() => migrateProjectBackup({ ...value, schema_version: 99 })).toThrow('版本不支持');
    const bad = structuredClone(value);
    bad.project.datasets['dataset-a'].rows[1].rowId = bad.project.datasets['dataset-a'].rows[0].rowId;
    expect(() => validateProjectBackup(bad)).toThrow('标识重复');
    const missing = structuredClone(value);
    missing.project.version.activeTabId = 'absent';
    expect(() => validateProjectBackup(missing)).toThrow('活动页不存在');
    const draft = structuredClone(value);
    draft.project.version.status = 'draft';
    expect(() => validateProjectBackup(draft)).toThrow('版本指针无效');
  });
  it('rejects credentials and executable objects before any store operation', () => {
    const value = backup(2);
    expect(() => validateProjectBackup({ ...value, token: 'private' })).toThrow('禁止项目字段');
    expect(() => validateProjectBackup({ ...value, arbitrary: () => 1 })).toThrow('需要对象');
    expect(() => validateProjectBackup('{broken')).toThrow('JSON无效');
  });
  it('preserves controlled extras, converts nonfinite cells with warnings', () => {
    const value = backup(2);
    const row = value.project.datasets['dataset-a'].rows[0];
    row.qs = Infinity;
    const checked = validateProjectBackup({ ...value,
      project: { ...value.project, version: { ...value.project.version, extension_label: '保留' } } });
    expect(checked.project.datasets['dataset-a'].rows[0].qs).toBeNull();
    expect(checked.project.version.extras?.extension_label).toBe('保留');
    expect(checked.project.version.extras?.validation_warnings).toEqual(['非有限数值已转为null']);
  });
  it('rejects broken block and provenance references, accepts reordered JSON object keys', () => {
    const value = backup(2);
    const reversed = Object.fromEntries(Object.entries(value.project.version.registryMetadata.byId['dataset-a']).reverse());
    const reordered = { ...value, project: { ...value.project, version: { ...value.project.version,
      registryMetadata: { ...value.project.version.registryMetadata, byId: { 'dataset-a': reversed } } } } };
    expect(validateProjectBackup(reordered).project.datasets['dataset-a'].rows).toHaveLength(2);
    const broken = structuredClone(value);
    broken.project.version.chunkRefs['dataset-a'] = [];
    expect(() => validateProjectBackup(broken)).toThrow('行块引用数量无效');
    const falseSource = structuredClone(value);
    falseSource.project.datasets['dataset-a'].rows[0]._source_ref = { ...sourceA, source_revision: 999 };
    expect(() => validateProjectBackup(falseSource)).toThrow('行来源与来源登记不一致');
  });
  it('legacy configuration migration does not claim row restoration', () => {
    const spec = migrateLegacyExplore({ chartType: 'scatter', xFields: ['eg'], data: [{ id: 1 }] });
    expect(spec.query_spec.fields).toEqual([]);
    expect('rows' in spec).toBe(false);
  });
});

it('keeps real backend manifest microseconds coherent with persisted metadata', () => {
  const value = backup(2), d = value.project.datasets['dataset-a'];
  d.manifest = { schema_version: 1, query_spec: { filters: {}, fields: ['qs'], order_by: null }, source_refs: [{ ...sourceA, source_device_key: null }], matched_total: 2, total_relation: 'exact', returned: 2, truncated: false, sampling: { method: 'none', rate: null }, local_edits: d.edits.length, excluded_rows: 0, generated_at: '2026-10-10T15:25:31.689999+00:00', consistency: 'verified' };
  const state = restoreWorkspace(value.project);
  value.project = serializeWorkspace(state, state.spec, value.project.head);
  const checked = validateProjectBackup(value);
  expect(checked.project.datasets['dataset-a'].manifest?.generated_at).toBe('2026-10-10T15:25:31.689999+00:00');
});
