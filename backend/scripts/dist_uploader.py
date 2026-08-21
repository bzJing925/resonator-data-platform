"""组装 aln-data-uploader 独立发布仓库（vendor 平台算法层子集）。

把运行简易上传页面所需的最小代码拷到目标目录，生成独立可 CI 构建的仓库：
    uv run python scripts/dist_uploader.py --out /Users/jingbozuo/Projects/aln-data-uploader

vendor 清单是显式的：算法层改动后重跑本脚本即可同步（勿在发布仓库里手改 src/）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 显式 vendor 清单（相对 backend/）
VENDOR_FILES = [
    "app/__init__.py",
    "app/config.py",
    "app/share_pack.py",
    "app/core/__init__.py",
    "app/core/filename.py",
    "app/core/mapping.py",
    "app/core/touchstone.py",
    "app/core/extract.py",
    "app/core/deembed.py",
    "app/schemas/__init__.py",
    "app/schemas/resonator.py",
    "app/webui/__init__.py",
    "app/webui/uploader/__init__.py",
    "app/webui/uploader/__main__.py",
    "app/webui/uploader/server.py",
    "app/webui/uploader/page.html",
    "app/workers/pipeline/processor.py",
    "app/workers/pipeline/calibration.py",
]

REQUIREMENTS = """\
fastapi
uvicorn
numpy
scipy
scikit-rf
pandas
openpyxl
pydantic
pydantic-settings
pyinstaller
"""

LAUNCH = '''"""aln-uploader 启动入口（PyInstaller 打包入口）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.webui.uploader.server import main

