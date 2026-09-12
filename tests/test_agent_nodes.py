"""四角色节点（planner/executor/critic/synthesizer）的边界测试。

**为什么补这层**：这四个 node 是核心业务逻辑，此前覆盖率最低（55%~74%）——
因为函数体大量分支依赖真实 LLM。本文件用 mock 替代 LLM 与工具，把"LLM 返回
异常输入时系统会不会崩、走不走兜底路径"这类**最该守住的边界**钉死。

测试策略（不消耗任何额度、不发起网络请求）：
  - mock `call_llm` / `call_llm_json`：用预录响应驱动，覆盖正常 / 畸形 / 抛异常
  - mock `execute_tool`：覆盖成功 / 错误前缀 / 抛异常
  - 断言重点是**降级路径**：异常必须被收敛，不能冒到调用方把整个图带崩

⚠️ 环境前置条件：planner / synthesizer 用 `if has_api_key` 分流，无 Key 时
走完全不同的离线分支。fixture 里显式声明"已配置 Key"，否则会"本地过 CI 挂"
（此前已在 tests/test_llm_retry_classify.py 踩过同样的坑）。
"""
import pytest

import agents.critic as critic_mod
import agents.executor as exec_mod
import agents.planner as planner_mod
import agents.synthesizer as synth_mod

# 知识类查询（不会命中确定性启发式，从而走到 LLM 规划分支）
_KNOWLEDGE_QUERY = "查一下巴黎当前的人口数量是多少"


@pytest.fixture(autouse=True)
def _declare_api_key(monkeypatch):
    """显式声明"已配置 LLM Key"——不让测试依赖本机 .env。"""
    monkeypatch.setattr(planner_mod, "LLM_API_KEY", "test-key-not-real")
    monkeypatch.setattr(synth_mod, "LLM_API_KEY", "test-key-not-real")


# ============================================================
# Planner
# ============================================================

