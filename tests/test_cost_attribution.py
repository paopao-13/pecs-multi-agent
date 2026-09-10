"""
成本归因单元测试（Day4）

覆盖：
  1. 三角（角色 / 工具 / 轮次）拆分的正确性
  2. 一致性校验：各角色之和 ≈ 总消耗（误差 < 1%），偏差可被检出
  3. 空状态 / 缺字段的健壮性
  4. 兼容 AgentState 与普通 dict
  5. record_token_usage 写入 iteration 字段（供按轮次归因）
  6. 文本报告渲染
"""
import pytest

from graph.state import AgentState
from graph.token_budget import estimate_tokens, record_token_usage
from metrics.cost_attribution import attribute_cost, render_report


def _state(**overrides) -> dict:
    base = {
        "token_used": 1000,
        "token_budget": 50000,
        "role_token_used": {"planner": 150, "executor": 500, "critic": 200, "synthesizer": 150},
        "budget_events": [],
        "results": [],
        "step_count": 0,
        "iteration": 0,
    }
    base.update(overrides)
    return base


# ============================================================
# 1. 角色维度
# ============================================================

class TestRoleAttribution:
    def test_role_sum_matches_total(self):
        report = attribute_cost(_state())
        assert report["by_role"] == {
            "planner": 150, "executor": 500, "critic": 200, "synthesizer": 150,
        }
        att = report["attribution"]
        assert att["role_sum"] == 1000
        assert att["delta"] == 0
        assert att["consistent"] is True
        assert report["top_role"] == "executor"

    def test_role_ratio_sums_to_one(self):
        report = attribute_cost(_state())
        assert sum(report["by_role_ratio"].values()) == pytest.approx(1.0, abs=1e-6)

    def test_missing_roles_default_to_zero(self):
        report = attribute_cost(_state(role_token_used={"executor": 1000}, token_used=1000))
        assert report["by_role"]["planner"] == 0
        assert report["by_role"]["synthesizer"] == 0
        assert report["attribution"]["consistent"] is True

    def test_inconsistency_is_detected(self):
        """总消耗与角色之和不符时必须显式暴露，而不是悄悄掩盖"""
        report = attribute_cost(_state(token_used=2000))  # 角色之和仍是 1000
        att = report["attribution"]
        assert att["role_sum"] == 1000
        assert att["delta"] == 1000
        assert att["consistent"] is False

    def test_small_rounding_delta_still_consistent(self):
        """误差 < 1% 视为一致（容忍四舍五入/估算误差）"""
        report = attribute_cost(_state(token_used=1005))
        assert report["attribution"]["delta"] == 5
        assert report["attribution"]["consistent"] is True


# ============================================================
# 2. 工具维度
# ============================================================

class TestToolAttribution:
    def test_groups_by_action_and_counts_calls(self):
        results = [
            {"action": "search", "result": "abcdef"},
            {"action": "search", "result": "xy"},
            {"action": "python", "result": "42"},
        ]
        report = attribute_cost(_state(results=results))
        assert report["by_tool"]["search"]["calls"] == 2
        assert report["by_tool"]["python"]["calls"] == 1
        expected_search = estimate_tokens("abcdef") + estimate_tokens("xy")
        assert report["by_tool"]["search"]["tokens"] == expected_search
        assert report["tool_tokens_total"] == (
            expected_search + estimate_tokens("42")
        )

    def test_missing_action_bucketed_as_unknown(self):
        report = attribute_cost(_state(results=[{"result": "abc"}]))
        assert "unknown" in report["by_tool"]

    def test_empty_results_yields_empty_mapping(self):
        report = attribute_cost(_state(results=[]))
        assert report["by_tool"] == {}
        assert report["tool_tokens_total"] == 0


# ============================================================
# 3. 轮次维度
# ============================================================

