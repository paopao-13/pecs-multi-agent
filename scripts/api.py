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

v0.6.0 改动（#4 全局限流 + #7 故障注入/混沌）：
- #4 令牌桶限流：新增 PEC_RATE_LIMIT_RPS / PEC_RATE_LIMIT_BURST（均为 0 关闭）。
  以 FastAPI Depends 形式应用于 /metrics、/metrics/prom、/run_task；
  /health 豁免（探针不能被限流误杀）。令牌桶进程内单 worker 语义，超限返回 429。
  计数器 RATE_LIMITED 暴露至 /metrics 与 Prometheus。
- #7 故障注入/混沌：PEC_CHAOS=1 + PEC_CHAOS_TOKEN 启用 /admin/chaos（GET 查看 / POST 切换模式）。
  支持 llm_down 模式：使 /run_task 返回结构化错误（非 500），验证依赖故障下优雅降级。
  任何传输层故障（畸形 JSON / 错误 Content-Type / 超大负载）均被结构化拒绝（400/422），绝不 panic。
  计数器 CHAOS_INJECTED 暴露至 /metrics 与 Prometheus。

v0.6.1 改动（依赖故障显式化，消除静默失败）：
- 启动自检由"检查是否配置了 key"升级为"真实探测凭据"（_probe_llm：GET /models，
  只看鉴权结论、不等模型生成）；凭据被拒（401/403）→ llm_configured=False，
  /health 同时暴露 llm_reason，/run_task 启动期即 fail-fast（503）。
  探测**未获结论**（超时/网络异常/端点不支持）时按"可用"放行 —— 理由：所用模型
  多为 reasoning 模型，单次生成实测 4.8~38.4s 剧烈波动，用固定超时等生成会把
  "模型只是慢"误判为"依赖不可用"（实测踩过）；而真不可用会由运行期 llm_error 上报。
  设 PEC_SKIP_LLM_PROBE=1 可跳过探测（离线/测试环境，退回"key 非空即就绪"）。
- /run_task 依赖故障显式化：当 LLM 调用失败且任务零步骤（空跑）时，返回
  success=False + 明确 error，而不再返回 success=True + 空答案掩盖故障。
  失败原因经 AgentState.llm_error 由 Planner/Synthesizer 上报。

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
from functools import partial
import asyncio
import concurrent.futures
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

# 确保项目根目录在 Python 路径中（与 scripts/app.py 保持一致）
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(_ROOT))

from config import (  # noqa: E402
    CHECKPOINT_DB,
    DEFAULT_TOKEN_BUDGET,
    LLM_API_KEY,
    LLM_BASE_URL,
    MAX_QUERY_CHARS,
    RUN_MODE,
)
from scripts.auth import assert_thread_owner, require_api_key  # noqa: E402

# /run_task 最长等待时间（秒），超时返回结构化错误，不无限挂起。
#
# 默认 120 → 300 的依据（2026-09-11 实测）：修复 Office 附件解析后，带附件的题
# 端到端实测 248.7s（.docx）/ 260.4s（.pptx），120s 会把刚修好的题**系统性判超时**，
# 把「能力不够」和「时间不够」混为一谈。300s 与 benchmarks 侧的单题超时默认值一致。
RUN_TASK_TIMEOUT_S = float(os.getenv("PEC_RUN_TASK_TIMEOUT", "300"))

# 启动自检真实探测 LLM 的超时（秒）。探测只打一次 GET /models（约 1s），
# 不依赖模型生成，因此这里给一个很短的上限即可。设 PEC_SKIP_LLM_PROBE=1 可跳过
# （离线/测试环境用；跳过时只要 key 非空即视为就绪，退回旧行为）。
LLM_PROBE_TIMEOUT_S = float(os.getenv("PEC_LLM_PROBE_TIMEOUT", "10"))

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
    RATE_LIMITED = Counter("pecs_rate_limited_total", "被限流(429)的请求总数", ["endpoint"])
    CHAOS_INJECTED = Counter("pecs_chaos_injected_total", "注入的混沌故障总数", ["mode"])
