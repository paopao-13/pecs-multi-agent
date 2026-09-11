"""wrapper 熔断/幂等共享存储测试。

覆盖三类断言：
  1. 共享模式下熔断状态跨进程可见（多进程并发失败 → 熔断对全部进程生效）
  2. 共享模式下幂等缓存跨进程命中 + TTL 过期
  3. 开关关闭时行为与进程内模式逐字一致（K3 约束回归）
"""
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import pytest

import tools.wrapper as w
from tools.wrapper_state import SharedStateStore


@pytest.fixture()
def shared(tmp_path, monkeypatch):
    """共享存储 + 开启熔断/幂等开关；结束时恢复进程内模式。"""
    store = SharedStateStore(str(tmp_path / "state.sqlite"))
    w.configure_shared_state(store)
    monkeypatch.setattr(w, "TOOL_BREAKER_ENABLED", True)
    monkeypatch.setattr(w, "TOOL_IDEMPOTENT_ENABLED", True)
    yield store
    w.configure_shared_state(None)


@pytest.fixture()
def breaker_on(monkeypatch):
    """进程内模式 + 开启熔断（对照组）。"""
    w.reset_breakers()
    monkeypatch.setattr(w, "TOOL_BREAKER_ENABLED", True)
    yield
    w.reset_breakers()


# ============ 1. 熔断：共享模式基本状态机 ============

def test_shared_breaker_opens_at_threshold(shared):
    for _ in range(w.TOOL_BREAKER_THRESHOLD):
        w.breaker_record_failure("search")
    assert w.breaker_is_open("search") is True
    assert w.breaker_is_open("web_browse") is False  # 其他工具不受影响


def test_shared_breaker_success_resets(shared):
    for _ in range(w.TOOL_BREAKER_THRESHOLD - 1):
        w.breaker_record_failure("search")
    w.breaker_record_success("search")
    for _ in range(w.TOOL_BREAKER_THRESHOLD - 1):
        w.breaker_record_failure("search")
    assert w.breaker_is_open("search") is False  # 成功清零后未再达阈值


def test_shared_breaker_half_open_after_cooldown(shared, monkeypatch):
    for _ in range(w.TOOL_BREAKER_THRESHOLD):
        w.breaker_record_failure("search")
    assert w.breaker_is_open("search") is True
    monkeypatch.setattr(w, "TOOL_BREAKER_RESET_SEC", 0.05)
    time.sleep(0.1)
    assert w.breaker_is_open("search") is False  # 冷却到期 → 半开放行


def test_shared_breaker_disabled_never_opens(shared, monkeypatch):
    """K3：TOOL_BREAKER_ENABLED=False 时 is_open 恒 False，与进程内模式一致。"""
    monkeypatch.setattr(w, "TOOL_BREAKER_ENABLED", False)
    for _ in range(w.TOOL_BREAKER_THRESHOLD * 2):
        w.breaker_record_failure("search")
    assert w.breaker_is_open("search") is False


# ============ 2. 熔断：跨进程共享 ============

def _fail_n_times(db_path: str, action: str, n: int) -> int:
    """子进程入口：独立进程里注入 n 次失败，返回该进程视角的熔断判定。"""
    import tools.wrapper as w_mod
    from tools.wrapper_state import SharedStateStore as S

    store = S(db_path)
    w_mod.configure_shared_state(store)
    # 测试进程内 TOOL_BREAKER_ENABLED 不会被继承（重新导入为默认 False），
    # 子进程里直接查存储快照而非 is_open（开关检查在 is_open 内部）
    w_mod.TOOL_BREAKER_ENABLED = True
    for _ in range(n):
        w_mod.breaker_record_failure(action)
    failures, opened = store.breaker_snapshot(action)
    return failures


def test_shared_breaker_across_processes(tmp_path, shared, monkeypatch):
    """两个子进程各注入 (threshold-1) 次失败 → 总计数达阈值，主进程视角已熔断。

    这是进程内 dict 做不到的：各进程计数互不可见时，总量永远凑不满阈值。
    """
    db_path = str(tmp_path / "state.sqlite")
    per_proc = max(1, w.TOOL_BREAKER_THRESHOLD - 1)
    with ProcessPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_fail_n_times, db_path, "search", per_proc) for _ in range(2)]
        counts = [f.result(timeout=30) for f in futs]
    total = sum(counts)
    assert total >= w.TOOL_BREAKER_THRESHOLD, f"子进程计数未达阈值: {counts}"
    failures, opened = shared.breaker_snapshot("search")
    assert failures >= w.TOOL_BREAKER_THRESHOLD
    assert opened is not None
    assert w.breaker_is_open("search") is True


