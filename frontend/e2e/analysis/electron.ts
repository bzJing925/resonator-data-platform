import { _electron as electron, expect } from '@playwright/test';

export async function launchHarness(pageName = 'harness.html') {
  const env = { ...process.env };
  // Some agent/CI hosts run Electron as Node for command execution. That mode
  // rejects Chromium flags Playwright needs, so the desktop harness must clear it.
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.ELECTRON_FORCE_IS_PACKAGED;
  const electronApp = await electron.launch({
    args: ['.'],
    cwd: process.cwd(),
    env: { ...env, ALN_E2E_URL: `http://127.0.0.1:5173/e2e/analysis/${pageName}` },
  });
  const page = await electronApp.firstWindow();
  await expect(page).toHaveTitle(/Analysis/);
  return { electronApp, page };
}
