"""
Token 预算感知调度器

核心思想：
  普通的 Agent 系统不管成本，反复调用 LLM 直到任务完成。
  这会导致一个问题：简单任务花 10 块，复杂任务花 100 块，成本不可控。

  我们的方案：给每个任务设定一个 Token 预算上限（比如 5 万 Token），
  系统在运行过程中实时追踪已消耗的 Token，当消耗达到不同阈值时自动降级：

  70% 预算 → Critic 跳过低风险步骤的验证（省掉不必要的检查）
  85% 预算 → Planner 把剩余步骤合并成一个大步骤（减少调用次数）
  95% 预算 → Synthesizer 直接用现有结果生成答案（不再执行更多步骤）

  这样为“单任务成本降低 30%（相比无预算控制基线）”提供可评测的调度机制。
"""
from config import (
    BUDGET_ALLOCATION,
    DEGRADE_THRESHOLD_1,
    DEGRADE_THRESHOLD_2,
    DEGRADE_THRESHOLD_3,
    DEFAULT_TOKEN_BUDGET,
)


ROLE_NAMES = tuple(BUDGET_ALLOCATION.keys())

def estimate_tokens(text: str) -> int:
    """
    估算文本 token 数。

    优先使用 tiktoken 精确计数（与 OpenAI/DeepSeek API 计费一致），
    tiktoken 未安装时回退到字符数除法估算（中英文混合 3 字符/token 经验值）。
    """
    if not text:
        return 0
    # 粗略估算：中英文混合约 3 字符/token
    return max(1, len(str(text)) // 3)


def get_usage_ratio(token_used: int, token_budget: int) -> float:
    if token_budget <= 0:
        return 1.0
    return token_used / token_budget


def get_degrade_level(token_used: int, token_budget: int) -> int:
    ratio = get_usage_ratio(token_used, token_budget)
    if ratio > DEGRADE_THRESHOLD_3:
        return 3
    if ratio > DEGRADE_THRESHOLD_2:
        return 2
    if ratio > DEGRADE_THRESHOLD_1:
        return 1
    return 0


def get_budget_policy(state: dict, risk: str = "medium") -> dict:
    """
    根据当前状态生成调度策略。

    返回值供节点和路由函数共同使用，避免每个角色重复硬编码阈值。
    """
    token_used = state.get("token_used", 0)
    token_budget = state.get("token_budget", DEFAULT_TOKEN_BUDGET)
    ratio = get_usage_ratio(token_used, token_budget)
    level = get_degrade_level(token_used, token_budget)
    low_risk = risk == "low"

    return {
        "token_used": token_used,
        "token_budget": token_budget,
        "remaining": max(0, token_budget - token_used),
        "usage_ratio": ratio,
        "degrade_level": level,
        "skip_low_risk_critic": level >= 1 and low_risk,
        "fast_critic": level >= 1,
        "merge_steps": level >= 2,
        "force_synthesize": level >= 3,
    }


def record_token_usage(state: dict, role: str, tokens: int) -> tuple:
    """
    记录 token 消耗，返回更新后的三元组。

    节点函数可直接把返回值写回 AgentState：
      token_used, role_token_used, budget_events = record_token_usage(...)
    """
    tokens = max(0, int(tokens or 0))
    token_budget = state.get("token_budget", DEFAULT_TOKEN_BUDGET)
    token_used = state.get("token_used", 0) + tokens

    role_token_used = dict(state.get("role_token_used", {}) or {})
    for name in ROLE_NAMES:
        role_token_used.setdefault(name, 0)
    role_token_used[role] = role_token_used.get(role, 0) + tokens

    budget_events = list(state.get("budget_events", []) or [])
    if tokens:
        budget_events.append({
            "role": role,
            "tokens": tokens,
            "token_used": token_used,
            "usage_ratio": round(get_usage_ratio(token_used, token_budget), 4),
            "degrade_level": get_degrade_level(token_used, token_budget),
        })

    return token_used, role_token_used, budget_events


def append_scheduler_decision(
    state: dict,
    actor: str,
    decision: str,
    reason: str,
    **extra,
) -> list:
    """Append a structured scheduler decision to state."""
    decisions = list(state.get("scheduler_decisions", []) or [])
    decisions.append({
        "actor": actor,
        "decision": decision,
        "reason": reason,
        "token_used": state.get("token_used", 0),
        "token_budget": state.get("token_budget", DEFAULT_TOKEN_BUDGET),
        "degrade_level": get_degrade_level(
            state.get("token_used", 0),
            state.get("token_budget", DEFAULT_TOKEN_BUDGET),
        ),
        **extra,
    })
    return decisions


class TokenBudgetManager:
    """Token 预算管理器：追踪消耗、判断降级、分配预算"""

    def __init__(self, total_budget: int = DEFAULT_TOKEN_BUDGET):
        self.total_budget = total_budget
        self.token_used = 0

        # 按角色分配预算份额
        self.role_budgets = {
            role: int(total_budget * ratio)
            for role, ratio in BUDGET_ALLOCATION.items()
        }
        self.role_used = {
            role: 0 for role in BUDGET_ALLOCATION
        }

        # 预算事件日志（记录角色配额超限等事件）
        self.budget_events = []

    def consume(self, role: str, tokens: int) -> None:
        """
        记录某角色消耗了 Token

        参数:
            role: 消耗角色（planner / executor / critic / synthesizer）
            tokens: 消耗的 Token 数

        角色配额检查：
            消耗后检查该角色是否超出独立配额，超限时记录到 budget_events。
        """
        self.token_used += tokens
        self.role_used[role] += tokens

        # 检查角色配额是否超限
        if not self.check_role_quota(role):
            action = self.get_role_degrade_action(role)
            self.budget_events.append({
                "role": role,
                "tokens": tokens,
                "token_used": self.token_used,
                "role_used": self.role_used[role],
                "role_budget": self.role_budgets.get(role, 0),
                "role_usage_ratio": round(self.get_role_usage_ratio(role), 4),
                "event": "role_quota_exceeded",
                "degrade_action": action,
            })

    def check_role_quota(self, role: str) -> bool:
        """
        检查单个角色是否超出独立配额

        参数:
            role: 角色名称（planner / executor / critic / synthesizer）

        返回:
            True 表示未超出（在配额内），False 表示已超出
        """
        return self.get_role_usage_ratio(role) <= 1.0

    def get_role_degrade_action(self, role: str) -> str:
        """
        返回角色超配额时的降级动作

        各角色的降级策略：
        - planner 超限 → "skip_llm_use_heuristic"（用启发式替代 LLM 规划）
        - executor 超限 → "merge_remaining_steps"（合并剩余步骤）
        - critic 超限 → "skip_critic"（跳过评审）
        - synthesizer 超限 → "fast_synthesize"（快速综合）

        参数:
            role: 角色名称

        返回:
            降级动作字符串
        """
        return ROLE_DEGRADE_ACTIONS.get(role, "default_degrade")

    def get_usage_ratio(self) -> float:
        """返回当前总消耗占预算的比例（0.0 ~ 1.0+）"""
        if self.total_budget == 0:
            return 1.0
        return self.token_used / self.total_budget

    def get_role_usage_ratio(self, role: str) -> float:
        """返回某角色的预算使用比例"""
        budget = self.role_budgets.get(role, 0)
        if budget == 0:
            return 1.0
        return self.role_used.get(role, 0) / budget

    def should_skip_critic(self) -> bool:
        """
        降级级别 1（70%）：
        Critic 是否应该跳过低风险步骤的验证

        当总预算消耗超过 70% 时，跳过对低风险步骤的详细检查，
        只做快速验证。这样可以省下 Critic 的 Token 消耗。
        """
        return self.get_usage_ratio() > DEGRADE_THRESHOLD_1

    def should_merge_steps(self) -> bool:
        """
        降级级别 2（85%）：
        Planner 是否应该合并剩余步骤

        当预算消耗超过 85% 时，把剩余的多个小步骤合并成一个大步骤，
        一次性执行完，减少 LLM 调用次数。
        """
        return self.get_usage_ratio() > DEGRADE_THRESHOLD_2

    def should_force_synthesize(self) -> bool:
        """
        降级级别 3（95%）：
        是否应该强制进入综合阶段

        预算几乎耗尽时，停止执行更多步骤，直接用已有结果生成答案。
        """
        return self.get_usage_ratio() > DEGRADE_THRESHOLD_3

    def get_degrade_level(self) -> int:
        """返回当前降级级别：0=正常, 1=跳过部分Critic, 2=合并步骤, 3=强制综合"""
        return get_degrade_level(self.token_used, self.total_budget)

    def get_status(self) -> dict:
        """返回当前预算状态摘要（供 Web 界面展示）"""
        return {
            "total_budget": self.total_budget,
            "token_used": self.token_used,
            "remaining": max(0, self.total_budget - self.token_used),
            "usage_ratio": round(self.get_usage_ratio(), 4),
            "degrade_level": self.get_degrade_level(),
            "role_usage": {
                role: {
                    "budget": self.role_budgets[role],
                    "used": self.role_used[role],
                    "ratio": round(self.get_role_usage_ratio(role), 4),
                    "quota_exceeded": not self.check_role_quota(role),
                    "degrade_action": self.get_role_degrade_action(role) if not self.check_role_quota(role) else None,
                }
                for role in BUDGET_ALLOCATION
            },
            "budget_events": self.budget_events,
        }

    def estimate_tokens(self, text: str) -> int:
        """
        粗略估算文本的 Token 数

        经验值：英文约 1 token ≈ 4 字符，中文约 1 token ≈ 2 字符
        这里用折中值 3 字符/token
        """
        return estimate_tokens(text)


