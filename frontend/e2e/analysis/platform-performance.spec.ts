import { expect, test } from '@playwright/test';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const exec = promisify(execFile);
for (const count of [50000, 200000]) test(`P4 project ${count} save/edit/restore block`, async ({ page, browser }, info) => {
  test.setTimeout(180000);
  await page.goto('http://127.0.0.1:5173/e2e/analysis/projects.html?worker'); await page.waitForFunction(() => 'projectHarness' in window);
  const cdp = await browser.newBrowserCDPSession();
  let peakRssKiB = 0, sampling = false;
  const sample = async () => {
    if (sampling) return; sampling = true;
    try {
      const { processInfo } = await cdp.send('SystemInfo.getProcessInfo');
      const ids = processInfo.map((p: { id: number }) => p.id).join(',');
      const { stdout } = await exec('ps', ['-o', 'rss=', '-p', ids]);
      peakRssKiB = Math.max(peakRssKiB, stdout.trim().split(/\s+/).reduce((sum, value) => sum + Number(value), 0));
    } finally { sampling = false; }
  };
  await sample(); const baselineRssKiB = peakRssKiB;
  const sampler = setInterval(() => { void sample().catch(() => {}); }, 100);
  let result;
  try { result = await page.evaluate(async (count) => {
    const h = (window as any).projectHarness;
    const writes: { name: string; method: string; ms: number }[] = [];
    for (const method of ['add', 'put'] as const) { const original = IDBObjectStore.prototype[method]; IDBObjectStore.prototype[method] = function(...args: any[]) { const start = performance.now(); const result = original.apply(this, args as any); writes.push({ name: this.name, method, ms: performance.now() - start }); return result; }; }
    const timings: { start: number; duration: number }[] = [];
    const observer = new PerformanceObserver((list) => { for (const entry of list.getEntries()) timings.push({ start: entry.startTime, duration: entry.duration }); });
    observer.observe({ type: 'longtask', buffered: false });
    let started = performance.now(); const head = await h.create(count); const firstSaveMs = performance.now() - started;
    started = performance.now(); const previous = await h.store.load(head.projectId), state = h.restoreWorkspace(previous); const restoreMs = performance.now() - started;
    const id = state.registry.order[0], dataset = state.registry.byId[id], rowId = dataset.rows[0].rowId;
    await new Promise((resolve) => setTimeout(resolve, 0)); const editStart = performance.now();
    started = performance.now(); const next = h.executeAnalysisCommand(state, { type: 'updateRows', datasetId: id, updates: [{ rowId, changes: { qs: 999 } }] }); const commandMs = performance.now() - started;
    const serialStart = performance.now(); const pendingSave = h.store.saveWorkspace({ ...next, spec: previous.version.spec }, head, head.generation); const serializeMs = performance.now() - serialStart; const saved = await pendingSave; const editEnd = performance.now();
    await new Promise((resolve) => setTimeout(resolve, 50)); observer.disconnect(); const loaded = await h.store.load(head.projectId);
    return { count, firstSaveMs, restoreMs, commandMs, serializeMs, maxWriteMs: Math.max(0, ...writes.map((entry) => entry.ms)), writeTimings: writes.filter((entry) => entry.ms > 10), singleCellSaveMs: editEnd - editStart, maxEditBlockMs: Math.max(0, ...timings.filter((entry) => entry.start + entry.duration >= editStart && entry.start < editEnd).map((entry) => entry.duration)), refs: loaded.version.chunkRefs[id], previousRefs: previous.version.chunkRefs[id], value: loaded.datasets[id].rows[0].qs, generation: saved.generation };
  }, count); } finally { clearInterval(sampler); await sample(); await cdp.detach(); }
  Object.assign(result, { baselineRssKiB, peakRssKiB, rssSamplingIntervalMs: 100 });
  console.log('PLATFORM_PERF', JSON.stringify(result)); await info.attach(`P4-${count}.json`, { body: JSON.stringify(result), contentType: 'application/json' });
  expect(result.value).toBe(999); expect(result.refs.slice(1)).toEqual(result.previousRefs.slice(1));
  expect(result.maxEditBlockMs).toBeLessThanOrEqual(100);
});
