import { expect, test } from '@playwright/test';

test.use({ baseURL: 'http://127.0.0.1:5173' });

test.beforeEach(async ({ page }) => {
  await page.goto('/e2e/analysis/projects.html');
  await page.waitForFunction(() => 'projectHarness' in window);
});

test('real IDB: two blocks, one-cell reuse, reload, previous version and GC', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const h = (window as any).projectHarness;
    const head = await h.create();
    const previous = await h.store.load(head.projectId);
    const id = previous.version.registryMetadata.order[0];
    const state = h.restoreWorkspace(previous);
    const rowId = state.registry.byId[id].rows[2000].rowId;
    const next = h.executeAnalysisCommand(state, { type: 'updateRows', datasetId: id,
      updates: [{ rowId, changes: { qs: 999 } }] });
    const saved = await h.store.save(h.serializeWorkspace(next, previous.version.spec, head), head.generation);
    const loaded = await h.store.load(head.projectId);
    const counts = await h.counts();
    await h.store.collectOrphans();
    const afterGC = await h.counts();
    return { projectId: head.projectId, oldRefs: previous.version.chunkRefs[id], refs: loaded.version.chunkRefs[id],
      value: loaded.datasets[id].rows[2000].qs, generation: saved.generation, counts, afterGC };
  });
  expect(result.oldRefs[0]).toBe(result.refs[0]);
  expect(result.oldRefs[1]).not.toBe(result.refs[1]);
  expect(result.value).toBe(999); expect(result.generation).toBe(2);
  expect(result.counts).toEqual({ heads: 1, versions: 2, chunks: 3 });
  expect(result.afterGC).toEqual(result.counts);
  await page.reload(); await page.waitForFunction(() => 'projectHarness' in window);
  const restored = await page.evaluate(async (id) => {
    const h = (window as any).projectHarness, p = await h.store.load(id);
    const state = h.restoreWorkspace(p), datasetId = p.version.registryMetadata.order[0];
    return { value: state.registry.byId[datasetId].rows[2000].qs,
      history: state.history.undoStack.length, selection: state.selection.rowIds.size };
  }, result.projectId);
  expect(restored).toEqual({ value: 999, history: 0, selection: 0 });
});

test('real IDB: two writers with one generation; loser cannot switch head', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const h = (window as any).projectHarness, head = await h.create(2);
    const a = new h.IndexedDbProjectStore(), b = new h.IndexedDbProjectStore();
    const pa = await a.load(head.projectId), pb = await b.load(head.projectId);
    const output = await Promise.allSettled([a.save(pa, 1), b.save(pb, 1)]);
    const loaded = await h.store.load(head.projectId);
    await h.store.collectOrphans();
    a.close(); b.close();
    return { states: output.map((r) => r.status), errors: output.filter((r) => r.status === 'rejected').map((r) => r.reason.code),
      generation: loaded.head.generation, rows: Object.values(loaded.datasets).map((d: any) => d.rows.length), counts: await h.counts() };
  });
  expect(result.states.filter((state) => state === 'fulfilled')).toHaveLength(1);
  expect(result.errors).toEqual(['CONFLICT']); expect(result.generation).toBe(2);
  expect(result.rows).toEqual([2]); expect(result.counts.versions).toBe(2);
});

test('real IDB: abort and quota leave the previous complete head intact', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const h = (window as any).projectHarness, head = await h.create(2);
    const p = await h.store.load(head.projectId);
    const id = p.version.registryMetadata.order[0], state = h.restoreWorkspace(p);
    const next = h.executeAnalysisCommand(state, { type: 'updateRows', datasetId: id,
      updates: [{ rowId: state.registry.byId[id].rows[0].rowId, changes: { qs: 300 } }] });
    const input = h.serializeWorkspace(next, p.version.spec, head);
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (value: any, key?: IDBValidKey) {
      if (this.name === 'heads') { this.transaction.abort(); throw new DOMException('quota fixture', 'QuotaExceededError'); }
      return key === undefined ? put.call(this, value) : put.call(this, value, key);
    };
    let code = '';
    try { await h.store.save(input, head.generation); } catch (e: any) { code = e.code; }
    finally { IDBObjectStore.prototype.put = put; }
    const actual = await h.store.load(head.projectId);
    await h.store.collectOrphans();
    return { code, version: actual.head.currentVersionId, original: head.currentVersionId,
      value: actual.datasets[id].rows[0].qs, counts: await h.counts() };
  });
  expect(result.code).toBe('QUOTA'); expect(result.version).toBe(result.original);
  expect(result.value).toBe(0); expect(result.counts).toEqual({ heads: 1, versions: 1, chunks: 1 });
});

