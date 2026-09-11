"""tools/web_search.py 与 tools/web_browser.py 单元测试。

两个模块都依赖外部网络，测试策略是「只测本地可判定的分支」：
  - 参数校验（缺 query / 缺 url）
  - mock 命中路径（不产生网络请求）
  - 真实后端被 mock 替换后的调用顺序与降级链
  - 网络异常时的错误文案（必须落在 _ERROR_MARKERS 内，否则
    is_tool_success 会把失败误判成成功）

绝不发起真实网络请求——测试必须离线可跑、且零额度消耗。
"""
import importlib

import pytest

from tools import _ERROR_MARKERS

# 注意：tools/__init__.py 里 `from tools.web_search import web_search` 会把
# tools 包的 web_search 属性覆盖成"函数"，因此 `import tools.web_search as ws`
# 拿到的是函数而非模块。必须从 sys.modules 取真正的模块对象。
ws = importlib.import_module("tools.web_search")
wb = importlib.import_module("tools.web_browser")


def _is_error_text(s: str) -> bool:
    return any(s.startswith(m) for m in _ERROR_MARKERS)


# ============ web_search：参数校验 ============

def test_web_search_missing_query():
    out = ws.web_search({})
    assert _is_error_text(out), f"缺 query 必须返回错误前缀文案，实际: {out}"


def test_web_search_empty_query():
    assert _is_error_text(ws.web_search({"query": "   "}))


# ============ web_search：mock 路径（eval 模式） ============

def test_web_search_eval_mode_hits_mock(monkeypatch):
    """eval 模式下命中预置键 → 返回 canned 内容（保证内置样例可复现）。"""
    monkeypatch.setattr(ws, "RUN_MODE", "eval")
    out = ws.web_search({"query": "python"})
    assert "Python" in out


def test_web_search_eval_mode_miss_returns_no_result(monkeypatch):
    """eval 模式未命中 mock 且真实后端全失败 → 回落 mock 的未找到文案。"""
    monkeypatch.setattr(ws, "RUN_MODE", "eval")
    monkeypatch.setattr(ws, "_ddgs_search", lambda q, n: "")
    monkeypatch.setattr(ws, "_duckduckgo_instant_answer", lambda q: "")
    monkeypatch.setattr(ws, "SEARCH_PROVIDER", "none")
    out = ws.web_search({"query": "绝对不会命中的查询词xyzzy"})
    assert "未找到" in out or "未检索到" in out


# ============ web_search：非 eval 模式绝不回落 mock ============

def test_web_search_business_mode_never_falls_back_to_mock(monkeypatch):
    """business 模式下真实后端全失败 → 明确"未检索到"，绝不返回编造内容。"""
    monkeypatch.setattr(ws, "RUN_MODE", "business")
    monkeypatch.setattr(ws, "SEARCH_PROVIDER", "none")
    monkeypatch.setattr(ws, "_ddgs_search", lambda q, n: "")
    monkeypatch.setattr(ws, "_duckduckgo_instant_answer", lambda q: "")
    out = ws.web_search({"query": "python"})
    assert "未检索到" in out
    assert "Guido" not in out, "business 模式回落到 mock 属于数据污染"


# ============ web_search：降级链顺序 ============

def test_web_search_tavily_preferred_when_configured(monkeypatch):
    monkeypatch.setattr(ws, "RUN_MODE", "business")
    monkeypatch.setattr(ws, "SEARCH_PROVIDER", "tavily")
    monkeypatch.setattr(ws, "SEARCH_API_KEY", "fake-key")
    calls = []
    monkeypatch.setattr(ws, "_tavily_search", lambda q, n: calls.append("tavily") or "TAVILY")
    monkeypatch.setattr(ws, "_ddgs_search", lambda q, n: calls.append("ddgs") or "DDGS")
    out = ws.web_search({"query": "x"})
    assert out == "TAVILY"
    assert calls == ["tavily"]  # Tavily 成功则不走 DDGS


def test_web_search_falls_back_to_ddgs_when_tavily_raises(monkeypatch):
    monkeypatch.setattr(ws, "RUN_MODE", "business")
    monkeypatch.setattr(ws, "SEARCH_PROVIDER", "tavily")
    monkeypatch.setattr(ws, "SEARCH_API_KEY", "fake-key")

    def _boom(q, n):
        raise RuntimeError("tavily down")

    monkeypatch.setattr(ws, "_tavily_search", _boom)
    monkeypatch.setattr(ws, "_ddgs_search", lambda q, n: "DDGS-OK")
    assert ws.web_search({"query": "x"}) == "DDGS-OK"


def test_web_search_all_backends_raise_returns_no_result(monkeypatch):
    """后端全部抛异常时也不得抛出到调用方（异常必须内部收敛）。"""
    monkeypatch.setattr(ws, "RUN_MODE", "business")
    monkeypatch.setattr(ws, "SEARCH_PROVIDER", "none")

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(ws, "_ddgs_search", _boom)
    monkeypatch.setattr(ws, "_duckduckgo_instant_answer", _boom)
    out = ws.web_search({"query": "x"})
    assert "未检索到" in out


# ============ web_browser ============

def test_web_browser_missing_url():
    assert _is_error_text(wb.web_browser({}))


def test_web_browser_mock_page_hit():
    """命中预置页面 → 返回内容且不发起网络请求。"""
    if not wb._MOCK_PAGES:
        pytest.skip("无预置 mock 页面")
    url = next(iter(wb._MOCK_PAGES))
    out = wb.web_browser({"url": url})
    assert out.startswith(f"[网页 {url}]")


def test_web_browser_respects_max_chars(monkeypatch):
    long_text = "A" * 5000
    monkeypatch.setattr(wb, "_mock_page", lambda u: "")
    monkeypatch.setattr(wb, "_MOCK_PAGES", {})

    class _Resp:
        text = f"<html><body><p>{long_text}</p></body></html>"
        apparent_encoding = "utf-8"

    class _FakeRequests:
        @staticmethod
        def get(url, headers=None, timeout=None):
            return _Resp()

    import sys
    import types

    fake_mod = types.ModuleType("requests")
    fake_mod.get = _FakeRequests.get
    monkeypatch.setitem(sys.modules, "requests", fake_mod)
    # bs4 已安装则直接用，否则用简易替换保证测试可跑
    try:
        import bs4  # noqa: F401
    except ImportError:
        pytest.skip("beautifulsoup4 未安装")

    out = wb.web_browser({"url": "http://example.com", "max_chars": 100})
    body = out.split("\n", 1)[1]
    assert len(body) <= 100


def test_web_browser_network_failure_returns_error_text(monkeypatch):
    monkeypatch.setattr(wb, "_mock_page", lambda u: "")

    def _boom(*a, **k):
        raise RuntimeError("connection refused")

    import sys
    import types

    fake_mod = types.ModuleType("requests")
    fake_mod.get = _boom
    monkeypatch.setitem(sys.modules, "requests", fake_mod)

    out = wb.web_browser({"url": "http://example.com"})
    assert _is_error_text(out), f"网络失败必须返回错误前缀文案，实际: {out}"


def test_web_browser_max_chars_invalid_type(monkeypatch):
    """max_chars 非数字时不应崩溃（int() 失败会被调用方按工具错误处理）。"""
    with pytest.raises((ValueError, TypeError)):
        wb.web_browser({"url": "http://x", "max_chars": "not-a-number"})
