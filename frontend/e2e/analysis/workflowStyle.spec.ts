import { expect, test, type Page } from '@playwright/test';

async function mockServer(page: Page) {
  let drafts: unknown[] = [];
  const uploads: string[] = [], commits: unknown[] = [];
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const request = route.request(), url = new URL(request.url()).pathname;
    let value: unknown = {};
    if (url.endsWith('/capabilities')) value = { schema_version: 1, mode: 'server', database: 'sqlite', protocols: ['sync-v3'], features: { upload_precheck: true, processing_reports: true, projects: true, full_export_csv: true, full_export_xlsx: true, batch_compare: true, trash: true, replication_v3: true, remote_delete: true, credential_storage: false }, limits: {} };
    else if (url.endsWith('/health')) value = { status: 'ok', db: 'ok', redis: 'ok' };
    else if (url.includes('/mappings')) value = [{ id: 1, name: 'ELB003', version: 1 }];
    else if (url.endsWith('/uploads/prepare')) {
      const body = request.postData()!; uploads.push(body);
      const group = body.match(/name="group_id"\r\n\r\n([^\r]+)/)![1];
      drafts = [{ draft_id: 'draft-auto', group_id: group, status: 'ready', prepare_progress: 100, check_digest: 'digest', report: { quality_status: 'partial', fatal_count: 0, warning_codes: ['UNRELATED_FILE'], issues: [{ code: 'UNRELATED_FILE', severity: 'warning', message: '说明文件将排除' }], items: [{ classification: 'dut', status: 'success', ports: ['S11', 'S22'] }], options: { mapping_id: 1, f_start_ghz: null, f_end_ghz: null } }, suggested_batch_no: 'auto', expires_at: new Date(Date.now() + 60000).toISOString(), consumed_task_id: null, consumed_task: null }];
      value = { draft_id: 'draft-auto', status: 'preparing' };
    } else if (url.includes('/uploads/groups/')) value = { group_id: decodeURIComponent(url.split('/').pop()!), drafts };
    else if (url.endsWith('/commit')) { commits.push(request.postDataJSON()); value = { task_id: '1', batch_no: 'auto', status: 'pending' }; }
    else if (url.endsWith('/replica-preferences') || url.endsWith('/trash')) value = { items: [], total: 0 };
    else if (url.endsWith('/fields')) value = { all: [] };
    else if (url.includes('/desktop-sync/')) value = { enabled: false };
    await route.fulfill({ json: value });
  });
  return { uploads, commits };
}

async function expectDarkControls(page: Page, selector: string) {
  const bright = await page.locator(selector).evaluateAll((controls) => controls.filter((control) => {
    const rgb = getComputedStyle(control).backgroundColor.match(/\d+/g)?.map(Number);
    return rgb && rgb[0] > 220 && rgb[1] > 220 && rgb[2] > 220;
  }).map((control) => control.outerHTML));
  expect(bright).toEqual([]);
}

test('real upload page leaves bounds automatic, keeps warning gate and folds technical evidence', async ({ page }) => {
  const server = await mockServer(page);
  await page.goto('/upload');
  await expect(page.getByText('留空自动使用文件频段')).toBeVisible();
  await expect(page.locator('.rail-btn.active')).toHaveCSS('color', 'rgb(95, 168, 211)');
  await expect(page.locator('select').first()).toHaveValue('1');
  await page.locator('input[type="file"]').setInputFiles({ name: 'auto.zip', mimeType: 'application/zip', buffer: Buffer.from('fixture') });
  const bounds = await page.getByPlaceholder('自动').last().evaluate((input) => ({ bottom: input.getBoundingClientRect().bottom, card: input.closest('.chart-card')!.getBoundingClientRect().bottom }));
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.card);
  await page.getByRole('button', { name: '检查数据', exact: true }).click();
  await expect(page.getByText(/器件\/端口 2/)).toBeVisible();
  expect(server.uploads).toHaveLength(1);
  expect(server.uploads[0]).not.toMatch(/name="f_(start|end)_ghz"/);
  await expect(page.getByText('频段：自动（按各文件实际范围）')).toBeVisible();
  const commit = page.getByRole('button', { name: '启动此文件入库' });
  await expect(commit).toBeDisabled();
  await expect(page.locator('.technical-details pre')).toBeHidden();
  await page.getByText('处理设置', { exact: true }).click();
  await expect(page.locator('.technical-details pre')).toBeVisible();
  await page.getByText('处理设置', { exact: true }).click();
  await page.getByLabel('已审查警告 UNRELATED_FILE').check();
  await expect(commit).toBeEnabled();
  await commit.click();
  await expect.poll(() => server.commits.length).toBe(1);
  await expectDarkControls(page, 'button, input:not([type="file"]):not([type="checkbox"]), select');
  await page.screenshot({ path: '../upload-dark.png', fullPage: true });
});

test('settings controls use dark theme; comparison does not fill empty chart', async ({ page }) => {
  await mockServer(page);
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeVisible();
  await expectDarkControls(page, '.workflow-page button, .workflow-page input:not([type="checkbox"])');
  await page.screenshot({ path: '../settings-dark.png', fullPage: true });
  await page.goto('/explore');
  await expect(page.locator('.comparison-settings')).toBeVisible();
  await expect(page.getByRole('combobox', { name: '比较范围' })).toBeHidden();
});
