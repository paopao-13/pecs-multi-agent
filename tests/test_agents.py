"""测试 agents/ 模块的核心辅助函数

聚焦核心纯逻辑函数（不依赖 LLM 调用）：
- planner._is_deterministic_task: 判断是否为确定性任务
- executor._sanitize_python_code: AST 安全沙箱的代码净化
- executor._args_incomplete: 工具参数完整性检查
- critic._fast_evaluate: 预算紧张时的快速评分
- critic._rule_evaluate: 不消耗 Token 的规则评分
- synthesizer._should_reflect: 反思循环触发判定
"""
import pytest

from agents.planner import _is_deterministic_task
from agents.executor import _sanitize_python_code, _args_incomplete, executor_node
from agents.critic import _fast_evaluate, _rule_evaluate
from agents.synthesizer import _should_reflect


# ============================================================
# 任务类型判定
# ============================================================

class TestIsDeterministicTask:
    """测试 _is_deterministic_task: 判断计划是否仅含确定性工具（python/webshop）"""

    def test_deterministic_python_only(self):
        """只有 python 步骤 → True"""
        plan = {"steps": [{"action": "python"}, {"action": "python"}]}
        assert _is_deterministic_task(plan) is True

    def test_deterministic_with_search(self):
        """含 search 步骤 → False"""
        plan = {"steps": [{"action": "python"}, {"action": "search"}]}
        assert _is_deterministic_task(plan) is False

    def test_deterministic_empty(self):
        """空步骤 → False"""
        assert _is_deterministic_task({"steps": []}) is False
        assert _is_deterministic_task({}) is False


# ============================================================
# Python 代码净化（AST 安全沙箱核心）
# ============================================================

class TestSanitizePythonCode:
    """测试 _sanitize_python_code: 自动移除 import 语句

    安全沙箱不允许用户代码 import 任意模块，
    预导入 math/json/re/datetime 供用户使用。
    """

    @pytest.mark.parametrize("code,expected", [
        ("import os", ""),                           # 基本 import → 移除
        ("from os import system", ""),               # from import → 移除
        ("from os.path import join", ""),            # 子模块 import → 移除 (bug #6)
        ("print(math.factorial(5))", "print(math.factorial(5))"),  # 正常代码保留
    ])
    def test_sanitize(self, code, expected):
        logs = []
        result = _sanitize_python_code(code, logs)
        assert result == expected

    def test_sanitize_logs_removal(self):
        """移除 import 时应记录日志"""
        logs = []
        _sanitize_python_code("import os\nimport sys", logs)
        assert len(logs) == 1
        assert "2" in logs[0]  # 移除了 2 条


# ============================================================
# 参数完整性检查
# ============================================================

class TestArgsIncomplete:
    """测试 _args_incomplete: 检查工具参数是否完整"""

    @pytest.mark.parametrize("action,args,expected", [
        ("search", {"query": "test"}, False),     # search 有 query → 完整
        ("search", {}, True),                      # search 缺 query → 不完整
        ("python", {"code": "print(1)"}, False),  # python 有 code → 完整
        ("python", {}, True),                      # python 缺 code → 不完整
        ("webshop", {"instruction": "买T恤"}, False),  # webshop 有 instruction → 完整
        ("webshop", {}, True),                     # webshop 缺 instruction → 不完整
    ])
    def test_args_incomplete(self, action, args, expected):
        assert _args_incomplete(action, args) == expected

    def test_args_incomplete_empty(self):
        """空 args → 不完整"""
        assert _args_incomplete("search", None) is True


# ============================================================
# 快速评分（预算紧张时不调 LLM）
# ============================================================

class TestFastEvaluate:
    """测试 _fast_evaluate: 用简单规则判断结果质量"""

    def test_fast_evaluate_failed(self):
        """success=False → 低分"""
        result = {"success": False, "result": "some output"}
        score = _fast_evaluate(result)
        assert score["overall"] == 2.0

    def test_fast_evaluate_error_in_text(self):
        """结果含"错误" → 低分（全文检查，无截断）"""
        result = {"success": True, "result": "执行错误：参数无效"}
        score = _fast_evaluate(result)
        assert score["overall"] == 2.0

    def test_fast_evaluate_short_text(self):
        """结果太短（<50字符）→ 中分"""
        result = {"success": True, "result": "短结果"}
        score = _fast_evaluate(result)
        assert score["overall"] == 2.7

    def test_fast_evaluate_normal(self):
        """正常结果 → 及格分"""
        result = {"success": True, "result": "a" * 60}
        score = _fast_evaluate(result)
        assert score["overall"] == 4.0


# ============================================================
# 规则评分（不消耗 Token）
# ============================================================

