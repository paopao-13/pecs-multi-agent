"""trace_id 全链路贯穿测试（Phase 4 可观测性）。

核心断言：
  - contextvars 基本语义（绑定 / 复位 / 占位符）
  - **线程池场景**：run_in_executor 不传播 contextvars，必须靠
    bind_trace_id 在工作线程内重新绑定（这是最容易漏的一环）
  - HTTP 层：中间件生成 trace_id 并回写到响应头，支持上游串联
  - 日志字段：工具调用日志必带 trace_id
  - 安全：非法 X-Trace-Id（含换行/超长）被拒，防日志注入
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from logger import trace_context as tc


@pytest.fixture(autouse=True)
def _clean_context():
    """每个用例后复位，避免 contextvars 跨用例泄漏。"""
    token = tc.set_trace_id("")
    tc.reset_trace_id(token)
    yield
    token = tc.set_trace_id("")
    tc.reset_trace_id(token)


# ============ 基础语义 ============

def test_new_trace_id_is_uuid():
    tid = tc.new_trace_id()
    assert len(tid) == 36 and tid.count("-") == 4


def test_placeholder_when_unbound():
    assert tc.get_trace_id() == tc.PLACEHOLDER == "-"


def test_bind_context_manager_sets_and_resets():
    tid = tc.new_trace_id()
    with tc.bind_trace_id(tid) as got:
        assert got == tid
        assert tc.get_trace_id() == tid
    assert tc.get_trace_id() == "-"  # 退出后自动复位


def test_bind_without_arg_generates_one():
    with tc.bind_trace_id() as tid:
        assert len(tid) == 36
        assert tc.get_trace_id() == tid


def test_trace_fields_dict():
    with tc.bind_trace_id("tid-123"):
        assert tc.trace_fields() == {"trace_id": "tid-123"}


# ============ 线程池传递（关键） ============

def test_contextvars_do_not_leak_into_threads():
    """先确认坑真实存在：线程池内读不到主线程绑定值。

    这条断言是"防御性文档"——若将来 Python 行为变化（或改用
    asyncio.to_thread），本用例会失败，提醒我们重新评估 bind 的必要性。
    """
    with tc.bind_trace_id("main-thread-tid"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            inside = pool.submit(tc.get_trace_id).result(timeout=5)
    assert inside == "-"  # 线程池不继承 contextvars


def test_bind_trace_id_works_inside_thread():
    """正解：在线程函数内绑定，日志链路才完整。"""
    tid = tc.new_trace_id()

    def _work():
        with tc.bind_trace_id(tid):
            return tc.get_trace_id()

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(_work).result(timeout=5) == tid
    assert tc.get_trace_id() == "-"  # 主线程不受影响


def test_nested_bind_restores_outer():
    outer = tc.new_trace_id()
    inner = tc.new_trace_id()
    with tc.bind_trace_id(outer):
        with tc.bind_trace_id(inner):
            assert tc.get_trace_id() == inner
        assert tc.get_trace_id() == outer  # 内层退出后回到外层


# ============ HTTP 中间件 ============

def test_middleware_sets_response_header(monkeypatch):
    import scripts.api as api
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "LLM_API_KEY", "")
    with TestClient(api.app) as client:
        resp = client.get("/health")
    tid = resp.headers.get("X-Trace-Id")
    assert tid and len(tid) == 36


def test_middleware_reuses_valid_incoming_trace_id(monkeypatch):
    """上游传入合法 X-Trace-Id → 复用，便于跨服务串联。"""
    import scripts.api as api
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "LLM_API_KEY", "")
    with TestClient(api.app) as client:
        resp = client.get("/health", headers={"X-Trace-Id": "upstream-abc-123"})
    assert resp.headers["X-Trace-Id"] == "upstream-abc-123"


@pytest.mark.parametrize(
    "bad",
    [
        "evil\nX-Injected: 1",      # 换行注入
        "a" * 65,                    # 超长
        "bad id with spaces",        # 空格
        "bad;rm -rf",                # 特殊字符
    ],
)
def test_middleware_rejects_malformed_trace_id(monkeypatch, bad):
    """非法 X-Trace-Id 必须被拒绝并重新生成（防日志注入）。"""
    import scripts.api as api
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "LLM_API_KEY", "")
    with TestClient(api.app) as client:
        resp = client.get("/health", headers={"X-Trace-Id": bad})
    tid = resp.headers["X-Trace-Id"]
    assert tid != bad
    assert len(tid) == 36  # 回退到新生成的 UUID


def test_run_task_response_carries_trace_id(monkeypatch):
    """响应体也带 trace_id：用户报问题时可直接给出。"""
    import scripts.api as api
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "LLM_API_KEY", "")
    with TestClient(api.app) as client:
        resp = client.post("/run_task", json={"query": "hi"})
    # LLM 未配置 → 503， Header 仍应有 trace_id
    assert len(resp.headers.get("X-Trace-Id", "")) == 36


# ============ 日志字段 ============

def test_execute_graph_binds_trace_id_in_worker_thread(monkeypatch):
    """端到端验证线程池绑定：_execute_graph 内必须能读到传入的 trace_id。

    这是整条链路最容易断的一环——run_in_executor 不传播 contextvars，
    若漏了 bind_trace_id，所有工具调用日志的 trace_id 都会是 "-"。
    """
    import scripts.api as api

    # 用桩替换真正的图执行，只检查线程内上下文
    monkeypatch.setattr(
        api, "_execute_graph_inner", lambda q, b, t: {"seen": tc.get_trace_id()}
    )
    got = api._execute_graph("q", 1000, None, "tid-worker-1")
    assert got["seen"] == "tid-worker-1"


def test_tool_call_log_contains_trace_id(caplog):
    import tools.wrapper as w

    caplog.set_level(logging.INFO, logger="pecs.tools.wrapper")
    with tc.bind_trace_id("tid-for-log"):
        w.log_tool_call("search", {"thread_id": "t1"}, 0.01, ok=True, args={"q": "x"})
    record = json.loads(caplog.records[-1].message)
    assert record["trace_id"] == "tid-for-log"


def test_tool_call_log_trace_id_placeholder_when_unbound(caplog):
    import tools.wrapper as w

    caplog.set_level(logging.INFO, logger="pecs.tools.wrapper")
    w.log_tool_call("search", None, 0.0, ok=True)
    record = json.loads(caplog.records[-1].message)
    assert record["trace_id"] == "-"  # 字段始终存在，类型稳定
