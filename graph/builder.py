"""
LangGraph 状态图构建器

核心概念：
  LangGraph 用"状态图"（StateGraph）来组织多 Agent 协作。
  - 节点（Node）= 每个角色（Planner/Executor/Critic/Synthesizer）
  - 边（Edge）= 角色之间的流转逻辑
  - 条件边（Conditional Edge）= 根据状态动态决定下一步去哪个节点

  Plan-Execute-Reflect 循环的流转：
    Planner → Executor → Critic → 判断(合格?)
                                    ├─ 是 → 还有下一步? → Executor（执行下一步）
                                    │                     └─ 没有了 → Synthesizer
                                    └─ 否 → Executor（重试当前步）
    Synthesizer → 判断(需要反思?)
                    ├─ 是 → Planner（重新规划）
                    └─ 否 → END（输出最终答案）
"""
from langgraph.graph import StateGraph, END
from graph.state import AgentState
from graph.token_budget import get_budget_policy, check_role_budget
from agents.planner import planner_node
from agents.executor import executor_node, executor_retry_node
from agents.critic import critic_node
from agents.synthesizer import synthesizer_node
from config import DEFAULT_TOKEN_BUDGET, MAX_ITERATIONS


def route_after_executor(state: dict) -> str:
    """
    Executor 之后的路由函数

    决策逻辑：
    1. Executor角色独立配额超限 → 强制去 Synthesizer（角色级降级）
    2. simple 任务且所有步骤已执行完 → 直接去 Synthesizer（跳过 Critic）
    3. simple 任务但还有步骤 → 继续执行下一步（不经过 Critic）
    4. medium/complex 任务 → 去 Critic 做质量评审
    """
    complexity = state.get("complexity", "medium")
    plan = state.get("plan", [])
    current_idx = state.get("current_step_idx", 0)
    results = state.get("results", [])

    latest_step = plan[current_idx - 1] if plan and 0 < current_idx <= len(plan) else {}
    latest_risk = latest_step.get("risk", "medium")
    latest_result = results[-1] if results else {}
    policy = get_budget_policy(state, latest_risk)

    # 角色独立配额检查：Executor 超配额直接强制收尾
    executor_quota = check_role_budget(state, "executor")
    if executor_quota["exceeded"]:
        return "synthesizer"

    if policy["force_synthesize"]:
        return "synthesizer"

    if complexity == "simple":
        # 简单任务跳过 Critic
        if current_idx >= len(plan):
            return "synthesizer"
        else:
            return "executor"  # 继续执行下一步（不经过 Critic）

    if policy["skip_low_risk_critic"] and latest_result.get("success", False):
        return "executor" if current_idx < len(plan) else "synthesizer"
    
    # medium/complex 任务走 Critic 评审
    return "critic"


def route_after_critic(state: dict) -> str:
    """
    Critic 之后的路由函数

    决策逻辑（优先级从高到低）：
    1. 预算耗尽（>95%）→ 强制去 Synthesizer
    2. 质量达标（综合分≥4）→ 检查是否还有未执行步骤
       - 有 → 回 Executor 执行下一步
       - 没有 → 去 Synthesizer
    3. 质量不达标但重试次数已达上限（≥3）→ 去 Synthesizer
    4. 质量不达标且可重试 → 回 Executor 重试

    返回值是下一个节点名称
    """
    critic_scores = state.get("critic_scores", [])
    results = state.get("results", [])
    plan = state.get("plan", [])
    current_idx = state.get("current_step_idx", 0)
    retry_feedback = state.get("retry_feedback", "")

    # 角色独立配额检查：Critic 超配额时跳过评审，直接进入下一步
    critic_quota = check_role_budget(state, "critic")
    if critic_quota["exceeded"]:
        if current_idx < len(plan):
            return "executor"  # 跳过评审，直接执行下一步
        return "synthesizer"

    # 预算检查
    if get_budget_policy(state)["force_synthesize"]:
        return "synthesizer"

    # 如果没有评分，去综合
    if not critic_scores:
        return "synthesizer"

    # 获取最新评分
    latest_score = critic_scores[-1]
    overall = latest_score.get("overall", 0)

    # 质量达标
    if overall >= 4.0:
        # 检查是否还有未执行的步骤
        if current_idx < len(plan):
            return "executor"  # 执行下一步
        else:
            return "synthesizer"  # 所有步骤完成，去综合

    # 质量不达标
    # 检查重试次数
    if results:
        latest_result = results[-1]
        step_id = latest_result.get("step_id", len(results))
        # 找到对应的步骤
        step = None
        for s in plan:
            if s.get("id") == step_id:
                step = s
                break
        if step and step.get("retry_count", 0) >= 3:
            # 重试上限，强制通过
            return "executor" if current_idx < len(plan) else "synthesizer"

    # 可以重试
    if retry_feedback:
        return "executor_retry"

    # 默认：执行下一步或综合
    return "executor" if current_idx < len(plan) else "synthesizer"


