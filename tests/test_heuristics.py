"""测试 agents/heuristics.py 的启发式路由逻辑

启发式路由是 PECS 的核心卖点（计算题 0-token 秒杀），
面试必问"启发式怎么识别题目类型"。

测试覆盖：
- build_heuristic_plan: webshop/file/url/幂次/阶乘/平方和/fib/无匹配
- _extract_math_expression: 幂差/单幂/首位/位数
- build_heuristic_args: python/search/webshop
- synthesize_heuristic_answer: SELECTED/python输出/失败跳过/空结果
- _is_failed_result: success=False/Traceback/正常
"""
import pytest

from agents.heuristics import (
    build_heuristic_plan,
    _extract_math_expression,
    build_heuristic_args,
    synthesize_heuristic_answer,
    _is_failed_result,
)


# ============================================================
# 启发式计划生成
# ============================================================

class TestBuildHeuristicPlan:
    """测试 build_heuristic_plan: 识别题目类型并生成确定性计划"""

    def test_plan_webshop(self):
        """购物意图 → webshop 计划"""
        plan = build_heuristic_plan("帮我在webshop买一件红色M码T恤")
        assert plan is not None
        assert plan["steps"][0]["action"] == "webshop"
        assert "instruction" in plan["steps"][0]["args"]

    def test_plan_file_parse(self):
        """文件路径 → file_parse 计划"""
        plan = build_heuristic_plan("请解析 report.xlsx")
        assert plan is not None
        assert plan["steps"][0]["action"] == "file_parse"
        assert plan["steps"][0]["args"]["path"] == "report.xlsx"

    def test_plan_file_parse_with_space_path(self):
        """bug #4: Windows 路径含空格时应完整匹配

        正则 [\\w\\-./\\\\]+ 不匹配空格，导致 C:\\Users\\My Name\\file.pdf
        只匹配到 Name\\file.pdf，丢失了路径前半段。
        """
        plan = build_heuristic_plan("请解析 C:\\Users\\My Name\\data.pdf")
        assert plan is not None
        path = plan["steps"][0]["args"]["path"]
        assert "My Name" in path, f"路径不应被空格截断，实际: {path}"
        assert path.endswith("data.pdf")

    def test_plan_url_browse(self):
        """URL → web_browse 计划"""
        plan = build_heuristic_plan("请浏览 https://example.com")
        assert plan is not None
        assert plan["steps"][0]["action"] == "web_browse"
        assert plan["steps"][0]["args"]["url"] == "https://example.com"

    def test_plan_power(self):
        """幂次计算 → python 计划"""
        plan = build_heuristic_plan("计算3的5次方")
        assert plan is not None
        assert plan["steps"][0]["action"] == "python"
        assert "3**5" in plan["steps"][0]["args"]["code"]

    def test_plan_factorial(self):
        """阶乘 → python 计划"""
        plan = build_heuristic_plan("计算10的阶乘")
        assert plan is not None
        assert plan["steps"][0]["action"] == "python"
        assert "math.factorial(10)" in plan["steps"][0]["args"]["code"]

    def test_plan_square_sum(self):
        """平方和 → python 计划"""
        plan = build_heuristic_plan("计算前5个正整数的平方和")
        assert plan is not None
        assert plan["steps"][0]["action"] == "python"
        assert "sum(i**2" in plan["steps"][0]["args"]["code"]

    def test_plan_fibonacci(self):
        """Fibonacci → python 计划"""
        plan = build_heuristic_plan("计算Fibonacci数列的第20项")
        assert plan is not None
        assert plan["steps"][0]["action"] == "python"
        assert "fib" in plan["steps"][0]["args"]["code"]

    def test_plan_no_match(self):
        """无匹配模式 → 返回 None（交给 LLM）"""
        plan = build_heuristic_plan("今天天气怎么样")
        assert plan is None


# ============================================================
# 数学表达式提取
# ============================================================

class TestExtractMathExpression:
    """测试 _extract_math_expression: 从自然语言提取数学表达式"""

    def test_extract_power_difference(self):
        """幂差: X^Y - A^B"""
        code = _extract_math_expression("计算2的10次方减去2的5次方")
        assert code == "print(2**10 - 2**5)"

    def test_extract_single_power(self):
        """单幂: X^Y"""
        code = _extract_math_expression("计算3的5次方")
        assert code == "print(3**5)"

    def test_extract_first_digit(self):
        """首位: str(X^Y)[0]"""
        code = _extract_math_expression("2的100次方的首位")
        assert code == "print(str(2**100)[0])"

    def test_extract_digit_count(self):
        """位数: len(str(X^Y))"""
        code = _extract_math_expression("2的100次方的位数")
        assert code == "print(len(str(2**100)))"


# ============================================================
# 启发式参数生成
# ============================================================

class TestBuildHeuristicArgs:
    """测试 build_heuristic_args: 为工具生成确定性参数"""

    def test_args_python(self):
        """python action → 从描述提取数学表达式"""
        args = build_heuristic_args("python", "计算3的5次方", "计算3的5次方", [])
        assert args is not None
        assert "3**5" in args["code"]

    def test_args_search(self):
        """search action → 用描述作为搜索关键词"""
        args = build_heuristic_args("search", "搜索奥运金牌", "任意query", [])
        assert args == {"query": "搜索奥运金牌"}

    def test_args_webshop(self):
        """webshop action → 从 plan 提取 instruction（含原始 query）"""
        args = build_heuristic_args("webshop", "买T恤", "帮我在webshop买T恤", [])
        assert args is not None
        assert "instruction" in args
        assert "帮我在webshop买T恤" in args["instruction"]


# ============================================================
# 启发式答案合成
# ============================================================

class TestSynthesizeHeuristicAnswer:
    """测试 synthesize_heuristic_answer: 从工具输出提取最终答案"""

    def test_synthesize_webshop_selected(self):
        """WebShop: 从 SELECTED 行提取商品信息"""
        results = [{"result": "SELECTED: 红色T恤 M码"}]
        answer = synthesize_heuristic_answer("帮我在webshop买T恤", results)
        assert "红色T恤" in answer

    def test_synthesize_python_output(self):
        """Python: 提取最后一个成功 python 步骤的输出"""
        results = [{"action": "python", "result": "243"}]
        answer = synthesize_heuristic_answer("计算3的5次方", results)
        assert "243" in answer

    def test_synthesize_skip_failed(self):
        """失败结果被跳过，不作为答案"""
        results = [{"action": "python", "result": "Traceback (most recent call last):", "success": False}]
        answer = synthesize_heuristic_answer("计算", results)
        assert answer == ""

    def test_synthesize_empty_results(self):
        """空结果列表 → 返回空串"""
        assert synthesize_heuristic_answer("任意", []) == ""


# ============================================================
# 失败结果判定
# ============================================================

class TestIsFailedResult:
    """测试 _is_failed_result: 判断步骤结果是否失败"""

    @pytest.mark.parametrize("result_dict,expected", [
        ({"success": False}, True),                              # 显式失败
        ({"result": "Traceback (most recent call last):"}, True),  # Traceback
        ({"result": "正常输出结果"}, False),                      # 正常输出
        ({}, False),                                             # 空字典不算失败
    ])
    def test_is_failed_result(self, result_dict, expected):
        assert _is_failed_result(result_dict) == expected

    def test_is_failed_result_non_dict(self):
        """非字典输入返回 False（防御性）"""
        assert _is_failed_result("not a dict") is False
        assert _is_failed_result(None) is False
