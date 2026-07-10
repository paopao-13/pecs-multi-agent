# 监控告警方案

> pecs-multi-agent 生产环境的监控指标定义、采集方案和告警规则。

## 1. 监控架构

```
┌─────────────┐     metrics      ┌──────────────┐     alert      ┌─────────────┐
│  PECS App   │ ──────────────→  │  Prometheus  │ ────────────→  │  AlertManager│
│  (Flask)    │                  │  (指标存储)   │                │  (告警分发)  │
└─────────────┘                  └──────────────┘                └─────────────┘
       │                                                                │
       │ logs                                                           │ notify
       ▼                                                                ▼
┌─────────────┐                                                ┌─────────────┐
│  Gunicorn   │                                                │  邮件/钉钉   │
│  (访问日志)  │                                                │  Webhook    │
└─────────────┘                                                └─────────────┘
```

## 2. 核心监控指标

### 2.1 业务指标

| 指标名 | 类型 | 说明 | 采集方式 |
|--------|------|------|----------|
| `pecs_task_total` | Counter | 任务执行总数 | Flask after_request 钩子 |
| `pecs_task_success_total` | Counter | 任务成功数 | 同上 |
| `pecs_task_duration_seconds` | Histogram | 任务执行耗时 | time.time() 差值 |
| `pecs_token_used_per_task` | Histogram | 单任务 Token 消耗 | AgentState.token_used |
| `pecs_role_token_used` | Gauge | 各角色 Token 消耗 | AgentState.role_token_used |
| `pecs_budget_degrade_total` | Counter | 触发降级次数 | budget_events 统计 |
| `pecs_critic_reject_total` | Counter | Critic 拦截次数 | critic_scores < 4.0 |
| `pecs_reflection_triggered_total` | Counter | 反思循环触发次数 | AgentState.iteration > 1 |

### 2.2 系统指标

| 指标名 | 类型 | 说明 | 采集方式 |
|--------|------|------|----------|
| `flask_request_duration_seconds` | Histogram | HTTP 请求耗时 | Gunicorn access log |
| `flask_request_total` | Counter | HTTP 请求总数 | 同上 |
| `flask_request_errors_total` | Counter | HTTP 5xx 错误数 | 同上 |
| `process_cpu_seconds_total` | Counter | 进程 CPU 使用 | psutil |
| `process_memory_bytes` | Gauge | 进程内存占用 | psutil |

### 2.3 外部依赖指标

| 指标名 | 类型 | 说明 | 采集方式 |
|--------|------|------|----------|
| `deepseek_api_duration_seconds` | Histogram | DeepSeek API 调用耗时 | call_llm 包装器 |
| `deepseek_api_errors_total` | Counter | API 调用失败数 | 同上 |
| `deepseek_api_rate_limit_total` | Counter | 触发限流次数 | HTTP 429 响应 |

## 3. 指标暴露方案

### 3.1 Prometheus 指标暴露

```python
# 在 app.py 中添加 Prometheus 指标暴露
from prometheus_client import Counter, Histogram, Gauge, generate_latest

pecs_task_total = Counter('pecs_task_total', 'Total tasks executed')
pecs_task_duration = Histogram('pecs_task_duration_seconds', 'Task execution duration')
pecs_token_used = Histogram('pecs_token_used_per_task', 'Token usage per task',
                            buckets=[0, 50, 100, 500, 1000, 5000, 50000])

@app.route("/api/run_task", methods=["POST"])
def api_run_task():
    start_time = time.time()
    # ... 执行任务 ...
    duration = time.time() - start_time
    pecs_task_total.inc()
    pecs_task_duration.observe(duration)
    pecs_token_used.observe(final_state.get("token_used", 0))
    return jsonify(...)

@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {'Content-Type': 'text/plain'}
```

### 3.2 Prometheus 抓取配置

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'pecs-agent'
    scrape_interval: 15s
    static_configs:
      - targets: ['localhost:5000']
    metrics_path: '/metrics'
```

## 4. 告警规则

### 4.1 业务告警

```yaml
# alert_rules.yml
groups:
  - name: pecs_business
    rules:
      # 任务成功率低于 80%
      - alert: LowTaskSuccessRate
        expr: |
          rate(pecs_task_success_total[5m]) / rate(pecs_task_total[5m]) < 0.8
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "任务成功率低于 80%"
          description: "最近5分钟任务成功率为 {{ $value | humanizePercentage }}"

      # 单任务 Token 消耗超过预算的 90%
      - alert: HighTokenUsage
        expr: |
          histogram_quantile(0.95, pecs_token_used_per_task_bucket) > 45000
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "95分位 Token 消耗超过 45000"
          description: "P95 Token usage: {{ $value }}"

      # 降级频繁触发
      - alert: FrequentDegradation
        expr: |
          rate(pecs_budget_degrade_total[10m]) > 0.5
        for: 10m
        labels:
          severity: info
        annotations:
          summary: "降级机制频繁触发"
            description: "每分钟触发 {{ $value }} 次降级"
```

### 4.2 系统告警

```yaml
  - name: pecs_system
    rules:
      # HTTP 5xx 错误率
      - alert: HighErrorRate
        expr: |
          rate(flask_request_errors_total[5m]) / rate(flask_request_total[5m]) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "HTTP 5xx 错误率超过 5%"

      # API 调用超时
      - alert: DeepSeekApiTimeout
        expr: |
          histogram_quantile(0.95, deepseek_api_duration_seconds_bucket) > 30
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "DeepSeek API P95 延迟超过 30s"

      # 内存使用过高
      - alert: HighMemoryUsage
        expr: |
          process_memory_bytes > 1073741824
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "进程内存超过 1GB"
```

## 5. Grafana 仪表盘

建议创建以下仪表盘面板：

| 面板 | 指标 | 图表类型 |
|------|------|----------|
| 任务成功率 | `pecs_task_success_total / pecs_task_total` | Gauge |
| 任务延迟分布 | `pecs_task_duration_seconds` | Histogram |
| Token 消耗趋势 | `pecs_token_used_per_task` | Time series |
| 角色 Token 明细 | `pecs_role_token_used` | Stacked bar |
| 降级触发次数 | `pecs_budget_degrade_total` | Counter |
| Critic 拦截率 | `pecs_critic_reject_total / pecs_task_total` | Gauge |
| API 调用延迟 | `deepseek_api_duration_seconds` | Histogram |
| HTTP 错误率 | `flask_request_errors_total / flask_request_total` | Time series |

## 6. 当前状态

本项目当前为求职展示项目，未接入 Prometheus/Grafana。上述方案为生产环境部署时的参考设计。接入步骤：

1. `pip install prometheus-client`
2. 在 `app.py` 中添加指标暴露代码
3. 添加 `/metrics` 端点
4. 部署 Prometheus + Grafana
5. 导入仪表盘配置
