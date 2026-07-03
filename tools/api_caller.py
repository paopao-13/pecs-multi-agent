"""
通用 API 调用工具

Executor 可以调用外部 REST API 获取数据。
支持 GET 和 POST 方法。
"""
import json
import urllib.request
import urllib.parse
import urllib.error


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
        API 响应内容字符串
    """
    url = args.get("url", "")
    method = args.get("method", "GET").upper()
    headers = args.get("headers", {})
    params = args.get("params", {})
    body = args.get("body")

    if not url:
        return "错误：缺少 url 参数"

    # 拼接查询参数
    if params:
        query_string = urllib.parse.urlencode(params)
        url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

    # 准备请求数据
    data = None
    if body and method == "POST":
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    # 设置默认 User-Agent
    headers.setdefault("User-Agent", "MultiAgent/1.0")

    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            response_data = resp.read().decode("utf-8")
            status_code = resp.getcode()

            # 尝试格式化 JSON 响应
            try:
                parsed = json.loads(response_data)
                return f"HTTP {status_code}\n{json.dumps(parsed, indent=2, ensure_ascii=False)}"
            except json.JSONDecodeError:
                return f"HTTP {status_code}\n{response_data}"

    except urllib.error.HTTPError as e:
        return f"HTTP错误 {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL错误: {str(e)}"
    except Exception as e:
        return f"API调用失败: {type(e).__name__}: {str(e)}"
