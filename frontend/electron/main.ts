import { app, BrowserWindow, ipcMain, shell, Menu } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, ChildProcess } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';

import { setupUpdater } from './updater.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const isPackaged = app.isPackaged;
const isDev = !isPackaged;

let mainWindow: BrowserWindow | null = null;
let splashWindow: BrowserWindow | null = null;
let backendProcess: ChildProcess | null = null;
let backendUrl = '';
let backendReady = false;
let healthCheckTimer: NodeJS.Timeout | null = null;

function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as net.AddressInfo).port;
      server.close(() => resolve(port));
    });
    server.on('error', reject);
  });
}

function notifyBackendState(state: 'starting' | 'ready' | 'error') {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('backend:state', state);
  }
}

function waitForBackend(url: string, maxAttempts = 300, intervalMs = 200): Promise<void> {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const parsed = new URL(url);
    const tryConnect = () => {
      attempts += 1;
      const req = net
        .connect(Number(parsed.port), parsed.hostname, () => {
          req.destroy();
          resolve();
        })
        .on('error', () => {
          if (attempts >= maxAttempts) {
            reject(new Error(`后端服务 ${url} 未就绪`));
          } else {
            setTimeout(tryConnect, intervalMs);
          }
        });
    };
    tryConnect();
  });
}

async function startBackend(): Promise<void> {
  const port = await findFreePort();
  const host = '127.0.0.1';
  backendUrl = `http://${host}:${port}`;
  backendReady = false;
  notifyBackendState('starting');

  const projectRoot = path.resolve(__dirname, '..', '..', '..');
  const desktopDir = path.join(os.homedir(), '.aln-data');
  fs.mkdirSync(desktopDir, { recursive: true });

  let command: string;
  let args: string[];
  let cwd: string;

  if (isPackaged) {
    const backendDir = path.join(process.resourcesPath, 'backend');
    const exe = process.platform === 'win32' ? 'aln-backend.exe' : 'aln-backend';
    const oneDir = path.join(backendDir, 'aln-backend', exe);
    const oneFile = path.join(backendDir, exe);
    command = fs.existsSync(oneDir) ? oneDir : oneFile;
    args = [];
    cwd = path.dirname(command);
  } else {
    command = 'python';
    args = ['-m', 'uvicorn', 'app.main:app', '--host', host, '--port', String(port)];
    cwd = path.join(projectRoot, 'backend');
  }

  const mplDir = path.join(desktopDir, 'matplotlib-cache');
  fs.mkdirSync(mplDir, { recursive: true });

  const backendEnv = {
    ...process.env,
    ALN_DESKTOP: '1',
    ALN_DESKTOP_MODE: 'true',
    ALN_DESKTOP_DIR: desktopDir,
    ALN_BACKEND_HOST: host,
    ALN_BACKEND_PORT: String(port),
    MPLCONFIGDIR: mplDir,
  };

  console.log('[main] 启动后端:', command, args.join(' '), 'on', backendUrl);
  backendProcess = spawn(command, args, {
    cwd,
    stdio: isDev ? 'inherit' : ['ignore', 'pipe', 'pipe'],
    detached: false,
    env: backendEnv,
  });

  backendProcess.on('error', (err) => {
    console.error('[main] 后端进程启动失败:', err.message);
    notifyBackendState('error');
  });

  backendProcess.on('exit', (code) => {
    console.log(`[main] 后端进程退出，code=${code}`);
    backendProcess = null;
    backendReady = false;
    notifyBackendState('error');
    if (!isDev) {
      setTimeout(() => startBackend().catch(console.error), 2000);
    }
  });

  try {
    await waitForBackend(backendUrl);
    backendReady = true;
    notifyBackendState('ready');
    console.log('[main] 后端服务就绪:', backendUrl);
    startHealthCheck();
  } catch (e) {
    console.error('[main] 等待后端就绪超时:', e);
    notifyBackendState('error');
  }
}

function startHealthCheck() {
  if (healthCheckTimer) clearInterval(healthCheckTimer);
  healthCheckTimer = setInterval(async () => {
    if (!backendUrl || !backendProcess) return;
    try {
      await fetch(`${backendUrl}/api/health`);
    } catch {
      console.warn('[main] 后端 health 检查失败，准备重启');
      stopBackend();
      setTimeout(() => startBackend().catch(console.error), 500);
    }
  }, 10000);
}

function stopBackend() {
  if (healthCheckTimer) {
    clearInterval(healthCheckTimer);
    healthCheckTimer = null;
  }
  if (backendProcess) {
    console.log('[main] 停止后端服务...');
    if (process.platform === 'win32' && backendProcess.pid) {
      spawn('taskkill', ['/pid', String(backendProcess.pid), '/f', '/t']);
    } else {
      backendProcess.kill('SIGTERM');
    }
    backendProcess = null;
  }
}

function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 480,
    height: 320,
    frame: false,
    alwaysOnTop: true,
    transparent: true,
    resizable: false,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
  splashWindow.once('ready-to-show', () => {
    splashWindow?.show();
  });
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    title: 'ALN Resonator Data Platform',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    const indexPath = path.join(__dirname, '..', '..', 'dist', 'index.html');
    mainWindow.loadFile(indexPath);
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
    }
    mainWindow?.show();
    if (isDev) mainWindow?.webContents.openDevTools({ mode: 'detach' });
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  if (process.platform === 'darwin') {
    Menu.setApplicationMenu(
      Menu.buildFromTemplate([
        {
          label: app.name,
          submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'quit' }],
        },
        { label: '编辑', submenu: [{ role: 'cut' }, { role: 'copy' }, { role: 'paste' }] },
        { label: '窗口', submenu: [{ role: 'minimize' }, { role: 'close' }] },
        {
          label: '视图',
          submenu: [
            { role: 'reload' },
            { role: 'forceReload' },
            { role: 'toggleDevTools' },
            { type: 'separator' },
            { role: 'resetZoom' },
            { role: 'zoomIn' },
            { role: 'zoomOut' },
          ],
        },
      ])
    );
  } else {
    Menu.setApplicationMenu(null);
  }

  createSplashWindow();
  createMainWindow();

  setupUpdater();

  startBackend().catch((e) => {
    console.error('[main] 启动后端失败:', e.message);
  });
});

app.on('window-all-closed', () => {
  stopBackend();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
});

app.on('before-quit', () => {
  stopBackend();
});

ipcMain.handle('app:get-version', () => app.getVersion());
ipcMain.handle('app:get-backend-url', () => backendUrl);
ipcMain.handle('app:open-external', (_event, url: string) => shell.openExternal(url));
ipcMain.handle('backend:is-ready', () => backendReady);
