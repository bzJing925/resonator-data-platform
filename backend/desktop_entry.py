"""
PyInstaller 入口：启动 FastAPI + 静态文件服务。
"""

import os
import sys
import time
import traceback

# PyInstaller 会把资源解压到 _MEIPASS；标记桌面模式
meipass = getattr(sys, '_MEIPASS', None)
if meipass:
    os.environ['ALN_DESKTOP'] = '1'
    # 桌面版数据目录强制固定到用户目录，覆盖 .env 里可能写死的开发绝对路径
    os.environ['DATA_ROOT'] = os.path.join(os.path.expanduser('~'), '.aln-data', 'data')
    env_path = os.path.join(meipass, '.env')
    if os.path.isfile(env_path):
        os.environ['DOTENV_PATH'] = env_path
    sys.path.insert(0, meipass)

# 固定 matplotlib 缓存目录，避免 PyInstaller 每次启动都重建字体缓存。
# 必须在导入任何 matplotlib 子模块之前设置。
cache_home = os.path.join(os.path.expanduser('~'), '.aln-data', 'matplotlib-cache')
os.makedirs(cache_home, exist_ok=True)
os.environ['MPLCONFIGDIR'] = cache_home

# 诊断日志文件，避免 stdout 缓冲丢失。
LOG_DIR = os.path.join(os.path.expanduser('~'), '.aln-data', 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, 'backend.log')

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
    except Exception:
        pass

log(f'[aln-backend] MPLCONFIGDIR={cache_home} home={os.path.expanduser("~")} meipass={meipass}')
log(f'[aln-backend] DOTENV_PATH={os.environ.get("DOTENV_PATH")} DATA_ROOT={os.environ.get("DATA_ROOT")}')
log(f'[aln-backend] sys.executable={sys.executable}')
log(f'[aln-backend] sys.path[0]={sys.path[0] if sys.path else "(empty)"}')

try:
    import uvicorn
except Exception as exc:
    log('[aln-backend] Failed to import uvicorn: ' + str(exc))
    traceback.print_exc()
    sys.exit(1)

if __name__ == '__main__':
    host = os.environ.get('ALN_BACKEND_HOST', '127.0.0.1')
    port = int(os.environ.get('ALN_BACKEND_PORT', '8000'))
    log(f'[aln-backend] starting server on {host}:{port}')
    try:
        t0 = time.time()
        log('[aln-backend] importing app.main...')
        import app.main as app_main
        log(f'[aln-backend] imported app.main in {time.time()-t0:.2f}s')
        log('[aln-backend] calling uvicorn.run...')
        uvicorn.run(app_main.app, host=host, port=port, log_level='info', access_log=False)
    except Exception as exc:
        log('[aln-backend] failed to start: ' + str(exc))
        traceback.print_exc()
        sys.exit(1)
