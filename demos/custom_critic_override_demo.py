"""
自定义 Critic 覆盖 Demo

本 Demo 展示如何：
1. 继承原生 Critic 的评审逻辑（critic_node 函数式节点）
2. 在原有三维评分（准确性/一致性/完整性）基础上增加"效率评分"维度
3. 将自定义 Critic 替换原生 Critic 注入 LangGraph 图
4. 构建完整的图并运行任务

设计思路：
  原生 critic_node 是函数式节点（不是类），无法直接继承。
  我们采用"包装模式"（Wrapper Pattern）：
  - CustomCritic 类在 __call__ 中先调用原生 critic_node 获取基础评分
  - 然后在基础评分上追加效率维度
  - 最后重新计算加权综合分

  这样既复用了原生逻辑（规则验证、LLM评分、预算感知等），
  又通过组合方式实现了维度扩展，符合开闭原则。

运行方式：
  python demos/custom_critic_override_demo.py
"""
import os
import sys

# 把项目根目录加到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.graph import StateGraph, END
from graph.state import AgentState
from graph.token_budget import get_budget_policy
from agents.planner import planner_node
from agents.executor import executor_node, executor_retry_node
from agents.critic import critic_node, CRITIC_SYSTEM_PROMPT
from agents.synthesizer import synthesizer_node
from graph.builder import (
    route_after_executor,
    route_after_critic,
    route_after_synthesizer,
    create_initial_state,
)
from config import DEFAULT_TOKEN_BUDGET


# ====================================================================
# 自定义 Critic 系统提示词（扩展原生提示词，增加效率维度）
# ====================================================================
CUSTOM_CRITIC_SYSTEM_PROMPT = CRITIC_SYSTEM_PROMPT + """

4. efficiency（执行效率）：执行过程是否高效，有无浪费 Token 或不必要的重试

效率评分标准：
- 5分：一次成功，结果精炼，Token 消耗低
- 4分：一次成功，但结果略冗长或 Token 偏多
- 3分：重试1次后成功，或结果信息密度偏低
- 2分：重试2次后成功，或 Token 消耗明显偏高
- 1分：多次重试，或执行过程严重低效

注意：overall = (accuracy + consistency + completeness + efficiency) / 4，保留一位小数。
"""


