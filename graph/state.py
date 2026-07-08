"""
AgentState 定义 —— LangGraph 共享状态

核心概念：
  LangGraph 的状态图（StateGraph）通过一个共享的字典在节点间传递数据。
  每个节点（Agent角色）读取状态中的信息，处理后将结果写回状态。
  这样四个角色就像在同一块白板上协作：前面的写计划，后面的读计划执行，
  评审者看执行结果打分，综合者收集所有结果生成最终答案。
"""
from typing import TypedDict, List, Dict, Any, Optional


class StepPlan(TypedDict):
    """单个执行步骤的结构"""
    id: int                    # 步骤编号
    action: str                # 动作类型：search / python / file_read / api_call / llm_reasoning
    description: str           # 步骤描述（给 Executor 看的自然语言指令）
    args: Dict[str, Any]       # 动作参数
    status: str                # pending / running / done / failed
    result: Optional[str]      # 执行结果
    retry_count: int           # 已重试次数
    risk: str                  # low / medium / high，用于预算调度
    depends_on: List[int]      # 依赖的步骤 ID 列表


class CriticScore(TypedDict):
    """Critic 评估结果"""
    accuracy: int              # 事实准确性 1-5
    consistency: int           # 逻辑一致性 1-5
    completeness: int          # 信息完整性 1-5
    overall: float             # 综合评分
    feedback: str              # 修改建议
    step_id: int               # 评估的步骤ID


class AgentState(TypedDict):
    """
    LangGraph 共享状态 —— 贯穿整个 Plan-Execute-Reflect 循环

    数据流向：
    用户输入 query
      → Planner 读取 query + reflection，生成 plan
      → Executor 读取 plan，逐步执行，写回 results
      → Critic 读取 results，打分写回 critic_scores
      → Synthesizer 读取所有 results，生成 final_answer
    """
    # ===== 输入 =====
    query: str                          # 用户的原始问题

    # ===== Planner 输出 =====
    plan: List[StepPlan]                # 分解后的执行计划（步骤列表）
    current_step_idx: int               # 当前执行到第几步
    complexity: str                     # 任务复杂度: simple / medium / complex

    # ===== Executor 输出 =====
    results: List[Dict[str, Any]]       # 每步执行结果 [{step_id, action, result, success}]

    # ===== Critic 输出 =====
    critic_scores: List[CriticScore]    # 每步的评审打分
    retry_feedback: str                 # 给 Executor 的重试建议

    # ===== Synthesizer 输出 =====
    final_answer: str                   # 最终答案
    answer_format: str                  # 输出格式：text / json / table / code

    # ===== Token 预算管理 =====
    token_used: int                     # 已消耗的 Token 数
    token_budget: int                   # Token 预算上限
    budget_degraded: bool               # 是否已触发降级
    role_token_used: Dict[str, int]      # 各角色 Token 消耗明细
    budget_events: List[Dict[str, Any]]  # Token 消耗与降级事件
    scheduler_decisions: List[Dict[str, Any]]  # 预算感知调度决策

    # ===== 反思循环 =====
    reflection: str                     # 上一轮的反思总结（供下一轮 Planner 参考）
    iteration: int                      # 当前循环轮次

    # ===== 执行日志 =====
    logs: List[str]                     # 执行过程日志（供 Web 界面展示）

    # ===== 模式控制 =====
    use_heuristics: bool                # 是否启用启发式规划/综合（成本消融时设为 False）
