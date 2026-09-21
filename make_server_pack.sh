#!/usr/bin/env bash
# 内网 Windows 服务器部署包制作
# 在【能联网 + 有 Docker】的机器上运行（如装了 Docker Desktop 的本 Mac）。
# 产出：dist-server-pack/aln-server-pack-<日期>.zip —— 拷到内网服务器即用。
#
# 用法：
#   ./make_server_pack.sh                     # 全流程（frontend/dist 已存在则跳过前端构建）
#   ./make_server_pack.sh --rebuild-frontend  # 强制重建前端
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$ROOT/dist-server-pack"
PKG="$OUT/aln-server-pack"
PLATFORM="linux/amd64" # 服务器是 Windows x64；Apple Silicon 上构建必须显式指定

step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

command -v docker >/dev/null 2>&1 || { echo "错误：需要 Docker（请先安装并启动 Docker Desktop）"; exit 1; }
docker version >/dev/null 2>&1 || { echo "错误：Docker 未启动"; exit 1; }

# 1. 前端（离线服务器不需要 Node：dist 随包发布，bootstrap.ps1 只检查不构建）
if [[ "${1:-}" == "--rebuild-frontend" || ! -d "$ROOT/frontend/dist" ]]; then
  step "构建前端 frontend/dist"
  (cd "$ROOT/frontend" && npm install && npm run build)
else
  step "复用已有 frontend/dist（--rebuild-frontend 可强制重建）"
fi

# 2. 后端镜像：api/worker 共用同一 Dockerfile，构建一次打两个 tag。
#    两个 tag 指向同一镜像 ID，docker save 只存一份层，体积不加倍。
step "构建后端镜像 deploy-api / deploy-worker（${PLATFORM}）"
docker build --platform "$PLATFORM" -t deploy-api "$ROOT/backend"
docker tag deploy-api deploy-worker

# 3. 基础镜像
step "拉取基础镜像（${PLATFORM}）"
docker pull --platform "$PLATFORM" docker.io/library/postgres:15-alpine
docker pull --platform "$PLATFORM" docker.io/library/redis:7-alpine
docker pull --platform "$PLATFORM" docker.io/library/nginx:alpine

# 4. 导出镜像（包含 torch 的后端镜像较大，tar 通常数 GB，耐心等）
step "导出镜像 → aln-server-pack/aln-images.tar"
mkdir -p "$PKG"
docker save -o "$PKG/aln-images.tar" \
  deploy-api deploy-worker \
  docker.io/library/postgres:15-alpine \
  docker.io/library/redis:7-alpine \
  docker.io/library/nginx:alpine

# 5. 组装包目录（布局即仓库子集：bootstrap.ps1 按此相对结构工作）
step "组装包目录"
mkdir -p "$PKG/deploy" "$PKG/frontend"
cp "$ROOT/deploy/docker-compose.yml" "$PKG/deploy/"
rm -rf "$PKG/deploy/nginx" "$PKG/frontend/dist"
cp -r "$ROOT/deploy/nginx" "$PKG/deploy/nginx"
cp -r "$ROOT/frontend/dist" "$PKG/frontend/dist"
cp "$ROOT/bootstrap.ps1" "$PKG/"
cp "$ROOT/.env.example" "$PKG/"
cp "$ROOT/部署说明-内网服务器.txt" "$PKG/"
mkdir -p "$PKG/docs"
cp "$ROOT/docs/deployment-windows.md" "$PKG/docs/"

# 6. 打 zip（数 GB 压缩需要几分钟到十几分钟）
step "打 zip"
ZIP="aln-server-pack-$(date +%Y%m%d).zip"
rm -f "$OUT/$ZIP"
(cd "$PKG" && zip -qr "$OUT/$ZIP" .)

step "完成"
echo "部署包：$OUT/$ZIP"
du -h "$PKG/aln-images.tar" "$OUT/$ZIP"
echo
echo "把 zip 拷到内网 Windows 服务器后，按包内《部署说明-内网服务器.txt》操作。"
