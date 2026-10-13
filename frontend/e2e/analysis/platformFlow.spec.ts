import { expect, test, type Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { launchHarness } from './electron';
for (const target of ['browser', 'static', 'electron'] as const) test(`${target}: actual query/edit/project reload/complete comparison/export/offline report/capabilities`, async ({ page, browser }, info) => {
  test.setTimeout(90000);
  const root = await mkdtemp(join(tmpdir(), 'aln-platform-flow-'));
  let preview: ReturnType<typeof spawn> | undefined, app: Awaited<ReturnType<typeof launchHarness>> | undefined;
  const requests: string[] = [];
  try {
    if (target === 'static') {
      preview = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', '5174', '--strictPort'], { cwd: process.cwd(), stdio: 'ignore' });
      await expect.poll(async () => { try { return (await fetch('http://127.0.0.1:5174/e2e/analysis/platform-flow.html')).status; } catch { return 0; } }).toBe(200);
    }
    if (target === 'electron') { app = await launchHarness('platform-flow.html'); page = app.page; }
    else await page.goto(`http://127.0.0.1:${target === 'static' ? 5174 : 5173}/e2e/analysis/platform-flow.html`);
    page.on('pageerror', (error) => console.log('FLOW_PAGE_ERROR', error.message));
    page.on('request', (r) => { if (new URL(r.url()).pathname.startsWith('/api/')) requests.push(r.url()); });
    await page.waitForFunction(() => 'platformFlow' in window);
    const queried = await page.evaluate(() => (window as any).platformFlow.query());
    expect(queried.manifest).toMatchObject({ returned: 2, truncated: true, total_relation: 'exact', consistency: 'verified' });
    await expect(page.getByTestId('rows')).toHaveText('2'); await expect(page.getByTestId('project-save-status')).toContainText('已保存');
    await page.evaluate(() => { const h = (window as any).platformFlow.workspace, d = h.datasets[0]; h.editCell(d.id, d.rows[0].rowId, 'qs', 999); });
    await expect(page.getByTestId('first-value')).toHaveText('999'); await expect(page.getByTestId('project-save-status')).toContainText('已保存');
    await page.reload(); await expect(page.getByTestId('first-value')).toHaveText('999');
    const compared = await page.evaluate(() => (window as any).platformFlow.compare());
    expect(compared.result.cohorts.a.manifest.truncated).toBe(false); expect(compared.result.cohorts.b.manifest.truncated).toBe(false);
    expect(compared.result.cohorts.a.rows.length).toBeGreaterThan(0); expect(compared.result.cohorts.b.rows.length).toBeGreaterThan(0);
    const caps = await page.evaluate(() => (window as any).platformFlow.getCapabilities());
    expect(caps).toMatchObject({ mode: target === 'static' ? 'static' : 'server', database: target === 'static' ? 'duckdb' : 'sqlite' });
    if (target !== 'static') expect(await page.evaluate(() => (window as any).platformFlow.status())).toHaveProperty('server');
    async function download(p: Page, button: string, output: string) {
      if (app) {
        await app.electronApp.evaluate(({ BrowserWindow }, file) => BrowserWindow.getAllWindows()[0].webContents.session.once('will-download', (_event, item) => item.setSavePath(file)), output);
        await p.getByRole('button', { name: button, exact: true }).click();
        await expect.poll(async () => { try { return (await readFile(output)).length; } catch { return 0; } }).toBeGreaterThan(10);
      } else { const pending = p.waitForEvent('download'); await p.getByRole('button', { name: button, exact: true }).click(); await (await pending).saveAs(output); }
    }
    await page.getByRole('button', { name: '完整导出', exact: true }).click();
    await page.getByLabel('全部筛选结果（固定版本源数据）').check(); await page.getByRole('button', { name: '创建完整源导出' }).click();
    await download(page, target === 'static' ? '下载完整 CSV' : '下载 data.csv', join(root, 'complete.csv'));
    expect(await readFile(join(root, 'complete.csv'), 'utf8')).not.toContain('999');
    await page.getByRole('button', { name: '关闭', exact: true }).click();
    await page.getByRole('button', { name: '生成HTML报告' }).click(); const reportStart = Date.now();
    await download(page, '下载离线HTML', join(root, 'report.html'));
    await info.attach(`${target}-flow.json`, { body: JSON.stringify({ caps, query: queried.manifest, comparison: compared.comparison, reportGenerationMs: Date.now() - reportStart, apiRequests: requests.length }), contentType: 'application/json' });
    const offline = await browser.newContext({ offline: true }), offlinePage = await offline.newPage();
    await offlinePage.goto(pathToFileURL(join(root, 'report.html')).href); await expect(offlinePage.locator('figure img')).toHaveCount(1);
    expect(await offlinePage.locator('figure img').evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    await offline.close(); if (target === 'static') expect(requests).toEqual([]);
  } finally { if (app) await app.electronApp.close(); preview?.kill('SIGTERM'); }
});
