import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e/analysis',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: { trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:5173/e2e/analysis/harness.html',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
