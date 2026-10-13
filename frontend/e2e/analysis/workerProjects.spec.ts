import { expect, test } from '@playwright/test';
import { launchHarness } from './electron';
test('actual storage Workers preserve format, CAS, blocks and browser reload', async ({ page }) => {
  await page.goto('http://127.0.0.1:5173/e2e/analysis/projects.html?worker');
  await page.waitForFunction(() => 'projectHarness' in window);
  const result = await page.evaluate(async () => {
    const h = (window as any).projectHarness, head = await h.create(2001);
    const a = new h.WorkerProjectStore(), b = new h.WorkerProjectStore();
    const pa = await a.load(head.projectId), pb = await b.load(head.projectId);
    const output = await Promise.allSettled([a.save(pa, 1), b.save(pb, 1)]);
    const direct = new h.IndexedDbProjectStore(), p = await direct.load(head.projectId);
    const result = { id: head.projectId, generation: p.head.generation, rows: p.datasets[p.version.registryMetadata.order[0]].rows.length,
      status: p.version.status, refs: p.version.chunkRefs, previousRefs: pa.version.chunkRefs,
      states: output.map((r) => r.status), errors: output.filter((r) => r.status === 'rejected').map((r) => r.reason.code) };
    a.close(); b.close(); direct.close(); return result;
  });
  expect(result.generation).toBe(2); expect(result.status).toBe('complete'); expect(result.rows).toBe(2001);
  expect(result.states.filter((s: string) => s === 'fulfilled')).toHaveLength(1); expect(result.errors).toEqual(['CONFLICT']);
  expect(result.refs).toEqual(result.previousRefs);
  await page.reload(); await page.waitForFunction(() => 'projectHarness' in window);
  expect(await page.evaluate(async (id) => (await (window as any).projectHarness.store.load(id)).head.generation, result.id)).toBe(2);
});
test('actual Electron process restart restores same profile without history or selection', async () => {
  test.setTimeout(90000);
  const first = await launchHarness('projectWorkspace.html');
  let app = first.electronApp;
  try {
    await first.page.getByTestId('load-rows').click(); await expect(first.page.getByTestId('project-save-status')).toContainText('已保存');
    await first.page.getByTestId('edit-row').click(); await expect(first.page.getByTestId('last-value')).toHaveText('991');
    await expect(first.page.getByTestId('project-save-status')).toContainText('已保存');
    await first.page.getByTestId('select-row').click();
    const original = await first.page.evaluate(() => (window as any).workspaceHarness.project.head);
    await app.close();
    const next = await launchHarness('projectWorkspace.html', first.storageRoot); app = next.electronApp;
    await expect(next.page.getByTestId('row-count')).toHaveText('2001'); await expect(next.page.getByTestId('last-value')).toHaveText('991');
    expect(await next.page.evaluate(() => {
      const h = (window as any).workspaceHarness;
      return { id: h.project.head.projectId, generation: h.project.head.generation, undo: h.state.history.undoStack.length, selected: h.state.selection.rowIds.size };
    })).toEqual({ id: original.projectId, generation: original.generation, undo: 0, selected: 0 });
  } finally { await app.close(); }
});
