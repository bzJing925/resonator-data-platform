import * as electron from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, ChildProcess } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';
import type { AnalysisDesktopCommand, AnalysisSurface } from './analysisCommands.js';

const { app, BrowserWindow, clipboard, ipcMain, shell, Menu } = electron;

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
const nativeRoot = path.resolve(process.resourcesPath, '..', '..');
const isNativeBundle = fs.existsSync(path.join(nativeRoot, 'native-bundle.json'));
const isDev = !isPackaged && !isNativeBundle;
if (isNativeBundle && !app.requestSingleInstanceLock()) app.quit();
const e2eUrl = process.env.ALN_E2E_URL?.startsWith('http://127.0.0.1:')
  ? process.env.ALN_E2E_URL
  : undefined;

let mainWindow: electron.BrowserWindow | null = null;
let splashWindow: electron.BrowserWindow | null = null;
let backendProcess: ChildProcess | null = null;
let backendUrl = '';
let backendReady = false;
let healthCheckTimer: NodeJS.Timeout | null = null;
let backendLogFd: number | null = null;
let analysisContext: { webContentsId: number; surface: AnalysisSurface } | null = null;

function isAnalysisSurface(value: unknown): value is AnalysisSurface {
  return value === 'chart' || value === 'worksheet';
}

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

// 窗口曾在后端未就绪时加载（或后端中途退出）→ 就绪后需要自动刷新一次，
// 否则窗口停留在冷启动期间的报错页（数据请求打到旧端口/未就绪服务）。
let reloadOnReady = false;

