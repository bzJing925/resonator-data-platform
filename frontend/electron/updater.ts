import { app, ipcMain } from 'electron';
import { createRequire } from 'node:module';
import type { UpdateCheckResult } from 'electron-updater';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const require = createRequire(import.meta.url);
const { autoUpdater } = require('electron-updater');

interface UpdateSource {
  type: 'github' | 'static';
  owner?: string;
  repo?: string;
  url?: string;
  channel?: string;
}

function settingsPath(): string {
  return path.join(os.homedir(), '.aln-data', 'settings.json');
}

function loadSettings(): { updateSource?: UpdateSource } {
  try {
    return JSON.parse(fs.readFileSync(settingsPath(), 'utf-8'));
  } catch {
    return {};
  }
}

export function setupUpdater(): void {
  const settings = loadSettings();
  const source = settings.updateSource || {
    type: 'github',
    owner: 'your-org',
    repo: 'aln-data',
  };

  if (source.type === 'github') {
    autoUpdater.setFeedURL({
      provider: 'github',
      owner: source.owner || 'your-org',
      repo: source.repo || 'aln-data',
    });
  } else if (source.type === 'static' && source.url) {
    autoUpdater.setFeedURL({ provider: 'generic', url: source.url });
  }

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;

  autoUpdater.on('update-available', (info: any) => {
    console.log('[updater] 有可用更新:', info.version);
  });

  autoUpdater.on('update-downloaded', (info: any) => {
    console.log('[updater] 更新已下载:', info.version);
  });

  ipcMain.handle('updater:check', async () => {
    try {
      const result: UpdateCheckResult | null = await autoUpdater.checkForUpdates();
      if (!result) {
        return { version: app.getVersion(), available: false };
      }
      return {
        version: result.updateInfo.version,
        available: result.updateInfo.version !== app.getVersion(),
      };
    } catch (err) {
      console.error('[updater] 检查更新失败:', err);
      return { version: app.getVersion(), available: false };
    }
  });

  ipcMain.handle('updater:install', () => {
    autoUpdater.quitAndInstall();
  });

  setTimeout(() => {
    autoUpdater.checkForUpdates().catch((err: any) => {
      console.error('[updater] 自动检查失败:', err);
    });
  }, 10000);
}