class CustomCritic:
    """
    自定义 Critic：在原有三维评分基础上增加"效率评分"维度

    包装模式：
      __call__ 方法兼容 LangGraph 节点接口 (state: dict) -> dict
      内部先调用原生 critic_node 获取三维基础评分，
      再追加效率维度并重新计算综合分。

    参数:
        efficiency_weight: 效率维度在综合分中的权重（0~1）
                           原有三维均分剩余权重 (1 - efficiency_weight) / 3
        use_llm_efficiency: 是否使用 LLM 评估效率（False=纯规则评估）
    """

    def __init__(
        self,
        efficiency_weight: float = 0.25,
        use_llm_efficiency: bool = False,
    ):
        self.efficiency_weight = efficiency_weight
        self.use_llm_efficiency = use_llm_efficiency
        # 保留原生系统提示词的引用，方便调试
        self.native_system_prompt = CRITIC_SYSTEM_PROMPT
        self.custom_system_prompt = CUSTOM_CRITIC_SYSTEM_PROMPT

    def __call__(self, state: dict) -> dict:
        """
        LangGraph 节点接口：接收状态，返回状态更新

        执行流程：
        1. 记录原始评分数（用于判断是否新增了评分）
        2. 调用原生 critic_node 获取基础三维评分
        3. 如果有新评分，追加效率维度
        4. 重新计算四维加权综合分
        5. 返回更新后的状态
        """
        # 1. 记录原始评分数
        original_scores = state.get("critic_scores", [])
        original_count = len(original_scores)

        # 2. 调用原生 critic_node（复用全部原生逻辑：规则验证、LLM评分、预算感知等）
        result = critic_node(state)

        # 3. 检查是否新增了评分
        new_scores = result.get("critic_scores", original_scores)
        if len(new_scores) <= original_count:
            # 没有新评分（可能已评估过或无结果可评估），直接返回
            return result

        # 4. 有新评分，追加效率维度
        latest_score = new_scores[-1]
        results = state.get("results", [])

        if results:
            latest_result = results[-1]
            efficiency, efficiency_feedback = self._evaluate_efficiency(state, latest_result, latest_score)

            # 追加效率维度到评分
            latest_score["efficiency"] = efficiency

            # 5. 重新计算四维加权综合分
            accuracy = latest_score.get("accuracy", 3)
            consistency = latest_score.get("consistency", 3)
            completeness = latest_score.get("completeness", 3)

            base_weight = (1 - self.efficiency_weight) / 3
            new_overall = (
                accuracy * base_weight
                + consistency * base_weight
                + completeness * base_weight
                + efficiency * self.efficiency_weight
            )
            old_overall = latest_score.get("overall", 0)
            latest_score["overall"] = round(new_overall, 1)

            # 合并反馈信息
            original_feedback = latest_score.get("feedback", "")
            latest_score["feedback"] = (
                f"{original_feedback} [效率维度] {efficiency_feedback}"
            )

            # 6. 更新日志
            logs = result.get("logs", state.get("logs", []))
            logs.append(
                f"[CustomCritic] 效率评分: {efficiency}/5 "
                f"(综合分: {old_overall} -> {latest_score['overall']})"
            )
            result["logs"] = logs

        return result

    def _evaluate_efficiency(self, state: dict, latest_result: dict, base_score: dict) -> tuple:
        """
        评估执行效率

        基于以下因素计算效率分（1-5）：
        1. 重试次数：retry_count 越少越好
        2. 结果长度：过长可能信息密度低
        3. 执行成功：失败的执行效率低
        4. Token 消耗：参考角色 Token 使用量

        参数:
            state: 当前状态
            latest_result: 最新的执行结果
            base_score: 原生 Critic 给出的基础评分

        返回:
            (efficiency_score, feedback_str)
        """
        # --- 获取重试次数 ---
        step_id = latest_result.get("step_id", 0)
        plan = state.get("plan", [])
        retry_count = 0
        for step in plan:
            if step.get("id") == step_id:
                retry_count = step.get("retry_count", 0)
                break

        # --- 获取结果文本 ---
        result_text = latest_result.get("result", "")
        result_len = len(result_text)
        success = latest_result.get("success", True)

        # --- 获取 Token 使用情况 ---
        role_token_used = state.get("role_token_used", {})
        executor_tokens = role_token_used.get("executor", 0)
        total_results = len(state.get("results", []))
        # 估算单步 Token（总 Token / 结果数）
        avg_step_tokens = executor_tokens / max(total_results, 1) if total_results else 0

        # --- 计算效率分 ---
        efficiency = 5  # 从满分开始扣减
        feedback_parts = []

        # 因素1：重试次数（每次重试扣1分，最多扣3分）
        if retry_count > 0:
            penalty = min(retry_count, 3)
            efficiency -= penalty
            feedback_parts.append(f"重试{retry_count}次(-{penalty})")

        # 因素2：结果长度（过长扣分，信息密度低）
        if result_len > 3000:
            efficiency -= 1
            feedback_parts.append("结果过长(-1)")
        elif result_len > 1500:
            efficiency -= 0.5
            feedback_parts.append("结果略长(-0.5)")

        # 因素3：执行失败扣分
        if not success:
            efficiency -= 1
            feedback_parts.append("执行失败(-1)")

        # 因素4：单步 Token 过高扣分
        if avg_step_tokens > 5000:
            efficiency -= 1
            feedback_parts.append(f"Token偏高({int(avg_step_tokens)}, -1)")
        elif avg_step_tokens > 2000:
            efficiency -= 0.5
            feedback_parts.append(f"Token略高({int(avg_step_tokens)}, -0.5)")

        # 限制在 1-5 范围
        efficiency = max(1, min(5, int(round(efficiency))))

        if not feedback_parts:
            feedback_parts.append("执行高效，一次成功")

        feedback = "; ".join(feedback_parts)
        return efficiency, feedback


# ====================================================================
# 自定义图构建：用 CustomCritic 替换原生 critic_node
# ====================================================================
def build_graph_with_custom_critic(
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    custom_critic: CustomCritic = None,
):
    """
    构建使用自定义 Critic 的 LangGraph 状态图

    与原生 build_graph 的唯一区别：
    critic 节点从 critic_node 函数替换为 CustomCritic 实例

    参数:
        token_budget: Token 预算上限
        custom_critic: 自定义 Critic 实例（None 时使用默认配置）

    返回:
        编译后的 LangGraph 图实例
    """
    if custom_critic is None:
        custom_critic = CustomCritic()

    # 创建状态图
    graph = StateGraph(AgentState)

    # ===== 添加节点 =====
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("executor_retry", executor_retry_node)
    # ★ 关键：用 CustomCritic 实例替换原生 critic_node ★
    # CustomCritic.__call__ 兼容 LangGraph 节点接口
    graph.add_node("critic", custom_critic)
    graph.add_node("synthesizer", synthesizer_node)

    # ===== 添加边（与原生图完全一致）=====
    graph.add_edge("planner", "executor")
    graph.add_conditional_edges("executor", route_after_executor)
    graph.add_edge("executor_retry", "executor")
    graph.add_conditional_edges("critic", route_after_critic)
    graph.add_conditional_edges("synthesizer", route_after_synthesizer)

    # ===== 设置入口点并编译 =====
    graph.set_entry_point("planner")
    compiled = graph.compile()
    return compiled