else:  # pragma: no cover - 缺依赖时占位
    REQ_TOTAL = REQ_LATENCY = LLM_TOKENS = LLM_TASKS = RATE_LIMITED = CHAOS_INJECTED = None


def _cleanup_prometheus() -> None:
    """关闭时清理本进程在 PROMETHEUS_MULTIPROC_DIR 中的死进程指标文件。"""
    if _PROM_AVAILABLE and os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        try:
            _mp_mod.mark_process_dead(os.getpid())
        except Exception:
            pass


# ---------- #4 全局限流（令牌桶）----------
# 进程内单 worker 语义：多 worker 部署时若需全局一致限流，应在网关/反向代理层做。
# PEC_RATE_LIMIT_RPS=0 或 PEC_RATE_LIMIT_BURST=0 表示关闭限流。
_RATE_LIMIT_RPS = float(os.getenv("PEC_RATE_LIMIT_RPS", "0"))
_RATE_LIMIT_BURST = float(os.getenv("PEC_RATE_LIMIT_BURST", "0"))
_RATE_LIMIT_ENABLED = _RATE_LIMIT_RPS > 0 and _RATE_LIMIT_BURST > 0


class _TokenBucket:
    """简单令牌桶：允许突发至 burst，长期速率受 rps 约束。线程安全。"""

    def __init__(self, rps: float, burst: float):
        self._rps = rps
        self._capacity = burst
        self._tokens = burst
        self._last = time.monotonic()
        self._lk = threading.Lock()

    def consume(self) -> bool:
        with self._lk:
            now = time.monotonic()
            # 补充令牌
            self._tokens = min(
                self._capacity, self._tokens + (now - self._last) * self._rps
            )
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


_RATE_BUCKETS: Dict[str, _TokenBucket] = {}

# 跨进程共享的限流状态（可选）：PEC_SHARED_STATE_DB 指向一个 SQLite 文件时启用，
# 让多 worker 部署共享同一份令牌桶。未设置时行为与改造前完全一致（进程内令牌桶）。
# 详见 tools/rate_store.py —— 那里说明了为什么不用 Redis（无环境）与何时该换。
_SHARED_STATE_DB = os.getenv("PEC_SHARED_STATE_DB", "")
_STATE_STORE = None
if _SHARED_STATE_DB:
    try:
        from tools.rate_store import StateStore

        _STATE_STORE = StateStore(_SHARED_STATE_DB)
    except Exception as exc:  # 导入或建库失败不应阻断启动（限流非核心路径）
        print(f"[启动] 共享限流状态初始化失败，回退到进程内令牌桶：{exc}")
        _STATE_STORE = None


def _get_bucket(endpoint: str) -> Optional[_TokenBucket]:
    if not _RATE_LIMIT_ENABLED:
        return None
    b = _RATE_BUCKETS.get(endpoint)
    if b is None:
        b = _TokenBucket(_RATE_LIMIT_RPS, _RATE_LIMIT_BURST)
        _RATE_BUCKETS[endpoint] = b
    return b


def _rate_limit_check(endpoint: str) -> None:
    """实际限流逻辑（同步依赖）：超限抛 429。"""
    # 共享状态模式（PEC_SHARED_STATE_DB 指定 SQLite 路径）：计数落在库里，
    # 多 worker / 多进程下全局生效。默认不启用 —— 单进程 uvicorn 用进程内令牌桶
    # 就够，启用它会给每个请求多一次写事务（实测 p99 0.67ms，见 tools/rate_store.py）。
    if _STATE_STORE is not None:
        if not _STATE_STORE.consume(endpoint, _RATE_LIMIT_RPS, _RATE_LIMIT_BURST):
            if _PROM_AVAILABLE:
                RATE_LIMITED.labels(endpoint=endpoint).inc()
            with _lock:
                _metrics.setdefault("rate_limited", {}).setdefault(endpoint, 0)
                _metrics["rate_limited"][endpoint] += 1
            raise HTTPException(
                status_code=429,
                detail=f"请求过于频繁（限流 {_RATE_LIMIT_RPS:g}/s，突发 {_RATE_LIMIT_BURST:g}）",
            )
        return

    bucket = _get_bucket(endpoint)
    if bucket is None:
        return
    if not bucket.consume():
        if _PROM_AVAILABLE:
            RATE_LIMITED.labels(endpoint=endpoint).inc()
        with _lock:
            _metrics.setdefault("rate_limited", {}).setdefault(endpoint, 0)
            _metrics["rate_limited"][endpoint] += 1
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁（限流 {_RATE_LIMIT_RPS:g}/s，突发 {_RATE_LIMIT_BURST:g}）",
        )


