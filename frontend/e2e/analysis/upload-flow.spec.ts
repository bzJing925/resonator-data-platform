import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
test.use({ baseURL: 'http://127.0.0.1:5173' });
test('real native executor: three files, two published cohorts and one fatal; refresh commit does not upload again', async ({ page }) => {
  const pointer = process.env.ALN_E2E_FIXTURE ?? (await readFile('/tmp/aln-g01-native-fixture-path', 'utf8')).trim();
  const fixture = JSON.parse(await readFile(pointer, 'utf8'));
  let prepares = 0, commits = 0;
  page.on('request', (request) => {
    const url = new URL(request.url()); if (!url.pathname.startsWith('/api/')) return;
    if (url.pathname === '/api/uploads/prepare') prepares++;
    if (url.pathname.endsWith('/commit')) commits++;
  });
  await page.goto(`/e2e/analysis/upload-flow.html?mappingId=${fixture.mapping_id}`);
  await page.getByLabel('选择测试数据').setInputFiles(fixture.files);
  await expect(page.getByRole('button', { name: '检查数据' })).toBeEnabled(); await page.getByRole('button', { name: '检查数据' }).click();
  const a = page.getByTestId('upload-entry-a.zip'), b = page.getByTestId('upload-entry-b.zip'), bad = page.getByTestId('upload-entry-bad.zip');
  await expect(a.getByText(/器件\/端口 2/)).toBeVisible(); await expect(b.getByText(/器件\/端口 1/)).toBeVisible();
  await expect(bad.getByText('存在致命问题，禁止启动入库。')).toBeVisible(); expect(prepares).toBe(3);
  const before = page.url(); await page.reload(); expect(page.url()).toBe(before);
  await expect(a.getByText(/器件\/端口 2/)).toBeVisible();
  for (const entry of [a, b]) {
    const warnings = entry.locator('input[type=checkbox]'); for (let index = 0; index < await warnings.count(); index++) await warnings.nth(index).check();
    await entry.getByRole('button', { name: '启动此文件入库' }).dblclick();
  }
  await expect(a.getByText(/success · done/)).toBeVisible({ timeout: 60000 }); await expect(b.getByText(/success · done/)).toBeVisible({ timeout: 60000 });
  await expect(a.getByText(/实际发布 2 器件/)).toBeVisible(); await expect(b.getByText(/实际发布 1 器件/)).toBeVisible();
  expect(prepares).toBe(3); expect(commits).toBe(2); await expect(bad.getByRole('button', { name: '启动此文件入库' })).toBeDisabled();
  expect(page.url()).toContain('groupId=');
});
