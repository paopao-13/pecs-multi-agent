# 生产部署方案

> 本文档描述 pecs-multi-agent 的容器化部署、环境配置、健康检查和扩展方案。

## 1. Docker 容器化部署

### 1.1 Dockerfile

项目根目录已包含 `Dockerfile`：

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 暴露端口
EXPOSE 5000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/gaia_samples')" || exit 1

# 启动命令
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "--timeout", "120", "app:app"]
```

### 1.2 构建与运行

```bash
# 构建镜像
docker build -t pecs-multi-agent:1.0 .

# 运行容器（注入 API Key）
docker run -d \
    --name pecs-agent \
    -p 5000:5000 \
    -e DEEPSEEK_API_KEY=your_api_key_here \
    --restart unless-stopped \
    pecs-multi-agent:1.0

# 查看日志
docker logs -f pecs-agent

# 进入容器调试
docker exec -it pecs-agent bash
```

### 1.3 docker-compose.yml（可选）

```yaml
version: '3.8'

services:
  pecs-agent:
    build: .
    container_name: pecs-agent
    ports:
      - "5000:5000"
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - FLASK_DEBUG=false
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/gaia_samples')"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '1.0'
```

## 2. 环境变量配置

| 变量名 | 必填 | 默认值 | 说明 |
|--------|:---:|--------|------|
| `DEEPSEEK_API_KEY` | 是 | 空 | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com/v1` | API 基础地址 |
| `FLASK_HOST` | 否 | `127.0.0.1` | Flask 监听地址 |
| `FLASK_PORT` | 否 | `5000` | Flask 监听端口 |
| `FLASK_DEBUG` | 否 | `true` | 调试模式（生产环境设 false） |

**生产环境配置示例：**

```bash
# .env.production
DEEPSEEK_API_KEY=sk-your-production-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=false
```

## 3. 健康检查

### 3.1 应用层健康检查

```bash
# 基础健康检查（返回 GAIA 样例列表，验证应用启动）
curl http://localhost:5000/api/gaia_samples

# 预期响应：200 + JSON {"samples": [...]}
```

### 3.2 容器层健康检查

Dockerfile 中已配置 `HEALTHCHECK`，Docker 会每 30 秒检查一次应用可用性。

```bash
# 查看容器健康状态
docker inspect --format='{{.State.Health.Status}}' pecs-agent

# 预期输出：healthy
```

## 4. 日志收集

### 4.1 Gunicorn 访问日志

```bash
# 启动时开启访问日志
gunicorn -w 4 -b 0.0.0.0:5000 \
    --access-logfile /var/log/pecs/access.log \
    --error-logfile /var/log/pecs/error.log \
    --timeout 120 \
    app:app
```

### 4.2 应用日志

PECS 框架的执行日志存储在 `AgentState.logs` 字段中，每次任务执行的完整日志通过 API 响应返回。如需持久化：

```python
# 在 app.py 中添加日志持久化
import logging
logging.basicConfig(
    filename='/var/log/pecs/agent.log',
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

### 4.3 全链路日志导出

```python
from logger.graph_trace_logger import export_task_trace
from graph.builder import run_task

result = run_task("计算2的100次方")
export_task_trace(result)  # 自动保存到 results/traces/
```

## 5. 扩展方案

### 5.1 垂直扩展（单机）

```bash
# 增加 Gunicorn worker 数量（建议 CPU 核数 × 2 + 1）
gunicorn -w 9 -b 0.0.0.0:5000 --timeout 120 app:app

# 增加 worker 连接数
gunicorn -w 4 --worker-connections 1000 -b 0.0.0.0:5000 app:app
```

### 5.2 水平扩展（多机）

```
                    ┌─────────────┐
                    │  Nginx LB   │
                    │  (反向代理)  │
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           │               │               │
    ┌──────┴──────┐ ┌──────┴──────┐ ┌──────┴──────┐
    │  App Server │ │  App Server │ │  App Server │
    │  (Worker 1) │ │  (Worker 2) │ │  (Worker 3) │
    └─────────────┘ └─────────────┘ └─────────────┘
           │               │               │
           └───────────────┼───────────────┘
                           │
                    ┌──────┴──────┐
                    │ DeepSeek API│
                    │  (外部服务)  │
                    └─────────────┘
```

**Nginx 配置示例：**

```nginx
upstream pecs_backend {
    server 10.0.0.1:5000 weight=1;
    server 10.0.0.2:5000 weight=1;
    server 10.0.0.3:5000 weight=1;
}

server {
    listen 80;
    server_name agent.example.com;

    location / {
        proxy_pass http://pecs_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_timeout 120s;
    }
}
```

### 5.3 状态共享（Redis）

多实例部署时，如需共享任务状态（如批量评测进度），可引入 Redis：

```python
# 未来扩展：Redis 状态共享
import redis
r = redis.Redis(host='redis', port=6379, db=0)

# 存储任务状态
r.set(f"task:{task_id}", json.dumps(state))
```

**当前状态：** 单机部署足够，Redis 为未来扩展预留方案。

## 6. 部署检查清单

- [ ] Dockerfile 构建成功
- [ ] 容器启动后健康检查通过
- [ ] `DEEPSEEK_API_KEY` 环境变量已注入
- [ ] `FLASK_DEBUG=false`（生产环境关闭调试）
- [ ] Gunicorn worker 数量合理（CPU×2+1）
- [ ] 超时时间 ≥120s（LLM 调用可能较慢）
- [ ] 日志文件路径可写
- [ ] 端口映射正确（5000:5000）
- [ ] 重启策略设置为 `unless-stopped`
