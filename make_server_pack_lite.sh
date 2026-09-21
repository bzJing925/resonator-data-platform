#!/usr/bin/env bash
# 原生模式（免 Docker）内网 Windows 服务器部署包制作
# 在【能访问 PyPI】的机器上运行（wheel 下载走 PyPI，不需要 Docker）。
# 产出：dist-server-pack/aln-server-lite-<日期>.zip
#
# 已知裁剪（见各步注释）：zipfile-deflate64（无 win wheel，代码已改可选导入）、
# uvloop（unix 专用；pip download 的 marker 按宿主平台评估会误拉）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$ROOT/dist-server-pack"
PKG="$OUT/aln-server-lite"
REQS="$OUT/requirements-server.txt"
PIP="$ROOT/backend/.venv/bin/python"
WIN_PLATFORM="win_amd64"
WIN_PYVER="3.12"

step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

# 0. 前端（已有 dist 则跳过；--rebuild-frontend 强制重建）
if [[ "${1:-}" == "--rebuild-frontend" || ! -d "$ROOT/frontend/dist" ]]; then
  step "构建前端"
  (cd "$ROOT/frontend" && npm install && npm run build)
fi

# 1. 依赖清单
step "导出依赖清单"
(cd "$ROOT/backend" && uv export --frozen --no-dev --no-hashes --no-emit-project -o "$REQS")
# zipfile-deflate64 全平台无 wheel（纯源码，import 已可选化）；uvloop 无 win 版
sed -i '' -e '/^zipfile-deflate64==/d' -e '/^uvloop==/d' "$REQS"

# 2. 离线 wheelhouse
step "下载 Windows amd64 wheels（需要访问 PyPI）"
"$PIP" -m ensurepip 2>/dev/null || true   # uv 创建的 venv 默认无 pip
"$PIP" -m pip download -r "$REQS" --platform "$WIN_PLATFORM" --python-version "$WIN_PYVER" \
  --only-binary=:all: -d "$PKG/wheelhouse" --quiet
# pip download 的 marker 按宿主平台评估，win32 侧必需的会被漏掉，显式补：
"$PIP" -m pip download colorama greenlet --platform "$WIN_PLATFORM" --python-version "$WIN_PYVER" \
  --only-binary=:all: -d "$PKG/wheelhouse" --quiet

# 3. 组装
step "组装包目录"
rm -rf "$PKG/backend" "$PKG/frontend"
mkdir -p "$PKG/backend" "$PKG/frontend" "$PKG/python"
cp -r "$ROOT/backend/app" "$PKG/backend/app"
cp -r "$ROOT/backend/alembic" "$PKG/backend/alembic"
cp "$ROOT/backend/alembic.ini" "$PKG/backend/"
find "$PKG/backend" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$PKG/backend" -name "*.pyc" -delete
cp -r "$ROOT/frontend/dist" "$PKG/frontend/dist"
cp "$REQS" "$PKG/requirements-server.txt"
cp "$ROOT/deploy/native/"* "$PKG/"
# Python 安装包需手动补（办公网过滤 exe）：保留占位说明
cat > "$PKG/python/PUT-PYTHON-INSTALLER-HERE.txt" <<'EOF'
把 Python 3.12 的 Windows 安装包放到本目录并重命名为 python-3.12-amd64.exe
下载页：https://www.python.org/downloads/release/python-31212/
找 "Windows installer (64-bit)"。
（办公网过滤 exe 下载，请用热点或其他网络下载。）
EOF

# 4. 打 zip
step "打 zip"
ZIP="aln-server-lite-$(date +%Y%m%d).zip"
rm -f "$OUT/$ZIP"
(cd "$PKG" && zip -qr "$OUT/$ZIP" .)

step "完成"
echo "部署包：$OUT/$ZIP"
du -sh "$PKG/wheelhouse" "$OUT/$ZIP"
echo "注意：python/python-3.12-amd64.exe 需手动补拷（见包内占位说明）。"
