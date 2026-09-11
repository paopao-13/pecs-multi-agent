"""tools/wrapper.py 单元测试（目标：行覆盖 ≥90%）。

测试维度：
  - classify_error：五个分支 + JSON 优先于 ValueError 的顺序敏感性
  - run_with_timeout：正常 / 超时 / 异常 / 返回耗时
  - check_permission：白名单命中 / 通配 / 未配置节点 / 配置类型错误
  - invoke_tool 完整链路：权限拒绝 → 熔断短路 → 幂等命中 → 真执行
    （成功 / 异常 / 空结果），K1 错误前缀约束逐条断言
  - log_tool_call / format_error / _digest 的输出契约
"""
import json
import logging

import pytest

import tools.wrapper as w


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每个用例独立状态：清熔断/幂等、开关全开（需要测的用例自行关）。"""
    w.reset_breakers()
    w.clear_idempotent_cache()
    w.configure_shared_state(None)
    monkeypatch.setattr(w, "TOOL_BREAKER_ENABLED", True)
    monkeypatch.setattr(w, "TOOL_IDEMPOTENT_ENABLED", True)
    monkeypatch.setattr(w, "TOOL_PERMISSION_ENABLED", True)
    monkeypatch.setattr(w, "PERMISSION_MAP", {})
    yield
    w.reset_breakers()
    w.clear_idempotent_cache()
    w.configure_shared_state(None)


def _ok_tool(args):
    return f"result:{args.get('q', '')}"


# ============ classify_error ============

def test_classify_json_decode_error():
    assert w.classify_error(json.JSONDecodeError("x", "{", 0)) == w.ToolErrorType.JSON_PARSE_ERR


def test_classify_json_beats_valueerror_order():
    """JSONDecodeError 是 ValueError 子类：必须先判 JSON（顺序敏感性回归）。"""
    exc = json.JSONDecodeError("x", "{", 0)
    assert w.classify_error(exc) != w.ToolErrorType.INVALID_ARGS


def test_classify_timeout_variants():
    assert w.classify_error(TimeoutError()) == w.ToolErrorType.TIMEOUT
    from concurrent.futures import TimeoutError as FT
    assert w.classify_error(FT()) == w.ToolErrorType.TIMEOUT


def test_classify_invalid_args():
    assert w.classify_error(KeyError("k")) == w.ToolErrorType.INVALID_ARGS
    assert w.classify_error(TypeError()) == w.ToolErrorType.INVALID_ARGS
    assert w.classify_error(ValueError("bad param")) == w.ToolErrorType.INVALID_ARGS


def test_classify_sandbox_by_message():
    class SandboxError(Exception):
        pass

    assert w.classify_error(SandboxError("安全检查未通过: 危险代码")) == w.ToolErrorType.SANDBOX_ERR
    assert w.classify_error(SandboxError("sandbox violation")) == w.ToolErrorType.SANDBOX_ERR


def test_classify_unknown():
    class Weird(Exception):
        pass

    assert w.classify_error(Weird("whatever")) == w.ToolErrorType.UNKNOWN


# ============ run_with_timeout ============

def test_run_with_timeout_normal():
    result, exc, duration = w.run_with_timeout(_ok_tool, {"q": "hi"}, 5.0)
    assert result == "result:hi"
    assert exc is None
    assert duration >= 0


def test_run_with_timeout_expires():
    def _slow(args):
        import time as t

        t.sleep(5)

    result, exc, _ = w.run_with_timeout(_slow, {}, 0.1)
    assert result is None
    assert isinstance(exc, TimeoutError)


def test_run_with_timeout_tool_exception():
    def _boom(args):
        raise RuntimeError("boom")

    result, exc, _ = w.run_with_timeout(_boom, {}, 5.0)
    assert result is None
    assert isinstance(exc, RuntimeError)


# ============ check_permission ============

def test_permission_disabled_allows_all(monkeypatch):
    monkeypatch.setattr(w, "TOOL_PERMISSION_ENABLED", False)
    monkeypatch.setattr(w, "PERMISSION_MAP", {"planner_node": []})
    assert w.check_permission("python", {"node_name": "planner_node"}) is True


def test_permission_no_node_info_allows():
    """无 node_name 时不拦截（机制依赖调用方显式传节点）。"""
    assert w.check_permission("python", {}) is True
    assert w.check_permission("python", None) is True


def test_permission_wildcard_allows(monkeypatch):
    # 注意：check_permission 读的是模块级 PERMISSION_MAP（导入时从
    # TOOL_PERMISSION_MAP 拷贝固化），测试需 patch 该变量本身
    monkeypatch.setattr(w, "PERMISSION_MAP", {"executor_node": "*"})
    assert w.check_permission("python", {"node_name": "executor_node"}) is True


def test_permission_whitelist_hit_and_miss(monkeypatch):
    monkeypatch.setattr(w, "PERMISSION_MAP", {"executor_node": ["search", "file_read"]})
    assert w.check_permission("search", {"node_name": "executor_node"}) is True
    assert w.check_permission("python", {"node_name": "executor_node"}) is False


def test_permission_unlisted_node_allows(monkeypatch):
    monkeypatch.setattr(w, "PERMISSION_MAP", {"executor_node": ["search"]})
    assert w.check_permission("python", {"node_name": "critic_node"}) is True


def test_permission_broken_config_fails_open(monkeypatch):
    """配置写错类型时不误伤：按放行处理。"""
    monkeypatch.setattr(w, "PERMISSION_MAP", {"executor_node": 12345})
    assert w.check_permission("search", {"node_name": "executor_node"}) is True


# ============ format_error / K1 约束 ============

def test_format_error_prefixes_within_error_markers():
    """所有错误文案前缀必须落在 _ERROR_MARKERS 内（K1：is_tool_success 才能判失败）。"""
    from tools import _ERROR_MARKERS

    for error_type in w.ToolErrorType:
        msg = w.format_error("sometool", error_type, sec=5)
        assert any(msg.startswith(m) for m in _ERROR_MARKERS), f"{error_type}: {msg}"


def test_format_error_unknown_type_falls_back():
    msg = w.format_error("t", "not-a-real-type", sec=1)  # type: ignore[arg-type]
    assert "t" in msg  # 回退到 UNKNOWN 模板且带工具名


# ============ _digest ============

def test_digest_truncates_long_args():
    long_args = {"q": "x" * 500}
    d = w._digest(long_args, max_len=50)
    assert len(d) < 80 and d.endswith("...(truncated)")


def test_digest_unserializable_args():
    class Obj:
        def __str__(self):
            return "obj-str"

    assert w._digest({"a": Obj()})  # default=str 兜底，不抛异常
    assert w._digest(None) == ""


# ============ log_tool_call ============

def test_log_tool_call_structured_record(caplog):
    caplog.set_level(logging.INFO, logger="pecs.tools.wrapper")
    w.log_tool_call(
        "search", {"thread_id": "t1-a", "node_name": "executor_node", "iteration": 2},
        0.123, ok=True, args={"q": "x"},
    )
    record = json.loads(caplog.records[-1].message)
    assert record["event"] == "tool_call"
    assert record["tool"] == "search"
    assert record["thread_id"] == "t1-a"
    assert record["node"] == "executor_node"
    assert record["ok"] is True
    assert record["error_type"] is None
    assert record["duration_ms"] == 123.0


def test_log_tool_call_extra_fields(caplog):
    caplog.set_level(logging.INFO, logger="pecs.tools.wrapper")
    w.log_tool_call("search", None, 0.0, ok=True, extra={"cached": True})
    record = json.loads(caplog.records[-1].message)
    assert record["cached"] is True
    assert record["thread_id"] == "-"  # 无 context 时占位符


# ============ invoke_tool 完整链路 ============

def test_invoke_permission_denied_not_executed(monkeypatch):
    monkeypatch.setattr(w, "PERMISSION_MAP", {"executor_node": ["search"]})

    def _never(args):
        raise AssertionError("越权后不应执行工具")

    result, error_type, duration = w.invoke_tool(
        _never, "python", {}, context={"node_name": "executor_node"}
    )
    assert error_type == w.ToolErrorType.PERMISSION_DENIED
    assert duration == 0.0
    assert "越权" in result


def test_invoke_circuit_open_not_executed():
    for _ in range(w.TOOL_BREAKER_THRESHOLD):
        w.breaker_record_failure("search")

    def _never(args):
        raise AssertionError("熔断后不应执行工具")

    result, error_type, duration = w.invoke_tool(_never, "search", {"q": "x"})
    assert error_type == w.ToolErrorType.CIRCUIT_OPEN
    assert duration == 0.0
    assert str(w.TOOL_BREAKER_RESET_SEC) in result  # 文案含恢复时间


def test_invoke_idempotent_hit_skips_execution():
    key = w.idempotency_key("search", {"q": "cache-me"}, {"thread_id": "t1-ih"})
    w._idempotent_store(key, "cached-value")
    calls = []

    def _counting(args):
        calls.append(args)
        return "fresh"

    result, error_type, duration = w.invoke_tool(
        _counting, "search", {"q": "cache-me"}, context={"thread_id": "t1-ih"}
    )
    assert result == "cached-value"
    assert error_type is None
    assert duration == 0.0
    assert not calls  # 未真执行


def test_invoke_write_tools_bypass_cache():
    """非只读工具（如 python）不进幂等缓存——缓存会掩盖副作用。"""
    calls = []

    def _python(args):
        calls.append(args)
        return "side-effect"

    ctx = {"thread_id": "t1-w"}
    w.invoke_tool(_python, "python", {"code": "x=1"}, context=ctx)
    w.invoke_tool(_python, "python", {"code": "x=1"}, context=ctx)
    assert len(calls) == 2  # 两次都真执行


def test_invoke_success_records_and_caches():
    result, error_type, duration = w.invoke_tool(
        _ok_tool, "search", {"q": "ok"}, context={"thread_id": "t1-ok"}
    )
    assert result == "result:ok"
    assert error_type is None
    # 成功后幂等缓存里应有该键
    key = w.idempotency_key("search", {"q": "ok"}, {"thread_id": "t1-ok"})
    assert w._idempotent_lookup(key) == "result:ok"


def test_invoke_exception_becomes_structured_error_and_fails_breaker():
    def _boom(args):
        raise ValueError("bad")

    result, error_type, _ = w.invoke_tool(_boom, "search", {"q": "x"})
    assert error_type == w.ToolErrorType.INVALID_ARGS
    assert result.startswith("执行错误")
    # 失败计入熔断
    failures, _ = _local_breaker("search")
    assert failures == 1


def _local_breaker(action):
    state = w._breaker_state.get(action, {})
    return state.get("failures", 0), state.get("opened_at")


def test_invoke_empty_result_counts_as_failure():
    def _empty(args):
        return "   "

    result, error_type, _ = w.invoke_tool(_empty, "search", {"q": "x"})
    assert error_type == w.ToolErrorType.UNKNOWN
    assert "空结果" in result
    failures, _ = _local_breaker("search")
    assert failures == 1  # 静默失败也要计入熔断


def test_invoke_success_resets_breaker():
    w.breaker_record_failure("search")
    w.invoke_tool(_ok_tool, "search", {"q": "heal"})
    failures, _ = _local_breaker("search")
    assert failures == 0  # 成功清零


def test_invoke_timeout_path():
    def _slow(args):
        import time as t

        t.sleep(5)

    result, error_type, _ = w.invoke_tool(_slow, "search", {}, timeout=0.1)
    assert error_type == w.ToolErrorType.TIMEOUT
    assert "超时" in result
