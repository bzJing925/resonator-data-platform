import { expect, it } from 'vitest';
import { createAnalysisDataset } from '../model/dataset';
import { INITIAL_WORKSPACE_STATE, workspaceReducer } from '../workspace';
import { serializeWorkspace } from '../project/projectCodec';
import { freezeReportInput } from './reportModel';
import { reportHtml } from './reportHtml';
import { describeModel, modelDescriptionText } from './modelDescription';
function input() {
  const state = workspaceReducer(INITIAL_WORKSPACE_STATE, { type: 'execute', command: { type: 'addDataset', dataset: createAnalysisDataset({ id: 'a', name: '<A>', kind: 'query', rows: [{ eg: 1, qs: 10 }] }), activate: 'chart' } });
  const now = new Date().toISOString();
  const project = serializeWorkspace(state, state.spec, { projectId: 'project', currentVersionId: 'version', generation: 1, name: '项目', kind: 'named', updatedAt: now, lastOpenedAt: now, schemaVersion: 1 }, '<script>fetch("https://bad.test")</script><img src=x onerror=alert(1)>');
  return freezeReportInput(project);
}
it('all user text is escaped and CSP disallows scripts/connections; scope and full metadata remain', () => {
  const frozen = input(), images = frozen.figures.map((figure) => ({ title: figure.title, dataUrl: 'data:image/png;base64,aGVsbG8=', bytes: 5 }));
  const html = reportHtml(frozen, images, '<img src=x onerror=alert(1)>');
  expect(html).not.toContain('<script>'); expect(html).not.toContain('<img src=x');
  expect(html).toContain('&lt;script&gt;'); expect(html).toContain("script-src 'none'"); expect(html).toContain("connect-src 'none'");
  expect(html).toContain('当前工作区部分数据'); expect(html).toContain('source_refs');
  expect(html).toContain('原始曲线未包含'); expect(html).toContain('会话撤销历史未包含');
  expect(html).toContain('模型版本：未知'); expect(html).toContain('A4 landscape');
});
it('missing images and remote/SVG image URLs fail rather than export partial report', () => {
  const frozen = input();
  expect(() => reportHtml(frozen, [])).toThrow('不完整');
  for (const dataUrl of ['https://bad/image.png', 'data:image/svg+xml,<svg/>']) expect(() => reportHtml(frozen, [{ title: '图', dataUrl, bytes: 1 }])).toThrow('PNG');
});
it('only existing model evidence is displayed; invalid evidence downgrades validation and out of range is explicit', () => {
  expect(modelDescriptionText(describeModel(null))).toContain('未知/未提供');
  const model = { schema_version: 1, model_digest: 'a'.repeat(64), training_range_status: 'known', training_range: { eg: [0, 2] }, training_source: 'simulation', validation_status: 'verified', validation_metrics: { mae: 1 } };
  expect(describeModel(model, { eg: 3 })).toMatchObject({ validation_status: 'unverified', applicability: 'out_of_range', training_source: 'simulation' });
  expect(describeModel({ ...model, validation_dataset_digest: 'b'.repeat(64) }, { eg: 1 })).toMatchObject({ validation_status: 'verified', applicability: 'within_range' });
});