test('real IDB: corrupted block is an explicit failure', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const h = (window as any).projectHarness, head = await h.create(2), p = await h.store.load(head.projectId);
    const ref = Object.values(p.version.chunkRefs)[0] as string[];
    await new Promise<void>((resolve) => {
      const open = indexedDB.open('aln_analysis_projects', 1);
      open.onsuccess = () => {
        const db = open.result, tx = db.transaction('chunks', 'readwrite'), rows = tx.objectStore('chunks');
        const get = rows.get(ref[0]); get.onsuccess = () => rows.put({ ...get.result, digest: 'false' });
        tx.oncomplete = () => { db.close(); resolve(); };
      };
    });
    try { await h.store.load(head.projectId); return 'unexpected'; } catch (e: any) { return e.code; }
  });
  expect(result).toBe('CORRUPT');
});

test('real IDB: missing version and future database version cannot restore silently', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const h = (window as any).projectHarness, head = await h.create(2);
    await new Promise<void>((resolve) => {
      const open = indexedDB.open('aln_analysis_projects', 1);
      open.onsuccess = () => {
        const db = open.result, tx = db.transaction('versions', 'readwrite');
        tx.objectStore('versions').delete([head.projectId, head.currentVersionId]);
        tx.oncomplete = () => { db.close(); resolve(); };
      };
    });
    let missing = '', future = '';
    try { await h.store.load(head.projectId); } catch (e: any) { missing = e.code; }
    await new Promise<void>((resolve, reject) => {
      const upgrade = indexedDB.open('aln_analysis_projects', 2);
      upgrade.onerror = () => reject(upgrade.error);
      upgrade.onsuccess = () => { upgrade.result.close(); resolve(); };
    });
    try { await h.store.list(); } catch (e: any) { future = e.code; }
    return { missing, future };
  });
  expect(result).toEqual({ missing: 'CORRUPT', future: 'UNSUPPORTED_VERSION' });
});

test('React autosave: edited/excluded/undo/config rows recover; selection never saves', async ({ page }) => {
  await page.goto('/e2e/analysis/projectWorkspace.html');
  await page.getByTestId('load-rows').click();
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  await page.getByTestId('edit-row').click();
  await expect(page.getByTestId('last-value')).toHaveText('991');
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  await page.getByTestId('exclude-row').click();
  await expect(page.getByTestId('row-count')).toHaveText('2000');
  await page.getByTestId('undo').click();
  await page.getByTestId('change-chart').click();
  await expect(page.getByTestId('row-count')).toHaveText('2001');
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  const generation = await page.evaluate(() => (window as any).workspaceHarness.project.head.generation);
  await page.clock.install();
  await page.getByTestId('select-row').click();
  await page.clock.runFor(1200);
  expect(await page.evaluate(() => (window as any).workspaceHarness.project.head.generation)).toBe(generation);
  await page.clock.resume();
  await page.reload();
  await expect(page.getByTestId('row-count')).toHaveText('2001');
  await expect(page.getByTestId('last-value')).toHaveText('991');
  await expect(page.getByTestId('chart-type')).toHaveText('box');
  await expect(page.getByTestId('undo')).toBeDisabled();
  expect(await page.evaluate(() => (window as any).workspaceHarness.selection.rowIds.size)).toBe(0);
});