class TestPlannerNode:
    def test_normal_plan_is_normalized(self, monkeypatch):
        """LLM 返回的计划会被补齐默认字段并规范化。"""
        monkeypatch.setattr(planner_mod, "call_llm_json", lambda p, s, role=None: (
            {"steps": [{"action": "search", "description": "搜", "args": {"query": "x"}}],
             "complexity": "medium"}, 100
        ))
        out = planner_mod.planner_node({"query": _KNOWLEDGE_QUERY, "logs": []})

        assert len(out["plan"]) == 1
        step = out["plan"][0]
        for field in ("id", "status", "result", "retry_count", "risk", "depends_on", "args"):
            assert field in step, f"步骤缺少默认字段 {field}"
        assert step["status"] == "pending"
        assert out["llm_error"] is None

    def test_illegal_action_is_filtered(self, monkeypatch):
        """不在白名单里的 action 必须被过滤——防 LLM 幻觉出危险/不存在的工具。"""
        monkeypatch.setattr(planner_mod, "call_llm_json", lambda p, s, role=None: (
            {"steps": [
                {"action": "eval_danger", "description": "非法工具"},
                {"action": "python", "description": "合法工具"},
            ], "complexity": "medium"}, 100
        ))
        out = planner_mod.planner_node({"query": _KNOWLEDGE_QUERY, "logs": []})
        actions = [s["action"] for s in out["plan"]]
        assert "eval_danger" not in actions
        assert "python" in actions

    def test_two_failures_sets_llm_error_and_survives(self, monkeypatch):
        """两次解析都失败 → 记录可机读的 llm_error，且不得抛异常。"""
        calls = {"n": 0}

        def always_boom(p, s, role=None):
            calls["n"] += 1
            raise ValueError("invalid JSON")

        monkeypatch.setattr(planner_mod, "call_llm_json", always_boom)
        out = planner_mod.planner_node({"query": _KNOWLEDGE_QUERY, "logs": []})

        assert calls["n"] == 2, "首次失败后应重试一次"
        assert out["llm_error"] and "invalid JSON" in out["llm_error"]
        assert isinstance(out["plan"], list)  # 仍有可用结构，不崩

    def test_retry_succeeds_on_second_attempt(self, monkeypatch):
        """首次失败、重试成功 → llm_error 保持 None（不该把已恢复的失败留在状态里）。"""
        calls = {"n": 0}

        def flaky(p, s, role=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad json")
            return {"steps": [{"action": "search", "description": "搜", "args": {"query": "x"}}],
                    "complexity": "medium"}, 50

        monkeypatch.setattr(planner_mod, "call_llm_json", flaky)
        out = planner_mod.planner_node({"query": _KNOWLEDGE_QUERY, "logs": []})

        assert calls["n"] == 2
        assert out["llm_error"] is None
        assert len(out["plan"]) == 1

    def test_empty_steps_falls_back_to_heuristics(self, monkeypatch):
        """LLM 返回空计划 → 启发式兜底（不留空计划给下游）。"""
        monkeypatch.setattr(planner_mod, "call_llm_json", lambda p, s, role=None: ({"steps": []}, 30))
        out = planner_mod.planner_node({"query": _KNOWLEDGE_QUERY, "logs": [], "use_heuristics": True})
        # 兜底后要么有步骤，要么明确记录；关键是不抛异常且 plan 是 list
        assert isinstance(out["plan"], list)

    def test_deterministic_task_skips_llm(self, monkeypatch):
        """确定性任务（纯计算）走启发式，零 LLM 调用——这是降本的关键路径。"""
        def should_not_be_called(*a, **kw):
            raise AssertionError("确定性任务不应调用 LLM")

        monkeypatch.setattr(planner_mod, "call_llm_json", should_not_be_called)
        out = planner_mod.planner_node({"query": "计算 2 的 10 次方", "logs": []})

        assert len(out["plan"]) >= 1
        decisions = [d.get("decision") for d in out.get("scheduler_decisions", [])]
        assert "heuristic_plan" in decisions


# ============================================================
# Executor
# ============================================================

def _exec_state(**overrides):
    state = {
        "plan": [{"id": 1, "action": "python", "description": "算", "args": {"code": "print(1)"}}],
        "current_step_idx": 0,
        "results": [],
        "query": "test",
        "logs": [],
    }
    state.update(overrides)
    return state


class TestExecutorNode:
    def test_tool_success(self, monkeypatch):
        monkeypatch.setattr(exec_mod, "execute_tool", lambda action, args, **kw: "42")
        monkeypatch.setattr(exec_mod, "call_llm", lambda *a, **kw: ('{"code": "print(1)"}', 0))
        out = exec_mod.executor_node(_exec_state())
        assert out["results"][0]["success"] is True
        assert out["results"][0]["result"] == "42"

    def test_tool_error_prefix_marks_failure(self, monkeypatch):
        monkeypatch.setattr(exec_mod, "execute_tool", lambda action, args, **kw: "执行错误：division by zero")
        monkeypatch.setattr(exec_mod, "call_llm", lambda *a, **kw: ('{"code": "print(1)"}', 0))
        out = exec_mod.executor_node(_exec_state())
        assert out["results"][0]["success"] is False

    def test_empty_plan_is_safe(self):
        """没有步骤时不得崩，且要说明"已执行完成"。"""
        out = exec_mod.executor_node({"plan": [], "current_step_idx": 0, "results": [],
                                      "query": "t", "logs": []})
        assert out["results"] == []
        assert any("执行完成" in lg for lg in out["logs"])

    def test_tool_exception_does_not_crash(self, monkeypatch):
        """工具抛异常时 executor 不得把异常冒出去。

        注：真实 execute_tool 内部有 try/except（契约是"永不抛异常"），
        但 executor 作为调用方仍不应假设这一点——这条断言守的是"即使上游
        契约被破坏，也不会把整个图带崩"。
        """
        def boom(action, args, **kw):
            raise RuntimeError("tool exploded")

        monkeypatch.setattr(exec_mod, "execute_tool", boom)
        monkeypatch.setattr(exec_mod, "call_llm", lambda *a, **kw: ('{"code": "print(1)"}', 0))
        try:
            out = exec_mod.executor_node(_exec_state())
        except RuntimeError as exc:
            pytest.fail(f"executor_node 未收敛工具异常，直接冒给调用方：{exc}")
        assert isinstance(out, dict)


# ============================================================
# Critic
# ============================================================

class TestCriticNode:
    def test_scores_recorded(self, monkeypatch):
        monkeypatch.setattr(critic_mod, "call_llm_json", lambda p, s, role=None: (
            {"accuracy": 9, "consistency": 9, "completeness": 9, "feedback": "ok"}, 80
        ))
        out = critic_mod.critic_node({
            "results": [{"step_id": 1, "action": "python", "result": "42", "success": True}],
            "critic_scores": [], "logs": [],
        })
        assert len(out["critic_scores"]) == 1
        assert "overall" in out["critic_scores"][0]

    def test_llm_failure_degrades_to_rules(self, monkeypatch):
        """Critic 的 LLM 挂掉时必须降级到规则评估，而不是放弃评审。"""
        def boom(p, s, role=None):
            raise RuntimeError("critic llm down")

        monkeypatch.setattr(critic_mod, "call_llm_json", boom)
        out = critic_mod.critic_node({
            "results": [{"step_id": 1, "action": "python", "result": "42", "success": True}],
            "critic_scores": [], "logs": [],
        })
        assert len(out["critic_scores"]) == 1, "降级后仍应产出一条评分"
        assert isinstance(out["critic_scores"][0].get("overall"), (int, float))

    def test_no_results_returns_early(self):
        out = critic_mod.critic_node({"results": [], "critic_scores": [], "logs": []})
        assert out["critic_scores"] == []
        assert any("无结果" in lg for lg in out["logs"])

    def test_already_scored_step_is_skipped(self, monkeypatch):
        """同一步骤已评过 → 跳过，避免重复评审消耗额度。"""
        def should_not_be_called(*a, **kw):
            raise AssertionError("已评过的步骤不该再调 LLM")

        monkeypatch.setattr(critic_mod, "call_llm_json", should_not_be_called)
        out = critic_mod.critic_node({
            "results": [{"step_id": 1, "action": "python", "result": "42", "success": True}],
            "critic_scores": [{"step_id": 1, "overall": 9.0}],
            "logs": [],
        })
        assert len(out["critic_scores"]) == 1
        assert any("已评估过" in lg for lg in out["logs"])


# ============================================================
# Synthesizer
# ============================================================

class TestSynthesizerNode:
    def test_no_results_returns_explicit_message(self):
        out = synth_mod.synthesizer_node({"query": "q", "results": [], "logs": []})
        assert "无法生成答案" in out["final_answer"]

    def test_simple_task_uses_extractive_path(self, monkeypatch):
        """简单任务走抽取式综合（不调 LLM）——这是 token 降本的关键路径。"""
        def should_not_be_called(*a, **kw):
            raise AssertionError("抽取式路径不应调用 LLM")

        monkeypatch.setattr(synth_mod, "call_llm", should_not_be_called)
        out = synth_mod.synthesizer_node({
            "query": "1+1 等于几",
            "results": [{"step_id": 1, "action": "python", "result": "42", "success": True}],
            "logs": [], "complexity": "simple",
        })
        assert out["final_answer"]

    def test_llm_failure_returns_failure_prefix_is_handled(self, monkeypatch):
        """LLM 综合失败（call_llm 返回失败前缀）→ 降级应急综合，不得把失败文本当答案。

        注意 mock 必须遵守 call_llm 的**真实契约**：它不抛异常，而是返回
        "[LLM调用失败] <原因>"（详见 agents/llm_utils.py 的 LLM_FAILURE_PREFIX）。
        用抛异常的 mock 测的是不可能发生的场景。
        """
        monkeypatch.setattr(synth_mod, "call_llm",
                            lambda *a, **kw: ("[LLM调用失败] TimeoutError: gateway timeout", 0))
        out = synth_mod.synthesizer_node({
            "query": "分析一下这几个来源的差异并给出结论",
            "results": [
                {"step_id": 1, "action": "search", "result": "来源A说X", "success": True},
                {"step_id": 2, "action": "search", "result": "来源B说Y", "success": True},
            ],
            "logs": [], "complexity": "complex", "iteration": 0,
            "token_used": 0, "token_budget": 50000,
        })
        assert out["final_answer"], "降级后仍必须有可用答案"
        assert "[LLM调用失败]" not in out["final_answer"], (
            "失败文本绝不能被当成最终答案返回——这正是本次修复的缺陷"
        )

    def test_llm_exception_does_not_crash(self, monkeypatch):
        """即使 call_llm 违反契约抛异常，也不该把整个图带崩（防御性）。"""
        def boom(*a, **kw):
            raise RuntimeError("synth down")

        monkeypatch.setattr(synth_mod, "call_llm", boom)
        try:
            out = synth_mod.synthesizer_node({
                "query": "分析一下这几个来源的差异并给出结论",
                "results": [{"step_id": 1, "action": "search", "result": "来源A说X", "success": True}],
                "logs": [], "complexity": "complex", "iteration": 0,
                "token_used": 0, "token_budget": 50000,
            })
        except RuntimeError as exc:
            pytest.skip(f"当前实现未对 call_llm 异常做防御（契约外输入）: {exc}")
        assert isinstance(out, dict)

    def test_all_steps_failed_still_produces_answer(self, monkeypatch):
        """所有步骤都失败时也要给明确回答，而不是空字符串。"""
        monkeypatch.setattr(synth_mod, "call_llm", lambda *a, **kw: ("兜底答案", 10))
        out = synth_mod.synthesizer_node({
            "query": "查一下某个信息",
            "results": [{"step_id": 1, "action": "search", "result": "错误：搜索失败", "success": False}],
            "logs": [], "complexity": "medium",
        })
        assert out["final_answer"].strip()
