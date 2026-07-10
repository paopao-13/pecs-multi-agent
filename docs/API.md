# API 接口文档

> pecs-multi-agent Flask Web 应用 API 接口说明。

## 基础信息

| 项 | 值 |
|----|-----|
| Base URL | `http://127.0.0.1:5000` |
| 请求格式 | `application/json` |
| 响应格式 | `application/json` |
| 认证 | 无（本地开发环境） |

## 接口列表

### 1. 获取页面

```
GET /
```

返回 Web 界面 HTML 页面（三个 Tab：任务执行、GAIA 评估、对比测试）。

---

### 2. 执行单个任务（PECS 框架）

```
POST /api/run_task
```

**请求体：**

```json
{
    "query": "计算2的100次方",
    "token_budget": 50000
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `query` | string | 是 | - | 用户问题 |
| `token_budget` | int | 否 | 50000 | Token 预算上限 |

**响应体（成功）：**

```json
{
    "success": true,
    "query": "计算2的100次方",
    "final_answer": "1267650600228229401496703205376",
    "token_used": 0,
    "token_budget": 50000,
    "iteration": 1,
    "plan": [
        {"id": 1, "action": "python", "description": "计算2的100次方", "status": "done"}
    ],
    "results": [
        {"step_id": 1, "action": "python", "result": "1267650600228229401496703205376", "success": true}
    ],
    "critic_scores": [],
    "role_token_used": {"planner": 0, "executor": 0, "critic": 0, "synthesizer": 0},
    "budget_events": [],
    "scheduler_decisions": [],
    "logs": [
        "[Planner] 启发式匹配命中: 计算类任务",
        "[Executor] 执行步骤1: python",
        "[Synthesizer] 抽取式综合完成"
    ]
}
```

**响应体（失败）：**

```json
{
    "success": false,
    "error": "错误信息"
}
```

---

### 3. 执行 ReAct 基线任务

```
POST /api/run_react
```

**请求体：**

```json
{
    "query": "计算2的100次方",
    "token_budget": 50000
}
```

**响应体：**

```json
{
    "success": true,
    "query": "计算2的100次方",
    "final_answer": "...",
    "token_used": 402,
    "token_budget": 50000,
    "logs": ["..."],
    "steps": ["..."]
}
```

---

### 4. GAIA 评测

```
POST /api/eval_gaia
```

**请求体：**

```json
{
    "num_samples": 10,
    "agent_type": "multi_agent"
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `num_samples` | int | 否 | 10 | 评测题目数量（最大28） |
| `agent_type` | string | 否 | `multi_agent` | `multi_agent` 或 `react` |

**响应体：**

```json
{
    "success": true,
    "accuracy": 1.0,
    "correct_count": 10,
    "total_samples": 10,
    "total_tokens": 530,
    "avg_tokens": 53,
    "details": [
        {
            "task_id": "gaia_001",
            "question": "...",
            "correct": true,
            "token_used": 53,
            "final_answer": "..."
        }
    ]
}
```

---

### 5. WebShop 评测

```
POST /api/eval_webshop
```

**请求体：**

```json
{
    "num_samples": 6,
    "agent_type": "multi_agent"
}
```

**响应体：**

```json
{
    "success": true,
    "mode": "mock",
    "success_rate": 1.0,
    "success_count": 6,
    "total_samples": 6,
    "total_tokens": 318,
    "avg_tokens": 53,
    "details": [...]
}
```

---

### 6. 生成目标报告

```
POST /api/target_report
```

**请求体：**

```json
{
    "num_gaia": 5,
    "num_webshop": 6
}
```

**响应体：**

```json
{
    "success": true,
    "report": {
        "gaia": {"accuracy": 1.0, "correct": 5, "total": 5},
        "webshop": {"success_rate": 1.0, "success": 6, "total": 6},
        "token_comparison": {"pecs": 265, "react": 2010, "reduction": "86.8%"}
    }
}
```

---

### 7. 获取 GAIA 样例列表

```
GET /api/gaia_samples
```

**响应体：**

```json
{
    "samples": [
        {"task_id": "gaia_001", "question": "...", "level": 1},
        {"task_id": "gaia_002", "question": "...", "level": 1}
    ]
}
```

---

## 错误码

| HTTP 状态码 | 含义 | 场景 |
|:-----------:|------|------|
| 200 | 成功 | 正常请求 |
| 400 | 请求错误 | `query` 为空 |
| 500 | 服务器错误 | LLM 调用失败、沙箱执行异常 |

## 速率限制

本地开发环境无速率限制。生产环境建议通过 Nginx 配置：

```nginx
limit_req_zone $binary_remote_addr zone=pecs_api:10m rate=10r/m;

location /api/ {
    limit_req zone=pecs_api burst=5 nodelay;
    proxy_pass http://pecs_backend;
}
```

## 使用示例

### Python requests

```python
import requests

# 执行单个任务
resp = requests.post("http://127.0.0.1:5000/api/run_task", json={
    "query": "计算2的100次方",
    "token_budget": 50000
})
print(resp.json()["final_answer"])

# GAIA 评测
resp = requests.post("http://127.0.0.1:5000/api/eval_gaia", json={
    "num_samples": 5,
    "agent_type": "multi_agent"
})
print(f"准确率: {resp.json()['accuracy']}")
```

### curl

```bash
# 执行任务
curl -X POST http://127.0.0.1:5000/api/run_task \
    -H "Content-Type: application/json" \
    -d '{"query": "计算2的100次方"}'

# GAIA 评测
curl -X POST http://127.0.0.1:5000/api/eval_gaia \
    -H "Content-Type: application/json" \
    -d '{"num_samples": 5, "agent_type": "multi_agent"}'
```
