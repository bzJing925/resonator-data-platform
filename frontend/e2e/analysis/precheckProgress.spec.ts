import { expect, test } from '@playwright/test';

test('restored upload polls stage counters, uses the dark bar, and reveals the final report', async ({ page }) => {
  const group = '3fa60c64-38a4-45a0-8123-1852c06dc184';
  let calls = 0;
  let draft = { draft_id: 'draft-progress', group_id: group, status: 'preparing', prepare_progress: 8,
    check_digest: null as string | null,
    report: { prepare_stage: 'hash_input', prepare_completed: 3 * 1024 ** 3,
      prepare_total: 10 * 1024 ** 3, prepare_unit: 'bytes' } as Record<string, unknown>,
    suggested_batch_no: 'sample', expires_at: '2099-01-01', consumed_task_id: null, consumed_task: null };
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const url = new URL(route.request().url()).pathname;
    let value: unknown = {};
    if (url.endsWith('/capabilities')) value = { schema_version: 1, mode: 'server', database: 'sqlite',
      protocols: ['sync-v3'], features: { upload_precheck: true, processing_reports: true, projects: true, full_export_csv: true, full_export_xlsx: true, batch_compare: true, trash: true, replication_v3: true, remote_delete: true, credential_storage: false }, limits: {} };
    else if (url.endsWith('/health')) value = { status: 'ok', db: 'ok' };
    else if (url.includes('/mappings')) value = [{ id: 1, name: 'map', version: 1 }];
    else if (url.includes('/uploads/groups/')) { calls++; value = { group_id: group, drafts: [draft] }; }
    await route.fulfill({ json: value });
  });
  await page.goto(`/upload?groupId=${group}`);
  await expect(page.getByText('校验原始文件 · 总进度 8% · 3 GB / 10 GB')).toBeVisible();
  const bar = page.getByRole('progressbar', { name: '预检查总进度' });
  await expect(bar).toHaveAttribute('aria-valuenow', '8');
  await expect(page.locator('.precheck-progress-fill')).toHaveCSS('background-color', 'rgb(95, 168, 211)');
  await expect(page.locator('.precheck-progress-track')).toHaveCSS('background-color', 'rgb(42, 46, 54)');
  await expect(page.getByRole('button', { name: '复查此文件' })).toBeDisabled();
  await expect(page.getByText(/检查结果/)).toHaveCount(0);
  draft = { ...draft, prepare_progress: 70, report: { prepare_stage: 'inspect_files', prepare_completed: 1000,
    prepare_total: 2000, prepare_unit: 'files' } };
  await expect(page.getByText('检查数据文件 · 总进度 70% · 1,000 / 2,000 个文件')).toBeVisible();
  await expect(bar).toHaveAttribute('aria-valuenow', '70');
  // A stable server counter stays stable; the page has no invented timer progress.
  const before = calls;
  await expect.poll(() => calls).toBeGreaterThan(before + 1);
  await expect(bar).toHaveAttribute('aria-valuenow', '70');
  await page.setViewportSize({ width: 960, height: 720 });
  const bounds = await bar.boundingBox();
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(960);
  await page.screenshot({ path: '../precheck-progress.png', fullPage: true });
  draft = { ...draft, status: 'ready', prepare_progress: 100, check_digest: 'completed',
    report: { fatal_count: 0, quality_status: 'complete', items: [{ classification: 'dut', status: 'success', ports: ['S11'] }] } };
  await expect(page.getByText('检查完成 · 100%')).toBeVisible();
  await expect(bar).toHaveCount(0);
  await expect(page.getByText(/器件\/端口 1/)).toBeVisible();
});
