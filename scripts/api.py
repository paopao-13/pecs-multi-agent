"""
生产级 API 服务入口（FastAPI，async 版本）

相比 scripts/app.py 的 Flask demo，这里更贴近生产形态：
- GET  /health        存活探针（状态 + 运行时长 + LLM 配置就绪状态）
- GET  /metrics       基础可观测性指标（JSON；请求计数、各端点耗时直方图、错误数、真实 token 计量）
- GET  /metrics/prom  Prometheus 多进程指标端点（gunicorn -w N 下各 worker 经共享目录聚合，供外部 scrape）
- POST /run_task      执行单个任务，复用 graph.builder 的四角色编排

v0.4.0 改动（修复 HOL 阻塞）：
- 端点改为 async def，LLM 同步调用通过 loop.run_in_executor 卸载到线程池，
  避免单 worker 在处理长耗时任务时阻塞 /health 等其他请求。
- /run_task 增加超时保护（默认 120s），超时返回结构化错误而非无限挂起。

v0.4.1 改动（隔离 LLM 线程池）：
- 使用独立 ThreadPoolExecutor 跑 LLM 调用，与默认 executor 解耦，
  避免重耗时任务占满默认池导致轻量请求（如参数校验）排队（HOL 变体）。

v0.4.2 改动（可观测性增强）：
- /metrics 增加按 endpoint 分桶延迟（/health、/metrics、/run_task 的 p50/p95/p99 分别统计）。
- /metrics 增加 token 计量：累计真实 LLM 任务数与总 token 数（来自 LLM 网关 usage_metadata）。

v0.5.0 改动（启动自检 + 多 worker 指标正确性）：
- 启动自检（lifespan）：服务启动时即校验 LLM 配置（config.LLM_API_KEY 是否就绪）。
  /health 暴露 llm_configured 状态；/run_task 在 LLM 未配置时立即返回 503（fail-fast），
  而非等到首个任务才在图深处崩出难懂异常。
- 多 worker 指标正确性：新增 /metrics/prom —— 基于 prometheus_client 多进程模式
  （设置 PROMETHEUS_MULTIPROC_DIR 后，gunicorn -w N 各 worker 的计数器经共享目录聚合，
  外部 Prometheus 直接 scrape 该端点；避免原进程内字典在多线程/多进程下失真）。
  /metrics（JSON）保留为单 worker / 开发态便利端点（含按端点分桶延迟与真实 token 计量）。

本地启动（开发 / 单 worker）：
    uvicorn scripts.api:app --host 0.0.0.0 --port 8000

生产多 worker（指标正确聚合）：
    export PROMETHEUS_MULTIPROC_DIR=/tmp/pecs_prom
    gunicorn scripts.api:app -w 4 -b 0.0.0.0:8000 \
        --prometheus-dir $PROMETHEUS_MULTIPROC_DIR
    # 外部 Prometheus scrape http://host:8000/metrics/prom
"""

import os
import sys
import time
import asyncio
import concurrent.futures
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

# 确保项目根目录在 Python 路径中（与 scripts/app.py 保持一致）
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(_ROOT))

from config import DEFAULT_TOKEN_BUDGET, LLM_API_KEY  # noqa: E402

# /run_task 最长等待时间（秒），超时返回结构化错误，不无限挂起
RUN_TASK_TIMEOUT_S = float(os.getenv("PEC_RUN_TASK_TIMEOUT", "120"))

# 独立 LLM 执行线程池：避免重耗时 LLM 调用占用默认 executor，
# 导致轻量请求（如参数校验失败）排队等待（HOL 变体）。
_LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="pecs-llm")

# ---------- 启动自检状态（lifespan 填充）----------
_STARTUP: Dict[str, Any] = {
    "llm_configured": False,
    "llm_reason": "",
}

# ---------- Prometheus 多进程指标（多 worker 正确性）----------
# 若未安装 prometheus_client，则 /metrics/prom 降级为 501，JSON /metrics 不受影响。
_PROM_AVAILABLE = False
_PROM_REGISTRY = None
try:
    from prometheus_client import (  # noqa: E402
        Counter,
        Histogram,
        generate_latest,
        REGISTRY,
        CONTENT_TYPE_LATEST,
    )
    import prometheus_client.multiprocess as _mp_mod  # noqa: E402

    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - 缺依赖则降级
    Counter = Histogram = generate_latest = REGISTRY = CONTENT_TYPE_LATEST = None
    _mp_mod = None


def _init_prometheus() -> Any:
    """启用 Prometheus 多进程模式（gunicorn -w N 跨 worker 聚合）。

    设置 PROMETHEUS_MULTIPROC_DIR 后，各 worker 的计数器按 pid 写入共享目录，
    generate_latest 在 scrape 时聚合所有文件，从而避免进程内字典在多多进程下失真。
    """
    if not _PROM_AVAILABLE:
        return None
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        d = os.environ["PROMETHEUS_MULTIPROC_DIR"]
        os.makedirs(d, exist_ok=True)
        _mp_mod.MultiProcessCollector(REGISTRY)
    return REGISTRY


_PROM_REGISTRY = _init_prometheus()