def _rate_limit_dep(endpoint: str) -> Any:
    """返回 FastAPI Depends（基于 functools.partial 的同步依赖）。/health 豁免。"""
    return Depends(partial(_rate_limit_check, endpoint))


# ---------- #7 故障注入 / 混沌状态 ----------
# 通过 /admin/chaos（需 PEC_CHAOS=1 + PEC_CHAOS_TOKEN）切换。生产默认关闭。
_CHAOS_ENABLED = os.getenv("PEC_CHAOS", "0") == "1"
_CHAOS_TOKEN = os.getenv("PEC_CHAOS_TOKEN", "")
_CHAOS_MODE: Optional[str] = None  # None / "llm_down"


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
    # 限流计数（按 endpoint）
    "rate_limited": {},
    # 当前混沌模式（None 表示未注入）
    "chaos_mode": None,
}


def _pct(sorted_vals: list, q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * q))
    return sorted_vals[idx]


def _record(endpoint: str, latency_ms: float, error: bool = False, tokens: int = 0, rate_limited: bool = False) -> None:
    with _lock:
        _metrics["total_requests"] += 1
        _metrics["by_endpoint"][endpoint] = _metrics["by_endpoint"].get(endpoint, 0) + 1
        if error:
            _metrics["errors"] += 1
        if rate_limited:
            _metrics["rate_limited"].setdefault(endpoint, 0)
            _metrics["rate_limited"][endpoint] += 1
            if _PROM_AVAILABLE:
                RATE_LIMITED.labels(endpoint=endpoint).inc()
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
        "rate_limit": {
            "enabled": _RATE_LIMIT_ENABLED,
            "rps": _RATE_LIMIT_RPS,
            "burst": _RATE_LIMIT_BURST,
            "rate_limited_by_endpoint": dict(_metrics["rate_limited"]),
        },
        "chaos_mode": _metrics["chaos_mode"],
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
def _probe_llm() -> tuple:
    """探测 LLM 凭据是否可用，返回 (status, reason)。

    status 取值与含义：
      - "ok"         : 凭据有效（HTTP 200）
      - "auth_error" : 凭据被拒（HTTP 401/403）——**确定性的配置错误**，应 fail-fast
      - "unknown"    : 未获结论（超时 / 网络异常 / 端点不支持 /models 等）

    为什么探测 GET /models 而不是真发一次对话：
      本项目常用的模型多为 **reasoning 模型**（先输出 reasoning_content 再输出
      content），实测单次「ping」耗时在 4.8s ~ 38.4s 之间剧烈波动。用固定超时去
      等一次对话完成，会把「模型只是慢」误判成「依赖不可用」，导致服务启动即
      503（实测踩过）。而鉴权结论是一次普通 GET，约 1s 且延迟稳定，
      既快速又准确，且完全不依赖模型生成能力。
    """
    import requests  # 局部导入：本函数只在启动期调用一次

    url = LLM_BASE_URL.rstrip("/") + "/models"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            timeout=LLM_PROBE_TIMEOUT_S,
        )
    except Exception as exc:  # 网络异常/超时均属「未获结论」，绝不因此阻断启动
        return "unknown", f"LLM 探测未获结论（{type(exc).__name__}: {exc}）"

    if resp.status_code == 200:
        return "ok", "LLM 凭据探测通过（GET /models 返回 200）"
    if resp.status_code in (401, 403):
        return "auth_error", f"LLM 凭据被拒（HTTP {resp.status_code}）：{resp.text[:200]}"
    return "unknown", f"LLM 探测未获结论（GET /models 返回 HTTP {resp.status_code}）"