test('React autosave: edits during an in-flight save remain dirty until the next commit', async ({ page }) => {
  await page.goto('/e2e/analysis/projectWorkspace.html');
  await page.getByTestId('load-rows').click();
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  await page.evaluate(() => {
    const api = (window as any).workspaceHarness, original = api.project.store.saveWorkspace.bind(api.project.store);
    let release!: () => void;
    const barrier = new Promise<void>((resolve) => release = resolve);
    let first = true;
    api.project.store.saveWorkspace = async (...args: any[]) => {
      if (first) { first = false; await barrier; }
      return original(...args);
    };
    Object.assign(window, { releaseProjectSave: release });
  });
  await page.getByTestId('edit-row').click();
  await expect(page.getByTestId('project-save-status')).toContainText('正在保存');
  await page.evaluate(() => {
    const api = (window as any).workspaceHarness, d = api.datasets[0];
    api.editCell(d.id, d.rows.at(-1).rowId, 'qs', 992);
    (window as any).releaseProjectSave();
  });
  await expect(page.getByTestId('last-value')).toHaveText('992');
  await expect(page.getByTestId('project-save-status')).toContainText('未保存');
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  await page.reload();
  await expect(page.getByTestId('last-value')).toHaveText('992');
});

test('React autosave: quota shows failure and preserves current edits and old complete data', async ({ page }) => {
  await page.route('**/projectStorageWorker.ts*', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, body: `
      let testQuota = false;
      self.addEventListener('message', ({data}) => { if (data.type === 'test-quota') testQuota = data.enabled; });
      const testAdd = IDBObjectStore.prototype.add;
      IDBObjectStore.prototype.add = function(...args) {
        if (testQuota && this.name === 'chunks') throw new DOMException('quota fixture', 'QuotaExceededError');
        return testAdd.apply(this, args);
      };
    ` + await response.text() });
  });
  await page.goto('/e2e/analysis/projectWorkspace.html');
  await page.getByTestId('load-rows').click();
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  await page.evaluate(() => (window as any).workspaceHarness.project.store.worker.postMessage({ type: 'test-quota', enabled: true }));
  await page.getByTestId('edit-row').click();
  await expect(page.getByTestId('project-save-status')).toContainText('QUOTA');
  await expect(page.getByTestId('last-value')).toHaveText('991');
  const stored = await page.evaluate(async () => {
    const api = (window as any).workspaceHarness, p = await api.project.store.load(api.project.head.projectId);
    return p.datasets[p.version.registryMetadata.order[0]].rows.at(-1).qs;
  });
  expect(stored).not.toBe(991);
  await page.evaluate(() => (window as any).workspaceHarness.project.store.worker.postMessage({ type: 'test-quota', enabled: false }));
  await page.getByRole('button', { name: '重试保存' }).click();
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
});

test('React autosave: multi-window conflict offers only reload or save as', async ({ page, context }) => {
  await page.goto('/e2e/analysis/projectWorkspace.html');
  await page.getByTestId('load-rows').click();
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  const other = await context.newPage();
  await other.goto('http://127.0.0.1:5173/e2e/analysis/projectWorkspace.html');
  await expect(other.getByTestId('row-count')).toHaveText('2001');
  const initialGeneration = await other.evaluate(() => (window as any).workspaceHarness.project.head.generation);
  await other.getByTestId('edit-row').click();
  await expect.poll(() => other.evaluate(() => (window as any).workspaceHarness.project.head.generation))
    .toBeGreaterThan(initialGeneration);
  await expect(other.getByTestId('project-save-status')).toContainText('已保存');
  await page.getByTestId('change-chart').click();
  await expect(page.getByTestId('project-save-status')).toContainText('CONFLICT');
  await expect(page.getByRole('button', { name: '重载其他窗口版本' })).toBeVisible();
  await expect(page.getByRole('button', { name: '另存为新项目' })).toBeVisible();
  await page.getByRole('button', { name: '重载其他窗口版本' }).click();
  await expect(page.getByTestId('last-value')).toHaveText('991');
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  await other.close();
});

