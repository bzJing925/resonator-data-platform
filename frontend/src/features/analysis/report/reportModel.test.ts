import { expect, it, vi } from 'vitest';
import { INITIAL_WORKSPACE_STATE, workspaceReducer } from '../workspace';
import { createAnalysisDataset } from '../model/dataset';
import { serializeWorkspace } from '../project/projectCodec';
import type { ProjectHead } from '../project/projectTypes';
import { freezeReportInput } from './reportModel';
import { renderReportImages } from './plotImages';
function project() {
  const dataset = createAnalysisDataset({ id: 'a', name: 'A', kind: 'query', rows: [{ eg: 1, qs: 10 }, { eg: 2, qs: null }] });
  const state = workspaceReducer(INITIAL_WORKSPACE_STATE, { type: 'execute', command: { type: 'addDataset', dataset, activate: 'chart' } });
  const now = new Date().toISOString();
  const head: ProjectHead = { projectId: crypto.randomUUID(), currentVersionId: crypto.randomUUID(), generation: 1, name: '报告', kind: 'named', updatedAt: now, lastOpenedAt: now, schemaVersion: 1 };
  return serializeWorkspace(state, state.spec, head, '保存的备注');
}
it('freezes complete version rows, metadata and statistics independently of later workspace edits', () => {
  const input = project(), frozen = freezeReportInput(input);
  input.datasets.a.rows[0].qs = 100; input.version.notes = '新备注';
  expect(frozen.project.datasets.a.rows[0].qs).toBe(10);
  expect(frozen.notes).toBe('保存的备注');
  expect(frozen.summaries[0].statistics.qs).toMatchObject({ valid_n: 1, missing_n: 1, mean: 10 });
  expect(frozen.partial).toBe(true);
  expect(Object.isFrozen(frozen.figures[0].scene.allTraces)).toBe(true);
  expect(() => { frozen.project.datasets.a.rows[0].qs = 20; }).toThrow();
  const draft = project(); draft.version.status = 'draft';
  expect(() => freezeReportInput(draft)).toThrow('版本指针');
});
it('renders fixed size serially and always purges detached DOM; later failure rejects whole export', async () => {
  const figures = freezeReportInput(project()).figures;
  const newPlot = vi.fn().mockResolvedValue(undefined), purge = vi.fn();
  const toImage = vi.fn().mockResolvedValueOnce('data:image/png;base64,aGVsbG8=').mockRejectedValueOnce(new Error('graph error'));
  await expect(renderReportImages([figures[0], figures[0]], { newPlot, toImage, purge })).rejects.toThrow('graph error');
  expect(newPlot).toHaveBeenCalledTimes(2); expect(purge).toHaveBeenCalledTimes(2);
  expect(toImage.mock.calls[0][1]).toEqual({ format: 'png', width: 1600, height: 1000, scale: 1 });
  expect(newPlot.mock.calls[0][2]).toMatchObject({ width: 1600, height: 1000, autosize: false });
  expect(document.querySelector('[data-report-plot]')).toBeNull();
});
it('purges if newPlot rejects and rejects untrusted image URLs or excess figure count', async () => {
  const figure = freezeReportInput(project()).figures[0], purge = vi.fn();
  await expect(renderReportImages([figure], { newPlot: vi.fn().mockRejectedValue(new Error('plot error')), toImage: vi.fn(), purge })).rejects.toThrow('plot error');
  expect(purge).toHaveBeenCalledTimes(1);
  await expect(renderReportImages([figure], { newPlot: vi.fn().mockResolvedValue(undefined), toImage: vi.fn().mockResolvedValue('https://remote/image.png'), purge })).rejects.toThrow('无效图片');
  await expect(renderReportImages(Array(21).fill(figure))).rejects.toThrow('20图');
  expect(document.querySelector('[data-report-plot]')).toBeNull();
});
