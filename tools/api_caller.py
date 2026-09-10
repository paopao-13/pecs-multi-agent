"""
通用 API 调用工具

Executor 可以调用外部 REST API 获取数据。支持 GET 和 POST 方法。

安全边界（2026-09-11 加固）：
本工具的 URL 由 LLM 生成，属于**不可信输入**。原实现直接 urlopen(任意 url)，
可被提示注入诱导去访问云元数据（169.254.169.254）、内网服务或 file:// 本地文件。
现加入四层限制：
  1. 协议白名单（仅 http/https）
  2. 目标 IP 禁私有/回环/链路本地/保留/多播/未指定（含域名解析后的全部 IP）
  3. 禁止重定向（否则可用 302 绕过第 2 层）
  4. 响应体大小上限（防止超大响应打爆内存）

已知边界（面试要能讲出来）：
  - 无法完全防御 DNS rebinding：校验与实际请求之间存在 TOCTOU 窗口。彻底方案
    是在 socket 层自定义 getaddrinfo 并复用同一条已校验的连接，属后续项。
  - 本地开发如需访问内网服务，设 PEC_EGRESS_ALLOW_PRIVATE=1 可关闭第 2 层。
"""
import ipaddress
import json
import os
import socket
import urllib.parse
import urllib.request
import urllib.error

# 响应体上限（字节）：LLM 只需要文本摘要，2MB 远超需求；超限即截断并标注，
# 绝不无上限地 read() 整个响应。
MAX_RESPONSE_BYTES = int(os.getenv("PEC_EGRESS_MAX_BYTES", str(2 * 1024 * 1024)))

# 本地开发豁免：需要调内网/本机服务时打开，生产必须保持关闭
ALLOW_PRIVATE = os.getenv("PEC_EGRESS_ALLOW_PRIVATE", "0") == "1"

_ALLOWED_SCHEMES = ("http", "https")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止重定向：否则校验通过的 URL 可被 302 跳到内网地址绕过。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "重定向被禁止（SSRF 防护）", headers, fp
        )


def _check_url_allowed(url: str) -> tuple:
    """判断 URL 是否允许访问，返回 (allowed, reason)。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as e:
        return False, f"URL 解析失败: {e}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False, f"不支持的协议 '{scheme or '空'}'（仅允许 {_ALLOWED_SCHEMES}）"

    host = parsed.hostname
    if not host:
        return False, "URL 缺少主机名"

    if ALLOW_PRIVATE:
        return True, ""

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception as e:
        # fail-closed：安全控制不能因为解析异常而放行
        return False, f"域名解析失败: {type(e).__name__}: {e}"

    for info in infos:
        ip_text = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return False, f"无法解析为 IP: {ip_text}"
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local      # 169.254.0.0/16 —— 云元数据服务就在这个段
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or not ip.is_global
        ):
            return False, f"目标地址 {ip} 属于禁止访问的地址段"

    return True, ""


def _open_with_limits(url: str, data, headers: dict, method: str, timeout: int):
    """带安全限制地发起请求，返回 (status_code, body_text, truncated)。"""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
        status = resp.getcode()
    truncated = len(raw) > MAX_RESPONSE_BYTES
    if truncated:
        raw = raw[:MAX_RESPONSE_BYTES]
    return status, raw.decode("utf-8", errors="replace"), truncated


def api_caller(args: dict) -> str:
    """
    通用 API 调用工具

    参数:
        args: {
            "url": "API地址",
            "method": "GET" | "POST",  # 默认 GET
            "headers": {"key": "value"},  # 可选
            "params": {"key": "value"},   # URL查询参数（GET）
            "body": {"key": "value"}      # 请求体（POST，JSON格式）
        }

    返回:
        API 响应内容字符串；被安全策略拒绝时返回以"错误："开头的说明
    """
    url = args.get("url", "")
    method = args.get("method", "GET").upper()
    headers = dict(args.get("headers") or {})
    params = args.get("params") or {}
    body = args.get("body")

    if not url:
        return "错误：缺少 url 参数"

    # 先拼查询参数再校验：避免校验通过后又被参数改写出意外地址
    if params:
        query_string = urllib.parse.urlencode(params)
        url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

    allowed, reason = _check_url_allowed(url)
    if not allowed:
        return f"错误：请求被安全策略拒绝 —— {reason}"

    data = None
    if body and method == "POST":
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    headers.setdefault("User-Agent", "MultiAgent/1.0")

    try:
        status, text, truncated = _open_with_limits(url, data, headers, method, timeout=10)
        suffix = f"\n[响应已截断至 {MAX_RESPONSE_BYTES} 字节]" if truncated else ""
        try:
            parsed = json.loads(text)
            return f"HTTP {status}\n{json.dumps(parsed, indent=2, ensure_ascii=False)}{suffix}"
        except json.JSONDecodeError:
            return f"HTTP {status}\n{text}{suffix}"
    except urllib.error.HTTPError as e:
        return f"HTTP错误 {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL错误: {str(e)}"
    except Exception as e:
        return f"API调用失败: {type(e).__name__}: {str(e)}"
