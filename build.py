#!/usr/bin/env python3
"""
一键构建 ALN 桌面应用。

步骤：
1. 构建前端（npm run build）
2. PyInstaller 打包后端
3. Electron-builder 输出安装包

用法：
    python build.py
    python build.py --skip-backend   # 跳过 PyInstaller（开发测试）
    python build.py --target mac     # 仅 macOS
    python build.py --target win     # 仅 Windows
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"

# 国内镜像加速 Electron 二进制下载
os.environ.setdefault("ELECTRON_MIRROR", "https://npmmirror.com/mirrors/electron/")
os.environ.setdefault("ELECTRON_BUILDER_BINARIES_MIRROR", "https://npmmirror.com/mirrors/electron-builder-binaries/")


def run(cmd, cwd=None, env=None):
    print(f"\n>> {' '.join(str(c) for c in cmd)}")
    subprocess.check_call(cmd, cwd=cwd, env=env)


def main():
    parser = argparse.ArgumentParser(description="Build ALN desktop app")
    parser.add_argument("--skip-backend", action="store_true", help="Skip PyInstaller backend build")
    parser.add_argument("--target", choices=["win", "mac", "linux"], default=None, help="Target platform")
    args = parser.parse_args()

    # 1. 构建前端
    print("=" * 60)
    print("Step 1: 构建前端")
    print("=" * 60)
    run(["npm", "run", "build"], cwd=FRONTEND)

    # 2. 打包后端
    if not args.skip_backend:
        print("=" * 60)
        print("Step 2: PyInstaller 打包后端")
        print("=" * 60)
        run([sys.executable, "build_backend.py"], cwd=BACKEND)
    else:
        print("跳过后端打包（--skip-backend）")

    # 3. Electron-builder 打包
    print("=" * 60)
    print("Step 3: Electron-builder 打包")
    print("=" * 60)
    cmd = ["npx", "electron-builder"]
    if args.target:
        cmd.extend([f"--{args.target}"])
    run(cmd, cwd=FRONTEND)

    print("\n构建完成。输出目录: frontend/release/")


if __name__ == "__main__":
    main()
