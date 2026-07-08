"""
PyInstaller 入口：启动 FastAPI + 静态文件服务。
"""

import os
import sys
import time
import traceback

# PyInstaller 会把资源解压到 _MEIPASS；标记桌面模式
meipass = getattr(sys, "_MEIPASS", None)
if meipass:
    os.environ["ALN_DESKTOP"] = "1"
    os.environ["ALN_DESKTOP_MODE"] = "true"
    # 桌面版数据目录强制固定到用户目录，覆盖 .env 里可能写死的开发绝对路径
    desktop_dir = os.path.join(os.path.expanduser("~"), ".aln-data")
    os.environ["ALN_DESKTOP_DIR"] = desktop_dir
    os.environ["DATA_ROOT"] = os.path.join(desktop_dir, "data")
    env_path = os.path.join(meipass, ".env")
    if os.path.isfile(env_path):
        os.environ["DOTENV_PATH"] = env_path
    sys.path.insert(0, meipass)

# PyInstaller 打包后 psycopg 可能出现 server version bytes 解析错误，
# 强制客户端编码为 UTF8 可规避 SQLAlchemy _get_server_version_info 的 re.match 异常。
os.environ.setdefault("PGCLIENTENCODING", "UTF8")

# 固定 matplotlib 缓存目录，避免 PyInstaller 每次启动都重建字体缓存。
# 必须在导入任何 matplotlib 子模块之前设置。
cache_home = os.path.join(os.path.expanduser("~"), ".aln-data", "matplotlib-cache")
os.makedirs(cache_home, exist_ok=True)
os.environ["MPLCONFIGDIR"] = cache_home

# 诊断日志文件，避免 stdout 缓冲丢失。
LOG_DIR = os.path.join(os.path.expanduser("~"), ".aln-data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "backend.log")


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except Exception:
        pass


log(f"[aln-backend] MPLCONFIGDIR={cache_home} home={os.path.expanduser('~')} meipass={meipass}")
log(
    f"[aln-backend] DOTENV_PATH={os.environ.get('DOTENV_PATH')} "
    f"DATA_ROOT={os.environ.get('DATA_ROOT')}"
)
log(f"[aln-backend] sys.executable={sys.executable}")
log(f"[aln-backend] sys.path[0]={sys.path[0] if sys.path else '(empty)'}")

try:
    import uvicorn
except Exception as exc:
    log("[aln-backend] Failed to import uvicorn: " + str(exc))
    traceback.print_exc()
    sys.exit(1)


def _start_worker() -> None:
    """桌面版 Celery worker 入口。

    由 Electron 通过 `aln-backend --worker` 启动。使用单进程 solo pool，
    避免 PyInstaller onefile 在 macOS 上 fork 子进程导致资源重复解压。
    """
    try:
        # 导入 app.workers 注册所有任务
        from app.workers import celery_app  # noqa: F401
    except Exception as exc:
        log("[aln-backend] Failed to import worker tasks: " + str(exc))
        traceback.print_exc()
        sys.exit(1)

    log("[aln-backend] starting celery worker...")
    try:
        from app.workers.celery_app import celery_app as app

        # argv 等价于 celery -A app.workers worker --loglevel=info --concurrency=1 --pool=solo
        app.worker_main(
            argv=[
                "worker",
                "--loglevel=info",
                "--concurrency=1",
                "--pool=solo",
                "-n",
                "desktop@%h",
            ]
        )
    except Exception as exc:
        log("[aln-backend] worker exited with error: " + str(exc))
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    is_worker = "--worker" in sys.argv

    if is_worker:
        _start_worker()
        sys.exit(0)

    host = os.environ.get("ALN_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("ALN_BACKEND_PORT", "8000"))
    log(f"[aln-backend] starting server on {host}:{port}")
    try:
        t0 = time.time()
        log("[aln-backend] importing app.main...")
        import app.main as app_main

        log(f"[aln-backend] imported app.main in {time.time() - t0:.2f}s")
        log("[aln-backend] calling uvicorn.run...")
        uvicorn.run(app_main.app, host=host, port=port, log_level="info", access_log=False)
    except Exception as exc:
        log("[aln-backend] failed to start: " + str(exc))
        traceback.print_exc()
        sys.exit(1)