def route_after_synthesizer(state: dict) -> str:
    """
    Synthesizer 之后的路由函数

    决策逻辑：
    1. Synthesizer角色独立配额超限 → 直接终止（不再触发重规划）
    2. 如果有反思（reflection 非空）且未达到最大迭代次数 → 回 Planner 重新规划
    3. 否则 → END（输出最终答案）
    """
    # 角色独立配额检查：Synthesizer 超配额时直接终止
    synth_quota = check_role_budget(state, "synthesizer")
    if synth_quota["exceeded"]:
        return END

    reflection = state.get("reflection", "")
    iteration = state.get("iteration", 0)

    if reflection and iteration < MAX_ITERATIONS:
        return "planner"
    return END


def build_graph(token_budget: int = DEFAULT_TOKEN_BUDGET):
    """
    构建 LangGraph 状态图

    返回编译后的图实例，可以通过 graph.invoke(initial_state) 执行
    """
    # 创建状态图，指定状态类型
    graph = StateGraph(AgentState)

    # ===== 添加节点（4个角色 + 1个重试节点）=====
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("executor_retry", executor_retry_node)
    graph.add_node("critic", critic_node)
    graph.add_node("synthesizer", synthesizer_node)

    # ===== 添加边（固定流转）=====
    # Planner → Executor（规划完就执行）
    graph.add_edge("planner", "executor")

    # Executor → 条件路由（simple 任务跳过 Critic，直接去 Synthesizer）
    graph.add_conditional_edges("executor", route_after_executor)

    # executor_retry → Executor（重试时重新执行）
    graph.add_edge("executor_retry", "executor")

    # ===== 添加条件边（动态路由）=====
    # Critic 之后：根据评分决定去重试、执行下一步、还是综合
    graph.add_conditional_edges("critic", route_after_critic)

    # Synthesizer 之后：根据反思决定回 Planner 还是结束
    graph.add_conditional_edges("synthesizer", route_after_synthesizer)

    # ===== 设置入口点 =====
    graph.set_entry_point("planner")

    # ===== 编译图 =====
    compiled = graph.compile()
    return compiled


def create_initial_state(query: str, token_budget: int = DEFAULT_TOKEN_BUDGET, use_heuristics: bool = True) -> AgentState:
    """
    创建初始状态

    这是 Plan-Execute-Reflect 循环的起点：
    用户问题 + 空的计划/结果 + Token预算

    参数:
        use_heuristics: 是否启用启发式规划/综合（成本消融时设为 False 以测量纯 LLM 消耗）

    返回:
        AgentState Pydantic 实例（兼容字典式访问）
    """
    return AgentState(
        query=query,
        token_budget=token_budget,
        use_heuristics=use_heuristics,
    )


def run_task(query: str, token_budget: int = DEFAULT_TOKEN_BUDGET, use_heuristics: bool = True) -> dict:
    """
    运行一个完整任务

    参数:
        query: 用户问题
        token_budget: Token 预算上限
        use_heuristics: 是否启用启发式（成本消融时设为 False）

    返回:
        最终状态（包含 final_answer、token_used、logs 等）
    """
    # 构建图
    compiled_graph = build_graph(token_budget)

    # 创建初始状态
    initial_state = create_initial_state(query, token_budget, use_heuristics=use_heuristics)

    # 执行图
    final_state = compiled_graph.invoke(initial_state)

    return final_state
