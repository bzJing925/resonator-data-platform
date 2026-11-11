import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  testDir: '.',
  testMatch: 'viewer.spec.ts',
  outputDir: process.env.ALN_P5_TEST_OUTPUT || path.resolve(here, '../../backend/surrogate_data/optimization_p5_20261016/browser_tests'),
  workers: 1,
  timeout: 30000,
  reporter: [['list'], ['json', { outputFile: process.env.ALN_P5_TEST_REPORT || path.resolve(here, '../../backend/surrogate_data/optimization_p5_20261016/browser_tests.json') }]],
  use: { browserName: 'chromium', headless: true, viewport: { width: 1480, height: 1000 }, offline: true, acceptDownloads: true },
});
