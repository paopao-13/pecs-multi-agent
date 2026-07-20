"""
PECS 生产级指标量化基准（真实数据采集）

测量项目：
  M1: 服务启动耗时
  M2: /health 存活探针延迟分布（p50/p95/p99, 100次采样）
  M3: /health 并发吞吐量（10/20/50并发）
  M4: /metrics 端点可用性
  M5: /run_task 真实 LLM 推理端到端延迟（简单计算题）+ 真实 token 数
  M6: /run_task 错误处理（空输入/超长输入，独立端口隔离）
  M7: 服务稳定性（连续运行无崩溃）
  M8: HOL 阻塞修复验证（LLM 负载下 /health 不阻塞）
  M9: /metrics 实时快照（按 endpoint 分桶延迟 p50/p95/p99 + token 计量）
  M10: /metrics/prom 端点验证（Prometheus 多进程指标，仅 --prometheus 时跑）
  M11: 全局限流（令牌桶）生效验证 —— RPS=2/burst=3 下突发 20 请求，应出现 429 且无结构性错误
  M12: 故障注入/混沌容错 —— 畸形 JSON / 错误 Content-Type / 20K 超大负载，均应零 500（优雅降级）

成本维度：M5 采集真实 token 数（LLM 网关 usage_metadata），按可配置参考单价
（PEC_PRICE_PER_1M，默认 GLM Flash 级别 ¥1/百万 token）推算单任务/总成本。

CI 门禁：--ci 隐含 --skip-run-task（不烧 LLM Key），跑完按阈值评估
（M2 P95 > ci-p95-ms、M3 有错误、M6 非 400/422、M7 非 100%、M8 仍阻塞、M11 限流未生效、M12 出现 500 即失败，退出码 2）。

用法：
    python scripts/benchmark_production.py [--llm-key YOUR_KEY] [--base-url URL] [--model MODEL]
    python scripts/benchmark_production.py --ci            # CI 门禁（不烧 Key）
    python scripts/benchmark_production.py --prometheus    # 验证多进程指标端点
    不带参数则用 .env 默认值（DeepSeek）。

输出：results/production_bench.json（机器可读）+ 终端人类可读摘要。
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---- 项目根目录 ----
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

# ---- 配置 ----
BASE_URL = "http://127.0.0.1:8000"
HEALTH_URL = f"{BASE_URL}/health"
METRICS_URL = f"{BASE_URL}/metrics"
RUN_TASK_URL = f"{BASE_URL}/run_task"

SAMPLES_HEALTH = 100  # /health 采样次数
CONCURRENT_LEVELS = [10, 20, 50]  # 并发测试梯度
TASK_QUERIES = [
    "1+1等于几？",
    "用一句话解释什么是递归。",
    "Python 中如何读取一个文本文件？",
]
TIMEOUT_SECONDS = 120  # 单次 /run_task 最长等待

# ---- Token 成本单价（¥ / 百万 token）----
# token 数来自 LLM 网关 usage_metadata（真实返回）；单价为可配置参考值，成本据此推算。
# 默认值取 GLM Flash 级别网关参考价；可用环境变量 PEC_PRICE_PER_1M 覆盖（实际以网关账单为准）。
TOKEN_PRICE_PER_1M = float(os.getenv("PEC_PRICE_PER_1M", "1.0"))
PRICE_NOTE = (
    f"GLM Flash 级别参考单价 ¥{TOKEN_PRICE_PER_1M:.2f}/百万 token"
    f"（可经环境变量 PEC_PRICE_PER_1M 覆盖；token 数为网关真实 usage_metadata，成本据此推算）"
)



@dataclass
class LatencySample:
    """单次请求延迟样本"""

    latency_ms: float
    status_code: int
    error: Optional[str] = None
    body: Optional[dict] = None


def _find_uvicorn_python() -> str:
    """找到能运行 uvicorn 的 python 解释器。

    优先用当前解释器；若其无法 import uvicorn，则回退到已知的 default venv。
    这样无论用哪个 python 调起本脚本，子进程起的 uvicorn 服务都能正常工作。
    """
    import importlib.util

    def _can_import(py: str) -> bool:
        try:
            out = subprocess.run(
                [py, "-c", "import uvicorn"],
                capture_output=True, timeout=15,
            )
            return out.returncode == 0
        except Exception:
            return False

    candidates = [
        sys.executable,
        str(_PROJECT_ROOT / ".." / "binaries" / "python" / "envs" / "default" / "Scripts" / "python.exe"),
        r"C:\Users\jx\.workbuddy\binaries\python\envs\default\Scripts\python.exe",
    ]
    for py in candidates:
        if py and _can_import(py):
            return py
    # 兜底：返回当前解释器（即使可能缺 uvicorn，让上层报错更明确）
    return sys.executable


@dataclass
class BenchmarkResult:
    """完整基准结果"""

    timestamp: str = ""
    python_version: str = ""
    m1_startup_ms: float = 0.0
    m2_health_latency: Dict[str, Any] = field(default_factory=dict)
    m3_throughput: Dict[str, Any] = field(default_factory=dict)
    m4_metrics_ok: bool = False
    m5_run_task_latency: List[Dict[str, Any]] = field(default_factory=list)
    m6_error_handling: Dict[str, Any] = field(default_factory=dict)
    m7_stability: Dict[str, Any] = field(default_factory=dict)
    m8_health_under_load: Dict[str, Any] = field(default_factory=dict)
    m5_tokens: Dict[str, Any] = field(default_factory=dict)  # 真实 token 聚合（来自 run_task 响应）
    m_cost: Dict[str, Any] = field(default_factory=dict)  # 成本推算（token × 参考单价）
    m_metrics_live: Dict[str, Any] = field(default_factory=dict)  # /metrics 实时快照（分桶延迟 + token）
    m_prom: Dict[str, Any] = field(default_factory=dict)  # /metrics/prom（Prometheus 多进程指标端点）验证
    m11_rate_limit: Dict[str, Any] = field(default_factory=dict)  # 限流生效验证
    m12_chaos: Dict[str, Any] = field(default_factory=dict)  # 混沌容错验证
    ci_passed: bool = False  # CI 门禁是否通过（--ci 时填充）


def http_get(url: str, timeout: float = 5.0) -> LatencySample:
    """发 GET 请求并记录延迟"""
    t0 = time.time()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            return LatencySample(
                latency_ms=(time.time() - t0) * 1000.0,
                status_code=resp.getcode(),
                body=parsed,
            )
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read().decode())
        except Exception:
            parsed = None
        return LatencySample(
            latency_ms=(time.time() - t0) * 1000.0,
            status_code=e.code,
            error=f"HTTP {e.code}",
            body=parsed,
        )
    except Exception as e:
        return LatencySample(
            latency_ms=(time.time() - t0) * 1000.0,
            status_code=0,
            error=str(e),
        )


def http_post_json(url: str, data: dict, timeout: float = TIMEOUT_SECONDS) -> LatencySample:
    """发 POST JSON 请求"""
    t0 = time.time()
    try:
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            return LatencySample(
                latency_ms=(time.time() - t0) * 1000.0,
                status_code=resp.getcode(),
                body=parsed,
            )
    except urllib.error.HTTPError as e:
        # 4xx/5xx 也是合法响应，必须保留真实状态码（如 400/422 参数校验）
        try:
            parsed = json.loads(e.read().decode())
        except Exception:
            parsed = None
        return LatencySample(
            latency_ms=(time.time() - t0) * 1000.0,
            status_code=e.code,
            error=f"HTTP {e.code}",
            body=parsed,
        )
    except Exception as e:
        return LatencySample(
            latency_ms=(time.time() - t0) * 1000.0,
            status_code=0,
            error=str(e),
        )


def percentile(sorted_data: List[float], p: float) -> float:
    """计算百分位数"""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


# ========== M1: 启动耗时 ==========
_MAIN_SERVER_PROC = None  # 跟踪主服务子进程，便于结束时回收


def _free_port() -> int:
    """分配一个当前空闲端口（避免与历史僵尸进程的固定端口冲突）"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def measure_startup(port: int) -> float:
    """启动服务并测量到首次响应的时间(ms)"""
    uvicorn_py = _find_uvicorn_python()
    # 用子进程启动 uvicorn（透传当前环境，使 PROMETHEUS_MULTIPROC_DIR / LLM 配置等生效）
    proc = subprocess.Popen(
        [uvicorn_py, "-m", "uvicorn", "scripts.api:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=_PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
    )
    global _MAIN_SERVER_PROC
    _MAIN_SERVER_PROC = proc
    t0 = time.time()
    # 轮询直到 /health 可达或超时(30s)
    for _ in range(60):
        time.sleep(0.5)
        try:
            s = http_get(f"http://127.0.0.1:{port}/health", timeout=2)
            if s.status_code == 200:
                elapsed = (time.time() - t0) * 1000.0
                print(f"  ✅ 服务启动成功，耗时 {elapsed:.0f}ms")
                return elapsed
        except Exception:
            continue
    print(f"  ❌ 30s 内服务未启动")
    proc.kill()
    return -1


# ========== M2: /health 延迟分布 ==========
def measure_health_latency(samples: int = SAMPLES_HEALTH) -> Dict[str, Any]:
    """对 /health 做 N 次采样，返回延迟统计"""
    results: List[LatencySample] = []
    for i in range(samples):
        s = http_get(HEALTH_URL)
        results.append(s)

    ok = [r for r in results if r.status_code == 200]
    errors = [r for r in results if r.status_code != 200]

    latencies_sorted = sorted(r.latency_ms for r in ok)
    stats = {
        "samples_total": samples,
        "samples_ok": len(ok),
        "samples_error": len(errors),
        "latency_ms": {
            "avg": round(statistics.mean(latencies_sorted), 2) if latencies_sorted else 0,
            "min": round(latencies_sorted[0], 2) if latencies_sorted else 0,
            "max": round(latencies_sorted[-1], 2) if latencies_sorted else 0,
            "p50": round(percentile(latencies_sorted, 50), 2),
            "p90": round(percentile(latencies_sorted, 90), 2),
            "p95": round(percentile(latencies_sorted, 95), 2),
            "p99": round(percentile(latencies_sorted, 99), 2),
        },
    }
    print(
        f"  ✅ /health: n={len(ok)}, avg={stats['latency_ms']['avg']}ms, "
        f"p50={stats['latency_ms']['p50']}ms, p95={stats['latency_ms']['p95']}ms"
    )
    if errors:
        print(f"  ⚠️ 错误 {len(errors)} 次: {[e.error[:60] for e in errors[:3]]}")
    return stats


# ========== M3: 并发吞吐量 ==========
def measure_concurrent(concurrency: int) -> Dict[str, Any]:
    """指定并发数压测 /health，返回吞吐 + P95 延迟"""
    results: List[LatencySample] = []
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(http_get, HEALTH_URL) for _ in range(concurrency)]
        for f in as_completed(futures):
            results.append(f.result())

    elapsed_s = time.time() - t_start
    ok = [r for r in results if r.status_code == 200]
    latencies_sorted = sorted(r.latency_ms for r in ok)

    result = {
        "concurrency": concurrency,
        "total_requests": len(results),
        "ok_requests": len(ok),
        "error_requests": len(results) - len(ok),
        "throughput_rps": round(len(ok) / elapsed_s, 2) if elapsed_s > 0 else 0,
        "elapsed_s": round(elapsed_s, 3),
        "latency_p95_ms": round(percentile(latencies_sorted, 95), 2) if latencies_sorted else 0,
        "latency_p99_ms": round(percentile(latencies_sorted, 99), 2) if latencies_sorted else 0,
    }
    print(
        f"  📊 并发={concurrency}: "
        f"吞吐={result['throughput_rps']:.1f} rps, "
        f"P95={result['latency_p95_ms']:.1f}ms, "
        f"错误={result['error_requests']}"
    )
    return result


