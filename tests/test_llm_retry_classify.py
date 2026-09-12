"""LLM 错误分类与重试策略测试。

背景（实测驱动的改造）：改造前的重试判断是**纯关键词子串匹配**，实测暴露两类问题——

  假阳性：错误消息里出现 "limit" 就重试。于是"上下文超长"
         （maximum context length limit exceeded）和"参数名 limit 非法"
         都会被重试 3 次，每次退避 8/16/32s，合计白等约 56 秒，
         而这两种错误重试必然还是失败。
  假阴性：500 / 502 / 504 这类典型可重试的服务端错误，消息里往往不命中
         关键词，于是**一次都不重试**，瞬时故障被当成永久故障。

改造后：优先解析显式 HTTP 状态码，缺失时才走收紧后的关键词兜底；
未知错误默认不重试（宁可少等，不可白等）。

测试全程 mock get_llm 并屏蔽 sleep，零网络、零额度、零等待。
"""
import time

import pytest

import agents.llm_utils as lu
from config import MAX_RETRIES


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """屏蔽退避等待（否则每个重试用例会真等 8/16/32 秒）。

    注意：llm_utils 内部是函数级 `import time as _time`，拿到的是同一个
    time 模块对象，因此 patch 模块属性即可生效。
    """
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _invoke_with_error(msg: str, monkeypatch) -> int:
    """让 LLM 每次都抛给定错误，返回实际调用次数。"""
    calls = {"n": 0}

    def fake_invoke(messages):
        calls["n"] += 1
        raise RuntimeError(msg)

    class FakeLLM:
        invoke = staticmethod(fake_invoke)

    monkeypatch.setattr(lu, "get_llm", lambda role="default": FakeLLM())
    lu.call_llm("hi", "sys", role="default")
    return calls["n"]


# ============ classify_llm_error：状态码优先 ============

@pytest.mark.parametrize(
    "msg,expected",
    [
        ("Error code: 429 - rate limit exceeded", "retryable"),
        ("Error code: 500 - internal server error", "retryable"),
        ("Error code: 502 - bad gateway", "retryable"),
        ("Error code: 503 - service unavailable", "retryable"),
        ("Error code: 504 - gateway timeout", "retryable"),
        ("Error code: 408 - request timeout", "retryable"),
        ("Error code: 409 - conflict", "retryable"),
    ],
)
def test_retryable_status_codes(msg, expected):
    assert lu.classify_llm_error(RuntimeError(msg)) == expected


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("Error code: 400 - bad request", "terminal"),
        ("Error code: 401 - unauthorized", "terminal"),
        ("Error code: 403 - forbidden", "terminal"),
        ("Error code: 404 - model not found", "terminal"),
        ("Error code: 413 - payload too large", "terminal"),
        ("Error code: 422 - unprocessable entity", "terminal"),
    ],
)
def test_terminal_status_codes(msg, expected):
    assert lu.classify_llm_error(RuntimeError(msg)) == expected


def test_status_code_pattern_variants():
    """状态码引导词的几种常见写法都要能识别。"""
    for msg in ["status_code=429", "HTTP 503", "HTTP/1.1 500", "error_code:429"]:
        assert lu.classify_llm_error(RuntimeError(msg)) == "retryable", msg


def test_long_number_not_mistaken_for_status_code():
    """上下文长度 128000 不得被误当状态码（正是改造前的假阳性来源之一）。"""
    exc = RuntimeError("maximum context length limit exceeded: 128000 tokens")
    assert lu.classify_llm_error(exc) == "terminal"


# ============ 关键词兜底（收紧后） ============

@pytest.mark.parametrize(
    "msg",
    [
        "Connection timeout after 30s",
        "connection reset by peer",
        "service temporarily unavailable",
        "The engine is currently overloaded, please try again later",
        "rate limit reached for requests",
        "too many requests",
        "quota exceeded for this minute",
    ],
)
def test_retryable_keywords(msg):
    assert lu.classify_llm_error(RuntimeError(msg)) == "retryable"


@pytest.mark.parametrize(
    "msg",
    [
        "insufficient balance, please top up",
        "Invalid API key provided",
        "maximum context length is 128000 tokens",
        "your request was rejected by the content policy",
        "unknown parameter: max_tokens_limit",   # 改造前误判为可重试
        "invalid parameter: limit must be <= 100",  # 改造前误判为可重试
    ],
)
def test_terminal_keywords(msg):
    assert lu.classify_llm_error(RuntimeError(msg)) == "terminal"


def test_terminal_wins_over_retryable_keyword():
    """同时命中两类关键词时按终止处理（保守：不为确定失败的请求继续等待）。"""
    exc = RuntimeError("insufficient balance; service temporarily unavailable")
    assert lu.classify_llm_error(exc) == "terminal"


def test_unknown_error_defaults_to_terminal():
    """未知错误不赌——白等 56s 的代价大于漏重试。"""
    assert lu.classify_llm_error(RuntimeError("something completely unexpected")) == "terminal"


def test_exception_type_name_participates():
    """异常类名也参与判定：TimeoutError 无需消息文本即可判可重试。"""
    assert lu.classify_llm_error(TimeoutError()) == "retryable"


def test_legacy_mode_switch(monkeypatch):
    """PEC_RETRY_CLASSIFY=0 回退旧行为（纯关键词子串匹配），便于线上快速回退。"""
    monkeypatch.setenv("PEC_RETRY_CLASSIFY", "0")
    # 旧行为下 "limit" 是关键词，上下文超长会被判可重试
    assert lu.classify_llm_error(RuntimeError("maximum context length limit exceeded")) == "retryable"
    # 旧行为下 500（消息不含关键词）反而被判不可重试
    assert lu.classify_llm_error(RuntimeError("Error code: 500 - server error")) == "terminal"


# ============ 端到端：call_llm 的实际重试次数 ============

def test_call_llm_retries_on_500(monkeypatch):
    """500 必须重试（改造前不重试——瞬时故障被当永久故障）。"""
    assert _invoke_with_error("Error code: 500 - internal server error", monkeypatch) == MAX_RETRIES


def test_call_llm_does_not_retry_on_401(monkeypatch):
    assert _invoke_with_error("Error code: 401 - unauthorized", monkeypatch) == 1


def test_call_llm_does_not_retry_on_context_overflow(monkeypatch):
    """上下文超长重试必然还是超长——改造前白等约 56 秒。"""
    n = _invoke_with_error("maximum context length limit exceeded: 128000", monkeypatch)
    assert n == 1


def test_call_llm_retries_on_rate_limit(monkeypatch):
    assert _invoke_with_error("Error code: 429 - rate limit exceeded", monkeypatch) == MAX_RETRIES


def test_failure_returns_structured_prefix(monkeypatch):
    """重试耗尽后仍返回统一失败前缀（下游 is_tool_success / 启发式判定依赖它）。"""
    monkeypatch.setattr(lu, "get_llm", lambda role="default": type(
        "F", (), {"invoke": staticmethod(lambda m: (_ for _ in ()).throw(RuntimeError("nope")))}
    )())
    text, tokens = lu.call_llm("hi", "sys", role="default")
    assert text.startswith(lu.LLM_FAILURE_PREFIX)
    assert tokens == 0
