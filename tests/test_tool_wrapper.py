"""
工具统一包装器单元测试（P0 生产化增强）

覆盖：
  1. 错误类型枚举与异常分类（含 JSONDecodeError 优先级）
  2. 契约保护：所有错误文案前缀必须落在 tools._ERROR_MARKERS 内（约束 K1）
  3. 超时控制（正常 / 超时 / 异常）
  4. invoke_tool 的统一返回形态
  5. 开关行为：关闭时走原路径（等同改造前），开启时走包装器
"""
import json
import time

import pytest

from tools import _ERROR_MARKERS, execute_tool, is_tool_success
from tools.wrapper import (
    ToolErrorType,
    classify_error,
    format_error,
    invoke_tool,
    run_with_timeout,
)


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