class TestRuleEvaluate:
    """测试 _rule_evaluate: 对特定类型结果用规则验证质量"""

    def test_rule_evaluate_failed(self):
        """success=False → 低分"""
        result = {"success": False, "result": "some output", "action": "search"}
        score = _rule_evaluate(result)
        assert score["overall"] == 2.0

    def test_rule_evaluate_error_in_first_20(self):
        """错误在前20字符 → 低分"""
        result = {"success": True, "result": "错误：参数无效", "action": "search"}
        score = _rule_evaluate(result)
        assert score["overall"] == 2.0

    def test_rule_evaluate_error_at_position_25(self):
        """bug #5: 错误在第25字符应检测到，但 text[:20] 截断导致漏判

        构造 "a"*20 + "错误信息"，"错误" 在第 21 字符位置。
        当前 text[:20] 只看前 20 字符，漏判为无错误。
        修复后应检测到错误并返回低分。
        """
        text = "a" * 20 + "错误信息：执行失败"
        result = {"success": True, "result": text, "action": "search"}
        score = _rule_evaluate(result)
        assert score is not None
        assert score["overall"] == 2.0, f"应在全文检测到'错误'，但 text[:20] 截断导致漏判"

    def test_rule_evaluate_python_no_output(self):
        """Python 无输出 → 低分"""
        result = {"success": True, "result": "", "action": "python"}
        score = _rule_evaluate(result)
        assert score is not None
        assert score["overall"] == 2.0

    def test_rule_evaluate_python_normal(self):
        """Python 有输出 → 高分或 None（回退 LLM）"""
        result = {"success": True, "result": "42", "action": "python"}
        score = _rule_evaluate(result)
        # 有输出且无错误，可能返回高分或 None（取决于实现）
        if score is not None:
            assert score["overall"] >= 3.0


# ============================================================
# 反思触发判定
# ============================================================

class TestShouldReflect:
    """测试 _should_reflect: 判断是否触发反思循环"""

    def test_reflect_simple_task(self):
        """simple 任务不触发反思"""
        assert _should_reflect("query", "a" * 100, [], 0, complexity="simple") is False

    def test_reflect_max_iteration(self):
        """达到最大迭代不触发反思"""
        assert _should_reflect("query", "a" * 100, [], 999, complexity="medium") is False

    def test_reflect_short_answer(self):
        """答案太短（<30字符）→ 触发反思"""
        assert _should_reflect("query", "短答案", [], 0, complexity="medium") is True

    def test_reflect_has_failed_step(self):
        """有步骤失败 → 触发反思"""
        results = [{"success": False}]
        assert _should_reflect("query", "a" * 100, results, 0, complexity="medium") is True

    def test_reflect_normal_no_trigger(self):
        """正常情况不触发反思"""
        results = [{"success": True}]
        assert _should_reflect("query", "a" * 100, results, 0, complexity="medium") is False


# ============================================================
# Executor 节点 success 判定（mock execute_tool）— bug #1 修复
# ============================================================

class TestExecutorSuccessDetection:
    """测试 executor_node 的 success 判定

    bug #1: "失败" not in result[:20] 只看前 20 字符，
    "失败" 在第 21+ 字符时漏判为成功。
    修复: result[:20] → result（全文检查）
    """

    def test_executor_failure_at_position_25(self, monkeypatch):
        """bug #1: '失败' 在第 21 字符应判为失败"""
        # mock execute_tool 返回 "失败" 在第 21 字符的结果
        monkeypatch.setattr(
            "agents.executor.execute_tool",
            lambda action, args: "a" * 20 + "执行失败",
        )
        # mock call_llm 以防参数不完整时调用
        monkeypatch.setattr(
            "agents.executor.call_llm",
            lambda *a, **kw: ('{"code": "print(1)"}', 0),
        )

        state = {
            "plan": [{"id": 1, "action": "python", "description": "计算", "args": {"code": "print(1)"}}],
            "current_step_idx": 0,
            "results": [],
            "query": "test",
            "logs": [],
        }

        result = executor_node(state)
        assert result["results"][0]["success"] is False, (
            "'失败' 在第 21 字符应判为失败，但 result[:20] 截断导致漏判"
        )

    def test_executor_success_normal(self, monkeypatch):
        """正常结果 → success=True"""
        monkeypatch.setattr(
            "agents.executor.execute_tool",
            lambda action, args: "42",
        )
        monkeypatch.setattr(
            "agents.executor.call_llm",
            lambda *a, **kw: ('{"code": "print(1)"}', 0),
        )

        state = {
            "plan": [{"id": 1, "action": "python", "description": "计算", "args": {"code": "print(1)"}}],
            "current_step_idx": 0,
            "results": [],
            "query": "test",
            "logs": [],
        }

        result = executor_node(state)
        assert result["results"][0]["success"] is True