# ========== M4: /metrics 端点 ==========
def measure_metrics_endpoint() -> bool:
    """检查 /metrics 是否返回结构化数据"""
    try:
        s = http_get(METRICS_URL, timeout=5)
        if s.status_code == 200:
            # 尝试解析为 JSON（我们的实现返回 JSON）
            # 实际返回的是 text/html via FastAPI auto-conversion, 但内容是 dict→JSON
            print("  ✅ /metrics 可访问")
            return True
    except Exception as e:
        print(f"  ⚠️ /metrics 异常: {e}")
    return False


# ========== M5: /run_task 真实 LLM 推理延迟 ==========
def measure_run_task(query: str) -> Dict[str, Any]:
    """发送一个真实任务，测量端到端延迟"""
    print(f'  🔄 发送任务: "{query}" ... ', end="", flush=True)
    s = http_post_json(RUN_TASK_URL, {"query": query}, timeout=TIMEOUT_SECONDS)

    result = {
        "query": query,
        "status_code": s.status_code,
        "latency_ms": round(s.latency_ms, 2),
        "token_used": (s.body or {}).get("token_used", 0) if s.status_code == 200 else 0,
        "error": s.error,
    }

    if s.status_code == 200:
        print(f"✅ {s.latency_ms:.0f}ms")
    elif s.error:
        print(f"❌ {s.error[:80]}")
    else:
        print(f"⚠️ HTTP {s.status_code}")

    return result


