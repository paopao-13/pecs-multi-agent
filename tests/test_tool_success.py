"""
工具执行结果成功判定单元测试

测试 tools/__init__.is_tool_success：
- 以显式错误前缀（错误 / 执行错误 / 安全检查未通过）判定失败
- 不再把含"失败"字样的正常值（如"失败率 = 0.05"）误判为失败

接口说明（已通过阅读源码确认）：
    from tools import is_tool_success
    is_tool_success(result: str) -> bool
"""
from tools import is_tool_success


def test_failure_rate_not_false_negative():
    """含"失败"字样的统计结果不应被误判为失败"""
    assert is_tool_success("失败率 = 0.05") is True
    assert is_tool_success("失败次数 = 3") is True


def test_normal_result_success():
    """正常工具输出应判为成功"""
    assert is_tool_success("输出:\n42") is True
    assert is_tool_success("2 的 100 次方 = 126765...") is True
    assert is_tool_success("执行成功（无输出）") is True


def test_error_prefix_failure():
    """三类错误前缀必须判为失败"""
    assert is_tool_success("错误：缺少 code 参数") is False
    assert is_tool_success("执行错误:\nTraceback (most recent call last):") is False
    assert is_tool_success("安全检查未通过，拒绝执行:\n  - 禁止调用 'open()'") is False


def test_empty_result_failure():
    """空结果判为失败"""
    assert is_tool_success("") is False


def test_execute_tool_exception_text_is_failure():
    """execute_tool 原路径的异常文案必须判为失败（2026-09-12 修复）。

    背景：execute_tool 的**原路径**（TOOL_WRAPPER_ENABLED=false，默认）在工具
    抛异常时返回 `"工具执行失败 [<action>]: <类型>: <信息>"`，但该文案此前不在
    _ERROR_MARKERS 里 → is_tool_success 返回 True。

    后果链（静默失败，比崩溃更危险）：
      工具崩溃 → 判为成功 → executor 记 success=True、步骤置 done
      → Critic 走"合格"路径 → Planner 不重试 → 最终答案建立在失败步骤上。
    整条链路无人察觉，而崩溃至少会被发现。

    wrapper 路径的错误文案以 "执行错误：" 开头（已被覆盖），原路径是唯一漏网分支。
    """
    assert is_tool_success("工具执行失败 [python]: RuntimeError: boom") is False
    assert is_tool_success("工具执行失败 [web_search]: TimeoutError: timed out") is False


def test_wrapper_error_texts_are_failures():
    """wrapper 各错误类型的文案都必须判为失败（约束 K1 的双向一致性）。"""
    from tools.wrapper import ToolErrorType, format_error

    for et in ToolErrorType:
        msg = format_error("python", et, sec=15)
        assert is_tool_success(msg) is False, f"{et} 的文案未被识别为失败: {msg}"


def test_all_error_markers_are_recognized():
    """_ERROR_MARKERS 里的每个前缀都必须真的被判定为失败（防止契约单侧失效）。"""
    from tools import _ERROR_MARKERS

    for marker in _ERROR_MARKERS:
        assert is_tool_success(marker + "：示例") is False, f"前缀 {marker} 未被识别"

    # 反向保护：正常统计值不能被误判（这是当初不用裸"失败"二字做标记的原因）
    for normal in ("失败率 = 0.05", "失败次数 = 3", "共失败 2 条，成功率 80%"):
        assert is_tool_success(normal) is True, f"正常值被误判为失败: {normal}"