class TestIterationAttribution:
    def test_groups_events_by_iteration(self):
        events = [
            {"role": "planner", "tokens": 100, "iteration": 0, "degrade_level": 0},
            {"role": "executor", "tokens": 300, "iteration": 0, "degrade_level": 0},
            {"role": "planner", "tokens": 50, "iteration": 1, "degrade_level": 1},
            {"role": "critic", "tokens": 200, "iteration": 1, "degrade_level": 1},
        ]
        report = attribute_cost(_state(budget_events=events))
        assert report["by_iteration"]["0"]["tokens"] == 400
        assert report["by_iteration"]["0"]["by_role"]["planner"] == 100
        assert report["by_iteration"]["1"]["tokens"] == 250
        assert report["by_iteration"]["1"]["by_role"]["critic"] == 200
        assert report["degrade_level"] == 1  # 取最后一条事件

    def test_event_without_iteration_defaults_to_zero(self):
        report = attribute_cost(_state(budget_events=[{"role": "executor", "tokens": 10}]))
        assert report["by_iteration"]["0"]["tokens"] == 10


# ============================================================
# 4. 健壮性 / 兼容性
# ============================================================

class TestRobustness:
    def test_empty_state_does_not_crash(self):
        report = attribute_cost({})
        assert report["total_tokens"] == 0
        assert report["usage_ratio"] == 0.0
        assert report["attribution"]["consistent"] is True
        assert "未消耗" in report["headline"]

    def test_none_state_does_not_crash(self):
        report = attribute_cost(None)
        assert report["total_tokens"] == 0

    def test_works_with_agent_state(self):
        state = AgentState(
            query="q",
            token_used=300,
            token_budget=50000,
            role_token_used={"planner": 100, "executor": 200, "critic": 0, "synthesizer": 0},
            step_count=2,
            iteration=1,
        )
        report = attribute_cost(state)
        assert report["total_tokens"] == 300
        assert report["by_role"]["executor"] == 200
        assert report["steps"] == 2
        assert report["attribution"]["consistent"] is True

    def test_malformed_entries_are_skipped(self):
        report = attribute_cost(_state(
            budget_events=[None, "not-a-dict", {"role": "executor", "tokens": 5, "iteration": 0}],
            results=[None, {"action": "search", "result": "aa"}],
        ))
        assert report["by_iteration"]["0"]["tokens"] == 5
        assert report["by_tool"]["search"]["calls"] == 1


# ============================================================
# 5. record_token_usage 与归因的衔接
# ============================================================

class TestRecordTokenUsageIntegration:
    def test_iteration_is_recorded_into_event(self):
        state = {"token_used": 0, "token_budget": 1000, "iteration": 2,
                 "role_token_used": {}, "budget_events": []}
        _, _, events = record_token_usage(state, "planner", 40)
        assert events[-1]["iteration"] == 2

    def test_end_to_end_attribution_is_consistent(self):
        """模拟一轮多角色消耗，归因结果必须自洽（角色之和 == 总消耗）"""
        state = {"token_used": 0, "token_budget": 1000, "iteration": 0,
                 "role_token_used": {}, "budget_events": [], "results": []}
        for role, tokens in [("planner", 100), ("executor", 400), ("critic", 150), ("synthesizer", 250)]:
            state["token_used"], state["role_token_used"], state["budget_events"] = (
                record_token_usage(state, role, tokens)
            )
        report = attribute_cost(state)
        assert report["total_tokens"] == 900
        assert report["attribution"]["role_sum"] == 900
        assert report["attribution"]["consistent"] is True
        assert report["by_iteration"]["0"]["tokens"] == 900


# ============================================================
# 6. 文本报告
# ============================================================

class TestRenderReport:
    def test_contains_all_sections(self):
        state = _state(
            results=[{"action": "search", "result": "hello world"}],
            budget_events=[{"role": "planner", "tokens": 150, "iteration": 0, "degrade_level": 0}],
        )
        text = render_report(state)
        assert "成本归因报告" in text
        assert "[按角色]" in text
        assert "[按工具" in text
        assert "[按轮次]" in text
        assert "[一致性]" in text
        assert "executor" in text

    def test_accepts_precomputed_report(self):
        report = attribute_cost(_state())
        text = render_report(report)
        assert "成本归因报告" in text
        assert report["headline"] in text
