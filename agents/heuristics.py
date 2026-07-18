"""
Heuristic fallbacks for offline runs and deterministic benchmarks.

Refactored: removed all hardcoded answers and question-specific text matching.
Now uses generalizable regex pattern extractors for math expressions only.
Knowledge-retrieval questions are left to the LLM path.

Patterns handled:
  - Power difference: "X的Y次方减去X的Z次方" -> print(X**Y - X**Z)
  - Single power: "X的Y次方" -> print(X**Y)
  - Power first digit: "X的Y次方的首位" -> print(str(X**Y)[0])
  - Factorial: "X的阶乘" / "X!" -> print(math.factorial(X))
  - Factorial digits: "X的阶乘的位数" -> print(len(str(math.factorial(X))))
  - Square sum: "前N个正整数的平方和" -> print(sum(i**2 for i in range(1, N+1)))
  - Even/odd sum: "N以内偶数的和" -> print(sum(i for i in range(2, N+1, 2)))
  - Fibonacci: "Fibonacci数列的第N项" -> dynamic code
  - WebShop: instruction passed as dynamic parameter
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _step(
    step_id: int,
    action: str,
    description: str,
    args: Optional[Dict[str, Any]] = None,
    risk: str = "medium",
    depends_on: Optional[List[int]] = None,
) -> Dict[str, Any]:
    return {
        "id": step_id,
        "action": action,
        "description": description,
        "args": args or {},
        "status": "pending",
        "result": None,
        "retry_count": 0,
        "risk": risk,
        "depends_on": depends_on or [],
    }


def build_heuristic_plan(
    query: str,
    merge_steps: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return a deterministic plan for generalizable math patterns and WebShop.

    This function NO LONGER matches specific question text or returns hardcoded
    answers. It only recognizes mathematical expression patterns that can be
    generalized to any input numbers.
    """
    q = query.lower()

    # === WebShop: 仅当存在明确购物意图时触发，避免「商品/购买」泛词误路由 ===
    # 明确购物意图词：webshop / 下单 / 加入购物车 / 购买商品 / 选购 / 找商品买
    # 注意：「这个商品的利润」之类计算题只含「商品」但不能判为 webshop
    _webshop_intent = (
        "webshop" in q or "shop" in q
        or "下单" in query or "加入购物车" in query
        or "购买商品" in query or "选购" in query
        or "帮我买" in query or "买一个" in query
    )
    if _webshop_intent:
        return {
            "complexity": "medium",
            "steps": [
                _step(
                    1,
                    "webshop",
                    "根据购物需求检索并选择最匹配的商品",
                    {"instruction": query},
                    risk="high",
                )
            ],
        }

    # === File parsing (PDF/Excel/CSV/图片) ===
    # 仅在「查询中能提取到真实文件路径」时路由，避免无路径误落到默认 data/sample.xlsx。
    # 无路径的模糊文件题（如「解析附件」）回落到 LLM Planner 决定工具与路径。
    file_ext_match = re.search(
        r"([\w\-./\\]+\.(?:pdf|xlsx?|csv|png|jpe?g|gif|bmp|webp))",
        query,
        re.IGNORECASE,
    )
    if file_ext_match:
        path = file_ext_match.group(1)
        return {
            "complexity": "medium",
            "steps": [
                _step(
                    1,
                    "file_parse",
                    f"解析文件 {path}",
                    {"path": path},
                    risk="medium",
                )
            ],
        }

    # === Web browsing (URL in query or 浏览/网页关键词) ===
    url_match = re.search(r"(https?://[^\s，。]+)", query)
    if url_match or any(kw in q for kw in ["网页", "网站", "浏览", "url"]):
        url = url_match.group(1) if url_match else "https://example.com"
        return {
            "complexity": "medium",
            "steps": [
                _step(
                    1,
                    "web_browse",
                    f"浏览网页 {url}",
                    {"url": url},
                    risk="medium",
                )
            ],
        }

    # === Math expression extraction (generalizable) ===
    math_code = _extract_math_expression(query)
    if math_code:
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", "执行数学计算", {"code": math_code}, risk="low")
            ],
        }

    # === Fibonacci (generalizable: extracts n from query) ===
    if "fibonacci" in q or "斐波那契" in query:
        n = _first_int(query, default=20)
        return {
            "complexity": "simple",
            "steps": [
                _step(
                    1,
                    "python",
                    f"计算Fibonacci数列的第{n}项",
                    {"code": _fib_code(n)},
                    risk="low",
                )
            ],
        }

    # === Factorial (generalizable) ===
    factorial_match = re.search(r"(\d+)\s*[的]?\s*阶乘", query)
    if factorial_match:
        n = int(factorial_match.group(1))
        # Check if asking for digit count
        if "位数" in query or "多少位" in query:
            return {
                "complexity": "simple",
                "steps": [
                    _step(1, "python", f"计算{n}的阶乘的位数",
                          {"code": f"print(len(str(math.factorial({n}))))"}, risk="low")
                ],
            }
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", f"计算{n}的阶乘",
                      {"code": f"print(math.factorial({n}))"}, risk="low")
            ],
        }

    # === Factorial with ! notation ===
    bang_match = re.search(r"(\d+)\s*[!！]", query)
    if bang_match:
        n = int(bang_match.group(1))
        if "位数" in query or "多少位" in query:
            return {
                "complexity": "simple",
                "steps": [
                    _step(1, "python", f"计算{n}!的位数",
                          {"code": f"print(len(str(math.factorial({n}))))"}, risk="low")
                ],
            }
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", f"计算{n}的阶乘",
                      {"code": f"print(math.factorial({n}))"}, risk="low")
            ],
        }

    # === Square sum (generalizable) ===
    sq_sum_match = re.search(r"前\s*(\d+)\s*[个]?\s*[正整自然数]*\s*的?平方和", query)
    if sq_sum_match:
        n = int(sq_sum_match.group(1))
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", f"计算前{n}个正整数的平方和",
                      {"code": f"print(sum(i**2 for i in range(1, {n+1})))"}, risk="low")
            ],
        }

    # === Even/odd sum (generalizable) ===
    even_sum_match = re.search(r"(\d+)\s*[以到内]*\s*[的]?\s*偶数.*和", query)
    if even_sum_match:
        n = int(even_sum_match.group(1))
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", f"计算2到{n}所有偶数的和",
                      {"code": f"print(sum(i for i in range(2, {n+1}, 2)))"}, risk="low")
            ],
        }

    odd_sum_match = re.search(r"(\d+)\s*[以到内]*\s*[的]?\s*奇数.*和", query)
    if odd_sum_match:
        n = int(odd_sum_match.group(1))
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", f"计算1到{n}所有奇数的和",
                      {"code": f"print(sum(i for i in range(1, {n+1}, 2)))"}, risk="low")
            ],
        }

    # No pattern matched - let the LLM handle it
    return None


