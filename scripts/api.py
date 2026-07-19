"""
生产级 API 服务入口（FastAPI）

相比 scripts/app.py 的 Flask demo，这里更贴近生产形态：
- GET  /health   存活探针（状态 + 运行时长 + 累计请求数）
- GET  /metrics  基础可观测性指标（请求计数、各端点耗时直方图、错误数）
- POST /run_task 执行单个任务，复用 graph.builder 的四角色编排

本地启动：
    uvicorn scripts.api:app --host 0.0.0.0 --port 8000
"""

import os
import sys
import time
import threading
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# 确保项目根目录在 Python 路径中（与 scripts/app.py 保持一致）
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(_ROOT))

from config import DEFAULT_TOKEN_BUDGET  # noqa: E402

app = FastAPI(title="PECS Multi-Agent API", version="0.3.0")

# ---------- 基础 metrics（进程内，零外部依赖，便于演示可观测性）----------
_lock = threading.Lock()
_metrics: Dict[str, Any] = {
    "start_time": time.time(),
    "total_requests": 0,
    "errors": 0,
    "latency_ms": [],
    "by_endpoint": {},
}


def _record(endpoint: str, latency_ms: float, error: bool = False) -> None:
    with _lock:
        _metrics["total_requests"] += 1
        _metrics["by_endpoint"][endpoint] = _metrics["by_endpoint"].get(endpoint, 0) + 1
        if error:
            _metrics["errors"] += 1
        else:
            _metrics["latency_ms"].append(latency_ms)
            if len(_metrics["latency_ms"]) > 1000:
                _metrics["latency_ms"] = _metrics["latency_ms"][-1000:]


def _summary() -> Dict[str, Any]:
    with _lock:
        lat = list(_metrics["latency_ms"])
    if lat:
        lat_sorted = sorted(lat)
        p50 = lat_sorted[len(lat_sorted) // 2]
        p95 = lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95))]
        avg = sum(lat) / len(lat)
    else:
        p50 = p95 = avg = 0.0
    return {
        "total_requests": _metrics["total_requests"],
        "errors": _metrics["errors"],
        "by_endpoint": dict(_metrics["by_endpoint"]),
        "latency_ms": {
            "avg": round(avg, 2),
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "samples": len(lat),
        },
        "uptime_seconds": round(time.time() - _metrics["start_time"], 1),
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
def health() -> Dict[str, Any]:
    _record("health", 0.0)
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _metrics["start_time"], 1),
    }


@app.get("/metrics")
def metrics() -> Dict[str, Any]:
    _record("metrics", 0.0)
    return _summary()


@app.post("/run_task", response_model=RunTaskResponse)
def run_task(req: RunTaskRequest) -> RunTaskResponse:
    if not req.query:
        _record("run_task", 0.0, error=True)
        raise HTTPException(status_code=400, detail="query 不能为空")
    t0 = time.time()
    try:
        # 延迟导入：无 API Key 时也能起服务，/health 不依赖 LLM 链路
        from graph.builder import build_graph, create_initial_state

        compiled_graph = build_graph(req.token_budget)
        initial_state = create_initial_state(req.query, req.token_budget)
        final_state = compiled_graph.invoke(initial_state)

        latency = (time.time() - t0) * 1000.0
        _record("run_task", latency)
        return RunTaskResponse(
            success=True,
            query=req.query,
            final_answer=final_state.get("final_answer", ""),
            token_used=final_state.get("token_used", 0),
            token_budget=req.token_budget,
            steps=final_state.get("step_count", 0),
        )
    except Exception as exc:  # noqa: BLE001 - 生产服务需吞掉异常返回结构化错误
        latency = (time.time() - t0) * 1000.0
        _record("run_task", latency, error=True)
        return RunTaskResponse(success=False, query=req.query, error=str(exc))
