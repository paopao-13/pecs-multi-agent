"""
工具统一包装器单元测试（P0 生产化增强）

覆盖：
  1. 错误类型枚举与异常分类（含 JSONDecodeError 优先级）
  2. 契约保护：所有错误文案前缀必须落在 tools._ERROR_MARKERS 内（约束 K1）
  3. 超时控制（正常 / 超时 / 异常）
  4. invoke_tool 的统一返回形态
  5. 开关行为：关闭时走原路径（等同改造前），开启时走包装器
  6. 【Day2】熔断：阈值触发 / 半开自愈 / 熔断时不执行工具
  7. 【Day2】幂等：仅只读工具缓存 / 参数与线程隔离 / 副作用工具不缓存
  8. 【Day2】权限白名单：越权拒绝且不执行 / 未配置节点默认放行
"""
import json
import time

import pytest

import tools.wrapper as wrapper
from tools import _ERROR_MARKERS, execute_tool, is_tool_success
from tools.wrapper import (
    READ_ONLY_TOOLS,
    ToolErrorType,
    breaker_is_open,
    breaker_record_failure,
    breaker_record_success,
    check_permission,
    classify_error,
    clear_idempotent_cache,
    format_error,
    idempotency_key,
    invoke_tool,
    reset_breakers,
    run_with_timeout,
)


@pytest.fixture(autouse=True)
def _isolate_wrapper_state():
    """熔断计数与幂等缓存是模块级全局状态，每个用例前后清空，避免相互污染。"""
    reset_breakers()
    clear_idempotent_cache()
    yield
    reset_breakers()
    clear_idempotent_cache()


# ============================================================
# 1. 异常分类
# ============================================================

class TestClassifyError:
    def test_json_decode_error(self):
        exc = json.JSONDecodeError("bad", "{}", 0)
        assert classify_error(exc) == ToolErrorType.JSON_PARSE_ERR

    def test_json_takes_priority_over_value_error(self):
        """JSONDecodeError 是 ValueError 的子类，必须归为 JSON_PARSE_ERR 而非 INVALID_ARGS"""
        exc = json.JSONDecodeError("bad", "", 0)
        assert isinstance(exc, ValueError)  # 前提确认
        assert classify_error(exc) == ToolErrorType.JSON_PARSE_ERR

    def test_timeout_error(self):
        assert classify_error(TimeoutError("slow")) == ToolErrorType.TIMEOUT

    def test_key_error_is_invalid_args(self):
        assert classify_error(KeyError("missing")) == ToolErrorType.INVALID_ARGS

    def test_type_error_is_invalid_args(self):
        assert classify_error(TypeError("bad type")) == ToolErrorType.INVALID_ARGS

    def test_sandbox_error_by_keyword(self):
        assert classify_error(RuntimeError("安全检查未通过：禁止调用 __import__")) == ToolErrorType.SANDBOX_ERR

    def test_unknown_error(self):
        assert classify_error(RuntimeError("某些没见过的故障")) == ToolErrorType.UNKNOWN


# ============================================================
# 2. 契约保护：错误文案必须能被 is_tool_success 判为失败（约束 K1）
# ============================================================

class TestErrorTextContract:
    def test_every_error_type_starts_with_error_marker(self):
        """包装器产生的每一种错误文案都必须以 _ERROR_MARKERS 前缀开头，
        否则 is_tool_success() 会把失败误判成成功。"""
        for error_type in ToolErrorType:
            message = format_error("search", error_type, sec=15)
            assert message.startswith(_ERROR_MARKERS), (
                f"{error_type} 的文案 {message!r} 不以错误标记开头，会破坏 is_tool_success 契约"
            )

    def test_every_error_type_is_judged_as_failure(self):
        for error_type in ToolErrorType:
            message = format_error("python", error_type, sec=15)
            assert is_tool_success(message) is False, f"{error_type} 应被判为失败"


# ============================================================
# 3. 超时控制
# ============================================================

