"""测试 conftest.py 的 API key 跳过逻辑

验证 conftest 检查的是 LLM_API_KEY（项目实际使用的变量名），
而不是 OPENAI_API_KEY（遗留的错误变量名）。
"""
import pytest


class _MockItem:
    """模拟 pytest.Item，只记录 add_marker 调用"""
    def __init__(self, has_marker):
        self.keywords = {"requires_api_key"} if has_marker else set()
        self.add_marker_calls = []

    def add_marker(self, marker):
        self.add_marker_calls.append(marker)


class _MockConfig:
    pass


def test_conftest_skips_api_test_when_no_llm_api_key(monkeypatch):
    """没有 LLM_API_KEY 时，requires_api_key 测试应被跳过"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from tests.conftest import pytest_collection_modifyitems

    item = _MockItem(has_marker=True)
    pytest_collection_modifyitems(_MockConfig(), [item])
    assert len(item.add_marker_calls) == 1  # 应该被标记为 skip


def test_conftest_runs_api_test_when_llm_api_key_set(monkeypatch):
    """设置了 LLM_API_KEY 时，requires_api_key 测试不应被跳过"""
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from tests.conftest import pytest_collection_modifyitems

    item = _MockItem(has_marker=True)
    pytest_collection_modifyitems(_MockConfig(), [item])
    assert len(item.add_marker_calls) == 0  # 不应该被标记为 skip


def test_conftest_ignores_openai_api_key(monkeypatch):
    """只设置 OPENAI_API_KEY（不设 LLM_API_KEY）时，仍应跳过

    这个测试复现 bug #7：conftest 错误地检查 OPENAI_API_KEY，
    导致只设了 OPENAI_API_KEY 时不跳过（但项目实际用 LLM_API_KEY）。
    """
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")

    from tests.conftest import pytest_collection_modifyitems

    item = _MockItem(has_marker=True)
    pytest_collection_modifyitems(_MockConfig(), [item])
    # 修复后：检查 LLM_API_KEY，没设 → 应该跳过
    assert len(item.add_marker_calls) == 1, "不应因为 OPENAI_API_KEY 存在就不跳过"
