import { expect, test, type Page } from '@playwright/test';
import { launchHarness } from './electron';

const pageErrors = new WeakMap<Page, string[]>();
test.beforeEach(({ page }) => {
  const errors: string[] = []; pageErrors.set(page, errors);
  page.on('pageerror', (error) => errors.push(error.message));
});
test.afterEach(({ page }) => { expect(pageErrors.get(page)).toEqual([]); });

async function selectRectangle(page: Page) {
  const drag = page.locator('#chart-unified .nsewdrag').first();
  await drag.scrollIntoViewIfNeeded();
  const box = (await drag.boundingBox())!;
  await page.mouse.move(box.x + box.width * .05, box.y + box.height * .15);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * .8, box.y + box.height * .85, { steps: 12 });
  await page.mouse.up();
  return box;
}

for (const mode of ['analysis', 'color']) {
  test(`${mode}: numeric Y range, reset and single blank click clear the real selection rectangle`, async ({ page }) => {
    await page.goto(`/e2e/analysis/chartControls.html?mode=${mode}`);
    const graph = page.locator('#chart-unified');
    await expect.poll(() => graph.evaluate((g: any) => g._fullLayout?.yaxis.range)).toEqual([95, 121]);
    await expect.poll(() => graph.evaluate((g: any) => g._fullLayout?.yaxis2.range)).toEqual([195, 221]);
    await page.getByLabel('指标1 Y轴最小值').fill('99.5');
    await page.getByLabel('指标1 Y轴最大值').fill('118');
    await page.getByRole('button', { name: '应用指标1 Y轴范围', exact: true }).click();
    await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.yaxis.range)).toEqual([99.5, 118]);
    await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.yaxis2.range)).toEqual([195, 221]);
    await page.getByTestId('interaction').selectOption('pan');
    await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.dragmode)).toBe('pan');
    const resetBox = (await graph.locator('.nsewdrag').first().boundingBox())!;
    await graph.locator('.nsewdrag').first().dblclick({ position: { x: resetBox.width*.97, y: resetBox.height*.03 } });
    await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.yaxis.range)).toEqual([95, 121]);
    await page.getByTestId('reset').click();
    await page.getByTestId('interaction').selectOption('select');
    await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.dragmode)).toBe('select');
    await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.yaxis.range)).toEqual([95, 121]);
    await page.getByLabel('指标1 Y轴最小值').fill('130');
    await page.getByRole('button', { name: '应用指标1 Y轴范围', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('最小值须小于最大值');
    expect(await graph.evaluate((g: any) => g._fullLayout.yaxis.range)).toEqual([95, 121]);
    await selectRectangle(page);
    await expect.poll(async () => Number(await page.getByTestId('selection').textContent())).toBeGreaterThan(0);
    await expect.poll(() => graph.evaluate((g: any) => g.layout.selections?.length)).toBe(1);
    await expect.poll(() => graph.evaluate((g: any) => g.data.find((t: any) => t.customdata?.[0]?.rowId)?.selectedpoints?.length || 0)).toBeGreaterThan(0);
    const blankBox = (await graph.locator('.nsewdrag').first().boundingBox())!;
    await page.mouse.click(blankBox.x + blankBox.width * .97, blankBox.y + blankBox.height * .03);
    await expect(page.getByTestId('selection')).toHaveText('0');
    await expect.poll(() => graph.evaluate((g: any) => g.layout.selections?.length || 0)).toBe(0);
    await expect(graph.locator('.select-outline')).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath(`controls-${mode}.png`), fullPage: true });
  });
}

test('many metrics use WebGL and one restyle; panning and selecting keep the data mapping', async ({ page }, info) => {
  await page.setViewportSize({ width: 1200, height: 1800 });
  await page.goto('/e2e/analysis/chartControls.html?rows=2016&metrics=8');
  const graph = page.locator('#chart-unified');
  await expect.poll(() => graph.evaluate((g: any) => g._fullData?.filter((t: any) => t.type === 'scattergl').length)).toBe(8);
  await page.evaluate(() => { (window as any).timings = []; });
  await selectRectangle(page);
  await expect.poll(() => page.evaluate(() => (window as any).timings.filter((t: any) => t.method === 'restyle').length)).toBe(1);
  const selectionTiming = await page.evaluate(() => (window as any).timings);
  expect(selectionTiming[0].ms).toBeLessThan(500);
  const selected = await page.getByTestId('selection').textContent();
  await page.getByTestId('interaction').selectOption('pan');
  await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.dragmode)).toBe('pan');
  await expect.poll(() => graph.evaluate((g: any) => g.layout.selections?.length || 0)).toBe(0);
  const panBox = (await graph.locator('.nsewdrag').first().boundingBox())!;
  const before = await graph.evaluate((g: any) => g._fullLayout.yaxis.range);
  await page.mouse.move(panBox.x + panBox.width * .4, panBox.y + panBox.height * .5);
  await page.mouse.down();
  await page.mouse.move(panBox.x + panBox.width * .6, panBox.y + panBox.height * .6, { steps: 15 });
  await page.mouse.up();
  await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.yaxis.range)).not.toEqual(before);
  await expect(page.getByTestId('selection')).toHaveText(selected!);
  const point = await graph.evaluate((g: any) => {
    const t = g.data.find((trace: any) => trace.meta?.analysisLayer === 'raw-points');
    const rect = g.getBoundingClientRect(), x = g._fullLayout.xaxis, y = g._fullLayout.yaxis;
    return { x: rect.x+x._offset+x.d2p(t.x[8]), y: rect.y+y._offset+y.d2p(t.y[8]) };
  });
  await page.mouse.move(point.x, point.y, { steps: 10 });
  await expect.poll(() => graph.evaluate((g: any) => g._hoverdata?.[0]?.data?.meta?.analysisLayer)).toBe('raw-points');
  await page.mouse.click(point.x, point.y);
  await expect(page.getByTestId('selection')).toHaveText('1');
  expect(await graph.evaluate((g: any) => g.data.filter((t: any) => t.meta?.analysisLayer === 'raw-points')
    .every((t: any) => t.customdata.length === 2016 && t.customdata[0].rowId))).toBe(true);
  await info.attach('selection-performance.json', { body: JSON.stringify(selectionTiming), contentType: 'application/json' });
});

