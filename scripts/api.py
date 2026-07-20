"""
生产级 API 服务入口（FastAPI，async 版本）

相比 scripts/app.py 的 Flask demo，这里更贴近生产形态：
- GET  /health   存活探针（状态 + 运行时长 + 累计请求数）
- GET  /metrics  基础可观测性指标（请求计数、各端点耗时直方图、错误数）
- POST /run_task 执行单个任务，复用 graph.builder 的四角色编排

v0.4.0 改动（修复 HOL 阻塞）：
- 端点改为 async def，LLM 同步调用通过 loop.run_in_executor 卸载到线程池，
  避免单 worker 在处理长耗时任务时阻塞 /health 等其他请求。
- /run_task 增加超时保护（默认 120s），超时返回结构化错误而非无限挂起。

v0.4.1 改动（隔离 LLM 线程池）：
- 使用独立 ThreadPoolExecutor 跑 LLM 调用，与默认 executor 解耦，
  避免重耗时任务占满默认池导致轻量请求（如参数校验）排队（HOL 变体）。

v0.4.2 改动（可观测性增强）：
- /metrics 增加按 endpoint 分桶延迟（/health、/metrics、/run_task 的 p50/p95/p99 分别统计，
  不再把 2ms 探针与 5s 任务混算导致 P95 失真）。
- /metrics 增加 token 计量：累计真实 LLM 任务数与总 token 数（数据来自 LLM 网关 usage_metadata）。

本地启动：
    uvicorn scripts.api:app --host 0.0.0.0 --port 8000
"""

import os
import sys
import time
import asyncio
import concurrent.futures
import threading
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# 确保项目根目录在 Python 路径中（与 scripts/app.py 保持一致）
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(_ROOT))

from config import DEFAULT_TOKEN_BUDGET  # noqa: E402

app = FastAPI(title="PECS Multi-Agent API", version="0.4.2")

# /run_task 最长等待时间（秒），超时返回结构化错误，不无限挂起
RUN_TASK_TIMEOUT_S = float(os.getenv("PEC_RUN_TASK_TIMEOUT", "120"))

# 独立 LLM 执行线程池：避免重耗时 LLM 调用占用默认 executor，
# 导致轻量请求（如参数校验失败）排队等待（HOL 变体）。
_LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="pecs-llm")

# /run_task 最长等待时间（秒），超时返回结构化错误，不无限挂起
RUN_TASK_TIMEOUT_S = float(os.getenv("PEC_RUN_TASK_TIMEOUT", "120"))

# ---------- 基础 metrics（进程内，零外部依赖，便于演示可观测性）----------
_lock = threading.Lock()
_metrics: Dict[str, Any] = {
    "start_time": time.time(),
    "total_requests": 0,
    "errors": 0,
    "by_endpoint": {},
    # 按 endpoint 分桶的延迟样本（毫秒），避免 /health(2ms) 与 /run_task(5s) 混算失真
    "latency_by_endpoint": {},
    # Token 计量：仅统计真实 LLM 任务（/run_task 成功），数据来自 LLM 网关 usage_metadata
    "llm_tasks": 0,
    "total_tokens": 0,
}


def _pct(sorted_vals: list, q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * q))
    return sorted_vals[idx]


def _record(endpoint: str, latency_ms: float, error: bool = False, tokens: int = 0) -> None:
    with _lock:
        _metrics["total_requests"] += 1
        _metrics["by_endpoint"][endpoint] = _metrics["by_endpoint"].get(endpoint, 0) + 1
        if error:
            _metrics["errors"] += 1
        bucket = _metrics["latency_by_endpoint"].setdefault(endpoint, [])
        bucket.append(latency_ms)
        if len(bucket) > 1000:
            _metrics["latency_by_endpoint"][endpoint] = bucket[-1000:]
        if tokens > 0:
            _metrics["llm_tasks"] += 1
            _metrics["total_tokens"] += tokens


