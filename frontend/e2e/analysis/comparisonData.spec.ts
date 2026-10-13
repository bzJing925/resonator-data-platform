import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
let preview: ReturnType<typeof spawn>;
test.beforeAll(async () => {
  preview = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', '5174', '--strictPort'], { cwd: process.cwd(), stdio: 'ignore' });
  await expect.poll(async () => { try { return (await fetch('http://127.0.0.1:5174/e2e/analysis/comparisonData.html')).status; } catch { return 0; } }).toBe(200);
});
test.afterAll(() => { preview.kill('SIGTERM'); });
test.beforeEach(async ({ page }) => {
  await page.goto('http://127.0.0.1:5174/e2e/analysis/comparisonData.html');
  await page.waitForFunction(() => 'comparisonDataHarness' in window);
});
test('actual static DuckDB complete P2 matches real SQLite service fixture, quality and provenance', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const h = (window as any).comparisonDataHarness;
    const data = await (await fetch('webdata/comparison-case.json')).json();
    const inspected = await h.inspectStaticConditions(data.request.comparison, data.request.quality_rule);
    const calculated = await h.loadStaticComparison(data.request.comparison, data.request.quality_rule, { qs: '1' });
    const raw = await h.queryDevices({ fields: ['qs'], limit: 2 });
    return { static: h.isStatic, inspected, calculated, expected: data.expected, raw };
  });
  expect(result.static).toBe(true);
  for (const side of ['a', 'b'] as const) {
    expect(result.calculated.cohorts[side].statistics.qs.mean).toBeCloseTo(result.expected.cohorts[side].statistics.qs.mean, 9);
    expect(result.calculated.cohorts[side].statistics.qs.p25).toBeCloseTo(result.expected.cohorts[side].statistics.qs.p25, 9);
    expect(result.calculated.cohorts[side].statistics.qs.valid_n).toBe(3);
    expect(result.calculated.cohorts[side].statistics.qs.missing_n).toBe(1);
    expect(result.calculated.cohorts[side].quality).toEqual(result.expected.cohorts[side].quality);
    expect(result.calculated.cohorts[side].pf).toEqual(result.expected.cohorts[side].pf);
    expect(result.calculated.cohorts[side].manifest).toMatchObject({ matched_total: 4, returned: 4, truncated: false, consistency: 'verified' });
    expect(result.inspected.endpoints[side].matched_total).toBe(4);
  }
  expect(result.calculated.deltas[0].mean_delta).toBeCloseTo(result.expected.deltas[0].mean_delta, 9);
  expect(result.raw.manifest).toMatchObject({ matched_total: 8, returned: 2, truncated: true, consistency: 'verified' });
});
test('actual DuckDB count rejects over-budget cohorts instead of silently using graph cap', async ({ page }) => {
  const message = await page.evaluate(async () => {
    const h = (window as any).comparisonDataHarness, data = await (await fetch('webdata/comparison-case.json')).json();
    await h.inspectStaticConditions(data.request.comparison, data.request.quality_rule);
    const conn = await h.getConn();
    // Fault fixture: mutate only this isolated test's in-memory table, not published/user data.
    await conn.query('INSERT INTO devices SELECT * FROM devices WHERE batch_id=(SELECT MIN(batch_id) FROM devices) LIMIT 1');
    await conn.query('INSERT INTO devices SELECT d.* FROM devices d, range(50000) r WHERE d.batch_id=(SELECT MIN(batch_id) FROM devices)');
    try { await h.loadStaticComparison(data.request.comparison, data.request.quality_rule, { qs: '1' }); return 'unexpected success'; }
    catch (e) { return e instanceof Error ? e.message : String(e); }
  });
  expect(message).toContain('200000');
});
test('published version changed during read refuses mixed cohorts', async ({ page }) => {
  await page.evaluate(async () => {
    const h = (window as any).comparisonDataHarness, data = await (await fetch('webdata/comparison-case.json')).json();
    await h.inspectStaticConditions(data.request.comparison, data.request.quality_rule);
  });
  await page.route('**/webdata/manifest.json', async (route) => {
    const response = await route.fetch(), body = await response.json();
    body.generated_at = '2099-01-01T00:00:00Z'; await route.fulfill({ response, json: body });
  });
  const message = await page.evaluate(async () => {
    const h = (window as any).comparisonDataHarness, data = await (await fetch('webdata/comparison-case.json')).json();
    try { await h.loadStaticComparison(data.request.comparison, data.request.quality_rule, { qs: '1' }); return 'unexpected success'; }
    catch (e) { return e instanceof Error ? e.message : String(e); }
  });
  expect(message).toContain('QUERY_SOURCE_CHANGED');
});
