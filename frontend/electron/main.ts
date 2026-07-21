import * as electron from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, ChildProcess } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';

const { app, BrowserWindow, ipcMain, shell, Menu } = electron;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// 开发调试日志：把主进程 console 输出写入文件，方便排查后端启动问题
const _logDir = path.join(os.homedir(), '.aln-data', 'logs');
fs.mkdirSync(_logDir, { recursive: true });
const _logFile = path.join(_logDir, 'electron-main.log');
const _logStream = fs.createWriteStream(_logFile, { flags: 'a' });
function _logToFile(...args: any[]) {
  const line = `[${new Date().toISOString()}] ${args.map((a) => (typeof a === 'object' ? JSON.stringify(a) : String(a))).join(' ')}\n`;
  _logStream.write(line);
}
const _origLog = console.log;
const _origError = console.error;
console.log = (...args: any[]) => {
  _origLog(...args);
  _logToFile(...args);
};
console.error = (...args: any[]) => {
  _origError(...args);
  _logToFile('[ERROR]', ...args);
};

const isPackaged = app.isPackaged;
const isDev = !isPackaged;

let mainWindow: electron.BrowserWindow | null = null;
let splashWindow: electron.BrowserWindow | null = null;
let backendProcess: ChildProcess | null = null;
let backendUrl = '';
let backendReady = false;
let healthCheckTimer: NodeJS.Timeout | null = null;
let backendLogFd: number | null = null;

function closeBackendLogFd() {
  if (backendLogFd !== null) {
    try {
      fs.closeSync(backendLogFd);
    } catch {
      // ignore
    }
    backendLogFd = null;
  }
}

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

/**
 * 清理残留的 aln-backend 进程（上次崩溃/强退遗留）。
 * 仅打包模式生效：开发模式的后端是 `python -m uvicorn`，不能按进程名误杀。
 * 命令退出码非 0（如无匹配进程）属正常，不视为错误。
 */
function killStaleBackendProcesses(): Promise<void> {
  if (!isPackaged) return Promise.resolve();

  const command = process.platform === 'win32' ? 'taskkill' : 'pkill';
  const args =
    process.platform === 'win32' ? ['/f', '/im', 'aln-backend.exe'] : ['-9', 'aln-backend'];

  return new Promise((resolve) => {
    let child: ChildProcess;
    try {
      child = spawn(command, args, { windowsHide: true });
    } catch (e) {
      console.warn('[main] 清理残留后端进程失败（忽略）:', e);
      resolve();
      return;
    }
    child.on('error', (err) => {
      console.warn('[main] 清理残留后端进程失败（忽略）:', err.message);
      resolve();
    });
    child.on('exit', (code) => {
      if (code === 0) console.log('[main] 已清理残留后端进程');
      resolve();
    });
  });
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
  // 上次崩溃/强退可能留下残留 aln-backend 进程，占着 SQLite 文件锁，
  // 不清理会导致新后端启动即 "database is locked"、/api/health 连接被拒绝。
  await killStaleBackendProcesses();

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
    command = process.env.ALN_PYTHON_PATH || 'python';
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
    KEEP_RAW_ZIP: 'true',
  };

  const backendLogDir = path.join(desktopDir, 'logs');
  fs.mkdirSync(backendLogDir, { recursive: true });
  closeBackendLogFd();
  backendLogFd = fs.openSync(path.join(backendLogDir, 'backend.log'), 'a');

  console.log('[main] 启动后端:', command, args.join(' '), 'on', backendUrl);
  backendProcess = spawn(command, args, {
    cwd,
    stdio: ['ignore', backendLogFd, backendLogFd],
    detached: false,
    env: backendEnv,
    windowsHide: true,
  });

  backendProcess.on('error', (err) => {
    console.error('[main] 后端进程启动失败:', err.message);
    closeBackendLogFd();
    notifyBackendState('error');
  });

  backendProcess.on('exit', (code) => {
    console.log(`[main] 后端进程退出，code=${code}`);
    backendProcess = null;
    backendReady = false;
    closeBackendLogFd();
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
  closeBackendLogFd();
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
      preload: path.join(__dirname, 'preload.cjs'),
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

  mainWindow.webContents.setWindowOpenHandler(({ url }: { url: string }) => {
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

  // electron-updater 在开发环境下会 require('electron')，但本地 node_modules 里的 electron
  // npm 包会把它解析成路径字符串，导致 app 为 undefined。只在打包后启用自动更新。
  if (app.isPackaged) {
    const { setupUpdater } = await import('./updater.js');
    setupUpdater();
  } else {
    ipcMain.handle('updater:check', async () => ({
      version: app.getVersion(),
      available: false,
    }));
    ipcMain.handle('updater:install', () => {});
  }

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