function onBackendReady() {
  backendReady = true;
  notifyBackendState('ready');
  console.log('[main] 后端服务就绪:', backendUrl);
  startHealthCheck();
  if (reloadOnReady && mainWindow && !mainWindow.isDestroyed()) {
    reloadOnReady = false;
    console.log('[main] 后端就绪，自动刷新主窗口');
    mainWindow.webContents.reload();
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
  if (!isNativeBundle) await killStaleBackendProcesses();

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

  if (isNativeBundle) {
    command = path.join(nativeRoot, 'runtime', 'python.exe');
    args = [path.join(nativeRoot, 'run_desktop_backend.py'), '--port', String(port)];
    cwd = path.join(nativeRoot, 'backend');
  } else if (isPackaged) {
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
    ALN_SERVER_MODE: 'false',
    DOTENV_PATH: path.join(desktopDir, 'no-dotenv'),
    DEBUG: 'false',
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
    reloadOnReady = true; // 窗口可能仍持有旧端口，下次就绪时需刷新
    closeBackendLogFd();
    notifyBackendState('error');
    if (!isDev) {
      setTimeout(() => startBackend().catch(console.error), 2000);
    }
  });

  try {
    await waitForBackend(backendUrl);
    onBackendReady();
  } catch (e) {
    console.error('[main] 等待后端就绪超时:', e);
    notifyBackendState('error');
    // 进程仍活着（冷启动导入慢，实测可达 ~40s）→ 转后台慢速继续等，
    // 就绪后走同一 onBackendReady（含窗口自动刷新）
    if (backendProcess) {
      waitForBackend(backendUrl, 900, 1000)
        .then(() => onBackendReady())
        .catch(() => {});
    }
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

function sendAnalysisCommand(
  command: AnalysisDesktopCommand,
  browserWindow: electron.BrowserWindow | null | undefined,
  surface?: AnalysisSurface,
) {
  if (!browserWindow || browserWindow.isDestroyed()) return false;
  if (!analysisContext || analysisContext.webContentsId !== browserWindow.webContents.id) return false;
  browserWindow.webContents.send('analysis:command', {
    command,
    surface: surface || analysisContext.surface,
  });
  return true;
}

function runEditCommand(
  command: 'deleteSelection' | 'undo' | 'redo' | 'selectAll' | 'copySelection',
  baseWindow: electron.BaseWindow | undefined,
) {
  const browserWindow = baseWindow instanceof BrowserWindow ? baseWindow : undefined;
  if (sendAnalysisCommand(command, browserWindow)) return;
  const contents = browserWindow?.webContents;
  if (!contents) return;
  if (command === 'deleteSelection') contents.delete();
  if (command === 'undo') contents.undo();
  if (command === 'redo') contents.redo();
  if (command === 'selectAll') contents.selectAll();
  if (command === 'copySelection') contents.copy();
}

function editMenuItem(
  id: string,
  label: string,
  command: Parameters<typeof runEditCommand>[0],
  accelerator?: string,
): electron.MenuItemConstructorOptions {
  return {
    id,
    label,
    accelerator,
    click: (_item, browserWindow) => runEditCommand(command, browserWindow),
  };
}

function analysisMenuItem(
  id: string,
  label: string,
  command: AnalysisDesktopCommand,
  accelerator?: string,
): electron.MenuItemConstructorOptions {
  return {
    id,
    label,
    accelerator,
    click: (_item, baseWindow) => sendAnalysisCommand(
      command,
      baseWindow instanceof BrowserWindow ? baseWindow : undefined,
    ),
  };
}

function buildApplicationMenu() {
  const template: electron.MenuItemConstructorOptions[] = [];
  if (process.platform === 'darwin') {
    template.push({
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    });
  } else {
    template.push({ label: '文件', submenu: [{ role: 'quit' }] });
  }
  template.push(
    {
      label: '编辑',
      submenu: [
        editMenuItem('analysis.undo', '撤销', 'undo', 'CommandOrControl+Z'),
        editMenuItem('analysis.redo', '重做', 'redo', 'CommandOrControl+Shift+Z'),
        { type: 'separator' },
        { role: 'cut' },
        editMenuItem('analysis.copy', '复制', 'copySelection', 'CommandOrControl+C'),
        { role: 'paste' },
        editMenuItem('analysis.delete', '删除所选数据', 'deleteSelection'),
        { type: 'separator' },
        editMenuItem('analysis.select-all', '全选', 'selectAll', 'CommandOrControl+A'),
      ],
    },
    {
      label: '分析',
      submenu: [
        analysisMenuItem('analysis.export-selection', '导出所选数据…', 'exportSelection'),
        analysisMenuItem('analysis.edit-style', '编辑数据点样式…', 'editStyle'),
        analysisMenuItem('analysis.autorange', '坐标轴自动调整', 'autorange'),
      ],
    },
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
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    { label: '窗口', submenu: [{ role: 'minimize' }, { role: 'close' }] },
  );
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function showAnalysisContextMenu(
  browserWindow: electron.BrowserWindow,
  surface: AnalysisSurface,
) {
  const item = (
    id: string,
    label: string,
    command: AnalysisDesktopCommand,
  ): electron.MenuItemConstructorOptions => ({
    id,
    label,
    click: (_menuItem, baseWindow) => sendAnalysisCommand(
      command,
      baseWindow instanceof BrowserWindow ? baseWindow : browserWindow,
      surface,
    ),
  });
  const template: electron.MenuItemConstructorOptions[] = [
    item('analysis.context.delete', '删除所选数据', 'deleteSelection'),
    { type: 'separator' },
    item('analysis.context.undo', '撤销', 'undo'),
    item('analysis.context.redo', '重做', 'redo'),
    { type: 'separator' },
    item('analysis.context.select-all', '全选', 'selectAll'),
    item('analysis.context.copy', '复制所选数据', 'copySelection'),
    item('analysis.context.export', '导出所选数据…', 'exportSelection'),
    item('analysis.context.style', '编辑数据点样式…', 'editStyle'),
  ];
  if (surface === 'chart') {
    template.push(item(
      'analysis.context.autorange',
      '坐标轴自动调整',
      'autorange',
    ));
  }
  const menu = Menu.buildFromTemplate(template);
  menu.popup({ window: browserWindow });
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

  if (e2eUrl) {
    mainWindow.loadURL(e2eUrl);
  } else if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    const indexPath = path.join(__dirname, '..', '..', 'dist', 'index.html');
    mainWindow.loadFile(indexPath);
  }
  if (!backendReady) reloadOnReady = true; // 冷启动：窗口先于后端加载

  mainWindow.webContents.setWindowOpenHandler(({ url }: { url: string }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
    }
    mainWindow?.show();
    if (isDev && !e2eUrl) mainWindow?.webContents.openDevTools({ mode: 'detach' });
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  buildApplicationMenu();

  if (e2eUrl) {
    createMainWindow();
    return;
  }

  createSplashWindow();
  createMainWindow();

  // electron-updater 在开发环境下会 require('electron')，但本地 node_modules 里的 electron
  // npm 包会把它解析成路径字符串，导致 app 为 undefined。只在打包后启用自动更新。
  if (app.isPackaged && !isNativeBundle) {
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
ipcMain.on('analysis:set-context', (event, value: unknown) => {
  if (event.sender !== mainWindow?.webContents || !value || typeof value !== 'object') return;
  const { active, surface } = value as { active?: unknown; surface?: unknown };
  if (typeof active !== 'boolean' || !isAnalysisSurface(surface)) return;
  analysisContext = active ? { webContentsId: event.sender.id, surface } : null;
});
ipcMain.on('analysis:show-context-menu', (event, surface: unknown) => {
  if (event.sender !== mainWindow?.webContents || !isAnalysisSurface(surface)) return;
  const browserWindow = BrowserWindow.fromWebContents(event.sender);
  if (browserWindow) showAnalysisContextMenu(browserWindow, surface);
});
ipcMain.handle('analysis:write-clipboard', (event, text: unknown) => {
  if (event.sender !== mainWindow?.webContents
    || typeof text !== 'string'
    || text.length > 10_000_000) throw new Error('Invalid analysis clipboard payload');
  clipboard.writeText(text);
});
