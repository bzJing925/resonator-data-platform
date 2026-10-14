import { expect, test, type Page } from '@playwright/test';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { launchHarness } from './electron';
for (const target of ['browser', 'electron'] as const) test(`${target}: actual query/edit/complete comparison/export/capabilities`, async ({ page }, info) => {
  test.setTimeout(90000);
  const root = await mkdtemp(join(tmpdir(), 'aln-platform-flow-'));
  let app: Awaited<ReturnType<typeof launchHarness>> | undefined;
  const requests: string[] = [];
  try {
    if (target === 'electron') { app = await launchHarness('platform-flow.html'); page = app.page; }
    else await page.goto('http://127.0.0.1:5173/e2e/analysis/platform-flow.html');
    page.on('pageerror', (error) => console.log('FLOW_PAGE_ERROR', error.message));
    page.on('request', (r) => { if (new URL(r.url()).pathname.startsWith('/api/')) requests.push(r.url()); });
    await page.waitForFunction(() => 'platformFlow' in window);
    const queried = await page.evaluate(() => (window as any).platformFlow.query());
    expect(queried.manifest).toMatchObject({ returned: 2, truncated: true, total_relation: 'exact', consistency: 'verified' });
    await expect(page.getByTestId('rows')).toHaveText('2');
    await page.evaluate(() => { const h = (window as any).platformFlow.workspace, d = h.datasets[0]; h.editCell(d.id, d.rows[0].rowId, 'qs', 999); });
    await expect(page.getByTestId('first-value')).toHaveText('999');
    const compared = await page.evaluate(() => (window as any).platformFlow.compare());
    expect(compared.result.cohorts.a.manifest.truncated).toBe(false); expect(compared.result.cohorts.b.manifest.truncated).toBe(false);
    expect(compared.result.cohorts.a.rows.length).toBeGreaterThan(0); expect(compared.result.cohorts.b.rows.length).toBeGreaterThan(0);
    const caps = await page.evaluate(() => (window as any).platformFlow.getCapabilities());
    expect(caps).toMatchObject({ mode: 'server', database: 'sqlite' });
    expect(await page.evaluate(() => (window as any).platformFlow.status())).toHaveProperty('server');
    async function download(p: Page, button: string, output: string) {
      if (app) {
        await app.electronApp.evaluate(({ BrowserWindow }, file) => BrowserWindow.getAllWindows()[0].webContents.session.once('will-download', (_event, item) => item.setSavePath(file)), output);
        await p.getByRole('button', { name: button, exact: true }).click();
        await expect.poll(async () => { try { return (await readFile(output)).length; } catch { return 0; } }).toBeGreaterThan(10);
      } else { const pending = p.waitForEvent('download'); await p.getByRole('button', { name: button, exact: true }).click(); await (await pending).saveAs(output); }
    }
    await page.getByRole('button', { name: '完整导出', exact: true }).click();
    await page.getByLabel('全部筛选结果（固定版本源数据）').check(); await page.getByRole('button', { name: '创建完整源导出' }).click();
    await download(page, '下载 data.csv', join(root, 'complete.csv'));
    expect(await readFile(join(root, 'complete.csv'), 'utf8')).not.toContain('999');
    await page.getByRole('button', { name: '关闭', exact: true }).click();
    await info.attach(`${target}-flow.json`, { body: JSON.stringify({ caps, query: queried.manifest, comparison: compared.comparison, apiRequests: requests.length }), contentType: 'application/json' });
  } finally { if (app) await app.electronApp.close(); }
});
