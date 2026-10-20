#!/usr/bin/env bash
# 免 Docker 内网服务器一键安装包（NSIS .exe）制作
# 输入：dist-server-pack/aln-server-lite/（由 make_server_pack_lite.sh 生成）
# 产出：dist-server-pack/ALN-Server-Setup-<版本>.exe（+ .sha256）
#
# 用法：
#   ./make_server_installer.sh                  # 用现有 lite 目录打包
#   ./make_server_installer.sh --rebuild-lite   # 先重打 lite（含前端重建）再打包
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$ROOT/dist-server-pack"
LITE="$OUT/aln-server-lite"
ASSETS="$OUT/installer-assets"
STAGE="$OUT/installer-stage"
VERSION="$(python3 -c "import json;print(json.load(open('$ROOT/frontend/package.json'))['version'])")"
PYVER="3.12.10"
PYEXE="python-${PYVER}-amd64.exe"
NSIS_VERSION="3.12"
NSIS_TAG="nsis@2.0.1"

step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

if [[ "${1:-}" == "--rebuild-lite" ]]; then
  step "重打 lite 包（make_server_pack_lite.sh --rebuild-frontend）"
  "$ROOT/make_server_pack_lite.sh" --rebuild-frontend
fi
[[ -d "$LITE/wheelhouse" ]] || { echo "缺少 $LITE/wheelhouse —— 先运行 make_server_pack_lite.sh"; exit 1; }

# 1. Python Windows 安装包（华为云镜像）
if [[ ! -f "$ASSETS/$PYEXE" ]]; then
  step "下载 $PYEXE"
  mkdir -p "$ASSETS"
  curl -sL -o "$ASSETS/$PYEXE" "https://mirrors.huaweicloud.com/python/${PYVER}/${PYEXE}"
fi

# 2. NSIS 工具链（electron-builder-binaries 的 nsis-bundle，含 mac 版 makensis）
if [[ ! -x "$ASSETS/nsis-bundle/makensis" ]]; then
  step "下载 NSIS 工具链（$NSIS_TAG）"
  mkdir -p "$ASSETS"
  gh release download "$NSIS_TAG" --repo electron-userland/electron-builder-binaries \
    --pattern "nsis-bundle-${NSIS_VERSION}.tar.gz" --dir "$ASSETS" --clobber
  tar xzf "$ASSETS/nsis-bundle-${NSIS_VERSION}.tar.gz" -C "$ASSETS"
fi

# 3. 组装
step "组装 installer-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/pack" "$STAGE/assets"
cp -R "$LITE/." "$STAGE/pack/"
cp "$ASSETS/$PYEXE" "$STAGE/assets/$PYEXE"
cp "$ROOT/frontend/build-resources/icon.ico" "$STAGE/pack/icon.ico"
# ps1 统一加 UTF-8 BOM（Windows PowerShell 5.1 无 BOM 会把中文按 GBK 误读导致解析失败）
for f in "$STAGE"/pack/*.ps1; do
  [ -f "$f" ] || continue
  [ "$(head -c 3 "$f" | xxd -p)" = "efbbbf" ] || { printf '\xef\xbb\xbf' | cat - "$f" > /tmp/ps1bom && mv /tmp/ps1bom "$f"; }
done
sed "s/!define APP_VERSION   \".*\"/!define APP_VERSION   \"$VERSION\"/" \
  "$ROOT/deploy/installer/server-installer.nsi" > "$STAGE/server-installer.nsi"

# 4. 编译
step "makensis 编译"
(cd "$STAGE" && "$ASSETS/nsis-bundle/makensis" server-installer.nsi)
mv "$STAGE/ALN-Server-Setup-${VERSION}.exe" "$OUT/"
shasum -a 256 "$OUT/ALN-Server-Setup-${VERSION}.exe" > "$OUT/ALN-Server-Setup-${VERSION}.exe.sha256"

step "完成"
ls -lh "$OUT/ALN-Server-Setup-${VERSION}.exe"
echo "校验：把 exe 拷到 Windows 机器双击安装；/S 可静默安装。"
