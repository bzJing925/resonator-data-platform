import { test, expect, _electron as electron, type Page } from '@playwright/test';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';

test('complete JSON transfers browser → static → actual Electron → browser with the same rows/spec/source', async ({ page, context }) => {
  test.setTimeout(90000);
  const root = await mkdtemp(join(tmpdir(), 'aln-project-transfer-'));
  const preview = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', '5174', '--strictPort'], { cwd: process.cwd(), stdio: 'ignore' });
  let app: Awaited<ReturnType<typeof electron.launch>> | undefined;
  async function ready(p: Page) {
    await expect(p.getByTestId('project-save-status')).toContainText('已保存');
    await p.getByTestId('project-menu').click();
  }
  async function exported(p: Page, name: string) {
    const download = p.waitForEvent('download');
    await p.getByRole('button', { name: '导出JSON' }).click();
    const file = await download, output = join(root, name);
    await file.saveAs(output);
    return output;
  }
  async function imported(p: Page, path: string) {
    await p.getByLabel('导入项目JSON').setInputFiles(path);
    await expect(p.getByTestId('row-count')).toHaveText('2001');
    await expect(p.getByTestId('project-save-status')).toContainText('已保存');
  }
  try {
    await page.goto('http://127.0.0.1:5173/e2e/analysis/projectWorkspace.html');
    await page.getByTestId('load-rows').click(); await ready(page);
    const first = await exported(page, 'browser.json');
    const original = JSON.parse(await readFile(first, 'utf8')).project;
    const staticPage = await context.newPage();
    await expect.poll(async () => { try { return (await fetch('http://127.0.0.1:5174/e2e/analysis/projectWorkspace.html')).status; } catch { return 0; } }).toBe(200);
    await staticPage.goto('http://127.0.0.1:5174/e2e/analysis/projectWorkspace.html');
    await ready(staticPage);
    expect(await staticPage.evaluate(() => (window as any).workspaceStatic)).toBe(true);
    await imported(staticPage, first); const second = await exported(staticPage, 'static.json');
    // Set Electron userData before the repository's actual main entry, and isolate its HOME logs.
    const wrapper = join(root, 'launch.cjs');
    await writeFile(wrapper, `require('electron').app.setPath('userData',${JSON.stringify(join(root, 'profile'))}); import(${JSON.stringify(pathToFileURL(resolve('electron-dist/electron/main.js')).href)});`);
    const env = { ...process.env, HOME: root, ALN_E2E_URL: 'http://127.0.0.1:5173/e2e/analysis/projectWorkspace.html' };
    delete env.ELECTRON_RUN_AS_NODE; delete env.ELECTRON_FORCE_IS_PACKAGED;
    app = await electron.launch({ args: [wrapper], cwd: process.cwd(), env });
    const desktop = await app.firstWindow(); await ready(desktop); await imported(desktop, second);
    const third = join(root, 'electron.json');
    await app.evaluate(({ BrowserWindow }, output) => {
      BrowserWindow.getAllWindows()[0].webContents.session.once('will-download', (_event, item) => {
        item.setSavePath(output);
      });
    }, third);
    await desktop.getByRole('button', { name: '导出JSON' }).click();
    await expect.poll(async () => { try { return JSON.parse(await readFile(third, 'utf8')).format; } catch { return ''; } }).toBe('aln-analysis-project');
    await imported(page, third); const fourth = await exported(page, 'browser-return.json');
    for (const path of [second, third, fourth]) {
      const text = await readFile(path, 'utf8'), result = JSON.parse(text).project;
      expect(result.datasets).toEqual(original.datasets);
      expect(result.version.spec).toEqual(original.version.spec);
      expect(result.version.provenance).toEqual(original.version.provenance);
      expect(result.head.projectId).not.toBe(original.head.projectId);
      expect(text).not.toMatch(/"(?:token|api_key|password|secret|raw_curves|history)"\s*:/i);
    }
  } finally { if (app) await app.close(); preview.kill('SIGTERM'); }
});