test('project menu: dirty switch cancel/discard, snapshot fork and template create new heads', async ({ page }) => {
  await page.goto('/e2e/analysis/projectWorkspace.html');
  await page.getByTestId('load-rows').click();
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  await page.getByTestId('project-menu').click();
  await page.getByLabel('项目名称', { exact: true }).fill('命名项目');
  await page.getByRole('button', { name: '重命名', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).workspaceHarness.project.head.name)).toBe('命名项目');
  await page.getByRole('button', { name: '固定快照', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).workspaceHarness.state.readOnly)).toBe(true);
  await page.getByTestId('edit-row').click();
  await expect(page.getByTestId('last-value')).not.toHaveText('991');
  await page.getByLabel('项目名称', { exact: true }).fill('可编辑副本');
  await page.getByRole('button', { name: '另存为', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).workspaceHarness.state.readOnly)).toBe(false);
  await page.clock.install();
  await page.getByTestId('edit-row').click();
  await page.getByRole('button', { name: '新建', exact: true }).click();
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await page.getByRole('button', { name: '取消切换' }).click();
  await expect(page.getByTestId('row-count')).toHaveText('2001');
  await page.getByRole('button', { name: '新建', exact: true }).click();
  await page.getByRole('button', { name: '放弃修改并继续' }).click();
  await expect(page.getByTestId('row-count')).toHaveText('0');
  await page.clock.resume();
  await page.getByRole('button', { name: '保存模板' }).click();
  await expect(page.getByLabel('分析模板').locator('option')).toHaveCount(2);
  await page.getByLabel('分析模板').selectOption({ index: 1 });
  const oldId = await page.evaluate(() => (window as any).workspaceHarness.project.head.projectId);
  await page.getByRole('button', { name: '应用模板' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).workspaceHarness.project.head.projectId)).not.toBe(oldId);
  expect(await page.evaluate(async () => (window as any).workspaceHarness.project.store.list().then((x: any[]) => x.length))).toBe(5);
});

test('project menu: download complete backup; invalid/future imports do zero writes', async ({ page }) => {
  await page.goto('/e2e/analysis/projectWorkspace.html');
  await page.getByTestId('load-rows').click();
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  await page.getByTestId('project-menu').click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出JSON' }).click();
  const file = await download;
  const path = await file.path();
  expect(path).toBeTruthy();
  const before = await page.evaluate(async () => (window as any).workspaceHarness.project.store.list().then((x: any[]) => x.length));
  await page.getByLabel('导入项目JSON').setInputFiles({ name: 'future.json', mimeType: 'application/json', buffer: Buffer.from('{"schema_version":99}') });
  await expect(page.getByRole('alert')).toBeVisible();
  expect(await page.evaluate(async () => (window as any).workspaceHarness.project.store.list().then((x: any[]) => x.length))).toBe(before);
  await page.getByLabel('导入项目JSON').setInputFiles(path!);
  await expect.poll(() => page.evaluate(async () => (window as any).workspaceHarness.project.store.list().then((x: any[]) => x.length))).toBe(before + 1);
  await expect(page.getByTestId('row-count')).toHaveText('2001');
});

test('marking a complete project opened changes only open metadata, never version/generation/chunks', async ({ page }) => {
  const value = await page.evaluate(async () => {
    const h = (window as any).projectHarness, head = await h.create(2001), counts = await h.counts();
    const opened = await h.store.markOpened(head.projectId, head.currentVersionId);
    const loaded = await h.store.load(head.projectId);
    let conflict = false;
    try { await h.store.markOpened(head.projectId, 'old-version'); } catch (failure: any) { conflict = failure.code === 'CONFLICT'; }
    return { before: head, after: opened, loaded: loaded.head, counts, afterCounts: await h.counts(), conflict };
  });
  expect(value.after.currentVersionId).toBe(value.before.currentVersionId); expect(value.after.generation).toBe(value.before.generation);
  expect(value.loaded.lastOpenedAt).toBe(value.after.lastOpenedAt); expect(value.afterCounts).toEqual(value.counts); expect(value.conflict).toBe(true);
});

test('actual Plotly hover never writes a project version or dataset chunks', async ({ page }) => {
  await page.goto('/e2e/analysis/projectWorkspace.html');
  await page.getByTestId('load-rows').click();
  await expect(page.getByTestId('project-save-status')).toContainText('已保存');
  await page.getByTestId('show-chart').click();
  const point = page.locator('.scatterlayer .point').first();
  await expect(point).toBeVisible();
  const before = await page.evaluate(async () => {
    const p = (window as any).workspaceHarness.project;
    return (await p.store.load(p.head.projectId)).version;
  });
  await point.hover({ force: true });
  await expect(page.locator('.hoverlayer .hovertext').first()).toBeVisible();
  await page.waitForTimeout(1200); // Observe beyond the real autosave debounce.
  const after = await page.evaluate(async () => {
    const p = (window as any).workspaceHarness.project;
    return (await p.store.load(p.head.projectId)).version;
  });
  expect(after).toEqual(before);
});