def _extract_math_expression(query: str) -> Optional[str]:
    """
    Extract a math expression from natural language and generate Python code.

    Supported patterns:
    - "X的Y次方减去X的Z次方" -> print(X**Y - X**Z)
    - "X的Y次方减去A的B次方" -> print(X**Y - A**B)
    - "X的Y次方" -> print(X**Y)
    - "X的Y次方的首位" -> print(str(X**Y)[0])
    - "X的Y次方的位数" -> print(len(str(X**Y)))
    """
    # Find all power expressions: "X的Y次方" or "X的Y次"
    power_matches = re.findall(r"(\d+)\s*的\s*(\d+)\s*次方?", query)

    has_subtract = any(kw in query for kw in ["减去", "减", "差", "减掉"])

    # Power difference: X^Y - A^B
    if len(power_matches) >= 2 and has_subtract:
        b1, e1 = int(power_matches[0][0]), int(power_matches[0][1])
        b2, e2 = int(power_matches[1][0]), int(power_matches[1][1])
        return f"print({b1}**{e1} - {b2}**{e2})"

    # Single power
    if power_matches:
        b, e = int(power_matches[0][0]), int(power_matches[0][1])
        # Check for "首位" / "第一位"
        if "首位" in query or "第一位" in query or "第一个数字" in query:
            return f"print(str({b}**{e})[0])"
        # Check for "位数" / "多少位"
        if "位数" in query or "多少位" in query:
            return f"print(len(str({b}**{e})))"
        # Plain power
        return f"print({b}**{e})"

    return None


