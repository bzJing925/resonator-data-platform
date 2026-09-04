import { expect, test } from '@playwright/test';
import { ANALYSIS_CHART_TYPES } from '../../src/features/analysis/__fixtures__/analysisRows';
import { launchHarness } from './electron';

test.describe('真实 Plotly 图形验收基线', () => {
  for (const chartType of ANALYSIS_CHART_TYPES) {
    test(`${chartType} 使用真实 Plotly 渲染并保留行映射`, async () => {
      const { electronApp, page } = await launchHarness();
      try {
        await page.getByTestId('chart-type').selectOption(chartType);
        const graph = page.locator('#chart-unified');
        await expect(graph).toHaveClass(/js-plotly-plot/);
        await expect.poll(
          () => graph.evaluate((element: any) => element.layout?.uirevision),
        ).toBe(`phase-0-${chartType}`);
        await expect.poll(() => graph.evaluate((element: any) => element.data?.length || 0)).toBeGreaterThan(0);

        const rowId = await graph.evaluate((element: any) => {
          const trace = element.data.find((item: any) =>
            item.meta?.analysisLayer === 'raw-points' && item.customdata?.length);
          const customdata = trace.customdata[0];
          return customdata.rowId || customdata.__rowId || customdata.__memberRowIds?.[0];
        });
        expect(rowId).toMatch(/^device:\d+$/);
      } finally {
        await electronApp.close();
      }
    });
  }

  test('Plotly 内部缩放不触发 React 页面重渲染', async ({ page: _page }) => {
    const { electronApp, page } = await launchHarness();
    try {
      const graph = page.locator('#chart-unified');
      await expect(graph).toHaveClass(/js-plotly-plot/);
      const before = Number(await page.getByTestId('render-count').textContent());
      await graph.hover();
      await page.mouse.wheel(0, -240);
      await page.waitForTimeout(200);
      await expect(page.getByTestId('render-count')).toHaveText(String(before));
    } finally {
      await electronApp.close();
    }
  });
});
