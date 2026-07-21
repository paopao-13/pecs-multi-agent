"""
AgentState 定义 —— LangGraph 共享状态（Pydantic 强类型）

核心概念：
  LangGraph 的状态图（StateGraph）通过一个共享的状态对象在节点间传递数据。
  每个节点（Agent角色）读取状态中的信息，处理后将结果写回状态。
  这样四个角色就像在同一块白板上协作：前面的写计划，后面的读计划执行，
  评审者看执行结果打分，综合者收集所有结果生成最终答案。

技术选型：
  使用 Pydantic BaseModel 替代 TypedDict，获得以下优势：
  1. 运行时字段校验：类型错误在赋值时即被拦截，而非运行时才暴露
  2. 默认值与 Field 工厂：无需在 create_initial_state 中手动初始化每个字段
  3. JSON 序列化/反序列化：原生支持 model_dump_json() / model_validate_json()
  4. IDE 自动补全：字段类型提示完整，开发体验更好
  5. LangGraph 1.x 原生支持 Pydantic BaseModel 作为 StateGraph 状态类型
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class StepPlan(BaseModel):
    """单个执行步骤的结构"""
    id: int                                    # 步骤编号
    action: str                                # 动作类型：search / python / file_read / api_call / llm_reasoning
    description: str                           # 步骤描述（给 Executor 看的自然语言指令）
    args: Dict[str, Any] = Field(default_factory=dict)   # 动作参数
    status: str = "pending"                    # pending / running / done / failed
    result: Optional[str] = None               # 执行结果
    retry_count: int = 0                       # 已重试次数
    risk: str = "medium"                       # low / medium / high，用于预算调度
    depends_on: List[int] = Field(default_factory=list)  # 依赖的步骤 ID 列表


class CriticScore(BaseModel):
    """Critic 评估结果"""
    accuracy: int              # 事实准确性 1-5
    consistency: int           # 逻辑一致性 1-5
    completeness: int          # 信息完整性 1-5
    overall: float             # 综合评分
    feedback: str = ""         # 修改建议
    step_id: int = 0           # 评估的步骤ID


class AgentState(BaseModel):
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
    query: str = ""                                     # 用户的原始问题

    # ===== Planner 输出 =====
    plan: List[Dict[str, Any]] = Field(default_factory=list)    # 分解后的执行计划（步骤列表）
    current_step_idx: int = 0                            # 当前执行到第几步
    complexity: str = "medium"                           # 任务复杂度: simple / medium / complex

    # ===== Executor 输出 =====
    results: List[Dict[str, Any]] = Field(default_factory=list)  # 每步执行结果

    # ===== Critic 输出 =====
    critic_scores: List[Dict[str, Any]] = Field(default_factory=list)  # 每步的评审打分
    retry_feedback: str = ""                            # 给 Executor 的重试建议

    # ===== Synthesizer 输出 =====
    final_answer: str = ""                              # 最终答案
    answer_format: str = "text"                         # 输出格式：text / json / table / code

    # ===== Token 预算管理 =====
    token_used: int = 0                                 # 已消耗的 Token 数
    token_budget: int = 50000                           # Token 预算上限
    budget_degraded: bool = False                       # 是否已触发降级
    role_token_used: Dict[str, int] = Field(            # 各角色 Token 消耗明细
        default_factory=lambda: {
            "planner": 0,
            "executor": 0,
            "critic": 0,
            "synthesizer": 0,
        }
    )
    budget_events: List[Dict[str, Any]] = Field(default_factory=list)        # Token 消耗与降级事件
    scheduler_decisions: List[Dict[str, Any]] = Field(default_factory=list)  # 预算感知调度决策

    # ===== 反思循环 =====
    reflection: str = ""                                # 上一轮的反思总结（供下一轮 Planner 参考）
    iteration: int = 0                                  # 当前循环轮次
    step_count: int = 0                                 # 已执行步骤数（= len(results)），供 API 上报

    # ===== 执行日志 =====
    logs: List[str] = Field(default_factory=list)       # 执行过程日志（供 Web 界面展示）

    # ===== 模式控制 =====
    use_heuristics: bool = True                         # 是否启用启发式规划/综合（成本消融时设为 False）

    model_config = {
        "arbitrary_types_allowed": True,
    }

    def __getitem__(self, key: str) -> Any:
        """支持 state["query"] 字典式访问，兼容 LangGraph 节点函数"""
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """支持 state.get("query", "") 字典式访问"""
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        """支持 'query' in state 判断"""
        return hasattr(self, key)

    def keys(self) -> list:
        """支持 dict(state) 和 {**state} 展开"""
        return list(type(self).model_fields.keys())

    def values(self) -> list:
        """支持 dict(state) 转换"""
        return [getattr(self, k) for k in type(self).model_fields.keys()]

    def items(self) -> list:
        """支持 dict(state) 和 {**state} 展开"""
        return [(k, getattr(self, k)) for k in type(self).model_fields.keys()]
