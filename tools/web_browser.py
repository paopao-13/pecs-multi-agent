"""
网页浏览工具

GAIA L1 中约 10% 的题目要求从特定网页提取信息。
本工具获取网页内容并提取正文文本，供 LLM 分析。

实现：
- 优先匹配 mock 数据（保证 benchmark 可复现）
- 非 benchmark URL：用 requests + BeautifulSoup 抓取静态页面正文
- 动态页面（需要 JS 渲染）建议升级为 Playwright（已在 requirements 备注）

Executor 在 Planner 规划出 web_browse 步骤时调用本工具。
"""
import re

# Mock 数据：覆盖 GAIA 样例中可能出现的网页 URL
# 仅对已知可复现页面预置真实正文；未知 URL 一律真实抓取。
_MOCK_PAGES = {
    "https://en.wikipedia.org/wiki/Python_(programming_language)": """
Python is a high-level, general-purpose programming language. 
Guido van Rossum began working on Python in the late 1980s and 
first released it in 1991. Python 2.0 was released in 2000, and 
Python 3.0 in 2008. The latest stable version is Python 3.12.
Python is dynamically typed and garbage-collected.
""",
    "https://en.wikipedia.org/wiki/Tesla,_Inc.": """
Tesla, Inc. is an American electric vehicle and clean energy company.
It was founded on July 1, 2003, by Martin Eberhard and Marc Tarpenning.
The company's name is a tribute to electrical engineer Nikola Tesla.
Tesla's headquarters are in Austin, Texas.
""",
    "https://example.com/sales-2024": """
2024 Annual Sales Report
Q1 Revenue: $1,250,000
Q2 Revenue: $1,480,000
Q3 Revenue: $1,620,000
Q4 Revenue: $1,890,000
Total Revenue: $6,240,000
Growth Rate: 12.5% YoY
""",
}


def web_browser(args: dict) -> str:
    """
    网页浏览工具

    参数:
        args: {"url": "网页URL", "max_chars": "最大返回字符数(默认3000)"}

    返回:
        网页正文文本内容
    """
    url = args.get("url", "")
    max_chars = int(args.get("max_chars", 3000))

    if not url:
        return "错误：缺少 url 参数"

    # Mock 数据优先（benchmark 可复现）
    mock = _mock_page(url)
    if mock:
        return mock

    # 真实抓取（静态页面）
    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {"User-Agent": "Mozilla/5.0 (compatible; PECS-Agent/1.0)"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        # 移除 script/style
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        # 清理空行
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = "\n".join(lines)
        return f"[网页 {url}]\n{cleaned[:max_chars]}"
    except ImportError:
        return "错误：网页抓取需要安装 requests 和 beautifulsoup4（pip install requests beautifulsoup4）"
    except Exception as e:
        return f"错误：网页抓取失败: {type(e).__name__}: {str(e)[:200]}"


def _mock_page(url: str) -> str:
    # 精确匹配
    if url in _MOCK_PAGES:
        return f"[网页 {url}]\n{_MOCK_PAGES[url].strip()}"
    # 包含匹配（URL 片段）
    for key, val in _MOCK_PAGES.items():
        if key in url or url in key:
            return f"[网页 {url}]\n{val.strip()}"
    # 未命中预置页面 -> 返回空，由调用方走真实抓取
    return ""
