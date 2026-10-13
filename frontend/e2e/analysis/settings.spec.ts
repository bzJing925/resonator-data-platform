import { test, expect } from '@playwright/test';
test.use({ baseURL: 'http://127.0.0.1:5173' });
const features = { upload_precheck: true, processing_reports: true, projects: true, full_export_csv: true, full_export_xlsx: true, batch_compare: true, trash: true, replication_v3: true, remote_delete: true, credential_storage: false };
test('one coordinator; partial conflict stays blocked after readonly refresh and credential is session only', async ({ page }) => {
  let pulls = 0, conflict = false;
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (!pathname.startsWith('/api/')) return route.continue();
    if (pathname.endsWith('/capabilities')) return route.fulfill({ json: { schema_version: 1, mode: 'desktop', database: 'sqlite', protocols: ['sync-v3'], features, limits: {} } });
    if (pathname.endsWith('/desktop-sync/status')) return route.fulfill({ json: { enabled: true, configured: true, reachable: true, server_url: 'http://fixture', username: 'u', unsynced_batches: [] } });
    if (pathname.endsWith('/desktop-sync/synchronize')) { pulls++; await new Promise((resolve) => setTimeout(resolve, 100)); return route.fulfill({ json: { batches_added: 0, mappings_added: 0, devices_added: 0, errors: conflict ? ['A: TABLE_CONFLICT 409'] : [] } }); }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto('/e2e/analysis/settings.html');
  await expect(page.getByRole('button', { name: '立即双向同步' })).toBeEnabled(); expect(pulls).toBe(1);
  await page.getByLabel('发布凭据（仅本次会话）').fill('fixture-session-token');
  await page.getByRole('button', { name: '切换设置页' }).click(); await page.getByRole('button', { name: '切换设置页' }).click();
  await expect(page.getByLabel('发布凭据（仅本次会话）')).toHaveValue('');
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('fixture-session-token');
  conflict = true;
  await page.getByRole('button', { name: '立即双向同步' }).dblclick();
  await expect(page.getByRole('alert').filter({ hasText: '同步冲突' })).toBeVisible(); expect(pulls).toBe(2);
  await page.getByRole('button', { name: '只读刷新连接' }).click();
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await expect(page.getByRole('button', { name: '立即双向同步' })).toBeDisabled(); expect(pulls).toBe(2);
});
