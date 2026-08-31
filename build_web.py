#!/usr/bin/env python3
"""静态网页版一键构建 + 部署（规格 §6）。

步骤：快照 → ONNX 导出校验 → VITE_STATIC=1 前端构建 → gh-pages 推送。
用法：
    backend/.venv/bin/python build_web.py              # 构建 + 部署
    backend/.venv/bin/python build_web.py --no-deploy  # 只构建
环境变量：
    ALN_WEB_REPO   部署仓库 URL（默认 https://github.com/bzJing925/aln-data-web.git）
    GITHUB_TOKEN   推送 token（只读环境变量，不落盘）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DEPLOY_DIR = ROOT / ".web-deploy"

DEFAULT_REPO = "https://github.com/bzJing925/aln-data-web.git"


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n>> {' '.join(str(c) for c in cmd)}")
    return subprocess.run(
        [str(c) for c in cmd], cwd=cwd, env=env, check=check,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true")
    args = ap.parse_args()
    py = BACKEND / ".venv" / "bin" / "python"
    python = str(py) if py.exists() else sys.executable

    print("=" * 60 + "\nStep 1: 数据快照\n" + "=" * 60)
    run([python, "scripts/export_web_snapshot.py"], cwd=BACKEND)

    print("=" * 60 + "\nStep 2: ONNX 导出 + 对齐校验\n" + "=" * 60)
    run([python, "scripts/export_surrogate_onnx.py"], cwd=BACKEND)

    print("=" * 60 + "\nStep 3: 前端静态构建\n" + "=" * 60)
    env = {**os.environ, "VITE_STATIC": "1"}
    run(["npx", "vite", "build"], cwd=FRONTEND, env=env)

    if args.no_deploy:
        print("\n构建完成（未部署）。本地预览：cd frontend/dist && python3 -m http.server 8642")
        return

    print("=" * 60 + "\nStep 4: 部署 gh-pages\n" + "=" * 60)
    repo = os.environ.get("ALN_WEB_REPO", DEFAULT_REPO)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("缺少 GITHUB_TOKEN 环境变量（只读使用，不会写入任何配置）")
    auth_repo = repo.replace("https://", f"https://x-access-token:{token}@")

    if not (DEPLOY_DIR / ".git").exists():
        if DEPLOY_DIR.exists():
            shutil.rmtree(DEPLOY_DIR)
        # gh-pages 分支可能尚不存在（新仓库）→ clone 失败时走 orphan 初始化
        r = run(["git", "clone", "--branch", "gh-pages", "--single-branch",
                 auth_repo, str(DEPLOY_DIR)], cwd=ROOT, check=False)
        if r.returncode != 0:
            print("gh-pages 分支不存在，初始化 orphan 分支")
            shutil.rmtree(DEPLOY_DIR, ignore_errors=True)
            DEPLOY_DIR.mkdir(parents=True)
            run(["git", "init"], cwd=DEPLOY_DIR)
            run(["git", "checkout", "--orphan", "gh-pages"], cwd=DEPLOY_DIR)
            run(["git", "config", "user.email",
                 os.environ.get("GIT_AUTHOR_EMAIL", "aln-web-deploy@local")], cwd=DEPLOY_DIR)
            run(["git", "config", "user.name",
                 os.environ.get("GIT_AUTHOR_NAME", "aln-web-deploy")], cwd=DEPLOY_DIR)

    # 清旧文件（保留 .git），拷新 dist
    for p in DEPLOY_DIR.iterdir():
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    shutil.copytree(FRONTEND / "dist", DEPLOY_DIR, dirs_exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    run(["git", "add", "-A"], cwd=DEPLOY_DIR)
    r = run(["git", "diff", "--cached", "--quiet"], cwd=DEPLOY_DIR, check=False)
    if r.returncode == 0:
        print("内容无变化，跳过提交")
    else:
        run(["git", "commit", "-m", f"deploy: 数据快照 {stamp}"], cwd=DEPLOY_DIR)
        run(["git", "push", auth_repo, "gh-pages:gh-pages"], cwd=DEPLOY_DIR)
    print("\n已部署。Pages 设置：仓库 Settings → Pages → Source 选 gh-pages 分支根目录")


if __name__ == "__main__":
    main()
