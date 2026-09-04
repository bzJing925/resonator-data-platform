import { expect, test } from '@playwright/test';
import { launchHarness } from './electron';

const ROW_COUNTS = [10_000, 50_000, 100_000];

test.describe('分析图表性能基线', () => {
  test.skip(process.env.ALN_RUN_PERF !== '1', '设置 ALN_RUN_PERF=1 才运行耗时基准');

  for (const rowCount of ROW_COUNTS) {
    test(`${rowCount.toLocaleString()} 行`, async ({}, testInfo) => {
      test.setTimeout(180_000);
      const { electronApp, page } = await launchHarness('benchmark.html');
      try {
        await expect.poll(() => page.evaluate(() => document.body.dataset.ready)).toBe('true');
        const result = await page.evaluate((count) => window.runAnalysisBenchmark(count), rowCount);
        console.log(`ANALYSIS_PERF ${JSON.stringify(result)}`);
        await testInfo.attach(`analysis-${rowCount}.json`, {
          body: JSON.stringify(result, null, 2),
          contentType: 'application/json',
        });
        expect(result.mappedPointCount).toBe(rowCount * 3);
        expect(result.emittedPointCount).toBeLessThanOrEqual(rowCount * 3);
        expect(result.cacheHitMs).toBeLessThan(5);
      } finally {
        await electronApp.close();
      }
    });
  }
});
