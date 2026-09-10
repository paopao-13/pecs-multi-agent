"""阶段 0 修复的回归测试（5 项小修，全部可独立回退）。

覆盖：
  1. 超长输入必须被 413 挡在校验层（原上限 10000 会让请求跑满四角色图 30s+，占满 worker）
  2. 重试退避必须有抖动（固定退避会形成同步重试风暴）
  3. mock 检索数据只在 eval 模式生效（生产路径不得返回编造内容）
"""
import time

import pytest

import agents.llm_utils as lu
import scripts.api as api

# 注意：不能直接 `import tools.web_search as ws` —— tools/__init__.py 里有
# `from tools.web_search import web_search`，同名函数会遮蔽子模块属性，
# 得到的会是函数而不是模块（monkeypatch 模块级常量会报 AttributeError）。
# 用 importlib 显式取模块对象是唯一稳妥写法。
import importlib

ws = importlib.import_module("tools.web_search")


# ---------------------------------------------------------------- 通用桩件
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


# ------------------------------------------------- 1. 超长输入边界（N1）
def test_query_at_limit_is_allowed(monkeypatch):
    """正好等于上限：属于合法输入，不得被 413 拦截"""
    monkeypatch.setattr(api, "MAX_QUERY_CHARS", 4000)
    assert api._validate_query("A" * 4000) is None


def test_query_over_limit_is_rejected(monkeypatch):
    """超过上限 1 个字符：必须拦截（上限含端点，len > MAX 才拒）"""
    monkeypatch.setattr(api, "MAX_QUERY_CHARS", 4000)
    msg = api._validate_query("A" * 4001)
    assert msg is not None and "过长" in msg


def test_run_task_returns_413_for_overlong_query(monkeypatch):
    """端到端：超长 query 返回 413，且不进入 LLM 链路（无 Key 也不该变成 503）"""
    from fastapi.testclient import TestClient

    # 置空 Key → lifespan 跳过真实探测，测试全程不打网络
    monkeypatch.setattr(api, "LLM_API_KEY", "")
    monkeypatch.setattr(api, "MAX_QUERY_CHARS", 100)

    with TestClient(api.app) as client:
        resp = client.post("/run_task", json={"query": "A" * 101})

    assert resp.status_code == 413, resp.text


def test_default_limit_rejects_10k_query(monkeypatch):
    """历史上 10000 字符的请求会跑满 30s；按当前默认上限必须被直接拒绝"""
    from config import MAX_QUERY_CHARS

    monkeypatch.setattr(api, "MAX_QUERY_CHARS", MAX_QUERY_CHARS)
    assert api._validate_query("A" * 10000) is not None


# ------------------------------------------------- 2. 重试退避抖动
def test_backoff_has_jitter_within_bounds(monkeypatch, _with_key):
    """退避值落在 [base/2, base]：base 分别为 8 / 16"""
    monkeypatch.setenv("LLM_CALL_DEADLINE", "0")  # 关掉 deadline，确保退避发生
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(lu, "get_llm", lambda role="default": _FakeLLM(
        exc=RuntimeError("rate limit exceeded")))

    lu.call_llm("hi")

    assert len(slept) == 2, f"应退避两次（3 次尝试），实际 {slept}"
    assert 4.0 <= slept[0] <= 8.0, f"第一次退避应在 [4,8]，实际 {slept[0]}"
    assert 8.0 <= slept[1] <= 16.0, f"第二次退避应在 [8,16]，实际 {slept[1]}"


def test_backoff_is_not_a_constant(monkeypatch, _with_key):
    """同样的失败不应每次都等完全相同的时长（否则就是同步重试风暴）"""
    monkeypatch.setenv("LLM_CALL_DEADLINE", "0")
    first_waits = []

    for _ in range(8):
        slept = []
        monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
        monkeypatch.setattr(lu, "get_llm", lambda role="default": _FakeLLM(
            exc=RuntimeError("rate limit exceeded")))
        lu.call_llm("hi")
        first_waits.append(round(slept[0], 6))

    assert len(set(first_waits)) > 1, f"退避值完全固定，抖动未生效：{first_waits}"


def test_jitter_respects_deadline(monkeypatch, _with_key):
    """deadline 仍然优先：到点后不再退避"""
    monkeypatch.setenv("LLM_CALL_DEADLINE", "1")
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    fake = _FakeLLM(exc=RuntimeError("rate limit exceeded"))
    monkeypatch.setattr(lu, "get_llm", lambda role="default": fake)

    out, _ = lu.call_llm("hi")

    assert lu.is_llm_failure(out)
    assert slept == [], f"deadline 已到仍在退避：{slept}"


# ------------------------------------------------- 3. mock 仅 eval 模式
def test_mock_hits_in_eval_mode(monkeypatch):
    """eval 模式：mock 优先命中，保证内置样例可复现（既有行为不变）"""
    monkeypatch.setattr(ws, "RUN_MODE", "eval")
    out = ws.web_search({"query": "python"})
    assert out.startswith("[模拟搜索]") and "未找到" not in out


def test_mock_skipped_outside_eval_mode(monkeypatch):
    """非 eval 模式：绝不返回 mock 的编造内容，必须走真实检索"""
    monkeypatch.setattr(ws, "RUN_MODE", "business")
    monkeypatch.setattr(ws, "_ddgs_search", lambda q, n: "REAL_RESULT")
    out = ws.web_search({"query": "python"})
    assert out == "REAL_RESULT"


def test_mock_not_returned_when_real_search_empty(monkeypatch):
    """非 eval 模式且真实检索无结果：返回降级文本，而不是回落到 mock"""
    monkeypatch.setattr(ws, "RUN_MODE", "business")
    monkeypatch.setattr(ws, "_ddgs_search", lambda q, n: "")
    monkeypatch.setattr(ws, "_duckduckgo_instant_answer", lambda q: "")
    out = ws.web_search({"query": "python"})
    assert not out.startswith("[模拟搜索]")
