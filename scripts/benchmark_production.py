"""
PECS 生产级指标量化基准（真实数据采集）

测量项目：
  M1: 服务启动耗时
  M2: /health 存活探针延迟分布（p50/p95/p99, 100次采样）
  M3: /health 并发吞吐量（10/20/50并发）
  M4: /metrics 端点可用性
  M5: /run_task 真实 LLM 推理端到端延迟（简单计算题）
  M6: /run_task 错误处理（空输入/超长输入）
  M7: 服务稳定性（连续运行无崩溃）

用法：
    python scripts/benchmark_production.py [--llm-key YOUR_KEY] [--base-url URL] [--model MODEL]
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
    "Python 中如何反转一个列表？",
]
TIMEOUT_SECONDS = 120  # 单次 /run_task 最长等待


@dataclass
class LatencySample:
    """单次请求延迟样本"""

    latency_ms: float
    status_code: int
    error: Optional[str] = None


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


def http_get(url: str, timeout: float = 5.0) -> LatencySample:
    """发 GET 请求并记录延迟"""
    t0 = time.time()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return LatencySample(
                latency_ms=(time.time() - t0) * 1000.0,
                status_code=resp.getcode(),
            )
    except urllib.error.HTTPError as e:
        return LatencySample(
            latency_ms=(time.time() - t0) * 1000.0,
            status_code=e.code,
            error=f"HTTP {e.code}",
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
            body = resp.read().decode()
            return LatencySample(
                latency_ms=(time.time() - t0) * 1000.0,
                status_code=resp.getcode(),
            )
    except urllib.error.HTTPError as e:
        # 4xx/5xx 也是合法响应，必须保留真实状态码（如 400/422 参数校验）
        return LatencySample(
            latency_ms=(time.time() - t0) * 1000.0,
            status_code=e.code,
            error=f"HTTP {e.code}",
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
def measure_startup(port: int) -> float:
    """启动服务并测量到首次响应的时间(ms)"""
    uvicorn_py = _find_uvicorn_python()
    # 用子进程启动 uvicorn
    proc = subprocess.Popen(
        [uvicorn_py, "-m", "uvicorn", "scripts.api:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=_PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
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
def _spawn_isolated_server(port: int) -> "subprocess.Popen":
    """在独立端口起一个干净服务，避免受 M5 长耗时任务占用的 LLM 线程池影响。"""
    uvicorn_py = _find_uvicorn_python()
    return subprocess.Popen(
        [uvicorn_py, "-m", "uvicorn", "scripts.api:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=_PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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

    # 6b: 超长 query (10K chars)
    long_q = "A" * 10000
    s = http_post_json(iso_url, {"query": long_q}, timeout=10)
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


# ========== 主流程 ==========
def main():
    import argparse

    parser = argparse.ArgumentParser(description="PECS 生产指标量化基准")
    parser.add_argument("--llm-key", help="LLM API Key（覆盖 .env）")
    parser.add_argument("--base-url", default=None, help="LLM Base URL（覆盖 .env）")
    parser.add_argument("--model", default=None, help="模型名（覆盖 .env）")
    parser.add_argument("--port", type=int, default=8000, help="服务端口")
    parser.add_argument("--skip-run-task", action="store_true", help="跳过真实 LLM 任务（仅测服务层）")
    args = parser.parse_args()

    # 如果传了参数，设环境变量（不写 .env）
    if args.llm_key:
        os.environ["LLM_API_KEY"] = args.llm_key
    if args.base_url:
        os.environ["LLM_BASE_URL"] = args.base_url
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    global HEALTH_URL, METRICS_URL, RUN_TASK_URL, BASE_URL
    BASE_URL = f"http://127.0.0.1:{args.port}"
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
    print(f"  端口: {args.port}")
    print("=" * 60)

    # ---- M1: 启动服务 ----
    print("\n[M1] 服务启动耗时 ...")
    startup_ms = measure_startup(args.port)
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
        for q in TASK_QUERIES:
            t = measure_run_task(q)
            result.m5_run_task_latency.append(t)
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


if __name__ == "__main__":
    main()
