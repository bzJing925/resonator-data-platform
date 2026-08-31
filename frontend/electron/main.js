import { app, BrowserWindow, ipcMain, shell, Menu } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ---------------------------------------------------------------------------
// 配置
// ---------------------------------------------------------------------------
const BACKEND_HOST = process.env.ALN_BACKEND_HOST || '127.0.0.1';
const BACKEND_PORT = Number(process.env.ALN_BACKEND_PORT || 8000);
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

const isPackaged = app.isPackaged;
const isDev = !isPackaged;

// ---------------------------------------------------------------------------
// 窗口管理
// ---------------------------------------------------------------------------
let mainWindow = null;
let splashWindow = null;
let backendProcess = null;
let workerProcess = null;
let backendReady = false;

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
    splashWindow.show();
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

  // 生产环境直接加载本地构建产物，瞬间显示；开发环境走 Vite dev server
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    const indexPath = path.join(__dirname, '..', 'dist', 'index.html');
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
    mainWindow.show();
    if (isDev) mainWindow.webContents.openDevTools({ mode: 'detach' });
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ---------------------------------------------------------------------------
// 后端服务管理
// ---------------------------------------------------------------------------
function waitForBackend(maxAttempts = 300, intervalMs = 200) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const tryConnect = () => {
      attempts += 1;
      const req = net
        .connect(BACKEND_PORT, BACKEND_HOST, () => {
          req.destroy();
          resolve(true);
        })
        .on('error', () => {
          if (attempts >= maxAttempts) {
            reject(new Error(`后端服务 ${BACKEND_HOST}:${BACKEND_PORT} 未就绪`));
          } else {
            setTimeout(tryConnect, intervalMs);
          }
        });
    };
    tryConnect();
  });
}

function resolveBackendCommand() {
  const projectRoot = path.resolve(__dirname, '..', '..');
  if (isPackaged) {
    const backendDir = path.join(process.resourcesPath, 'backend');
    const oneFile =
      process.platform === 'win32'
        ? path.join(backendDir, 'aln-backend.exe')
        : path.join(backendDir, 'aln-backend');
    const oneDir =
      process.platform === 'win32'
        ? path.join(backendDir, 'aln-backend', 'aln-backend.exe')
        : path.join(backendDir, 'aln-backend', 'aln-backend');
    return {
      command: fs.existsSync(oneDir) ? oneDir : oneFile,
      cwd: path.dirname(fs.existsSync(oneDir) ? oneDir : oneFile),
    };
  }
  return {
    command: 'python',
    cwd: path.join(projectRoot, 'backend'),
  };
}

function buildBackendEnv() {
  // matplotlib 会在 MPLCONFIGDIR 下缓存字体；PyInstaller 每次启动的临时目录不同，
  // 不固定该目录会导致每次启动都重建字体缓存（耗时 60~90 秒）。
  const mplDir = path.join(os.homedir(), '.aln-data', 'matplotlib-cache');
  fs.mkdirSync(mplDir, { recursive: true });

  return {
    ...process.env,
    ALN_DESKTOP: '1',
    MPLCONFIGDIR: mplDir,
    // 桌面版强制数据根目录为用户目录，与 desktop_entry.py 双重保险
    DATA_ROOT: path.join(os.homedir(), '.aln-data', 'data'),
    // worker 进程需要知道后端监听端口，避免与开发环境冲突
    ALN_BACKEND_PORT: String(BACKEND_PORT),
  };
}

async function startBackend() {
  try {
    await waitForBackend(8, 100);
    console.log('[main] 后端服务已运行');
    backendReady = true;
    notifyBackendReady();
    return;
  } catch {
    console.log('[main] 后端未运行，准备启动...');
  }

  const { command, cwd } = resolveBackendCommand();
  const args = isPackaged ? [] : ['-m', 'uvicorn', 'app.main:app', '--host', BACKEND_HOST, '--port', String(BACKEND_PORT)];
  const backendEnv = buildBackendEnv();

  console.log('[main] 启动后端:', command, args.join(' '));
  backendProcess = spawn(command, args, {
    cwd,
    stdio: isDev ? 'inherit' : ['ignore', 'pipe', 'pipe'],
    detached: false,
    env: backendEnv,
  });

  backendProcess.on('error', (err) => {
    console.error('[main] 后端进程启动失败:', err.message);
  });

  backendProcess.on('exit', (code) => {
    console.log(`[main] 后端进程退出，code=${code}`);
    backendProcess = null;
  });

  if (!isDev && backendProcess.stdout) {
    backendProcess.stdout.on('data', (d) => console.log('[backend]', d.toString().trim()));
  }
  if (!isDev && backendProcess.stderr) {
    backendProcess.stderr.on('data', (d) => console.error('[backend]', d.toString().trim()));
  }

  try {
    await waitForBackend(300, 200);
    backendReady = true;
    console.log('[main] 后端服务就绪');
    notifyBackendReady();
  } catch (e) {
    console.error('[main] 等待后端就绪超时:', e.message);
  }
}

async function startWorker() {
  if (isDev) {
    // 开发环境依赖用户手动启动 Celery worker
    console.log('[main] 开发模式：请手动运行 celery -A app.workers worker --loglevel=info');
    return;
  }

  const { command, cwd } = resolveBackendCommand();
  const workerEnv = buildBackendEnv();

  console.log('[main] 启动 worker:', command, '--worker');
  workerProcess = spawn(command, ['--worker'], {
    cwd,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false,
    env: workerEnv,
  });

  workerProcess.on('error', (err) => {
    console.error('[main] worker 进程启动失败:', err.message);
  });

  workerProcess.on('exit', (code) => {
    console.log(`[main] worker 进程退出，code=${code}`);
    workerProcess = null;
  });

  if (workerProcess.stdout) {
    workerProcess.stdout.on('data', (d) => console.log('[worker]', d.toString().trim()));
  }
  if (workerProcess.stderr) {
    workerProcess.stderr.on('data', (d) => console.error('[worker]', d.toString().trim()));
  }
}

function notifyBackendReady() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('backend:ready');
  }
}

function stopBackend() {
  if (backendProcess) {
    console.log('[main] 停止后端服务...');
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', backendProcess.pid, '/f', '/t']);
    } else {
      backendProcess.kill('SIGTERM');
    }
    backendProcess = null;
  }
}

function stopWorker() {
  if (workerProcess) {
    console.log('[main] 停止 worker...');
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', workerProcess.pid, '/f', '/t']);
    } else {
      workerProcess.kill('SIGTERM');
    }
    workerProcess = null;
  }
}

// ---------------------------------------------------------------------------
// 应用生命周期
// ---------------------------------------------------------------------------
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

  // 关键：主窗口立即创建并加载本地前端，不再等待后端
  createMainWindow();

  // 后端在后台启动；就绪后再启动 Celery worker 处理上传任务
  startBackend()
    .then(() => startWorker())
    .catch((e) => {
      console.error('[main] 启动后端失败:', e.message);
    });
});

app.on('window-all-closed', () => {
  stopBackend();
  stopWorker();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
});

app.on('before-quit', () => {
  stopBackend();
  stopWorker();
});

// ---------------------------------------------------------------------------
// IPC
// ---------------------------------------------------------------------------
ipcMain.handle('app:get-version', () => app.getVersion());
ipcMain.handle('app:get-backend-url', () => BACKEND_URL);
ipcMain.handle('app:open-external', (_event, url) => shell.openExternal(url));
ipcMain.handle('backend:is-ready', () => backendReady);