def _resolve_startup_from_probe(status: str, reason: str) -> tuple:
    """把探测结论映射为启动自检状态 (llm_configured, llm_reason)。

    只有拿到**确定性的凭据错误**才判不就绪。理由：503 会把服务整体摘流，
    代价远高于「带着不确定先放行」——若放行后依赖真的不可用，任务失败会由
    AgentState.llm_error 在 /run_task 响应里显式说明（见 v0.6.1 运行期上报）。
    """
    if status == "ok":
        return True, reason
    if status == "auth_error":
        return False, reason
    return True, reason + "（探测未获结论，已按可用处理；若依赖实际不可用，任务会显式报错）"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动即校验 LLM 可用性（fail-fast 的源头：让配置/依赖问题在启动期而非首个任务时才暴露）
    if not LLM_API_KEY:
        _STARTUP["llm_configured"] = False
        _STARTUP["llm_reason"] = (
            "未配置 LLM_API_KEY，/run_task 将立即返回 503（/health、/metrics 仍正常工作）"
        )
    elif os.environ.get("PEC_SKIP_LLM_PROBE") == "1":
        _STARTUP["llm_configured"] = True
        _STARTUP["llm_reason"] = "已配置 LLM_API_KEY（PEC_SKIP_LLM_PROBE=1，跳过真实探测）"
    else:
        # 真实探测：key 已填但要确认真的能用（只看鉴权结论，不等模型生成）
        status, reason = "unknown", "LLM 探测未执行"
        try:
            loop = asyncio.get_running_loop()
            status, reason = await asyncio.wait_for(
                loop.run_in_executor(None, _probe_llm), timeout=LLM_PROBE_TIMEOUT_S + 5
            )
        except asyncio.TimeoutError:
            status, reason = "unknown", f"LLM 探测超时（>{LLM_PROBE_TIMEOUT_S:.0f}s）"
        ok, resolved = _resolve_startup_from_probe(status, reason)
        _STARTUP["llm_configured"] = ok
        _STARTUP["llm_reason"] = resolved
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


app = FastAPI(title="PECS Multi-Agent API", version="0.6.1", lifespan=lifespan)


def _ensure_db_dir(db_path: str) -> None:
    """确保检查点文件所在目录存在。

    CHECKPOINT_DB 可能是裸文件名（PEC_CHECKPOINT_DB 覆盖时 dirname 为空串），
    此时无需建目录——直接 os.makedirs("") 会抛 FileNotFoundError。
    """
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


# ---------- 同步执行体（在线程池中跑，避免阻塞事件循环）----------
def _execute_graph(query: str, token_budget: int, thread_id: Optional[str] = None) -> Dict[str, Any]:
    """在 worker 线程中运行四角色图（同步阻塞调用）。

    传入 thread_id 时启用 SQLite 检查点持久化（复用 graph/builder 的既有能力），
    使该任务可被 /api/replay/{thread_id} 回放或断点续跑；不传则与改造前一致。
    """
    from graph.builder import build_graph, create_initial_state  # 延迟导入
    from metrics.cost_attribution import attribute_cost  # 延迟导入

    initial_state = create_initial_state(query, token_budget)

    if thread_id:
        from langgraph.checkpoint.sqlite import SqliteSaver

        _ensure_db_dir(CHECKPOINT_DB)
        with SqliteSaver.from_conn_string(CHECKPOINT_DB) as saver:
            compiled_graph = build_graph(token_budget, checkpointer=saver)
            final_state = compiled_graph.invoke(
                initial_state, {"configurable": {"thread_id": thread_id}}
            )
    else:
        compiled_graph = build_graph(token_budget)
        final_state = compiled_graph.invoke(initial_state)

    return {
        "final_answer": final_state.get("final_answer", ""),
        "token_used": final_state.get("token_used", 0),
        "step_count": final_state.get("step_count", 0),
        # LLM 依赖失败原因（None = 无失败）：供 run_task 显式区分
        # “任务本身无解” 与 “依赖故障导致空跑”，避免静默失败
        "llm_error": final_state.get("llm_error"),
        # 成本归因：直接复用已有的 role_token_used / budget_events / results，不新增埋点
        "cost_report": attribute_cost(final_state),
    }