# ============ 3. 幂等：共享模式 ============

def test_shared_idempotent_hit_and_miss(shared):
    key = w.idempotency_key("search", {"q": "hello"}, {"thread_id": "t1-abc"})
    other = w.idempotency_key("search", {"q": "world"}, {"thread_id": "t1-abc"})
    w._idempotent_store(key, "cached")
    assert w._idempotent_lookup(key) == "cached"
    assert w._idempotent_lookup(other) is None  # 不同参数不串味


def test_shared_idempotent_cross_process(tmp_path):
    """进程 A 写入 → 进程 B 命中（独立进程读同一 SQLite）。"""
    import tools.wrapper as w_mod
    from tools.wrapper_state import SharedStateStore as S

    db_path = str(tmp_path / "state.sqlite")
    w_mod.configure_shared_state(S(db_path))
    key = w_mod.idempotency_key("file_read", {"path": "a.txt"}, {"thread_id": "t9-x"})
    w_mod._idempotent_store(key, "cross-proc-value")
    w_mod.configure_shared_state(None)

    # 模拟另一个进程：全新实例读同一 DB
    store_b = S(db_path)
    w_mod.configure_shared_state(store_b)
    assert w_mod._idempotent_lookup(key) == "cross-proc-value"
    w_mod.configure_shared_state(None)


def test_shared_idempotent_ttl_expiry(shared, monkeypatch):
    key = w.idempotency_key("search", {"q": "ttl"}, {"thread_id": "t1-ttl"})
    w._idempotent_store(key, "old")
    assert w._idempotent_lookup(key) == "old"
    monkeypatch.setattr(w, "_IDEMPOTENT_TTL_SEC", -1.0)  # 强制过期
    assert w._idempotent_lookup(key) is None


def test_idempotent_key_scoped_by_thread(shared):
    """同参数不同 thread_id 不串味（租户隔离依赖 thread_id 命名约定）。"""
    k1 = w.idempotency_key("search", {"q": "x"}, {"thread_id": "ta-1"})
    k2 = w.idempotency_key("search", {"q": "x"}, {"thread_id": "tb-1"})
    assert k1 != k2


# ============ 4. 进程内模式回归（K3：默认行为不变） ============

def test_local_mode_breaker_unchanged(breaker_on):
    for _ in range(w.TOOL_BREAKER_THRESHOLD):
        w.breaker_record_failure("search")
    assert w.breaker_is_open("search") is True
    w.breaker_record_success("search")
    assert w.breaker_is_open("search") is False


def test_local_mode_idempotent_unchanged(monkeypatch):
    w.clear_idempotent_cache()
    monkeypatch.setattr(w, "TOOL_IDEMPOTENT_ENABLED", True)
    key = w.idempotency_key("search", {"q": "local"}, {"thread_id": "t1-loc"})
    w._idempotent_store(key, "local-cached")
    assert w._idempotent_lookup(key) == "local-cached"
    w.clear_idempotent_cache()
    assert w._idempotent_lookup(key) is None


# ============ 5. 端到端：invoke_tool 走共享熔断 ============

def test_invoke_tool_short_circuits_via_shared_breaker(shared):
    """共享熔断打开后，invoke_tool 直接短路返回 CIRCUIT_OPEN，不执行工具。"""
    for _ in range(w.TOOL_BREAKER_THRESHOLD):
        w.breaker_record_failure("search")

    def _never_called(args):
        raise AssertionError("熔断打开后不应执行工具")

    result, error_type, _ = w.invoke_tool(
        _never_called, "search", {"q": "x"}, context={"thread_id": "t1-e2e"}
    )
    assert error_type == w.ToolErrorType.CIRCUIT_OPEN
    assert "熔断" in result  # K1：错误前缀必须落在 _ERROR_MARKERS 内