def run_task_with_custom_critic(
    query: str,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    custom_critic: CustomCritic = None,
) -> dict:
    """
    使用自定义 Critic 运行一个完整任务

    参数:
        query: 用户问题
        token_budget: Token 预算上限
        custom_critic: 自定义 Critic 实例

    返回:
        最终状态（包含 final_answer、critic_scores 等）
    """
    # 构建使用自定义 Critic 的图
    compiled_graph = build_graph_with_custom_critic(token_budget, custom_critic)

    # 创建初始状态
    initial_state = create_initial_state(query, token_budget)

    # 执行图
    final_state = compiled_graph.invoke(initial_state)
    return final_state


# ====================================================================
# 演示主函数
# ====================================================================
def main():
    """
    演示：使用自定义 Critic 运行任务，展示四维评分效果
    """
    print("=" * 70)
    print("  自定义 Critic 覆盖 Demo")
    print("  在原有三维评分(准确性/一致性/完整性)基础上增加效率评分维度")
    print("=" * 70)
    print()

    # --- 创建自定义 Critic 实例 ---
    # efficiency_weight=0.25 表示效率维度占综合分的 25%
    # 原有三维各占 (1-0.25)/3 = 25%
    custom_critic = CustomCritic(
        efficiency_weight=0.25,
        use_llm_efficiency=False,  # 使用规则评估效率（不额外消耗 Token）
    )

    print(f"  效率维度权重: {custom_critic.efficiency_weight}")
    print(f"  效率评估方式: {'LLM' if custom_critic.use_llm_efficiency else '规则'}")
    print()

    # --- 运行任务 ---
    task = "13的阶乘(13!)是多少？"
    print(f"  任务: {task}")
    print("-" * 70)
    print()

    final_state = run_task_with_custom_critic(task, custom_critic=custom_critic)

    # --- 输出结果 ---
    print()
    print("=" * 70)
    print("  执行结果")
    print("=" * 70)
    print(f"  最终答案: {final_state.get('final_answer', '无输出')}")
    print(f"  Token 消耗: {final_state.get('token_used', 0)}")
    print()

    # --- 展示评分详情 ---
    critic_scores = final_state.get("critic_scores", [])
    print(f"  Critic 评分数: {len(critic_scores)}")
    print("-" * 70)

    for i, score in enumerate(critic_scores):
        print(f"  步骤 {score.get('step_id', i + 1)} 评分:")
        print(f"    准确性 (accuracy):     {score.get('accuracy', 'N/A')}")
        print(f"    一致性 (consistency):  {score.get('consistency', 'N/A')}")
        print(f"    完整性 (completeness): {score.get('completeness', 'N/A')}")
        print(f"    效率   (efficiency):   {score.get('efficiency', 'N/A')}  <-- 自定义新增")
        print(f"    综合分 (overall):      {score.get('overall', 'N/A')}")
        print(f"    反馈: {score.get('feedback', 'N/A')}")
        print()

    # --- 展示日志 ---
    print("-" * 70)
    print("  执行日志（最后10条）:")
    print("-" * 70)
    logs = final_state.get("logs", [])
    for log in logs[-10:]:
        print(f"  {log}")

    print()
    print("=" * 70)
    print("  Demo 说明")
    print("=" * 70)
    print("""
  1. CustomCritic 类通过包装模式继承原生 critic_node 的全部逻辑
     （规则验证、LLM 评分、预算感知快速模式等）

  2. 新增的效率维度基于以下因素计算：
     - 重试次数（retry_count）
     - 结果长度（信息密度）
     - 执行成功/失败状态
     - 单步 Token 消耗

  3. 综合分计算公式（四维加权）：
     overall = accuracy * base_weight
             + consistency * base_weight
             + completeness * base_weight
             + efficiency * efficiency_weight
     其中 base_weight = (1 - efficiency_weight) / 3

  4. 注入方式：在 build_graph 中用 CustomCritic 实例替换 critic_node
     graph.add_node("critic", custom_critic)
     CustomCritic.__call__ 兼容 LangGraph 节点接口
""")


if __name__ == "__main__":
    main()
