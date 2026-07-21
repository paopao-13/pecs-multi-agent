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
