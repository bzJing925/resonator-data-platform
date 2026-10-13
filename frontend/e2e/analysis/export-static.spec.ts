import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
import { tableFromArrays, tableToIPC } from 'apache-arrow';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
let preview: ReturnType<typeof spawn>;
test.beforeAll(async () => { preview = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', '5174', '--strictPort'], { stdio: 'ignore' }); await expect.poll(async () => { try { return (await fetch('http://127.0.0.1:5174/e2e/analysis/export-flow.html')).status; } catch { return 0; } }).toBe(200); });
test.afterAll(() => { preview.kill('SIGTERM'); });
test('actual static independent worker exports all eight rows; workbook option disabled', async ({ page }) => {
  const apiWrites: string[] = []; page.on('request', (request) => { if (new URL(request.url()).pathname.startsWith('/api/')) apiWrites.push(request.url()); });
  await page.goto('http://127.0.0.1:5174/e2e/analysis/export-flow.html');
  await page.getByLabel('全部筛选结果（固定版本源数据）').check(); await expect(page.getByText(/静态完整CSV上限/)).toBeVisible(); expect(await page.locator('option[value=xlsx]').evaluate((element) => (element as HTMLOptionElement).disabled)).toBe(true);
  await page.getByRole('button', { name: '创建完整源导出' }).dblclick();
  const download = page.waitForEvent('download'); await page.getByRole('button', { name: '下载完整 CSV', exact: true }).click(); const file = await download;
  const csv = await readFile((await file.path())!, 'utf8'); expect(csv.trim().split(/\r?\n/)).toHaveLength(9); expect(csv).not.toContain('999');
  const manifestDownload = page.waitForEvent('download'); await page.getByRole('button', { name: '下载来源说明' }).click(); const manifestFile = await manifestDownload;
  const manifest = JSON.parse(await readFile((await manifestFile.path())!, 'utf8')); expect(manifest).toMatchObject({ matched_total: 8, returned: 8, truncated: false, consistency: 'verified' }); expect(apiWrites).toEqual([]);
});

test('actual static worker refuses 200001-row Arrow before download, never truncates success', async ({ page }) => {
  const metadata = JSON.parse(await readFile('public/e2e/analysis/webdata/manifest.json', 'utf8'));
  const count = 200001, source = metadata.comparison_sources[0];
  const arrow = Buffer.from(tableToIPC(tableFromArrays({ id: Int32Array.from({ length: count }, (_, i) => i + 1), batch_id: Int32Array.from({ length: count }, () => source.batch_id), qs: Float64Array.from({ length: count }, () => 10), source_device_key: Array.from({ length: count }, () => '872a5ffc-cf46-4f77-9cbf-c47b7f46401b') })));
  metadata.devices_arrow_sha256 = createHash('sha256').update(arrow).digest('hex');
  await page.route('**/webdata/manifest.json', (route) => route.fulfill({ json: metadata }));
  await page.route('**/webdata/devices.arrow.gz', (route) => route.fulfill({ body: arrow, contentType: 'application/octet-stream' }));
  await page.goto('http://127.0.0.1:5174/e2e/analysis/export-flow.html');
  await page.getByLabel('全部筛选结果（固定版本源数据）').check(); await page.getByRole('button', { name: '创建完整源导出' }).click();
  await expect(page.getByRole('alert')).toContainText('最多200000行'); expect(await page.getByRole('button', { name: '下载完整 CSV' }).count()).toBe(0);
});