def build_heuristic_args(
    action: str,
    description: str,
    query: str,
    prior_results: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Generate deterministic tool args when LLM parameter generation fails.

    Uses the same generalizable patterns as build_heuristic_plan.
    """
    plan = build_heuristic_plan(f"{query}\n{description}")
    if plan:
        for step in plan.get("steps", []):
            if step.get("action") == action and step.get("args"):
                return dict(step["args"])

    # Fallback for search/webshop
    if action == "search":
        return {"query": description or query}

    if action == "webshop":
        return {"instruction": query}

    if action != "python":
        return None

    # Try extracting math from the combined context
    combined = "\n".join(str(r.get("result", "")) for r in prior_results)
    text = f"{query}\n{description}\n{combined}"
    math_code = _extract_math_expression(text)
    if math_code:
        return {"code": math_code}

    return None


# 失败结果的特征词：命中则视为该步骤执行出错，不能当作答案
_FAIL_MARKERS = (
    "Traceback", "NameError", "TypeError", "SyntaxError", "ValueError",
    "执行错误", "Error:", "[LLM调用失败]", "未定义", "IndentationError",
    "ZeroDivisionError", "KeyError", "IndexError", "FileNotFoundError",
)


def _is_failed_result(r: Dict[str, Any]) -> bool:
    """判断某步骤结果是否为失败结果，失败结果不能被当作最终答案。"""
    if not isinstance(r, dict):
        return False
    # 工具显式返回 success=False
    if r.get("success") is False:
        return True
    # 结果文本含错误特征词
    text = str(r.get("result", ""))
    if any(m in text for m in _FAIL_MARKERS):
        return True
    return False


def synthesize_heuristic_answer(query: str, results: List[Dict[str, Any]]) -> str:
    """Extract a direct answer from deterministic tool outputs.

    This function NO LONGER returns hardcoded answer strings.
    All answers are extracted from actual tool execution results.

    安全约束：失败结果（success=False 或含 Traceback/NameError 等错误特征）
    一律被排除，避免「报错文本被当最终答案」导致错误答案被冻结。
    若所有结果均为失败，返回空串交由上层 LLM 综合或重试。
    """
    if not results:
        return ""

    combined = "\n".join(str(r.get("result", "")) for r in results)
    q = query.lower()

    # WebShop: extract from SELECTED line
    if "webshop" in q or "购买" in query or "商品" in query or "shop" in q:
        selected = _match_line(combined, r"SELECTED:\s*(.+)")
        if selected and not any(m in selected for m in _FAIL_MARKERS):
            return selected
        return combined.strip()

    # All other types: extract from Python output
    # 仅采纳「成功且不含错误特征」的步骤结果
    python_outputs = [
        _clean_python_output(str(r.get("result", "")))
        for r in results
        if r.get("action") == "python" and not _is_failed_result(r)
    ]
    python_outputs = [o for o in python_outputs if o]
    if python_outputs:
        return python_outputs[-1]

    # Single result fallback: only if it is NOT a failed result
    if len(results) == 1:
        r = results[0]
        if not _is_failed_result(r):
            return str(r.get("result", "")).strip()

    return ""


def _first_int(text: str, default: int) -> int:
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else default


def _fib_code(n: int) -> str:
    return (
        "def fib(n):\n"
        "    a, b = 0, 1\n"
        "    for _ in range(n):\n"
        "        a, b = b, a + b\n"
        "    return a\n"
        f"print(fib({n}))"
    )


def _clean_python_output(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = []
    for line in lines:
        # Remove "输出:" prefix if present
        if line.startswith("输出:"):
            line = line[len("输出:"):].strip()
        if line:
            cleaned.append(line)
    return cleaned[-1] if cleaned else ""


def _match_line(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""