test('real Explore page leaves chart types unobstructed and applies the sidebar numeric range', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const path = new URL(route.request().url()).pathname;
    let value: any = {};
    if (path.endsWith('/capabilities')) value = { schema_version: 1, mode: 'server', database: 'sqlite', protocols: [], features: {}, limits: {} };
    else if (path.endsWith('/fields')) value = { process: [{ name: 'eg', label: 'EG', dtype: 'float' }], numeric: [{ name: 'qs', label: 'Qs', dtype: 'float' }], categorical: [], geometric: [] };
    else if (path.endsWith('/query/devices')) value = { rows: [{ id: 1, eg: 1, qs: 10 }, { id: 2, eg: 2, qs: 20 }], returned: 2, truncated: false };
    else if (path.endsWith('/distinct')) value = [];
    else if (path.endsWith('/batches')) value = { items: [], total: 0 };
    else if (path.endsWith('/health')) value = { status: 'ok', db: 'ok', redis: 'ok' };
    else if (path.includes('/desktop-sync/')) value = { enabled: false };
    await route.fulfill({ json: value });
  });
  await page.goto('/explore');
  const toolbar = page.locator('.analysis-toolbar');
  await expect(toolbar.getByTestId('project-menu')).toHaveCount(0);
  await expect(toolbar.getByRole('button', { name: '生成HTML报告' })).toHaveCount(0);
  await expect(toolbar.locator('.crumb')).toHaveCount(0);
  for (const name of ['散点图', '蜂群图']) {
    await toolbar.getByRole('button', { name, exact: true }).click();
    await expect(toolbar.getByRole('button', { name, exact: true })).toHaveClass('active');
  }
  await page.getByRole('button', { name: '运行查询', exact: true }).click();
  for (const name of ['散点图', '箱型图', '小提琴', '蜂群图', '折线图', 'Wafer 版图']) {
    const button = toolbar.getByRole('button', { name, exact: true });
    await expect(button).toBeInViewport();
    expect(await button.evaluate((element) => { const box = element.getBoundingClientRect(); return element.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2)); })).toBe(true);
  }
  const graph = page.locator('#chart-unified');
  await expect.poll(() => graph.evaluate((g: any) => g._fullLayout?.yaxis.range)).toEqual([5, 25]);
  await page.getByLabel('Qs Y轴最小值').fill('8');
  await page.getByLabel('Qs Y轴最大值').fill('22');
  await page.getByRole('button', { name: '应用Qs Y轴范围', exact: true }).click();
  await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.yaxis.range)).toEqual([8, 22]);
  await page.getByRole('button', { name: '复位', exact: true }).click();
  await expect.poll(() => graph.evaluate((g: any) => g._fullLayout.yaxis.range)).toEqual([5, 25]);
  await page.screenshot({ path: test.info().outputPath('explore-controls.png'), fullPage: true });
});

test('macOS Electron uses the same Y reset and blank-click selection behavior', async () => {
  const { electronApp, page } = await launchHarness('chartControls.html');
  try {
    const graph = page.locator('#chart-unified');
    await expect.poll(() => graph.evaluate((g: any) => g._fullLayout?.yaxis.range)).toEqual([95, 121]);
    await selectRectangle(page);
    await expect.poll(async () => Number(await page.getByTestId('selection').textContent())).toBeGreaterThan(0);
    await expect.poll(() => graph.evaluate((g: any) => g.data.find((t: any) => t.customdata?.[0]?.rowId)?.selectedpoints?.length || 0)).toBeGreaterThan(0);
    const blankBox = (await graph.locator('.nsewdrag').first().boundingBox())!;
    await page.mouse.click(blankBox.x + blankBox.width * .97, blankBox.y + blankBox.height * .03);
    await expect(page.getByTestId('selection')).toHaveText('0');
    await expect.poll(() => graph.evaluate((g: any) => g.layout.selections?.length || 0)).toBe(0);
  } finally { await electronApp.close(); }
});
