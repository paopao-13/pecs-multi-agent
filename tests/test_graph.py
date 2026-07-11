"""
图构建单元测试

测试 graph/builder.py：
- build_graph() 返回可编译的图
- create_initial_state() 返回正确初始状态
- route_after_critic 路由逻辑（达标 / 不达标）

接口说明（已通过阅读源码确认）：
    from graph.builder import (
        build_graph, create_initial_state,
        route_after_critic, route_after_synthesizer,
    )

注意：本测试只验证纯逻辑（图编译 / 初始状态 / 路由函数），
不调用 graph.invoke()，不依赖外部 LLM API。
"""
from graph.builder import (
    build_graph,
    create_initial_state,
    route_after_critic,
    route_after_synthesizer,
    route_after_executor,
)


def test_build_graph():
    """build_graph() 返回可编译的图"""
    graph = build_graph()
    assert graph is not None
    # 编译后的 LangGraph 实例应支持 invoke 调用
    assert hasattr(graph, "invoke")


def test_create_initial_state():
    """create_initial_state() 返回正确初始状态"""
    state = create_initial_state("测试问题")

    assert isinstance(state, dict)
    # 输入字段
    assert state["query"] == "测试问题"
    # 计划与结果应为空
    assert state["plan"] == []
    assert state["results"] == []
    assert state["critic_scores"] == []
    assert state["current_step_idx"] == 0
    # 综合输出初始为空
    assert state["final_answer"] == ""
    assert state["reflection"] == ""
    assert state["retry_feedback"] == ""
    # 迭代与预算
    assert state["iteration"] == 0
    assert state["token_used"] == 0
    assert state["token_budget"] > 0
    assert state["role_token_used"]["planner"] == 0
    assert state["budget_events"] == []
    assert state["scheduler_decisions"] == []
    # 日志
    assert state["logs"] == []


def test_route_after_critic_pass():
    """评分 >= 4 且还有步骤时路由到 executor"""
    state = {
        "critic_scores": [{"overall": 4.5}],
        "results": [
            {"step_id": 1, "action": "search", "result": "...", "success": True}
        ],
        "plan": [
            {"id": 1, "action": "search", "description": "step1",
             "args": {}, "status": "done", "result": "...", "retry_count": 0},
            {"id": 2, "action": "python", "description": "step2",
             "args": {}, "status": "pending", "result": None, "retry_count": 0},
        ],
        # 当前执行到第 2 步（索引 1），plan 还有第 2 步未完成
        "current_step_idx": 1,
        "token_used": 0,
        "token_budget": 50000,
        "retry_feedback": "",
    }
    # 评分 4.5 >= 4.0，且 current_step_idx(1) < len(plan)(2) → executor
    result = route_after_critic(state)
    assert result == "executor"


def test_route_after_critic_fail():
    """评分 < 4 时路由到 executor_retry 或 synthesizer"""
    state = {
        "critic_scores": [{"overall": 2.0}],
        "results": [
            {"step_id": 1, "action": "search", "result": "...", "success": False}
        ],
        "plan": [
            {"id": 1, "action": "search", "description": "step1",
             "args": {}, "status": "running", "result": "...", "retry_count": 0},
        ],
        # 当前步骤索引已超过 plan 长度（无更多新步骤）
        "current_step_idx": 1,
        "token_used": 0,
        "token_budget": 50000,
        "retry_feedback": "请修正搜索查询关键词",
    }
    result = route_after_critic(state)
    # 评分 2.0 < 4.0，retry_count(0) < 3，且存在 retry_feedback → executor_retry
    assert result in ("executor_retry", "synthesizer")
    assert result == "executor_retry"


def test_route_after_executor_simple_done():
    """simple 任务所有步骤执行完后直接去 synthesizer（跳过 Critic）"""
    state = {
        "complexity": "simple",
        "plan": [{"id": 1, "action": "python", "description": "calc", "args": {}, "status": "done", "result": "42", "retry_count": 0}],
        "current_step_idx": 1,  # 超过 plan 长度
    }
    result = route_after_executor(state)
    assert result == "synthesizer"


def test_route_after_executor_simple_more_steps():
    """simple 任务还有步骤时继续执行（不经过 Critic）"""
    state = {
        "complexity": "simple",
        "plan": [
            {"id": 1, "action": "search", "description": "step1", "args": {}, "status": "done", "result": "...", "retry_count": 0},
            {"id": 2, "action": "python", "description": "step2", "args": {}, "status": "pending", "result": None, "retry_count": 0},
        ],
        "current_step_idx": 1,  # 还有第 2 步
    }
    result = route_after_executor(state)
    assert result == "executor"


def test_route_after_executor_medium_goes_critic():
    """medium/complex 任务经过 Critic 评审"""
    state = {
        "complexity": "medium",
        "plan": [{"id": 1, "action": "search", "description": "step1", "args": {}, "status": "done", "result": "...", "retry_count": 0}],
        "current_step_idx": 1,
    }
    result = route_after_executor(state)
    assert result == "critic"


def test_route_after_executor_skips_low_risk_critic_when_budget_tight():
    """预算超过70%且最近步骤低风险成功时，跳过详细 Critic"""
    state = {
        "complexity": "medium",
        "plan": [
            {"id": 1, "action": "python", "description": "calc", "args": {}, "status": "done", "result": "42", "retry_count": 0, "risk": "low"}
        ],
        "results": [{"step_id": 1, "action": "python", "result": "42", "success": True}],
        "current_step_idx": 1,
        "token_used": 800,
        "token_budget": 1000,
    }
    result = route_after_executor(state)
    assert result == "synthesizer"
