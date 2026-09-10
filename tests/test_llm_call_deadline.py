"""call_llm 整体墙钟上界（LLM_CALL_DEADLINE）单元测试

背景：get_llm() 的 timeout=60 只约束「单次 HTTP 请求」，而一次 llm.invoke()
内部还有 openai SDK 自己的 max_retries=2（最多 3 次请求）⇒ 单次 invoke 最长
180s；外层再重试 3 次 + 8/16/32s 退避 ⇒ **最坏约 9 分钟且无整体上界**。
实测网关「只连不发」时单道 GAIA 题空转 15 分钟即由此导致。

测试要点：
- 正常成功路径不受影响（deadline 不该误伤快调用）
- 到点后不再 sleep 退避（原本会白等 8+16=24s）
- LLM_CALL_DEADLINE=0 时保留旧行为（可回退）

注：sleep 用 monkeypatch 记录而非真睡，保证用例毫秒级完成。
"""
import time

import pytest

import agents.llm_utils as lu


class _FakeResp:
    def __init__(self, content="ok"):
        self.content = content
        self.usage_metadata = {"total_tokens": 7}
        self.response_metadata = {}


class _FakeLLM:
    def __init__(self, exc=None):
        self._exc = exc
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return _FakeResp()


@pytest.fixture
def _with_key(monkeypatch):
    """伪造已配置 Key（模块级常量需直接改属性，改 env 无效）"""
    monkeypatch.setattr(lu, "LLM_API_KEY", "sk-test-key")
    monkeypatch.setenv("LLM_MIN_GAP", "0")


def test_success_unaffected_by_deadline(monkeypatch, _with_key):
    """正常成功：deadline 不介入，只调用一次"""
    monkeypatch.setenv("LLM_CALL_DEADLINE", "120")
    fake = _FakeLLM()
    monkeypatch.setattr(lu, "get_llm", lambda role="default": fake)

    out, token_used = lu.call_llm("hi", role="executor")

    assert out == "ok"
    assert token_used == 7
    assert fake.calls == 1


def test_deadline_skips_backoff(monkeypatch, _with_key):
    """到点后不再退避等待：原本会 sleep 8+16=24s，实际应一次都不睡"""
    monkeypatch.setenv("LLM_CALL_DEADLINE", "1")
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    fake = _FakeLLM(exc=RuntimeError("rate limit exceeded"))
    monkeypatch.setattr(lu, "get_llm", lambda role="default": fake)

    out, _ = lu.call_llm("hi", role="executor")

    assert lu.is_llm_failure(out)
    assert slept == [], f"deadline 已到仍在退避: {slept}"
    assert fake.calls == 1


def test_deadline_disabled_preserves_backoff(monkeypatch, _with_key):
    """LLM_CALL_DEADLINE=0 → 保留旧行为（8/16s 退避、共 3 次尝试），确保可回退"""
    monkeypatch.setenv("LLM_CALL_DEADLINE", "0")
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    fake = _FakeLLM(exc=RuntimeError("rate limit exceeded"))
    monkeypatch.setattr(lu, "get_llm", lambda role="default": fake)

    out, _ = lu.call_llm("hi", role="executor")

    assert lu.is_llm_failure(out)
    assert slept == [8, 16], f"旧行为应保持 8/16 退避，实际 {slept}"
    assert fake.calls == 3
