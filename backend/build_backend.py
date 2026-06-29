"""
用 PyInstaller 把后端打包成独立可执行文件，供 Electron 桌面应用调用。

用法：
    cd backend
    python build_backend.py

输出：../frontend/build/backend/aln-backend(.exe)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND_BUILD = ROOT.parent / "frontend" / "build" / "backend"


def run(onedir: bool = False):
    try:
        import PyInstaller.__main__  # noqa: F401
    except ImportError:
        print("PyInstaller 未安装，正在安装...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # 清理旧产物
    if FRONTEND_BUILD.exists():
        shutil.rmtree(FRONTEND_BUILD)
    FRONTEND_BUILD.mkdir(parents=True, exist_ok=True)

    spec_dir = ROOT / "build" / "pyinstaller"
    spec_dir.mkdir(parents=True, exist_ok=True)

    work_dir = ROOT / "build" / "pyinstaller" / "work"
    dist_dir = ROOT / "build" / "pyinstaller" / "dist"

    # 基础 PyInstaller 参数：默认 --onefile，便于分发；加 --onedir 启动更快
    mode_flag = "--onedir" if onedir else "--onefile"
    args = [
        str(ROOT / "desktop_entry.py"),
        "--name=aln-backend",
        mode_flag,
        "--console",
        "-y",
        f"--distpath={dist_dir}",
        f"--workpath={work_dir}",
        f"--specpath={spec_dir}",
        # 包含 Alembic 迁移文件、配置文件等
        f"--add-data={ROOT / 'alembic'}{os.pathsep}alembic",
        f"--add-data={ROOT / 'alembic.ini'}{os.pathsep}.",
        f"--add-data={ROOT / 'pyproject.toml'}{os.pathsep}.",
        # 打包根目录 .env（如存在），让后端可执行文件自带默认配置
        *(
            [f"--add-data={env_path}{os.pathsep}."]
            if (env_path := ROOT.parent / ".env").exists()
            else []
        ),
    ]

    # 收集 hidden imports（SQLAlchemy、Pydantic、Celery 等容易漏的包）
    hidden = [
        "app.main",
        "app.workers.celery_app",
        "app.workers",
        "app.workers.process_batch",
        "app.workers.progress",
        "uvicorn",
        "uvloop",
        "asyncio",
        "click",
        "h11",
        "httptools",
        "websockets",
        "wsproto",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "sqlalchemy.ext.asyncio",
        "sqlalchemy.dialects.postgresql",
        "pydantic",
        "pydantic_settings",
        "celery",
        "celery.loaders.default",
        "kombu",
        "redis",
        "passlib",
        "jinja2",
        "alembic",
        "alembic.runtime.migration",
        "alembic.script.base",
        "email_validator",
        "zipstream",
    ]
    for imp in hidden:
        args.append(f"--hidden-import={imp}")

    # Celery / kombu 大量动态导入，collect-all 最稳
    args.extend(["--collect-all", "celery", "--collect-all", "kombu", "--collect-all", "billiard"])

    # scipy / numpy 子模块众多，PyInstaller 容易漏掉，导致桌面版导入卡住
    args.extend(["--collect-all", "scipy", "--collect-all", "numpy"])

    # psycopg 需要完整打包，否则 PyInstaller 中可能出现编码/bytes 解析错误
    args.extend(["--collect-all", "psycopg", "--hidden-import", "psycopg_binary"])

    print("执行 PyInstaller...")
    subprocess.check_call([sys.executable, "-m", "PyInstaller"] + args)

    if onedir:
        src = dist_dir / "aln-backend"
        dst = FRONTEND_BUILD / "aln-backend"
        if src.exists():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            raise FileNotFoundError(f"PyInstaller 未生成 onedir 目录: {src}")
        print(f"后端 onedir 目录已输出: {dst}")
    else:
        exe_name = "aln-backend.exe" if sys.platform == "win32" else "aln-backend"
        src = dist_dir / exe_name
        dst = FRONTEND_BUILD / exe_name
        if not src.exists():
            raise FileNotFoundError(f"PyInstaller 未生成可执行文件: {src}")
        shutil.copy2(src, dst)
        print(f"后端可执行文件已输出: {dst}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="打包后端为桌面可执行文件")
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="使用 onedir 模式（启动更快，文件更多）",
    )
    args = parser.parse_args()
    run(onedir=args.onedir)
