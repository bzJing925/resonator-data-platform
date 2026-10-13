import { test, expect, type Locator, type Page } from '@playwright/test';
async function clickPoint(page: Page, point: Locator) {
  await point.scrollIntoViewIfNeeded(); const box = await point.boundingBox();
  if (!box) throw new Error('点不可见');
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}
test.use({ baseURL: 'http://127.0.0.1:5173' });
test('real Plotly group click selects both worksheets; exclude and undo update charts', async ({ page }) => {
  await page.goto('/e2e/analysis/comparison.html');
  await page.getByRole('button', { name: '加载比较夹具' }).click();
  await page.getByRole('button', { name: '计算比较', exact: true }).click();
  const graph = page.locator('#comparison-difference');
  await expect(graph.locator('.point').first()).toBeVisible();
  await clickPoint(page, graph.locator('.point').first());
  await expect(page.getByRole('status').filter({ hasText: 'A 3 行，B 3 行' })).toBeVisible();
  for (const side of [0, 1]) await expect(page.getByTestId(`worksheet-${side}`).locator('.analysis-grid-row.selected')).toHaveCount(3);
  await page.getByRole('button', { name: '排除所选比较行' }).click();
  for (const side of [0, 1]) await expect(page.getByTestId(`worksheet-${side}`).locator('.analysis-grid-row')).toHaveCount(1);
  await expect(graph.locator('.point')).toHaveCount(0);
  await page.getByRole('button', { name: '撤销比较修改' }).click();
  for (const side of [0, 1]) await expect(page.getByTestId(`worksheet-${side}`).locator('.analysis-grid-row')).toHaveCount(4);
  await expect(graph.locator('.point').first()).toBeVisible();
  await clickPoint(page, page.locator('#comparison-distribution-0 .point').first());
  await expect(page.getByTestId('worksheet-0').locator('.analysis-grid-row.selected')).toHaveCount(1);
  await expect(page.getByTestId('worksheet-1').locator('.analysis-grid-row.selected')).toHaveCount(0);
});
