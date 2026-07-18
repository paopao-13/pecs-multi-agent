#!/usr/bin/env bash
# 真实 WebShop 环境一键部署（Linux / WSL / macOS / 云服务器）
# 前置：已安装 Docker 且 daemon 在运行（docker info 无报错）
set -e
cd "$(dirname "$0")"

WS_REPO="https://github.com/ai-nikolai/WebShop.git"
WS_DIR="webshop-src"

echo "[0/4] 检查 Docker 是否运行..."
if ! docker info >/dev/null 2>&1; then
  echo "[错误] Docker 未运行，请先启动 docker daemon。"
  exit 1
fi

if [ ! -d "$WS_DIR" ]; then
  echo "[1/4] clone 带 Dockerfile 的 WebShop fork ($WS_REPO) ..."
  git clone "$WS_REPO" "$WS_DIR"
else
  echo "[1/4] 已存在 $WS_DIR，跳过 clone。"
fi

echo "[2/4] 构建镜像（首次约 10-30 分钟，需联网下载商品数据 + 构建索引）..."
docker compose build

echo "[3/4] 启动容器（HTTP 桥 :8000 + HTML 预览 :3000）..."
docker compose up -d

echo "[4/4] 等待服务就绪..."
sleep 25
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health | grep -q 200; then
  echo "[OK] WebShop HTTP 桥已在 http://localhost:8000 运行"
else
  echo "[注意] 端口未立即响应，服务可能仍在启动，用 'docker compose logs webshop' 查看。"
fi

echo
echo "接下来在 PECS 项目里设置环境变量并运行评测（端口是 8000）："
echo "  export WEBSHOP_SERVER_URL=http://localhost:8000"
echo "  python run_resumable.py webshop_001"
