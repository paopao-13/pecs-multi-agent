@echo off
REM ============================================================================
REM 真实 WebShop 环境一键部署（Windows / Docker Desktop）
REM 前置：已安装 Docker Desktop 并启动（状态栏鲸鱼图标变绿），见 docs/docker_install_windows.md
REM 说明：真实 WebShop 文本环境是进程内的（需 Java + 商品索引），容器里会跑我们的
REM       HTTP 桥（tools/webshop_server.py），PECS 通过 WEBSHOP_SERVER_URL 连 8000 端口。
REM ============================================================================
cd /d %~dp0
set WS_REPO=https://github.com/ai-nikolai/WebShop.git
set WS_DIR=webshop-src

echo [0/4] 检查 Docker 是否运行...
docker info >nul 2>&1
if errorlevel 1 (
  echo [错误] Docker 未运行。请先启动 Docker Desktop，等鲸鱼图标变绿后再试。
  pause
  exit /b 1
)

if not exist %WS_DIR% (
  echo [1/4] 克隆带 Dockerfile 的 WebShop fork（%WS_REPO%）...
  git clone %WS_REPO% %WS_DIR%
  if errorlevel 1 (
    echo [失败] clone 失败，请检查网络或手动 clone 到 %WS_DIR% 目录。
    pause
    exit /b 1
  )
) else (
  echo [1/4] 已存在 %WS_DIR%，跳过 clone。
)

echo [2/4] 构建镜像（首次约 10-30 分钟，需联网下载商品数据 + 构建索引）...
docker compose build
if errorlevel 1 (
  echo [失败] 镜像构建失败。常见原因：内存不足 / 网络中断 / Docker 未启动。
  echo        可尝试在 Docker Desktop 里调大资源（内存 ^>=8GB），或改用 conda 原生路线（见文档）。
  pause
  exit /b 1
)

echo [3/4] 启动容器（HTTP 桥 :8000 + HTML 预览 :3000）...
docker compose up -d
if errorlevel 1 (
  echo [失败] 容器启动失败，可能端口被占用或上次容器未清理。
  echo        尝试：docker compose down 后重试。
  pause
  exit /b 1
)

echo [4/4] 等待服务就绪（约 25 秒）...
timeout /t 25 /nobreak >nul
curl -s -o nul -w "HTTP %{http_code}\n" http://localhost:8000/health >nul 2>&1 && (
  echo [OK] WebShop HTTP 桥已在 http://localhost:8000 运行
) || (
  echo [注意] 端口未立即响应，服务可能仍在启动。用 "docker compose logs webshop" 查看进度。
)

echo.
echo ===================== 接下来在 PECS 项目里 =====================
echo 设置环境变量后运行评测（注意端口是 8000，不是 3000）：
echo   set WEBSHOP_SERVER_URL=http://localhost:8000
echo   python run_resumable.py webshop_001
echo.
echo 停止：docker compose down
echo 查看日志：docker compose logs -f webshop
echo ===============================================================
pause
