import { _electron as electron, expect } from '@playwright/test';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export async function launchHarness(pageName = 'harness.html', storageRoot?: string) {
  const root = storageRoot ?? await mkdtemp(join(tmpdir(), 'aln-electron-e2e-'));
  const wrapper = join(root, 'launch.cjs');
  await writeFile(wrapper, `require('electron').app.setPath('userData',${JSON.stringify(join(root, 'profile'))}); import(${JSON.stringify(pathToFileURL(resolve('electron-dist/electron/main.js')).href)});`);
  const env = { ...process.env, HOME: root, ALN_DESKTOP_DIR: join(root, 'data'), ALN_E2E_URL: `http://127.0.0.1:5173/e2e/analysis/${pageName}` };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.ELECTRON_FORCE_IS_PACKAGED;
  const electronApp = await electron.launch({ args: [wrapper], cwd: process.cwd(), env });
  // Crash-stop only this isolated fixture process during cleanup. Dirty beforeunload
  // guards are product behavior, not a browser dialog Electron's CDP can dismiss.
  electronApp.close = async () => {
    const process = electronApp.process();
    if (process.exitCode !== null || process.signalCode !== null) return;
    await new Promise<void>((resolve) => { process.once('exit', () => resolve()); process.kill('SIGKILL'); });
  };
  const page = await electronApp.firstWindow();
  await expect(page).toHaveTitle(/Analysis/);
  return { electronApp, page, storageRoot: root };
}
