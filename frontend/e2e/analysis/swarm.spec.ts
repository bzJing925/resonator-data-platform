import { expect, test } from '@playwright/test';

test.describe('蜂群图真实 Plotly 回归', () => {
  test('初始未选字段时，选择字段后仍按实际窄容器加宽', async ({ page }) => {
    await page.goto('/e2e/analysis/swarm.html?empty=1');
    await expect(page.getByText('请至少选一个 X 和 Y 字段')).toBeVisible();
    await page.getByTestId('enable-fields').click();
    await expect.poll(() => page.locator('#chart-unified').evaluate((element: any) =>
      element._fullLayout?._size.w)).toBeGreaterThanOrEqual(700);
  });

  test('点击中位数标记不清空真实散点选择', async ({ page }) => {
    await page.goto('/e2e/analysis/swarm.html');
    await page.getByTestId('slots').fill('2');
    const graph = page.locator('#chart-unified');
    await expect.poll(() => graph.evaluate((element: any) => element.data?.[0].y.length)).toBe(32);
    const ids = await graph.evaluate((element: any) => ({
      raw: element.data.find((trace: any) => trace.meta?.analysisLayer === 'raw-points').uid,
      median: element.data.find((trace: any) => trace.meta?.analysisLayer === 'swarm-median' && trace.mode === 'lines+markers').uid,
    }));
    await graph.locator(`.trace${ids.raw} .point`).first().click({ force: true });
    await expect(page.getByTestId('selected')).toHaveText(/^r\d+$/);
    const selected = await page.getByTestId('selected').textContent();
    await graph.locator(`.trace${ids.median} .point`).first().click({ force: true });
    await expect(page.getByTestId('selected')).toHaveText(selected!);
  });

  test('大数据使用WebGL散点，SVG中位数仍绘制且保留真实行映射', async ({ page }) => {
    await page.goto('/e2e/analysis/swarm.html?large=1');
    const graph = page.locator('#chart-unified');
    await expect.poll(() => graph.evaluate((element: any) =>
      element._fullData?.filter((trace: any) => trace.type === 'scattergl').length)).toBe(2);
    await expect.poll(() => graph.locator('.scatterlayer .trace .lines path').count()).toBeGreaterThanOrEqual(4);
    const result = await graph.evaluate((element: any) => ({
      counts: element.data.filter((trace: any) => trace.type === 'scattergl').map((trace: any) => trace.y.length),
      rowIds: element.data.filter((trace: any) => trace.type === 'scattergl').map((trace: any) => trace.customdata[0].rowId),
      medianTypes: element._fullData.filter((trace: any) => trace.meta?.analysisLayer === 'swarm-median').map((trace: any) => trace.type),
    }));
    expect(result.counts).toEqual([5040, 5040]);
    expect(result.rowIds).toEqual(['r0', 'r0']);
    expect(result.medianTypes).toEqual(['scatter', 'scatter', 'scatter', 'scatter']);
  });

  for (const mode of ['analysis', 'color']) {
    test(`${mode}：窄窗口按档位加宽，少档位恢复自适应`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto('/e2e/analysis/swarm.html');
      await page.getByTestId('mode').selectOption(mode);
      const graph = page.locator('#chart-unified');
      await expect(graph).toHaveClass(/js-plotly-plot/);
      await expect.poll(() => graph.evaluate((element: any) => element._fullLayout?._size.w)).toBeGreaterThanOrEqual(700);
      await expect.poll(() => page.getByTestId('viewport').evaluate((element) => element.scrollWidth)).toBeGreaterThan(520);
      await page.getByTestId('slots').fill('2');
      await expect.poll(() => graph.evaluate((element: any) => element._fullLayout?.width)).toBe(520);
      expect(errors).toEqual([]);
    });

    test(`${mode}：真实散点、SVG 中位数线、标注和档位框线均绘制`, async ({ page }) => {
      await page.goto('/e2e/analysis/swarm.html');
      await page.getByTestId('mode').selectOption(mode);
      const graph = page.locator('#chart-unified');
      await expect.poll(() => graph.evaluate((element: any) => element.data?.filter((trace: any) => trace.mode === 'markers').length)).toBe(mode === 'analysis' ? 2 : 4);
      await expect.poll(() => graph.locator('.scatterlayer .trace .lines path').count()).toBeGreaterThan(0);
      const result = await graph.evaluate((element: any) => ({
        points: element.data.filter((trace: any) => trace.mode === 'markers').reduce((count: number, trace: any) => count + trace.y.length, 0),
        opaque: element.data.filter((trace: any) => trace.mode === 'markers').every((trace: any) => trace.marker.opacity === 1),
        medians: element.data.filter((trace: any) => trace.meta?.analysisLayer === 'swarm-median').length,
        frames: element.layout.shapes.length,
        annotations: element.layout.annotations.filter((note: any) => note.font?.size === 9).length,
      }));
      expect(result).toMatchObject({ points: 448, opaque: true, frames: 13 });
      expect(result.medians).toBe(mode === 'analysis' ? 4 : 8);
      expect(result.annotations).toBeGreaterThan(0);
      await page.screenshot({ path: test.info().outputPath(`swarm-${mode}.png`), fullPage: true });
    });
  }
});