# ---------- 请求模型 ----------
class RunTaskRequest(BaseModel):
    query: str
    token_budget: Optional[int] = DEFAULT_TOKEN_BUDGET
    # 可选：传入则持久化到 SQLite 检查点，之后可用 /api/replay/{thread_id} 回放
    thread_id: Optional[str] = None


class RunTaskResponse(BaseModel):
    success: bool
    query: str
    final_answer: str = ""
    token_used: int = 0
    token_budget: int = 0
    steps: int = 0
    # 成本归因报告（按角色/工具/轮次拆分）；失败时为空
    cost_report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


_EMPTY_QUERY_MSG = "query 不能为空"


def _validate_query(query: str) -> Optional[str]:
    """入口输入校验，返回错误说明；无问题返回 None。

    两类问题分别对应不同 HTTP 状态码，由调用方据返回值判定：
      - 空 / 全空白  → 400（_EMPTY_QUERY_MSG）
      - 超过长度上限 → 413（上限含端点：len(query) > MAX_QUERY_CHARS 才拒绝，
                           即最多接受 MAX_QUERY_CHARS 个字符）

    上限默认 4000（原为 10000，实测 10000 字符会完整跑一遍四角色图、耗时 30s+，
    4 个这样的请求即可占满全部 worker —— 详见 config.py 的注释）。
    它挡的是「合法但极耗」的输入：这类输入之前的保护形同虚设。

    校验发生在 LLM 可用性检查之前：坏输入即使 LLM 未配置也应得到明确的
    请求错误，而不是被 503（依赖不可用）吞掉。
    """
    if query is None or not query.strip():
        return _EMPTY_QUERY_MSG
    if len(query) > MAX_QUERY_CHARS:
        return f"query 过长：{len(query)} 字符，超过上限 {MAX_QUERY_CHARS} 字符"
    return None


# ---------- 端点 ----------
@app.get("/health")
async def health() -> Dict[str, Any]:
    t0 = time.time()
    out = {
        "status": "ok",
        "uptime_seconds": round(time.time() - _metrics["start_time"], 1),
        # 启动自检结果：让运维/探针一眼看清 LLM 是否就绪（不影响 /health 自身返回 200）
        "llm_configured": _STARTUP["llm_configured"],
        # 未就绪的具体原因（如 key 失效 / 服务不可达 / 未配置），便于运维定位
        "llm_reason": _STARTUP["llm_reason"],
        # 当前运行模式（eval / business）：business 会打开工具加固
        "run_mode": RUN_MODE,
        # 限流状态是否跨进程共享（多 worker 下生效的前提）
        "shared_state": bool(_STATE_STORE),
        "ready": True,
    }
    _record("health", (time.time() - t0) * 1000.0)
    return out


@app.get("/metrics", dependencies=[_rate_limit_dep("metrics")])
async def metrics() -> Dict[str, Any]:
    t0 = time.time()
    out = _summary()
    _record("metrics", (time.time() - t0) * 1000.0)
    return out