# 全局 Prometheus 指标（多进程模式下按 pid 写入共享目录，scrape 时聚合）
if _PROM_AVAILABLE:
    REQ_TOTAL = Counter(
        "pecs_requests_total", "HTTP 请求总数", ["endpoint", "status"]
    )
    REQ_LATENCY = Histogram(
        "pecs_request_latency_seconds",
        "HTTP 请求延迟(秒)",
        ["endpoint"],
        buckets=[0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
    )
    LLM_TOKENS = Counter("pecs_llm_tokens_total", "LLM 消耗 token 总数")
    LLM_TASKS = Counter("pecs_llm_tasks_total", "LLM 任务执行总数")
else:  # pragma: no cover - 缺依赖时占位
    REQ_TOTAL = REQ_LATENCY = LLM_TOKENS = LLM_TASKS = None


def _cleanup_prometheus() -> None:
    """关闭时清理本进程在 PROMETHEUS_MULTIPROC_DIR 中的死进程指标文件。"""
    if _PROM_AVAILABLE and os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        try:
            _mp_mod.mark_process_dead(os.getpid())
        except Exception:
            pass


# ---------- 基础 metrics（进程内，开发/单 worker 便利端点，零外部依赖）----------
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

    # Prometheus（多进程安全：单进程 / gunicorn -w N 均正确）
    if _PROM_AVAILABLE:
        status = "error" if error else "ok"
        REQ_TOTAL.labels(endpoint=endpoint, status=status).inc()
        REQ_LATENCY.labels(endpoint=endpoint).observe(latency_ms / 1000.0)
        if tokens > 0:
            LLM_TOKENS.inc(tokens)
            LLM_TASKS.inc()


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
        # 诚实声明：JSON /metrics 来自进程内字典，仅供单 worker / 开发态查看；
        # 多 worker 生产部署请 scrape /metrics/prom（Prometheus 多进程聚合）。
        "deployment_note": (
            "single-worker / dev only — 多 worker 生产部署请 scrape /metrics/prom"
            if not os.environ.get("PROMETHEUS_MULTIPROC_DIR")
            else "multi-worker (PROMETHEUS_MULTIPROC_DIR set) — 仍以 /metrics/prom 为准"
        ),
    }


# ---------- 启动自检（lifespan）----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动即校验 LLM 配置（fail-fast 的源头：让配置缺失在启动期而非首个任务时才暴露）
    if LLM_API_KEY:
        _STARTUP["llm_configured"] = True
        _STARTUP["llm_reason"] = "LLM_API_KEY 已配置"
    else:
        _STARTUP["llm_configured"] = False
        _STARTUP["llm_reason"] = (
            "未配置 LLM_API_KEY，/run_task 将立即返回 503（/health、/metrics 仍正常工作）"
        )
    print("=" * 50)
    print("  PECS API 启动自检")
    print(f"  LLM 配置就绪: {_STARTUP['llm_configured']} — {_STARTUP['llm_reason']}")
    print(
        f"  Prometheus 多进程模式: "
        f"{'启用 (PROMETHEUS_MULTIPROC_DIR)' if os.environ.get('PROMETHEUS_MULTIPROC_DIR') else '未启用 (单 worker / 开发态)'}"
    )
    print("=" * 50)
    yield
    # 关闭：清理多进程指标死进程文件
    _cleanup_prometheus()


app = FastAPI(title="PECS Multi-Agent API", version="0.5.0", lifespan=lifespan)


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
        # 启动自检结果：让运维/探针一眼看清 LLM 是否就绪（不影响 /health 自身返回 200）
        "llm_configured": _STARTUP["llm_configured"],
        "ready": True,
    }
    _record("health", (time.time() - t0) * 1000.0)
    return out


@app.get("/metrics")
async def metrics() -> Dict[str, Any]:
    t0 = time.time()
    out = _summary()
    _record("metrics", (time.time() - t0) * 1000.0)
    return out


@app.get("/metrics/prom")
async def metrics_prom() -> Response:
    """Prometheus 多进程指标端点（生产多 worker 的 scrape target）。"""
    if not _PROM_AVAILABLE:
        raise HTTPException(status_code=501, detail="prometheus_client 未安装，/metrics/prom 不可用")

    data = generate_latest(_PROM_REGISTRY or REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.post("/run_task", response_model=RunTaskResponse)
async def run_task(req: RunTaskRequest) -> RunTaskResponse:
    # 输入校验优先于依赖可用性检查：即便 LLM 未配置，坏输入也应得到明确的 400/422，
    # 而不是被 503（依赖不可用）吞掉，便于上游正确区分“请求错误”与“服务不可用”
    if not req.query or not req.query.strip():
        _record("run_task", 0.0, error=True)
        raise HTTPException(status_code=400, detail="query 不能为空")

    # 启动自检未通过 → fail-fast 真正 503，让负载均衡/编排器正确摘流，
    # 而非在图深处崩出难懂异常（也避免空跑消耗线程池）
    if not _STARTUP["llm_configured"]:
        _record("run_task", 0.0, error=True)
        raise HTTPException(
            status_code=503,
            detail="LLM 未配置：缺少 LLM_API_KEY，/run_task 暂不可用。请配置后重启服务。",
        )

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