class TestRunWithTimeout:
    def test_normal_execution_returns_result(self):
        result, exc, duration = run_with_timeout(lambda args: "ok", {}, timeout=2)
        assert result == "ok"
        assert exc is None
        assert duration >= 0

    def test_timeout_returns_timeout_error(self):
        def slow(_args):
            time.sleep(0.5)
            return "too late"

        result, exc, _duration = run_with_timeout(slow, {}, timeout=0.1)
        assert result is None
        assert isinstance(exc, TimeoutError)
        assert classify_error(exc) == ToolErrorType.TIMEOUT

    def test_tool_exception_is_captured_not_raised(self):
        def boom(_args):
            raise ValueError("参数炸了")

        result, exc, _duration = run_with_timeout(boom, {}, timeout=2)
        assert result is None
        assert isinstance(exc, ValueError)
        assert classify_error(exc) == ToolErrorType.INVALID_ARGS


# ============================================================
# 4. invoke_tool 统一返回形态
# ============================================================

class TestInvokeTool:
    def test_success_returns_str_and_no_error(self):
        result, error_type, duration = invoke_tool(lambda args: "42", "python", {}, timeout=2)
        assert result == "42"
        assert error_type is None
        assert duration >= 0

    def test_timeout_produces_failure_prefix(self):
        def slow(_args):
            time.sleep(0.4)
            return "late"

        result, error_type, _duration = invoke_tool(slow, "search", {}, timeout=0.1)
        assert error_type == ToolErrorType.TIMEOUT
        assert result.startswith(_ERROR_MARKERS)
        assert is_tool_success(result) is False

    def test_empty_result_is_treated_as_failure(self):
        """工具返回空串属静默失败，必须显式暴露而不是当作正常结果"""
        result, error_type, _duration = invoke_tool(lambda args: "   ", "search", {}, timeout=2)
        assert error_type == ToolErrorType.UNKNOWN
        assert is_tool_success(result) is False

    def test_context_fields_are_optional(self):
        """不传 context 不应报错（保持对旧调用方的兼容）"""
        result, error_type, _duration = invoke_tool(lambda args: "ok", "search", {}, context=None, timeout=2)
        assert result == "ok"
        assert error_type is None


# ============================================================
# 5. 开关行为（约束 K3：关闭时等同改造前）
# ============================================================

class TestWrapperSwitch:
    def test_disabled_path_preserves_legacy_behavior(self, monkeypatch):
        """开关关闭时，execute_tool 必须走原路径：异常文案仍是 '工具执行失败 [...]' 格式"""
        import tools as tools_pkg

        def boom(_args):
            raise RuntimeError("炸了")

        monkeypatch.setitem(tools_pkg.TOOL_REGISTRY, "boom_tool", boom)
        monkeypatch.setattr(tools_pkg, "TOOL_WRAPPER_ENABLED", False)

        result = tools_pkg.execute_tool("boom_tool", {})
        # 原路径的文案格式，逐字保留
        assert result == "工具执行失败 [boom_tool]: RuntimeError: 炸了"

    def test_enabled_path_uses_wrapper_and_classifies_error(self, monkeypatch):
        """开关开启时走包装器：异常被分类，且文案带错误前缀（可被 is_tool_success 正确判失败）"""
        import tools as tools_pkg

        def boom(_args):
            raise ValueError("参数炸了")

        monkeypatch.setitem(tools_pkg.TOOL_REGISTRY, "boom_tool", boom)
        monkeypatch.setattr(tools_pkg, "TOOL_WRAPPER_ENABLED", True)

        result = tools_pkg.execute_tool("boom_tool", {}, context={"thread_id": "t1", "node_name": "executor"})
        assert result.startswith(_ERROR_MARKERS)
        assert is_tool_success(result) is False

    def test_enabled_path_success_still_returns_plain_str(self, monkeypatch):
        """开关开启时成功路径仍返回原始 str，调用方契约不变"""
        import tools as tools_pkg

        monkeypatch.setitem(tools_pkg.TOOL_REGISTRY, "ok_tool", lambda args: "结果")
        monkeypatch.setattr(tools_pkg, "TOOL_WRAPPER_ENABLED", True)

        assert tools_pkg.execute_tool("ok_tool", {}) == "结果"

    def test_unknown_tool_message_unchanged(self):
        """未知工具的提示不因开关而变"""
        assert execute_tool("不存在的工具", {}).startswith("错误：未知工具")