@app.get("/metrics/prom", dependencies=[_rate_limit_dep("metrics_prom")])
async def metrics_prom() -> Response:
    """Prometheus 多进程指标端点（生产多 worker 的 scrape target）。"""
    if not _PROM_AVAILABLE:
        raise HTTPException(status_code=501, detail="prometheus_client 未安装，/metrics/prom 不可用")

    data = generate_latest(_PROM_REGISTRY or REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.post("/run_task", response_model=RunTaskResponse, dependencies=[_rate_limit_dep("run_task")])
async def run_task(
    req: RunTaskRequest,
    tenant: str = Depends(require_api_key),
) -> RunTaskResponse:
    # 输入校验优先于依赖可用性检查：即便 LLM 未配置，坏输入也应得到明确的 400/413，
    # 而不是被 503（依赖不可用）吞掉，便于上游正确区分"请求错误"与"服务不可用"
    validation_error = _validate_query(req.query)
    if validation_error:
        _record("run_task", 0.0, error=True)
        raise HTTPException(
            status_code=400 if validation_error == _EMPTY_QUERY_MSG else 413,
            detail=validation_error,
        )

    # 租户归属校验：传入 thread_id 时必须属于当前租户，否则 404（不泄露存在性）。
    # 放在输入校验之后、LLM 可用性检查之前——越权是请求错误，不该被 503 掩盖。
    if req.thread_id:
        assert_thread_owner(req.thread_id, tenant)

    # 启动自检未通过 → fail-fast 真正 503，让负载均衡/编排器正确摘流，
    # 而非在图深处崩出难懂异常（也避免空跑消耗线程池）
    if not _STARTUP["llm_configured"]:
        _record("run_task", 0.0, error=True)
        raise HTTPException(
            status_code=503,
            detail=(
                f"LLM 依赖未就绪，/run_task 暂不可用：{_STARTUP['llm_reason']}。"
                "请在 /health 查看 llm_reason，配置有效凭据后重启服务。"
            ),
        )

    # #7 故障注入：llm_down 模式模拟下游 LLM 层故障 → 结构化错误（非 500），验证优雅降级
    if _CHAOS_MODE == "llm_down":
        _record("run_task", 0.0)
        with _lock:
            _metrics["chaos_mode"] = _CHAOS_MODE
        if _PROM_AVAILABLE:
            CHAOS_INJECTED.labels(mode="llm_down").inc()
        return RunTaskResponse(
            success=False,
            query=req.query,
            error="[CHAOS] 模拟 LLM 层故障：下游推理服务不可达（依赖故障被隔离，服务未崩溃）",
        )

    loop = asyncio.get_event_loop()
    t0 = time.time()
    try:
        # 在独立 LLM 线程池中执行同步图调用，释放事件循环且不与默认池争用
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _LLM_EXECUTOR, _execute_graph, req.query, req.token_budget, req.thread_id
            ),
            timeout=RUN_TASK_TIMEOUT_S,
        )
        latency = (time.time() - t0) * 1000.0
        step_count = result["step_count"]
        llm_error = result.get("llm_error")

        # 依赖故障显式化：LLM 调用失败且任务未产出任何步骤 → 这是"空跑"而非"任务完成"。
        # 既有实现会返回 success=True + 空答案，掩盖真实故障；此处改为明确的失败响应。
        if llm_error and step_count == 0:
            _record("run_task", latency, error=True)
            return RunTaskResponse(
                success=False,
                query=req.query,
                token_used=result["token_used"],
                token_budget=req.token_budget,
                steps=0,
                cost_report=result.get("cost_report"),
                error=f"LLM 依赖失败，任务未执行：{llm_error}",
            )

        _record("run_task", latency, tokens=result["token_used"])
        return RunTaskResponse(
            success=True,
            query=req.query,
            final_answer=result["final_answer"],
            token_used=result["token_used"],
            token_budget=req.token_budget,
            steps=step_count,
            cost_report=result.get("cost_report"),
        )
    except asyncio.TimeoutError:
        latency = (time.time() - t0) * 1000.0
        _record("run_task", latency, error=True)
        return RunTaskResponse(success=False, query=req.query, error=f"超时（>{RUN_TASK_TIMEOUT_S:.0f}s）")
    except Exception as exc:  # noqa: BLE001 - 生产服务需吞掉异常返回结构化错误
        latency = (time.time() - t0) * 1000.0
        _record("run_task", latency, error=True)
        return RunTaskResponse(success=False, query=req.query, error=str(exc))


