"""api_caller 的 SSRF / egress 防护测试。

覆盖：云元数据、回环、私有网段、file://、userinfo 绕过被拦截；
公网 URL 放行；响应体超限截断；禁重定向；本地开发豁免开关。

注意：tools/__init__.py 里有 `from tools.api_caller import api_caller`，
同名函数会遮蔽子模块，必须用 importlib 取模块对象。
"""
import importlib

import pytest

ac = importlib.import_module("tools.api_caller")


@pytest.fixture
def _public_dns(monkeypatch):
    """把任意域名解析到一个公网 IP，避免测试依赖真实网络"""
    monkeypatch.setattr(
        ac.socket,
        "getaddrinfo",
        lambda host, port, proto=None: [(None, None, None, None, ("93.184.216.34", port))],
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",   # 云元数据
        "http://127.0.0.1:8000/admin",                 # 回环
        "http://localhost:8000/",                      # 回环（域名形式）
        "http://10.0.0.5/internal",                    # 私有
        "http://192.168.1.1/",                         # 私有
        "http://172.16.0.1/",                          # 私有
        "file:///etc/passwd",                          # 非 http 协议
        "ftp://internal/",                             # 非 http 协议
        "http://example.com@127.0.0.1/",               # userinfo 绕过
    ],
)
def test_dangerous_urls_are_blocked(url):
    # 不使用 _public_dns：这些 URL 多为 IP 字面量，真实 getaddrinfo 即可解析；
    # 若在此 mock 成公网 IP，反而会把危险地址误判为放行（测试自身的陷阱）。
    allowed, reason = ac._check_url_allowed(url)
    assert not allowed, f"{url} 不应被放行"
    assert reason, "被拒时必须给出原因，便于 Executor 修正"


def test_public_url_is_allowed(_public_dns):
    allowed, reason = ac._check_url_allowed("https://example.com/api")
    assert allowed, reason


def test_allow_private_switch(monkeypatch):
    """本地开发豁免开关：打开后内网地址放行（生产必须关闭）"""
    monkeypatch.setattr(ac, "ALLOW_PRIVATE", True)
    assert ac._check_url_allowed("http://127.0.0.1:8000/")[0] is True


def test_relative_or_empty_host_rejected():
    assert ac._check_url_allowed("http:///path")[0] is False


def test_api_caller_rejects_before_request(monkeypatch):
    """端到端：危险 URL 在未发起任何请求前就被拒绝"""
    called = []
    monkeypatch.setattr(ac, "_open_with_limits", lambda *a, **k: called.append(a))

    out = ac.api_caller({"url": "http://169.254.169.254/latest/meta-data/"})

    assert out.startswith("错误：请求被安全策略拒绝")
    assert called == [], "被拒绝的请求不应真正发出"


def test_api_caller_happy_path(monkeypatch, _public_dns):
    """端到端：合法 URL 正常返回，且保留原有的 'HTTP <code>' 前缀格式"""
    monkeypatch.setattr(
        ac, "_open_with_limits", lambda *a, **k: (200, '{"ok": true}', False)
    )
    out = ac.api_caller({"url": "https://example.com/api"})
    assert out.startswith("HTTP 200")
    assert '"ok": true' in out


def test_response_is_truncated(monkeypatch, _public_dns):
    monkeypatch.setattr(ac, "_open_with_limits", lambda *a, **k: (200, "x" * 1000, True))
    out = ac.api_caller({"url": "https://example.com/api"})
    assert "响应已截断" in out


def test_redirect_is_forbidden():
    """302 必须被禁：否则可用重定向绕过 IP 校验"""
    import urllib.error
    import urllib.request

    handler = ac._NoRedirect()
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(
            urllib.request.Request("http://example.com"),
            None, 302, "Found", {}, "http://169.254.169.254/",
        )