# ============================================================
# 6. Day2 熔断
# ============================================================

def _counting_tool(counter, result="结果"):
    """返回一个会记录调用次数的假工具。"""
    def fn(args):
        counter.append(args)
        return result
    return fn


class TestCircuitBreaker:
    def test_disabled_never_opens(self, monkeypatch):
        """熔断开关关闭时，无论失败多少次都不熔断（等同改造前）"""
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_ENABLED", False)
        for _ in range(10):
            breaker_record_failure("search")
        assert breaker_is_open("search") is False

    def test_opens_after_threshold(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_ENABLED", True)
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_THRESHOLD", 3)
        breaker_record_failure("search")
        breaker_record_failure("search")
        assert breaker_is_open("search") is False  # 未达阈值
        breaker_record_failure("search")
        assert breaker_is_open("search") is True   # 达阈值

    def test_success_resets_failure_counter(self, monkeypatch):
        """成功一次即清零，避免「历史累计失败」误触发熔断"""
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_ENABLED", True)
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_THRESHOLD", 3)
        breaker_record_failure("search")
        breaker_record_failure("search")
        breaker_record_success("search")
        breaker_record_failure("search")
        breaker_record_failure("search")
        assert breaker_is_open("search") is False

    def test_auto_resets_after_window(self, monkeypatch):
        """RESET_SEC 到期后自动半开，清空计数并放行"""
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_ENABLED", True)
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_THRESHOLD", 3)
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_RESET_SEC", 60)
        for _ in range(3):
            breaker_record_failure("search")
        assert breaker_is_open("search") is True
        # 把打开时间回拨到窗口之外，模拟 60s 已过
        wrapper._breaker_state["search"]["opened_at"] = time.monotonic() - 61
        assert breaker_is_open("search") is False
        assert "search" not in wrapper._breaker_state  # 半开时已清空

    def test_isolated_per_tool(self, monkeypatch):
        """熔断按工具隔离：search 熔断不影响 python"""
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_ENABLED", True)
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_THRESHOLD", 3)
        for _ in range(3):
            breaker_record_failure("search")
        assert breaker_is_open("search") is True
        assert breaker_is_open("python") is False

    def test_invoke_short_circuits_when_open(self, monkeypatch):
        """熔断中调用必须【不执行】工具，直接返回 CIRCUIT_OPEN"""
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_ENABLED", True)
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_THRESHOLD", 3)
        for _ in range(3):
            breaker_record_failure("search")

        calls = []
        result, error_type, duration = invoke_tool(_counting_tool(calls), "search", {}, timeout=2)
        assert error_type == ToolErrorType.CIRCUIT_OPEN
        assert calls == []  # 关键：工具未被调用
        assert duration == 0.0
        assert is_tool_success(result) is False

    def test_repeated_failures_open_breaker_through_invoke(self, monkeypatch):
        """端到端：invoke_tool 连续失败达阈值后自动熔断，后续调用被短路"""
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_ENABLED", True)
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_THRESHOLD", 3)

        def boom(_args):
            raise ValueError("炸了")

        for _ in range(3):
            invoke_tool(boom, "search", {}, timeout=2)
        assert breaker_is_open("search") is True

        calls = []
        _, error_type, _ = invoke_tool(_counting_tool(calls), "search", {}, timeout=2)
        assert error_type == ToolErrorType.CIRCUIT_OPEN
        assert calls == []

    def test_success_through_invoke_keeps_breaker_closed(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_ENABLED", True)
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_THRESHOLD", 3)
        fn = lambda args: "ok"  # noqa: E731
        for _ in range(5):
            invoke_tool(fn, "search", {}, timeout=2)
        assert breaker_is_open("search") is False


# ============================================================
# 7. Day2 幂等
# ============================================================

class TestIdempotency:
    def test_read_only_set_is_expected(self):
        """只读工具集合必须与设计一致（写/副作用工具绝不在内）"""
        assert READ_ONLY_TOOLS == frozenset(
            {"search", "web_browse", "file_read", "file_parse", "multimodal"}
        )
        assert "python" not in READ_ONLY_TOOLS
        assert "api_call" not in READ_ONLY_TOOLS
        assert "webshop" not in READ_ONLY_TOOLS

    def test_disabled_calls_tool_every_time(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_IDEMPOTENT_ENABLED", False)
        calls = []
        fn = _counting_tool(calls, "缓存值")
        invoke_tool(fn, "search", {"query": "x"}, context={"thread_id": "t1"}, timeout=2)
        invoke_tool(fn, "search", {"query": "x"}, context={"thread_id": "t1"}, timeout=2)
        assert len(calls) == 2

    def test_read_only_tool_is_cached(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_IDEMPOTENT_ENABLED", True)
        calls = []
        fn = _counting_tool(calls, "缓存值")
        r1, e1, _ = invoke_tool(fn, "search", {"query": "x"}, context={"thread_id": "t1"}, timeout=2)
        r2, e2, _ = invoke_tool(fn, "search", {"query": "x"}, context={"thread_id": "t1"}, timeout=2)
        assert r1 == r2 == "缓存值"
        assert e1 is None and e2 is None
        assert len(calls) == 1  # 第二次命中缓存，未执行

    def test_side_effect_tool_not_cached(self, monkeypatch):
        """python / api_call / webshop 有副作用，绝不缓存"""
        monkeypatch.setattr(wrapper, "TOOL_IDEMPOTENT_ENABLED", True)
        calls = []
        fn = _counting_tool(calls, "ok")
        for _ in range(2):
            invoke_tool(fn, "python", {"code": "print(1)"}, context={"thread_id": "t1"}, timeout=2)
        assert len(calls) == 2

    def test_different_args_not_shared(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_IDEMPOTENT_ENABLED", True)
        calls = []
        fn = _counting_tool(calls, "ok")
        invoke_tool(fn, "search", {"query": "a"}, context={"thread_id": "t1"}, timeout=2)
        invoke_tool(fn, "search", {"query": "b"}, context={"thread_id": "t1"}, timeout=2)
        assert len(calls) == 2

    def test_different_thread_not_shared(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_IDEMPOTENT_ENABLED", True)
        calls = []
        fn = _counting_tool(calls, "ok")
        invoke_tool(fn, "search", {"query": "a"}, context={"thread_id": "t1"}, timeout=2)
        invoke_tool(fn, "search", {"query": "a"}, context={"thread_id": "t2"}, timeout=2)
        assert len(calls) == 2

    def test_key_is_order_insensitive(self):
        """参数字典键顺序不同不应产生不同幂等键"""
        k1 = idempotency_key("search", {"a": 1, "b": 2}, {"thread_id": "t"})
        k2 = idempotency_key("search", {"b": 2, "a": 1}, {"thread_id": "t"})
        assert k1 == k2

    def test_failed_call_is_not_cached(self, monkeypatch):
        """失败结果不进缓存，否则会把一次失败固化成后续所有调用的结果"""
        monkeypatch.setattr(wrapper, "TOOL_IDEMPOTENT_ENABLED", True)
        attempts = {"n": 0}

        def flaky(_args):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ValueError("第一次失败")
            return "第二次成功"

        _, error_type, _ = invoke_tool(flaky, "search", {"query": "x"}, context={"thread_id": "t1"}, timeout=2)
        assert error_type == ToolErrorType.INVALID_ARGS
        r2, e2, _ = invoke_tool(flaky, "search", {"query": "x"}, context={"thread_id": "t1"}, timeout=2)
        assert r2 == "第二次成功"
        assert e2 is None
        assert attempts["n"] == 2  # 失败未缓存，第二次真正执行


# ============================================================
# 8. Day2 权限白名单
# ============================================================

class TestPermissionWhitelist:
    def test_disabled_allows_all(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_PERMISSION_ENABLED", False)
        monkeypatch.setattr(wrapper, "PERMISSION_MAP", {"executor_node": ["search"]})
        assert check_permission("api_call", {"node_name": "executor_node"}) is True

    def test_missing_context_allows(self, monkeypatch):
        """无调用上下文时不拦截（权限依赖显式 node_name）"""
        monkeypatch.setattr(wrapper, "TOOL_PERMISSION_ENABLED", True)
        monkeypatch.setattr(wrapper, "PERMISSION_MAP", {"executor_node": ["search"]})
        assert check_permission("api_call", None) is True
        assert check_permission("api_call", {"thread_id": "t1"}) is True

    def test_unconfigured_node_allows_by_default(self, monkeypatch):
        """未在 map 里配置的节点默认全允许（宽松兜底）"""
        monkeypatch.setattr(wrapper, "TOOL_PERMISSION_ENABLED", True)
        monkeypatch.setattr(wrapper, "PERMISSION_MAP", {"executor_node": ["search"]})
        assert check_permission("api_call", {"node_name": "critic_node"}) is True

    def test_whitelisted_tool_allowed(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_PERMISSION_ENABLED", True)
        monkeypatch.setattr(wrapper, "PERMISSION_MAP", {"executor_node": ["search", "python"]})
        assert check_permission("search", {"node_name": "executor_node"}) is True
        assert check_permission("python", {"node_name": "executor_node"}) is True

    def test_non_whitelisted_tool_denied(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_PERMISSION_ENABLED", True)
        monkeypatch.setattr(wrapper, "PERMISSION_MAP", {"executor_node": ["search"]})
        assert check_permission("api_call", {"node_name": "executor_node"}) is False

    def test_wildcard_allows_all(self, monkeypatch):
        monkeypatch.setattr(wrapper, "TOOL_PERMISSION_ENABLED", True)
        monkeypatch.setattr(wrapper, "PERMISSION_MAP", {"executor_node": "*"})
        assert check_permission("api_call", {"node_name": "executor_node"}) is True

    def test_denied_call_does_not_execute_tool(self, monkeypatch):
        """越权时必须【不执行】工具，直接返回 PERMISSION_DENIED"""
        monkeypatch.setattr(wrapper, "TOOL_PERMISSION_ENABLED", True)
        monkeypatch.setattr(wrapper, "PERMISSION_MAP", {"executor_node": ["search"]})

        calls = []
        result, error_type, duration = invoke_tool(
            _counting_tool(calls), "api_call", {"url": "http://evil"},
            context={"node_name": "executor_node"}, timeout=2,
        )
        assert error_type == ToolErrorType.PERMISSION_DENIED
        assert calls == []  # 关键：工具未被调用
        assert duration == 0.0
        assert is_tool_success(result) is False

    def test_permission_precedence_over_breaker(self, monkeypatch):
        """同时越权且熔断时，优先返回 PERMISSION_DENIED（权限是策略层，先于可用性层）"""
        monkeypatch.setattr(wrapper, "TOOL_PERMISSION_ENABLED", True)
        monkeypatch.setattr(wrapper, "PERMISSION_MAP", {"executor_node": ["search"]})
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_ENABLED", True)
        monkeypatch.setattr(wrapper, "TOOL_BREAKER_THRESHOLD", 3)
        for _ in range(3):
            breaker_record_failure("api_call")

        calls = []
        _, error_type, _ = invoke_tool(
            _counting_tool(calls), "api_call", {}, context={"node_name": "executor_node"}, timeout=2,
        )
        assert error_type == ToolErrorType.PERMISSION_DENIED
        assert calls == []



# ============================================================
# 【真实链路】幂等缓存的租户/会话隔离（端到端）
# ============================================================

class TestIdempotentIsolationEndToEnd:
    """端到端验证：不同 thread_id 的只读工具调用**不会**共享缓存。

    为什么需要这一层：已有的幂等用例直接构造 `context={"thread_id": "t1"}`，
    验证的是"给定 thread_id 时 wrapper 正确"；但生产缺陷恰恰是"thread_id 没
    传进 state"（见 tests/test_thread_id_propagation.py）。本节从
    create_initial_state → executor_node → wrapper 走完整链路。

    ⚠️ 实现坑（踩过）：开关分散在**两个模块**，且不是各自一份副本——
      - `tools/__init__.py` 持有 `TOOL_WRAPPER_ENABLED`（决定是否走 wrapper 路径）
      - `tools/wrapper.py` 持有 `TOOL_IDEMPOTENT_ENABLED`（决定是否启用缓存）
      两者都从 config 导入。只开后者而没开前者，`execute_tool` 仍走原路径，
      缓存根本不生效——那会让"每次都真实执行"被误读成"隔离生效"（假阳性）。
      另注意：`tools.wrapper` **没有** `TOOL_WRAPPER_ENABLED` 属性
      （monkeypatch.setattr 对不存在的属性会直接报错，别想当然地写）。
    """

    @pytest.fixture
    def isolated_cache(self, monkeypatch):
        """开启 wrapper + 幂等，注册计数 fake 只读工具，返回调用计数器。"""
        import tools
        import tools.wrapper as w

        monkeypatch.setattr(tools, "TOOL_WRAPPER_ENABLED", True)
        monkeypatch.setattr(w, "TOOL_IDEMPOTENT_ENABLED", True)

        calls = {"n": 0}

        def fake_readonly(args: dict) -> str:
            calls["n"] += 1
            return f"result#{calls['n']}"

        monkeypatch.setitem(tools.TOOL_REGISTRY, "fake_ro", fake_readonly)
        monkeypatch.setattr(w, "READ_ONLY_TOOLS",
                            frozenset(set(w.READ_ONLY_TOOLS) | {"fake_ro"}))
        return calls

    @staticmethod
    def _run_once(thread_id: str) -> str:
        from agents.executor import executor_node
        from graph.builder import create_initial_state

        st = create_initial_state("q", thread_id=thread_id)
        st.plan = [{
            "id": 1, "action": "fake_ro", "description": "只读检索",
            "args": {"query": "同一份内部文档"}, "status": "pending",
            "result": None, "retry_count": 0, "risk": "low", "depends_on": [],
        }]
        executor_node(st)
        return st.results[0]["result"]

    def test_different_threads_do_not_share_cache(self, isolated_cache):
        """两个租户同参数 → 各自真实执行，不得命中对方缓存。"""
        a = self._run_once("tenant_a-111")
        b = self._run_once("tenant_b-222")
        assert a != b, f"跨租户串味：A 与 B 都得到 {a!r}"
        assert isolated_cache["n"] == 2

    def test_same_thread_hits_cache(self, isolated_cache):
        """同一租户重复调用 → 命中自己的缓存，不再真实执行。"""
        first = self._run_once("tenant_a-111")
        n_after_first = isolated_cache["n"]
        second = self._run_once("tenant_a-111")
        assert second == first
        assert isolated_cache["n"] == n_after_first, "同一会话同参数应命中缓存"

    def test_anonymous_threads_share_dash_keyspace(self, isolated_cache):
        """对照组：thread_id 都为 '-' 时共享键空间（这正是修复前的串味行为）。

        保留这条断言是为了把"缺陷长什么样"钉在测试里——一旦有人回退修复，
        其他用例会拿到对方的缓存，这里会立刻变红。
        """
        a = self._run_once("-")
        b = self._run_once("-")
        assert a == b
        assert isolated_cache["n"] == 1, "匿名请求共享 '-' 键空间（历史行为）"
