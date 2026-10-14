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


def run(onefile: bool = False):
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
    # PyInstaller 会仅依据 .toc 缓存判断 PYZ 是否可复用；若构建曾中断或清理过
    # PYZ-00.pyz，本次会生成缺失 Python 归档的不可启动应用。工作目录必须成对清理。
    shutil.rmtree(work_dir, ignore_errors=True)

    # 默认 --onedir：onefile 每次启动都解压到全新 _MEI 临时目录，macOS 会对其中
    # 数百个 dylib 逐个重新做安全校验（路径无缓存复用），实测仅此一项就要 60–90s；
    # onedir 路径固定，校验结果可跨启动复用，稳态冷启动 ~2s。onefile 仅作备选。
    mode_flag = "--onefile" if onefile else "--onedir"
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
        # algorithm_digest()（app/services/processing_retry.py）运行时读取算法源码
        # 计算指纹；冻结包内 __file__ 指向 _internal/app/，这些源文件必须随包带上，
        # 否则启动入库会在创建 ProcessingRun 时 FileNotFoundError（500）
        f"--add-data={ROOT / 'app' / 'core'}{os.pathsep}app/core",
        f"--add-data={ROOT / 'app' / 'share_pack.py'}{os.pathsep}app",
        # 注意：不要在这里打包根目录 .env。桌面版应使用默认配置或用户本机环境变量，
        # 否则构建机上的 DATABASE_URL/端口会覆盖用户本机配置。
    ]

    # 收集 hidden imports（SQLAlchemy、Pydantic、Celery 等容易漏的包）
    hidden = [
        "app.main",
        "app.workers.celery_app",
        "app.workers",
        "app.workers.process_batch",
        "app.workers.extract_batch",
        "app.workers.compute_batch",
        "app.workers.pipeline_batch",
        "app.workers.pipeline.calibration",
        "app.workers.pipeline.extractor",
        "app.workers.pipeline.processor",
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
    # 注意：hidden 列表的消费在下方 PyInstaller 调用前统一进行，
    # 后续段落如需追加直接 hidden.extend/append 即可

    # Celery / kombu 大量动态导入，collect-all 最稳
    args.extend(["--collect-all", "celery", "--collect-all", "kombu", "--collect-all", "billiard"])

    # scipy / numpy 子模块众多，PyInstaller 容易漏掉，导致桌面版导入卡住
    args.extend(["--collect-all", "scipy", "--collect-all", "numpy"])

    # psycopg 需要完整打包，否则 PyInstaller 中可能出现编码/bytes 解析错误
    args.extend(["--collect-all", "psycopg", "--hidden-import", "psycopg_binary"])

    # 器件级稀疏重建（DeviceModal 稀疏谱，app.ml.sparse.reconstructor）走 torch 懒加载，
    # PyInstaller 静态分析发现不了，必须显式收集
    args.extend(["--collect-all", "torch"])
    hidden.append("app.ml.sparse.reconstructor")

    for imp in hidden:
        args.append(f"--hidden-import={imp}")

    print("执行 PyInstaller...")
    subprocess.check_call([sys.executable, "-m", "PyInstaller"] + args)

    if onefile:
        exe_name = "aln-backend.exe" if sys.platform == "win32" else "aln-backend"
        src = dist_dir / exe_name
        dst = FRONTEND_BUILD / exe_name
        if not src.exists():
            raise FileNotFoundError(f"PyInstaller 未生成可执行文件: {src}")
        shutil.copy2(src, dst)
        print(f"后端可执行文件已输出: {dst}")
    else:
        src = dist_dir / "aln-backend"
        dst = FRONTEND_BUILD / "aln-backend"
        if src.exists():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            raise FileNotFoundError(f"PyInstaller 未生成 onedir 目录: {src}")
        print(f"后端 onedir 目录已输出: {dst}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="打包后端为桌面可执行文件")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="使用 onefile 模式（单文件便于分发；但启动时要解压并被 macOS 重新校验，慢约 1 分钟）",
    )
    args = parser.parse_args()
    run(onefile=args.onefile)