main()
'''

BUILD_PY = '''#!/usr/bin/env python3
"""构建 aln-uploader 安装包（本机或 CI）。

macOS:   python build_uploader.py   → dist/ALN-Uploader.app + dist/aln-uploader-macos-arm64.zip
Windows: python build_uploader.py   → dist/aln-uploader-windows-amd64.zip
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Windows GBK 控制台打不了中文/特殊字符
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent

# 明确排除主仓库 venv 里可能存在的重依赖（只按 import 图打包，不 collect app 全子模块）
EXCLUDES = [
    "torch", "pyarrow", "onnxruntime", "onnx", "sklearn", "matplotlib",
    "celery", "redis", "psycopg2", "sqlalchemy", "alembic", "PIL",
    "pytest", "IPython", "jupyter",
]


def main() -> None:
    dist = ROOT / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile" if platform.system() != "Darwin" else "--windowed",
        "--name", "ALN-Uploader",
        "--add-data", "src/app/webui/uploader/page.html:app/webui/uploader",
        "--collect-all", "skrf",
        "--hidden-import", "tkinter",
        "--hidden-import", "uvicorn.logging",
        "--clean", "--noconfirm",
    ]
    for ex in EXCLUDES:
        cmd += ["--exclude-module", ex]
    cmd.append("src/launch.py")
    print(">>", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)

    # 打包产物 → 规范命名的 zip
    if platform.system() == "Darwin":
        app = dist / "ALN-Uploader.app"
        if not app.exists():
            sys.exit("未找到 ALN-Uploader.app")
        out = dist / "aln-uploader-macos-arm64.zip"
        # ditto 保留符号链接（Python.framework 内部结构依赖 Current/ 链接；
        # shutil.make_archive 会把目录链接变成空目录 → 运行时 ModuleNotFoundError）
        subprocess.check_call(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(out)],
            cwd=ROOT,
        )
    else:
        exe = dist / "ALN-Uploader.exe"
        if not exe.exists():
            sys.exit("未找到 ALN-Uploader.exe")
        out = dist / "aln-uploader-windows-amd64.zip"
        shutil.make_archive(str(out.with_suffix("")), "zip", dist, "ALN-Uploader.exe")
    print("产物:", out)


if __name__ == "__main__":
    main()
'''

RELEASE_YML = '''name: release-installers

on:
  push:
    tags: ['v*']
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build:
    strategy:
      matrix:
        include:
          - os: macos-latest   # Apple Silicon
            artifact: aln-uploader-macos-arm64.zip
          - os: windows-latest
            artifact: aln-uploader-windows-amd64.zip
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r src/requirements.txt
      - name: 构建
        run: python build_uploader.py
      - uses: actions/upload-artifact@v4
        with:
          name: ${{ matrix.artifact }}
          path: dist/${{ matrix.artifact }}

  release:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          pattern: aln-uploader-*.zip
          merge-multiple: true
      - name: 发布 Release
        uses: softprops/action-gh-release@v2
        with:
          files: aln-uploader-*.zip
          generate_release_notes: true
'''

README = '''# aln-data-uploader

ALN 谐振器数据平台的**同事侧上传工具**：在本机把 snp 压缩包提取成参数分享包，
一键提交后网站（<https://bzjing925.github.io/aln-data-web/>）自动更新数据批次。

## 下载安装

到 [Releases](../../releases) 下载对应系统：

- macOS（Apple 芯片）：`aln-uploader-macos-arm64.zip` → 解压得 `ALN-Uploader.app`
  - 首次打开如提示"无法验证开发者"：右键 → 打开；或终端执行
    `xattr -d com.apple.quarantine /path/to/ALN-Uploader.app`
- Windows：`aln-uploader-windows-amd64.zip` → 解压得 `ALN-Uploader.exe`，双击运行

运行后浏览器自动打开上传页面（http://127.0.0.1:8630），关闭页面右上角"退出"即结束。

## 使用

1. **添加数据文件**：snp 压缩包（.zip）或散文件（.s1p/.s2p），可多个
2. **批次号**：默认取首个 zip 文件名；与网站现有批次不能重复（在线时自动查重）
3. **对照表**：默认列出让网站已有的对照表（选中即用，无需本地文件）；
   新对照表选"本地 xlsx/csv 文件"
4. 频率范围可空（全频段）；去嵌需要 zip 内含 OPEN/SHORT 校准件
5. 开始打包 → 生成 `分享包_<批次>.zip`
6. 提交：填入 GitHub token 一键创建 PR；没有 token 就把分享包发给管理员代传

## 开发者

- 源码由主仓库 `backend/scripts/dist_uploader.py` vendor 生成，勿直接改 `src/`
- 本机构建：`python build_uploader.py`（macOS 构建 mac 包，Windows 构建 Win 包）
- 发版：打 tag `v*` 推送，CI 自动构建双平台安装包并发 Release
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="目标目录（发布仓库工作区）")
    ap.add_argument("--check", action="store_true", help="只检查 vendor 后测试能否跑通")
    args = ap.parse_args()
    out = Path(args.out)
    src_dir = out / "src"

    if src_dir.exists():
        shutil.rmtree(src_dir)
    (src_dir / "app").mkdir(parents=True)

    for rel in VENDOR_FILES:
        src = PROJECT_ROOT / rel
        dst = src_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)

    # workers/__init__.py 原版会连带 celery —— vendor 为空包
    (src_dir / "app/workers/__init__.py").write_text("", encoding="utf-8")
    pipeline_init = src_dir / "app/workers/pipeline/__init__.py"
    if not pipeline_init.exists():
        pipeline_init.write_text("", encoding="utf-8")

    (src_dir / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
    (src_dir / "launch.py").write_text(LAUNCH, encoding="utf-8")
    (out / "build_uploader.py").write_text(BUILD_PY, encoding="utf-8")
    (out / "README.md").write_text(README, encoding="utf-8")
    wf = out / ".github/workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "release.yml").write_text(RELEASE_YML, encoding="utf-8")

    print(f"vendor 完成: {len(VENDOR_FILES)} 个文件 → {src_dir}")

    if args.check:
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'src'); "
             "from app.webui.uploader.server import create_app; create_app(); print('import ok')"],
            cwd=out, capture_output=True, text=True,
        )
        print(r.stdout.strip() or r.stderr.strip()[-500:])
        sys.exit(r.returncode)


if __name__ == "__main__":
    main()
