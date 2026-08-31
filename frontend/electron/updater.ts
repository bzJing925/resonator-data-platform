import * as electron from 'electron';
import type { UpdateCheckResult } from 'electron-updater';
import { autoUpdater } from './updater-loader.cjs';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const { app, ipcMain } = electron;

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

// 默认更新源：github.com 主站在国内网络不可达（api.github.com 与镜像站可达），
// electron-updater 的 github provider 版本检查走 github.com/.../releases.atom 必然失败。
// 故默认走 generic provider + gh-proxy 镜像，指向固定 tag "latest" 的 release 资产目录；
// 发版时除版本 tag 外需同步维护 latest release（替换 3 个资产：dmg/blockmap/latest-mac.yml）。
// 网络可直连 github.com 的环境可在 ~/.aln-data/settings.json 配 updateSource 切回 github。
const DEFAULT_UPDATE_URL =
  'https://gh-proxy.com/https://github.com/bzJing925/resonator-data-platform/releases/download/latest';

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
    type: 'static',
    url: DEFAULT_UPDATE_URL,
  };

  if (source.type === 'github') {
    autoUpdater.setFeedURL({
      provider: 'github',
      owner: source.owner || 'bzJing925',
      repo: source.repo || 'resonator-data-platform',
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