# ---------- 链路回放（复用已持久化的检查点，不重跑图）----------
@app.get("/api/replay/{thread_id}", dependencies=[_rate_limit_dep("replay")])
async def replay(
    thread_id: str,
    tenant: str = Depends(require_api_key),
) -> Dict[str, Any]:
    """回放某个 thread_id 的完整执行链路。

    数据来源全部是既有资产：SQLite 检查点（graph/builder）+ GraphTraceLogger
    + 成本归因，本端点只做「读取并暴露」，不重新执行任务（避免二次计费与副作用）。
    """
    # 越权回放会读到别人的任务内容（query / 结果 / token），必须归属校验
    assert_thread_owner(thread_id, tenant)

    from graph.builder import load_task_state
    from logger.graph_trace_logger import GraphTraceLogger
    from metrics.cost_attribution import attribute_cost

    if not os.path.exists(CHECKPOINT_DB):
        raise HTTPException(
            status_code=404,
            detail="暂无检查点文件：需在调用 /run_task 时传入 thread_id 才会持久化",
        )

    state = load_task_state(thread_id, CHECKPOINT_DB)
    if not state:
        raise HTTPException(status_code=404, detail=f"未找到 thread_id={thread_id} 的检查点")

    return {
        "thread_id": thread_id,
        "state": {
            "query": state.get("query", ""),
            "final_answer": state.get("final_answer", ""),
            "token_used": state.get("token_used", 0),
            "token_budget": state.get("token_budget", 0),
            "step_count": state.get("step_count", 0),
            "iterations": state.get("iteration", 0),
            "plan": state.get("plan", []),
            "results": state.get("results", []),
            "critic_scores": state.get("critic_scores", []),
        },
        "cost_report": attribute_cost(state),
        "trace_markdown": GraphTraceLogger(verbose=False).build_trace(state),
    }


# ---------- #7 故障注入 / 混沌管理端点 ----------
@app.get("/admin/chaos")
async def chaos_get() -> Dict[str, Any]:
    """查看当前混沌状态。未启用混沌时返回 404，避免暴露内部能力面。"""
    if not _CHAOS_ENABLED:
        raise HTTPException(status_code=404, detail="混沌测试未启用（设置 PEC_CHAOS=1 启动）")
    return {
        "enabled": _CHAOS_ENABLED,
        "token_required": bool(_CHAOS_TOKEN),
        "current_mode": _CHAOS_MODE,
        "available_modes": [None, "llm_down"],
    }


@app.post("/admin/chaos")
async def chaos_post(req: Request) -> Dict[str, Any]:
    """切换混沌模式。需 PEC_CHAOS=1 且（若设了 PEC_CHAOS_TOKEN）携带正确 token（header X-Chaos-Token）。"""
    if not _CHAOS_ENABLED:
        raise HTTPException(status_code=404, detail="混沌测试未启用（设置 PEC_CHAOS=1 启动）")
    # token 校验：若设置了 PEC_CHAOS_TOKEN，必须从 header 携带正确值
    if _CHAOS_TOKEN:
        provided = req.headers.get("X-Chaos-Token", "")
        if provided != _CHAOS_TOKEN:
            raise HTTPException(status_code=403, detail="混沌 token 错误（header X-Chaos-Token）")
    # 解析 body 中的 mode（兼容空 body → 关闭）
    try:
        body = await req.json()
        mode = body.get("mode") if isinstance(body, dict) else None
    except Exception:
        mode = None
    # 校验模式合法性
    if mode not in (None, "llm_down"):
        raise HTTPException(status_code=400, detail=f"未知混沌模式: {mode!r}（仅支持 null / 'llm_down'）")
    global _CHAOS_MODE
    _CHAOS_MODE = mode
    with _lock:
        _metrics["chaos_mode"] = _CHAOS_MODE
    if _PROM_AVAILABLE and _CHAOS_MODE:
        CHAOS_INJECTED.labels(mode=_CHAOS_MODE).inc()
    return {"enabled": _CHAOS_ENABLED, "current_mode": _CHAOS_MODE}