def _endpoint_stats(endpoint: str) -> Dict[str, Any]:
    with _lock:
        vals = sorted(_metrics["latency_by_endpoint"].get(endpoint, []))
    if not vals:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0, "samples": 0}
    return {
        "p50": round(_pct(vals, 0.50), 2),
        "p95": round(_pct(vals, 0.95), 2),
        "p99": round(_pct(vals, 0.99), 2),
        "avg": round(sum(vals) / len(vals), 2),
        "samples": len(vals),
    }


def _summary() -> Dict[str, Any]:
    with _lock:
        by_ep = dict(_metrics["by_endpoint"])
        llm_tasks = _metrics["llm_tasks"]
        total_tokens = _metrics["total_tokens"]
    return {
        "total_requests": _metrics["total_requests"],
        "errors": _metrics["errors"],
        "by_endpoint": by_ep,
        "latency_by_endpoint_ms": {ep: _endpoint_stats(ep) for ep in by_ep},
        "tokens": {
            "llm_tasks": llm_tasks,
            "total_tokens": total_tokens,
            "avg_tokens_per_task": round(total_tokens / llm_tasks, 1) if llm_tasks else 0,
        },
        "uptime_seconds": round(time.time() - _metrics["start_time"], 1),
    }


# ---------- 同步执行体（在线程池中跑，避免阻塞事件循环）----------
def _execute_graph(query: str, token_budget: int) -> Dict[str, Any]:
    """在 worker 线程中运行四角色图（同步阻塞调用）。"""
    from graph.builder import build_graph, create_initial_state  # 延迟导入

    compiled_graph = build_graph(token_budget)
    initial_state = create_initial_state(query, token_budget)
    final_state = compiled_graph.invoke(initial_state)
    return {
        "final_answer": final_state.get("final_answer", ""),
        "token_used": final_state.get("token_used", 0),
        "step_count": final_state.get("step_count", 0),
    }


# ---------- 请求模型 ----------
class RunTaskRequest(BaseModel):
    query: str
    token_budget: Optional[int] = DEFAULT_TOKEN_BUDGET


class RunTaskResponse(BaseModel):
    success: bool
    query: str
    final_answer: str = ""
    token_used: int = 0
    token_budget: int = 0
    steps: int = 0
    error: Optional[str] = None


# ---------- 端点 ----------
@app.get("/health")
async def health() -> Dict[str, Any]:
    t0 = time.time()
    out = {
        "status": "ok",
        "uptime_seconds": round(time.time() - _metrics["start_time"], 1),
    }
    _record("health", (time.time() - t0) * 1000.0)
    return out


@app.get("/metrics")
async def metrics() -> Dict[str, Any]:
    t0 = time.time()
    out = _summary()
    _record("metrics", (time.time() - t0) * 1000.0)
    return out


@app.post("/run_task", response_model=RunTaskResponse)
async def run_task(req: RunTaskRequest) -> RunTaskResponse:
    if not req.query or not req.query.strip():
        _record("run_task", 0.0, error=True)
        raise HTTPException(status_code=400, detail="query 不能为空")

    loop = asyncio.get_event_loop()
    t0 = time.time()
    try:
        # 在独立 LLM 线程池中执行同步图调用，释放事件循环且不与默认池争用
        result = await asyncio.wait_for(
            loop.run_in_executor(_LLM_EXECUTOR, _execute_graph, req.query, req.token_budget),
            timeout=RUN_TASK_TIMEOUT_S,
        )
        latency = (time.time() - t0) * 1000.0
        _record("run_task", latency, tokens=result["token_used"])
        return RunTaskResponse(
            success=True,
            query=req.query,
            final_answer=result["final_answer"],
            token_used=result["token_used"],
            token_budget=req.token_budget,
            steps=result["step_count"],
        )
    except asyncio.TimeoutError:
        latency = (time.time() - t0) * 1000.0
        _record("run_task", latency, error=True)
        return RunTaskResponse(success=False, query=req.query, error=f"超时（>{RUN_TASK_TIMEOUT_S:.0f}s）")
    except Exception as exc:  # noqa: BLE001 - 生产服务需吞掉异常返回结构化错误
        latency = (time.time() - t0) * 1000.0
        _record("run_task", latency, error=True)
        return RunTaskResponse(success=False, query=req.query, error=str(exc))