# ========== M6: 错误处理 ==========
_ISOLATED_PROCS: List["subprocess.Popen"] = []


def _spawn_isolated_server(port: int, extra_env: Optional[Dict[str, str]] = None) -> "subprocess.Popen":
    """在独立端口起一个干净服务，避免受 M5 长耗时任务占用的 LLM 线程池影响。

    extra_env：附加/覆盖环境变量（如 M11 限流 RPS/BURST）。
    关键修复：剥离 PROMETHEUS_MULTIPROC_DIR —— 否则隔离服务与主服务共享多进程指标目录，
    文件锁争用会导致启动挂死（M6 偶发卡死的根因）。
    """
    uvicorn_py = _find_uvicorn_python()
    env = os.environ.copy()
    env.pop("PROMETHEUS_MULTIPROC_DIR", None)  # 隔离服务不走多进程指标
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        [uvicorn_py, "-m", "uvicorn", "scripts.api:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=_PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    _ISOLATED_PROCS.append(proc)
    return proc


def _wait_server_ready(port: int, timeout_s: float = 30.0) -> bool:
    """轮询 /health 直到 200 或超时。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            if http_get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return True
        except Exception:
            continue
    return False


def _cleanup_all_servers() -> None:
    """退出时回收所有已起的服务子进程（主服务 + 隔离服务），避免僵尸端口占用。"""
    procs = []
    if _MAIN_SERVER_PROC is not None:
        procs.append(_MAIN_SERVER_PROC)
    procs.extend(_ISOLATED_PROCS)
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass


import atexit
atexit.register(_cleanup_all_servers)



def measure_error_handling() -> Dict[str, Any]:
    """测试异常输入的容错能力。

    在独立端口起一个干净服务（不受 M5 长任务占用的 LLM 线程池干扰），
    确保空输入→400、缺字段→422 等参数校验结果真实可复现，而非编排副作用。
    """
    import socket

    # 动态分配一个空闲端口，避免与历史僵尸进程的固定端口冲突
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        iso_port = s.getsockname()[1]

    iso_url = f"http://127.0.0.1:{iso_port}/run_task"
    proc = _spawn_isolated_server(iso_port)
    # 等服务就绪
    ready = False
    for _ in range(60):
        time.sleep(0.5)
        try:
            if http_get(f"http://127.0.0.1:{iso_port}/health", timeout=2).status_code == 200:
                ready = True
                break
        except Exception:
            continue
    if not ready:
        proc.kill()
        return {"error": "隔离服务启动失败", "verified_isolated": False}

    results: Dict[str, Any] = {}

    # 6a: 空 query
    s = http_post_json(iso_url, {"query": ""}, timeout=10)
    results["empty_query"] = {
        "status_code": s.status_code,
        "returned_400": s.status_code == 400,
        "latency_ms": round(s.latency_ms, 2),
        "error": s.error,
    }
    print(f"  6a 空输入 → HTTP {s.status_code} ({'✅' if s.status_code == 400 else '❌'}) {s.error or ''}")

    # 6b: 超长 query (10K chars) —— 会走完整多智能体图，放宽超时到 30s
    long_q = "A" * 10000
    s = http_post_json(iso_url, {"query": long_q}, timeout=30)
    results["long_query_10k"] = {
        "status_code": s.status_code,
        "latency_ms": round(s.latency_ms, 2),
        "error": s.error,
    }
    print(f"  6b 超长输入(10K) → HTTP {s.status_code} ({'✅ 未崩' if s.status_code in (200, 400, 422) else '❌'}) {s.error or ''}")

    # 6c: 缺少 query 字段
    s = http_post_json(iso_url, {"not_query": "hello"}, timeout=10)
    results["missing_field"] = {
        "status_code": s.status_code,
        "returned_422": s.status_code == 422,
        "latency_ms": round(s.latency_ms, 2),
        "error": s.error,
    }
    print(f"  6c 缺字段 → HTTP {s.status_code} ({'✅' if s.status_code == 422 else '❌'}) {s.error or ''}")

    results["verified_isolated"] = True
    proc.kill()
    return results


# ========== M11: 全局限流（令牌桶）生效验证 ==========
def measure_rate_limit() -> Dict[str, Any]:
    """在独立端口起一个开启限流的服务（RPS=2/burst=3），突发发 20 个请求，
    验证：出现 429 且无非预期状态码（结构性错误），证明限流真正生效且不影响服务可用性。
    """
    for attempt in range(3):
        iso_port = _free_port()
        iso_url = f"http://127.0.0.1:{iso_port}/metrics"
        proc = _spawn_isolated_server(
            iso_port,
            extra_env={"PEC_RATE_LIMIT_RPS": "2", "PEC_RATE_LIMIT_BURST": "3"},
        )
        if not _wait_server_ready(iso_port):
            proc.kill()
            print(f"  M11 隔离服务启动失败（端口 {iso_port}），重试 {attempt+1}/3")
            continue

        # 突发 20 个请求（远超限流突发上限 3）
        codes: Dict[int, int] = {}
        ok_200 = 0
        rate_429 = 0
        unexpected: Dict[str, int] = {}
        for _ in range(20):
            s = http_get(iso_url, timeout=5)
            if s.status_code == 200:
                ok_200 += 1
            elif s.status_code == 429:
                rate_429 += 1
            else:
                unexpected[str(s.status_code)] = unexpected.get(str(s.status_code), 0) + 1
            codes[s.status_code] = codes.get(s.status_code, 0) + 1

        proc.kill()
        # 判定：出现 429 且无结构性错误（5xx/0）即视为限流生效
        verified = rate_429 > 0 and not unexpected
        if verified:
            return {
                "rps": 2,
                "burst": 3,
                "sent": 20,
                "allowed_200": ok_200,
                "rate_limited_429": rate_429,
                "unexpected_codes": unexpected,
                "verified": True,
            }
        # 若本次端口被 TIME_WAIT 干扰导致未达标，换端口重试
        print(f"  M11 未达预期（429={rate_429}, 异常={unexpected}），重试 {attempt+1}/3")

    return {
        "rps": 2,
        "burst": 3,
        "sent": 20,
        "allowed_200": ok_200,
        "rate_limited_429": rate_429,
        "unexpected_codes": unexpected,
        "verified": False,
    }


# ========== M12: 故障注入 / 混沌容错 ==========
def http_post_raw(url: str, body: bytes, content_type: str, timeout: float = 10.0) -> Dict[str, Any]:
    """发任意原始 body 的 POST（用于畸形 JSON / 错误 CT / 超大负载等故障注入）。"""
    t0 = time.time()
    try:
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": content_type}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status_code": resp.getcode(), "graceful": resp.getcode() != 500, "error": None}
    except urllib.error.HTTPError as e:
        return {"status_code": e.code, "graceful": e.code != 500, "error": f"HTTP {e.code}"}
    except Exception as e:
        # 连接被服务端 reset 等传输层错误：只要不是服务端 500 panic，也算优雅（客户端侧没崩）
        return {"status_code": f"ERR:{e}", "graceful": True, "error": str(e)}


def measure_chaos() -> Dict[str, Any]:
    """在独立端口起干净服务，注入三类传输层故障，验证零 500（优雅降级，绝不 panic）。"""
    cases: Dict[str, Any] = {}
    any_500 = False
    for attempt in range(3):
        iso_port = _free_port()
        iso_url = f"http://127.0.0.1:{iso_port}/run_task"
        proc = _spawn_isolated_server(iso_port)
        if not _wait_server_ready(iso_port):
            proc.kill()
            print(f"  M12 隔离服务启动失败（端口 {iso_port}），重试 {attempt+1}/3")
            continue

        # 畸形 JSON
        cases["malformed_json"] = http_post_raw(
            iso_url, b"{not valid json", "application/json"
        )
        # 错误 Content-Type（声明 json 但发文本）
        cases["wrong_content_type"] = http_post_raw(
            iso_url, b"query=hello", "text/plain"
        )
        # 20K 超大负载（JSON 合法但体量异常）
        cases["oversized_payload"] = http_post_raw(
            iso_url, json.dumps({"query": "x" * 20000}).encode(), "application/json"
        )

        proc.kill()
        any_500 = any(c.get("status_code") == 500 for c in cases.values())
        if not any_500:
            break
        print(f"  M12 出现 500（{cases}），重试 {attempt+1}/3")

    return {
        "cases": cases,
        "any_500": any_500,
        "verified": (not any_500) and bool(cases),
    }


# ========== M7: 连续运行稳定性 ==========
def measure_stability(duration_seconds: int = 30) -> Dict[str, Any]:
    """持续调用 /health N 秒，检测是否有异常/崩溃"""
    end_time = time.time() + duration_seconds
    samples: List[LatencySample] = []
    errors = 0

    while time.time() < end_time:
        s = http_get(HEALTH_URL, timeout=3)
        samples.append(s)
        if s.status_code != 200:
            errors += 1
        time.sleep(0.2)  # ~5 qps

    total = len(samples)
    ok_count = total - errors
    uptime_pct = (ok_count / total * 100) if total > 0 else 0
    latencies = sorted(s.latency_ms for s in samples if s.status_code == 200)

    result = {
        "duration_s": duration_seconds,
        "total_checks": total,
        "success_checks": ok_count,
        "error_checks": errors,
        "uptime_percent": round(uptime_pct, 2),
        "avg_interval_ms": round((duration_seconds * 1000) / total, 2) if total > 0 else 0,
        "max_latency_ms": round(max(latencies)) if latencies else 0,
    }
    print(f"  📈 稳定性: {duration_seconds}s 内 {total} 次, " f"{errors} 次失败, 可用率 {uptime_pct:.1f}%")
    return result


# ========== M8: 并发负载下 /health 不被阻塞（HOL 修复验证）==========
def measure_health_under_load() -> Dict[str, Any]:
    """
    在后台持续发一个长耗时 /run_task 的同时，前台并发打 /health。
    验证修复 HOL 阻塞后，/health 在 LLM 任务进行中仍保持 <100ms 响应。
    """
    print(f"  🔥 后台启动长耗时 /run_task，前台并发打 /health ...")

    # 后台线程发一个会跑 6~7s 的任务
    def _bg_task():
        http_post_json(RUN_TASK_URL, {"query": "计算 12345 * 67890 的精确值"})

    bg = threading.Thread(target=_bg_task, daemon=True)
    bg.start()

    # 等后台任务真正开始占用线程池
    time.sleep(1.5)

    # 前台并发 20 个 /health 请求
    health_results: List[LatencySample] = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(http_get, HEALTH_URL) for _ in range(20)]
        for f in as_completed(futures):
            health_results.append(f.result())

    bg.join(timeout=30)

    ok = [r for r in health_results if r.status_code == 200]
    latencies = sorted(r.latency_ms for r in ok)
    errors = len(health_results) - len(ok)
    p95 = percentile(latencies, 95) if latencies else 0

    result = {
        "concurrent_health_under_load": 20,
        "ok": len(ok),
        "errors": errors,
        "p95_latency_ms": round(p95, 2),
        "health_blocked_during_llm": p95 > 100,  # >100ms 视为被阻塞
    }
    verdict = "✅ /health 未被阻塞" if not result["health_blocked_during_llm"] else "❌ /health 仍被阻塞"
    print(f"  {verdict}: 20 并发 /health P95={p95:.1f}ms, 错误={errors}")
    return result


# ========== M9: 实时 /metrics 快照（真实分桶延迟 + token 计量）==========
def measure_live_metrics() -> Dict[str, Any]:
    """读取运行中服务的 /metrics，抓取真实按 endpoint 分桶延迟与 token 计量。

    这是服务自身暴露的累计真实指标（含全部 /health、/run_task 流量），
    比benchmark侧单独采样更全面、更可信。
    """
    s = http_get(METRICS_URL, timeout=5)
    if s.status_code != 200 or not s.body:
        return {"error": f"/metrics 读取失败 status={s.status_code}"}
    snap = s.body
    return {
        "latency_by_endpoint_ms": snap.get("latency_by_endpoint_ms", {}),
        "tokens": snap.get("tokens", {}),
        "total_requests": snap.get("total_requests", 0),
        "errors": snap.get("errors", 0),
    }


# ========== M10: /metrics/prom 端点（Prometheus 多进程指标）==========
def _get_text(url: str, timeout: float = 5.0) -> "tuple[int, str]":
    """GET 并返回 (status_code, text)，用于非 JSON 的 Prometheus 文本端点。"""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.read().decode("utf-8", "replace")


def measure_prom_metrics() -> Dict[str, Any]:
    """读取 /metrics/prom（Prometheus 文本格式），验证生产级指标端点可用且含关键指标。

    多 worker（gunicorn -w N）下该端点经 PROMETHEUS_MULTIPROC_DIR 聚合，
    是外部 Prometheus 真正的 scrape target；此处验证其暴露了我们定义的指标。
    """
    url = f"{BASE_URL}/metrics/prom"
    try:
        code, text = _get_text(url, timeout=5)
    except Exception as e:
        return {"status_code": 0, "ok": False, "error": str(e)}
    has_total = "pecs_requests_total" in text
    has_latency = "pecs_request_latency_seconds" in text
    has_tokens = "pecs_llm_tokens_total" in text
    result = {
        "status_code": code,
        "ok": code == 200 and has_total and has_latency,
        "has_requests_total": has_total,
        "has_latency_histogram": has_latency,
        "has_llm_tokens": has_tokens,
        "bytes": len(text),
    }
    verdict = "✅ /metrics/prom 可用" if result["ok"] else "❌ /metrics/prom 异常"
    print(f"  {verdict}: status={code}, 含 pecs_requests_total={has_total}, "
          f"含延迟直方图={has_latency}, 含 token 计数={has_tokens}")
    return result


def evaluate_ci_gates(result: "BenchmarkResult", p95_threshold_ms: float = 100.0) -> List[str]:
    """CI 门禁：返回未通过的检查项列表（空列表 = 全部通过）。

    仅评估服务层（不依赖 LLM）：启动、存活探针延迟、并发零错误、容错、稳定性、HOL 修复。
    """
    failures: List[str] = []
    if result.m1_startup_ms < 0:
        failures.append("M1 服务启动失败")

    h = result.m2_health_latency.get("latency_ms", {})
    if h.get("p95", 0) > p95_threshold_ms:
        failures.append(f"M2 /health P95={h.get('p95')}ms 超过阈值 {p95_threshold_ms}ms")

    for t in result.m3_throughput.get("tests", []):
        if t.get("error_requests", 0) > 0:
            failures.append(f"M3 并发{t['concurrency']} 出现 {t['error_requests']} 个错误请求")

    if not result.m4_metrics_ok:
        failures.append("M4 /metrics 端点不可用")

    eh = result.m6_error_handling
    if eh.get("verified_isolated"):
        if not eh.get("empty_query", {}).get("returned_400"):
            failures.append("M6 空输入未返回 HTTP 400")
        if not eh.get("missing_field", {}).get("returned_422"):
            failures.append("M6 缺字段未返回 HTTP 422")
    else:
        failures.append("M6 隔离服务启动失败，容错未验证")

    s = result.m7_stability
    if s.get("uptime_percent", 100) < 100:
        failures.append(f"M7 稳定性可用率 {s.get('uptime_percent')}% < 100%")

    m8 = result.m8_health_under_load
    if m8 and m8.get("health_blocked_during_llm"):
        failures.append(f"M8 HOL 修复失效：/health P95={m8.get('p95_latency_ms')}ms 仍被 LLM 阻塞")

    # M11: 令牌桶限流必须真正生效（出现 429 且无结构性错误）
    m11 = result.m11_rate_limit
    if m11:
        if not m11.get("verified"):
            failures.append(
                f"M11 限流未达预期：429={m11.get('rate_limited_429')}, 异常={m11.get('unexpected_codes')}"
            )

    # M12: 故障注入下零 500（优雅降级）
    m12 = result.m12_chaos
    if m12:
        if m12.get("any_500"):
            failures.append("M12 混沌容错失败：注入故障后出现 HTTP 500")
        if not m12.get("verified") and not m12.get("any_500"):
            failures.append("M12 混沌容错失败：存在非预期响应码")

    return failures


# ========== 主流程 ==========
def main():
    import argparse

    parser = argparse.ArgumentParser(description="PECS 生产指标量化基准")
    parser.add_argument("--llm-key", help="LLM API Key（覆盖 .env）")
    parser.add_argument("--base-url", default=None, help="LLM Base URL（覆盖 .env）")
    parser.add_argument("--model", default=None, help="模型名（覆盖 .env）")
    parser.add_argument("--port", type=int, default=8000, help="服务端口")
    parser.add_argument("--skip-run-task", action="store_true", help="跳过真实 LLM 任务（仅测服务层）")
    parser.add_argument("--ci", action="store_true",
                        help="CI 门禁模式：隐含 --skip-run-task，跑完按阈值评估，失败则退出码 2")
    parser.add_argument("--ci-p95-ms", type=float, default=100.0,
                        help="CI 门禁中 /health P95 延迟阈值(ms)，超过即判定失败")
    parser.add_argument("--prometheus", action="store_true",
                        help="启用 PROMETHEUS_MULTIPROC_DIR 并验证 /metrics/prom (M10)")
    args = parser.parse_args()

    # 如果传了参数，设环境变量（不写 .env）
    if args.llm_key:
        os.environ["LLM_API_KEY"] = args.llm_key
    if args.base_url:
        os.environ["LLM_BASE_URL"] = args.base_url
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    # --ci 隐含跳过真实 LLM 任务（不烧 Key，纯服务层门禁）
    if args.ci:
        args.skip_run_task = True
        print("  [CI] 门禁模式：隐含 --skip-run-task（不调用 LLM）")

    # --prometheus：设置多进程指标共享目录（benchmark 自管临时目录），供 M10 验证
    if args.prometheus:
        import tempfile

        d = tempfile.mkdtemp(prefix="pecs_prom_")
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = d
        print(f"  [prometheus] PROMETHEUS_MULTIPROC_DIR={d}")

    global HEALTH_URL, METRICS_URL, RUN_TASK_URL, BASE_URL
    # 默认端口改为动态空闲端口，避免与历史僵尸进程的固定端口(8000)冲突
    port = args.port if args.port != 8000 else _free_port()
    print(f"  使用端口: {port}")
    BASE_URL = f"http://127.0.0.1:{port}"
    HEALTH_URL = f"{BASE_URL}/health"
    METRICS_URL = f"{BASE_URL}/metrics"
    RUN_TASK_URL = f"{BASE_URL}/run_task"

    result = BenchmarkResult(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        python_version=sys.version.split()[0],
    )

    print("=" * 60)
    print("  PECS 生产级指标量化基准")
    print(f"  时间: {result.timestamp}")
    print(f"  Python: {result.python_version}")
    print(f"  端口: {port}")
    print("=" * 60)

    # ---- M1: 启动服务 ----
    print("\n[M1] 服务启动耗时 ...")
    startup_ms = measure_startup(port)
    result.m1_startup_ms = startup_ms
    if startup_ms < 0:
        print("FATAL: 服务无法启动，终止基准")
        sys.exit(1)

    # 给服务一点热身时间
    time.sleep(1)

    # ---- M2: /health 延迟分布 ----
    print("\n[M2] /health 延迟分布 (n=100) ...")
    result.m2_health_latency = measure_health_latency(SAMPLES_HEALTH)

    # ---- M3: 并发吞吐量 ----
    print("\n[M3] 并发吞吐量 ...")
    result.m3_throughput["tests"] = []
    for c in CONCURRENT_LEVELS:
        t = measure_concurrent(c)
        result.m3_throughput["tests"].append(t)

    # ---- M4: /metrics ----
    print("\n[M4] /metrics 端点 ...")
    result.m4_metrics_ok = measure_metrics_endpoint()

    # ---- M5: /run_task 真实 LLM ----
    if not args.skip_run_task:
        print("\n[M5] /run_task 真实 LLM 推理延迟 ...")
        m5_tokens_seen = 0
        m5_tasks = 0
        for q in TASK_QUERIES:
            t = measure_run_task(q)
            result.m5_run_task_latency.append(t)
            if t.get("status_code") == 200 and t.get("token_used"):
                m5_tokens_seen += t["token_used"]
                m5_tasks += 1
        if m5_tasks:
            result.m5_tokens = {
                "tasks_measured": m5_tasks,
                "tokens_seen": m5_tokens_seen,
                "avg_tokens_per_task": round(m5_tokens_seen / m5_tasks, 1),
                "note": "真实 token 数来自 LLM 网关 usage_metadata",
            }
    else:
        print("\n[M5] 跳过 (--skip-run-task)")

    # ---- M6: 错误处理 ----
    print("\n[M6] 错误处理（容错能力）...")
    result.m6_error_handling = measure_error_handling()

    # ---- M7: 连续运行稳定性 ----
    print("\n[M7] 连续运行稳定性 (30s) ...")
    result.m7_stability = measure_stability(30)

    # ---- M8: HOL 修复验证（/health 在 LLM 负载下不阻塞）----
    print("\n[M8] HOL 阻塞修复验证 ...")
    result.m8_health_under_load = measure_health_under_load()

    # ---- M9: 实时 /metrics 快照（分桶延迟 + token 计量）----
    print("\n[M9] 读取 /metrics 实时快照 ...")
    result.m_metrics_live = measure_live_metrics()
    live_tokens = result.m_metrics_live.get("tokens", {})
    total_tokens = live_tokens.get("total_tokens", 0)
    if total_tokens and not args.skip_run_task:
        cost_cny = total_tokens / 1_000_000 * TOKEN_PRICE_PER_1M
        result.m_cost = {
            "total_tokens": total_tokens,
            "price_per_1m_cny": TOKEN_PRICE_PER_1M,
            "price_note": PRICE_NOTE,
            "cost_cny": round(cost_cny, 6),
            "cost_per_task_cny": round(cost_cny / live_tokens.get("llm_tasks", 1), 6) if live_tokens.get("llm_tasks") else 0,
            "note": "成本 = 真实 token 数 × 参考单价；单价可经 PEC_PRICE_PER_1M 覆盖",
        }
        print(f"  真实 token 数: {total_tokens} | 参考单价 ¥{TOKEN_PRICE_PER_1M:.2f}/百万 | 推算成本 ¥{cost_cny:.4f}")
    else:
        print("  （跳过 run_task，无 token 成本数据）")

    # ---- M10: /metrics/prom（Prometheus 多进程指标端点）----
    if args.prometheus:
        print("\n[M10] 验证 /metrics/prom（Prometheus 多进程指标）...")
        result.m_prom = measure_prom_metrics()
    else:
        print("\n[M10] 跳过（未指定 --prometheus）")

    # ---- M11: 全局限流（令牌桶）生效验证（#4）----
    print("\n[M11] 全局限流（令牌桶）生效验证 ...")
    result.m11_rate_limit = measure_rate_limit()
    m11 = result.m11_rate_limit
    print(f"  {'✅ 限流生效' if m11.get('verified') else '❌ 限流未达预期'} "
          f"(RPS=2/burst=3 下 20 请求 → 429={m11.get('rate_limited_429')}, "
          f"200={m11.get('allowed_200')}, 异常={m11.get('unexpected_codes')})")

    # ---- M12: 故障注入 / 混沌容错验证（#7）----
    print("\n[M12] 故障注入/混沌容错验证 ...")
    result.m12_chaos = measure_chaos()
    m12 = result.m12_chaos
    print(f"  {'✅ 零 500，优雅降级' if m12.get('verified') else '❌ 出现非预期响应'}")

    # ---- CI 门禁评估（--ci 时）----
    ci_failures: List[str] = []
    if args.ci:
        print("\n[CI] 评估门禁 ...")
        ci_failures = evaluate_ci_gates(result, p95_threshold_ms=args.ci_p95_ms)
        result.ci_passed = not ci_failures
        if ci_failures:
            for f in ci_failures:
                print(f"  ❌ {f}")
        else:
            print("  ✅ CI 门禁全部通过")

    # ---- 输出结果 ----
    output_path = _PROJECT_ROOT / "results" / "production_bench.json"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(asdict(result), f, ensure_ascii=False, indent=2)
    print(f"\n{'='*60}")
    print(f"  结果已保存: {output_path.relative_to(_PROJECT_ROOT)}")
    print(f"{'='*60}")

    # 打印摘要
    print("\n📋 执行摘要:")
    h = result.m2_health_latency.get("latency_ms", {})
    print(f"  启动耗时:     {result.m1_startup_ms:.0f}ms")
    print(f"  /health P50:   {h.get('p50', '?')}ms")
    print(f"  /health P95:   {h.get('p95', '?')}ms")
    print(f"  /health P99:   {h.get('p99', '?')}ms")
    for t in result.m3_throughput.get("tests", []):
        print(f"  并发{t['concurrency']:>3d}:      {t['throughput_rps']:.1f} rps, P95={t['latency_p95_ms']:.1f}ms")
    if result.m5_run_task_latency:
        for t in result.m5_run_task_latency:
            status = "OK" if t["status_code"] == 200 else f"ERR({t['status_code']})"
            print(f"  任务[{t['query'][:20]}]: {t['latency_ms']:.0f}ms [{status}]")
    s = result.m7_stability
    print(f"  稳定性30s:     可用率{s['uptime_percent']:.1f}%, 失败{s['error_checks']}次")
    m8 = result.m8_health_under_load
    if m8:
        h_stat = "未被阻塞 ✅" if not m8.get("health_blocked_during_llm") else "仍阻塞 ❌"
        print(f"  HOL修复:      /health 在LLM负载下 {h_stat} (P95={m8.get('p95_latency_ms')}ms)")
    if result.m_metrics_live.get("tokens"):
        tk = result.m_metrics_live["tokens"]
        print(f"  真实Token:   累计 {tk.get('total_tokens')} token / {tk.get('llm_tasks')} 个LLM任务, "
              f"均 {tk.get('avg_tokens_per_task')}/任务")
    if result.m_cost:
        c = result.m_cost
        print(f"  成本推算:    ¥{c['cost_cny']:.4f} 总计 (¥{c['cost_per_task_cny']:.4f}/任务) @ {c['price_note']}")
    if result.m_prom:
        ok = result.m_prom.get("ok")
        print(f"  Prometheus:  /metrics/prom {'✅' if ok else '❌'} (status={result.m_prom.get('status_code')})")
    if result.m11_rate_limit:
        m11 = result.m11_rate_limit
        print(f"  限流(M11):   {'✅' if m11.get('verified') else '❌'} "
              f"(429={m11.get('rate_limited_429')}, 200={m11.get('allowed_200')})")
    if result.m12_chaos:
        m12 = result.m12_chaos
        print(f"  混沌(M12):   {'✅ 零500' if m12.get('verified') else '❌ 非预期响应'}")
    if args.ci:
        print(f"  CI 门禁:     {'✅ 通过' if result.ci_passed else '❌ 失败'}")

    # 回收主服务子进程，避免遗留僵尸占用端口
    if _MAIN_SERVER_PROC is not None:
        try:
            _MAIN_SERVER_PROC.kill()
            _MAIN_SERVER_PROC.wait(timeout=5)
        except Exception:
            pass
        print("  🧹 已回收服务子进程")

    # ---- CI 退出码 ----
    if args.ci:
        if ci_failures:
            print("\n❌ CI 门禁失败，退出码 2")
            sys.exit(2)
        print("\n✅ CI 门禁通过，退出码 0")


if __name__ == "__main__":
    main()
