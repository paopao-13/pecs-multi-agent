# PECS 服务镜像
#
# 2026-09-11 修正：旧版指向 Flask 演示应用（app:app / 5000 端口 / /api/gaia_samples），
# 与真实服务（scripts/api.py 的 FastAPI，8000 端口）完全脱节 —— 容器能起来但
# 健康检查必失败，等于部署件一直是坏的。现全部对齐 FastAPI 服务。
#
# 构建：docker build -t pecs:local .
# 运行：docker run --rm -p 8000:8000 pecs:local
# 验证：curl -sf localhost:8000/health

FROM python:3.11-slim

WORKDIR /app

# 编译型依赖（部分包无预编译 wheel 时需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# 依赖锁定优先：requirements-lock.txt 是精确版本快照，保证构建可复现。
# requirements.txt 只声明顶层依赖的下界，单独用它会在上游发新版时构建出不同结果。
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt

# 复制项目文件（大体积的 webshop/、data/、results/ 由 .dockerignore 排除）
COPY . .

# 真实服务端口（与 scripts/api.py 的 --port 8000 一致）
EXPOSE 8000

# 健康检查打 /health —— 该端点始终返回 200，并用 llm_configured 单独暴露依赖状态，
# 因此"进程存活"与"依赖就绪"是两个可区分的信号，探针不会被依赖故障误杀。
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# 启动命令
# 单 worker 用 uvicorn 即可（lock 中已含）；如需多 worker：
#   1) 在同环境 pip install gunicorn 后重新生成 requirements-lock.txt
#   2) 设置 PROMETHEUS_MULTIPROC_DIR 让各 worker 的指标能聚合
#   3) CMD ["gunicorn", "scripts.api:app", "-w", "4", "-b", "0.0.0.0:8000",
#           "--timeout", "300", "--prometheus-dir", "/tmp/pecs_prom"]
# 注意 --timeout 必须 ≥ 300：附件类任务实测 248~260s，120s 会系统性误杀。
CMD ["uvicorn", "scripts.api:app", "--host", "0.0.0.0", "--port", "8000"]
