"""跨进程状态存储（tools/rate_store.py）测试。

背景：限流/熔断/幂等原本是进程内 dict，多 worker 部署时各进程各算一份，
限流额度会被放大 N 倍。本模块用 SQLite（标准库、零安装）把状态外置。

覆盖：
  - 令牌桶在 burst 内放行、超出拒绝（严格不补充时的精确性）
  - 令牌按 rps 补充
  - 幂等缓存 TTL
  - DB 异常时 fail-open（限流组件不能成为新的故障源）
  - 【关键】多进程共享：N 个独立进程的放行总数 == burst（进程内方案会是 N×burst）
"""
import multiprocessing
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

import tools.rate_store as rs


@pytest.fixture
def store(tmp_path):
    db = str(tmp_path / "state.sqlite")
    s = rs.StateStore(db)
    s.reset()
    return s


def test_bucket_allows_up_to_burst(store):
    """rps=0（不补充）时，放行总数必须恰好等于 burst"""
    allowed = sum(1 for _ in range(30) if store.consume("ep", rps=0, burst=10))
    assert allowed == 10


def test_bucket_refills_by_rps(store):
    """按 rps 补充：间隔 50ms、rps=50 → 每次补充 2.5 个，连打 20 次应全放行"""
    ok = 0
    for _ in range(20):
        if store.consume("ep", rps=50, burst=10):
            ok += 1
        time.sleep(0.05)
    assert ok == 20


def test_separate_endpoints_have_separate_buckets(store):
    assert store.consume("a", rps=0, burst=1) is True
    assert store.consume("b", rps=0, burst=1) is True   # 不同端点互不影响
    assert store.consume("a", rps=0, burst=1) is False


def test_idempotency_cache_ttl(store):
    store.cache_put("k", "RESULT")
    assert store.cache_get("k", ttl_s=60) == "RESULT"
    assert store.cache_get("k", ttl_s=-1) is None     # 已过期
    assert store.cache_get("missing", ttl_s=60) is None


def test_fail_open_on_db_error(store, monkeypatch):
    """DB 故障时限流组件必须放行（它不能成为新的故障源）"""

    def _boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(rs.sqlite3, "connect", _boom)
    assert store.consume("ep", rps=0, burst=1) is True
    assert store.cache_get("k", ttl_s=60) is None


def test_cache_put_swallows_db_error(store, monkeypatch):
    def _boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(rs.sqlite3, "connect", _boom)
    store.cache_put("k", "R")  # 不抛异常即通过


# ------------------------------------------------------------------ 多进程
def _child_worker(db: str, attempts: int, burst: int, out: str) -> None:
    """子进程：独立进程独立连接，模拟 gunicorn -w N"""
    s = rs.StateStore(db)
    allowed = 0
    for _ in range(attempts):
        if s.consume("run_task", rps=0, burst=burst):
            allowed += 1
        time.sleep(0.002)
    Path(out).write_text(str(allowed), encoding="utf-8")


def test_shared_across_processes(tmp_path):
    """4 个独立进程 × 60 次尝试，burst=100 → 全局放行恰好 100。

    若状态在进程内（改造前），总数会是 400 —— 这就是多 worker 下
    限流额度被放大 4 倍的直接证据。
    """
    db = str(tmp_path / "shared.sqlite")
    rs.StateStore(db).reset()

    workers, attempts, burst = 4, 60, 100
    outs = [str(tmp_path / f"c{i}.txt") for i in range(workers)]
    procs = []
    for i in range(workers):
        p = multiprocessing.Process(
            target=_child_worker, args=(db, attempts, burst, outs[i])
        )
        p.start()
        procs.append(p)
    for p in procs:
        p.join(timeout=60)

    per_worker = [int(Path(f).read_text(encoding="utf-8")) for f in outs]
    total = sum(per_worker)

    assert total == burst, (
        f"跨进程限流失效：放行 {total}（应为 {burst}），各进程 {per_worker}；"
        f"进程内方案会放行 {workers * burst}"
    )
    assert max(per_worker) > 0
