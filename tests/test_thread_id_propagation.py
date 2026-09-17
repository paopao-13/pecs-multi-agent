"""thread_id 真实链路传播测试（幂等键租户隔离的回归保护）。

## 背景：一个被测试盲区掩盖的真实缺陷

`tools/wrapper.py` 的 `idempotency_key` 设计为 `f"{thread_id}|{action}|{digest}"`，
并在注释中承诺"不同 thread_id 之间不串味""键里天然带租户边界"。

但 `agents/executor.py` 传给 `execute_tool` 的 context 取的是
`state.get("thread_id", "-")`，而 `AgentState` **此前没有 thread_id 字段**
→ 该值恒为 `"-"` → 幂等键退化成 `-|action|digest`，**不同租户的相同查询会
命中同一个缓存**（跨租户数据串味）。

## 为什么此前测不出来

已有测试（`tests/test_wrapper.py`、`tests/test_wrapper_state.py`、
`tests/test_tool_wrapper.py`）在构造 context 时**直接硬编码** thread_id：

    w.idempotency_key("search", {"q":"hello"}, {"thread_id":"ta-1"})
    assert k1 != k2      # 因为测试自己给了不同值，当然不等

它们验证的是"**给定 thread_id 时 wrapper 行为正确**"，而生产问题是
"**thread_id 根本没传进 state**"——测试构造的是已修复后的 context，
绕过了真实链路，所以缺陷被长期掩盖。

本文件专门覆盖那条链路：**create_initial_state → AgentState →
executor_node → execute_tool 的 context**，确保 thread_id 真的走到了终点。
"""
import pytest

from agents.executor import executor_node
from graph.builder import create_initial_state


def _state_with_plan(query="q", **init_kwargs):
    """构造一个含单个 search 步骤的初始状态（直接调 executor_node 用）。"""
    st = create_initial_state(query, **init_kwargs)
    st.plan = [
        {
            "id": 1,
            "action": "search",
            "description": "检索",
            "args": {"query": "x"},
            "status": "pending",
            "result": None,
            "retry_count": 0,
            "risk": "low",
            "depends_on": [],
        }
    ]
    return st


def _capture_context(monkeypatch, state):
    """拦截 execute_tool，返回它实际收到的 context。"""
    captured = {}

    def fake_execute_tool(action, args, context=None):
        captured.update(context or {})
        return "ok"

    monkeypatch.setattr("agents.executor.execute_tool", fake_execute_tool)
    executor_node(state)
    return captured


class TestThreadIdPropagation:
    """thread_id 是否真的从 state 走到了工具 context。"""

    def test_real_thread_id_reaches_tool_context(self, monkeypatch):
        """注入真实 thread_id 后，executor 必须把它传给工具。

        修复前：AgentState 无该字段 → 得到 "-"（幂等键跨租户串味）。
        """
        st = _state_with_plan(thread_id="tenant-real-9")
        captured = _capture_context(monkeypatch, st)
        assert captured.get("thread_id") == "tenant-real-9", (
            f"thread_id 未传播到工具 context，实际={captured.get('thread_id')!r}。"
            "这会让不同租户的幂等键退化成同一个，造成跨租户数据串味。"
        )

    def test_anonymous_request_keeps_dash(self, monkeypatch):
        """不传 thread_id 的匿名请求仍为 "-"，与改造前行为逐字一致。"""
        st = _state_with_plan()
        captured = _capture_context(monkeypatch, st)
        assert captured.get("thread_id") == "-"

    def test_explicit_none_normalized_to_dash(self, monkeypatch):
        """显式传 None 应归一为 "-"，避免键里出现 None。"""
        st = _state_with_plan(thread_id=None)
        captured = _capture_context(monkeypatch, st)
        assert captured.get("thread_id") == "-"

    def test_node_name_still_passed(self, monkeypatch):
        """权限校验依赖 node_name，必须一并传到位。"""
        st = _state_with_plan(thread_id="tenant-x")
        captured = _capture_context(monkeypatch, st)
        assert captured.get("node_name") == "executor_node"


class TestIdempotencyKeyIsolation:
    """端到端：不同 thread_id 的幂等键必须不同（这是隔离的根本）。"""

    def test_keys_differ_across_threads(self):
        from tools.wrapper import idempotency_key

        k1 = idempotency_key("search", {"query": "内部财报"},
                             {"thread_id": "tenant_a-111"})
        k2 = idempotency_key("search", {"query": "内部财报"},
                             {"thread_id": "tenant_b-222"})
        assert k1 != k2

    def test_same_thread_same_args_is_stable(self):
        from tools.wrapper import idempotency_key

        ctx = {"thread_id": "tenant_a-111"}
        k1 = idempotency_key("search", {"query": "q"}, ctx)
        k2 = idempotency_key("search", {"query": "q"}, ctx)
        assert k1 == k2, "同一会话同参数必须命中同一缓存键"
