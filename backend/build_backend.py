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

    # 代理模型推理：torch 体量大但页面实时推理必需（推理链路为懒加载导入，
    # PyInstaller 静态分析发现不了，必须显式收集）
    args.extend(["--collect-all", "torch"])
    # Zp 校正器（zp_corrector.joblib，ExtraTrees）推理时需要 sklearn/joblib/threadpoolctl
    args.extend(["--collect-all", "sklearn", "--collect-all", "joblib"])
    hidden.append("threadpoolctl")
    hidden.extend([
        "app.ml.surrogate.infer",
        "app.ml.surrogate.model",
        "app.ml.surrogate.baseline",
        "app.ml.sparse.reconstructor",
    ])

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

    # 代理模型产物：只并入推理必需文件（b3 joblib 35MB、训练 history 等不带进安装包）
    surr_data = ROOT / "surrogate_data"
    surr_ckpt = ROOT / "checkpoints_surrogate"
    if surr_data.exists() and surr_ckpt.exists():
        # 冻结后运行时代码以 __file__ 解析（app/api/surrogate.py 的 parents[2]
        # 在冻结包里指向 aln-backend/_internal/），所以 onedir 必须并到
        # _internal/ 内 —— 放在 FRONTEND_BUILD 顶层（Resources/backend/）会被
        # 运行时报"产物缺失"。onefile（已弃用）保持旧行为。
        dest_base = FRONTEND_BUILD if onefile else FRONTEND_BUILD / "aln-backend" / "_internal"
        shutil.copytree(surr_data, dest_base / "surrogate_data", dirs_exist_ok=True)
        (dest_base / "checkpoints_surrogate").mkdir(exist_ok=True)
        for f in ("stage1.pt", "stage2.pt", "stage2_ft.pt", "stage2_config.json",
                  "zp_corrector.joblib"):
            src_f = surr_ckpt / f
            if src_f.exists():
                shutil.copy2(src_f, dest_base / "checkpoints_surrogate" / f)
        print("代理模型产物已并入安装包")
    else:
        print("警告：代理模型产物缺失（backend/surrogate_data 或 checkpoints_surrogate），"
              "安装包内代理模型页将显示未部署")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="打包后端为桌面可执行文件")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="使用 onefile 模式（单文件便于分发；但启动时要解压并被 macOS 重新校验，慢约 1 分钟）",
    )
    args = parser.parse_args()
    run(onefile=args.onefile)
