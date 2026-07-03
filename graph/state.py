"""
AgentState 定义 —— LangGraph 共享状态

核心概念：
  LangGraph 的状态图（StateGraph）通过一个共享的状态对象在节点间传递数据。
  每个节点（Agent角色）读取状态中的信息，处理后将结果写回状态。
  这样四个角色就像在同一块白板上协作：前面的写计划，后面的读计划执行，
  评审者看执行结果打分，综合者收集所有结果生成最终答案。
"""
from typing import List, Dict, Any, Optional, TypedDict


class StepPlan(TypedDict):
    """单个执行步骤的结构"""
    id: int
    action: str
    description: str
    args: Dict[str, Any]
    status: str
    result: Optional[str]
    retry_count: int
    risk: str
    depends_on: List[int]


class CriticScore(TypedDict):
    """Critic 评估结果"""
    accuracy: int
    consistency: int
    completeness: int
    overall: float
    feedback: str
    step_id: int


class AgentState(TypedDict):
    """
    LangGraph 共享状态 —— 贯穿整个 Plan-Execute-Reflect 循环

    数据流向：
    用户输入 query
      -> Planner 读取 query + reflection，生成 plan
      -> Executor 读取 plan，逐步执行，写回 results
      -> Critic 读取 results，打分写回 critic_scores
      -> Synthesizer 读取所有 results，生成 final_answer
    """
    query: str
    plan: List[Dict[str, Any]]
    current_step_idx: int
    complexity: str
    results: List[Dict[str, Any]]
    critic_scores: List[Dict[str, Any]]
    retry_feedback: str
    final_answer: str
    answer_format: str
    token_used: int
    token_budget: int
    budget_degraded: bool
    role_token_used: Dict[str, int]
    budget_events: List[Dict[str, Any]]
    scheduler_decisions: List[Dict[str, Any]]
    reflection: str
    iteration: int
    logs: List[str]
    use_heuristics: bool
