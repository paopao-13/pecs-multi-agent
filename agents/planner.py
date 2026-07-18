"""
Planner Agent —— 规划者

职责：
  接收用户的原始问题，将其分解为有序的子任务步骤列表。
  类比：就像项目经理拿到需求后，拆分成开发任务分配给工程师。

输入：AgentState 中的 query + reflection（上一轮反思）
输出：更新 AgentState 中的 plan（步骤列表）

Token 预算感知：
  当预算充足时，细粒度分解（多个小步骤，每步只做一件事）
  当预算紧张（>85%）时，合并剩余步骤（减少 LLM 调用次数）
"""
import json
from agents.heuristics import build_heuristic_plan
from agents.llm_utils import call_llm_json
from graph.token_budget import (
    append_scheduler_decision,
    estimate_tokens,
    get_budget_policy,
    record_token_usage,
)
from tools import TOOL_DESCRIPTIONS
from config import LLM_API_KEY, MAX_RETRIES

# Planner 的系统提示词 —— 定义角色和行为规范
PLANNER_SYSTEM_PROMPT = """你是一个任务规划专家（Planner），负责将用户的复杂任务分解为可执行的步骤列表。

你的职责：
1. 分析用户任务，判断任务复杂度
2. 将任务分解为有序的执行步骤，每步使用一个工具
3. 确保步骤之间有清晰的依赖关系和数据流

任务复杂度判断规则：
- simple: 单步即可完成（一次搜索或一次计算就能回答），无需交叉验证
- medium: 需要2-3步，搜索+计算，或多次搜索后综合
- complex: 需要4+步，多源信息交叉验证、多轮计算、需要反思重试

可用工具：
- search: Web搜索工具，查找实时信息
- web_browse: 网页浏览工具，抓取指定URL页面内容（适用于已知具体网址的查询）
- python: Python代码执行，计算和数据处理
  ⚠️ 重要规则：
  - math、json、re、datetime 模块已预导入，直接使用即可
  - 禁止写 import 语句（会被安全沙箱拦截导致执行失败）
  - 正确写法：print(math.sqrt(25))
  - 错误写法：import math; print(math.sqrt(25))
- file_read: 读取纯文本文件
- file_parse: 文件解析工具，自动识别PDF/Excel/CSV/图片格式并提取内容（适用于GAIA文件处理任务）
- api_call: 调用外部API
- webshop: WebShop商品选择工具，适用于购物导航/商品匹配任务

输出格式（严格JSON）：
```json
{
    "complexity": "simple",
    "steps": [
        {
            "id": 1,
            "action": "search",
            "description": "搜索2024年诺贝尔物理学奖得主",
            "args": {"query": "2024 诺贝尔 物理学奖 得主"},
            "status": "pending",
            "result": null,
            "retry_count": 0
        }
    ]
}
```

规则：
- simple任务只需1步，medium任务2-3步，complex任务4-5步
- action必须是: search / web_browse / python / file_read / file_parse / api_call / webshop 之一
- args必须包含该工具需要的参数
- 如果有上一轮反思(reflection)，根据反思调整计划
"""


def planner_node(state: dict) -> dict:
    """
    Planner 节点函数

    LangGraph 的节点函数签名：
      输入：当前状态（AgentState 字典）
      输出：要更新的状态字段（会合并到状态中）

    工作流程：
    1. 从状态中读取用户问题和历史反思
    2. 构造提示词，调用 LLM 生成执行计划
    3. 解析 JSON 响应，更新状态中的 plan 字段
    """
    query = state["query"]
    reflection = state.get("reflection", "")
    iteration = state.get("iteration", 0)
    token_used = state.get("token_used", 0)
    token_budget = state.get("token_budget", 50000)
    logs = state.get("logs", [])
    policy = get_budget_policy(state)

    # 构造提示词：如果有反思，告诉 Planner 根据反思调整计划
    prompt = f"用户任务: {query}\n"
    if reflection:
        prompt += f"\n上一轮反思（请根据反思调整计划）:\n{reflection}\n"
    if iteration > 0:
        prompt += f"\n当前是第 {iteration + 1} 轮迭代，请优化计划。\n"

    # 预算紧张时提示 Planner 合并步骤
    if policy["merge_steps"]:
        prompt += "\n⚠️ 预算紧张，请将剩余步骤合并为1-2个大步骤，减少调用次数。\n"

    prompt += "\n请生成执行计划（JSON格式）。"

    use_heuristics = state.get("use_heuristics", True)
    heuristic_plan = build_heuristic_plan(query, merge_steps=policy["merge_steps"]) if use_heuristics else None
    scheduler_decisions = state.get("scheduler_decisions", [])
    has_api_key = bool(LLM_API_KEY)

    if has_api_key:
        # 真实 API 模式：LLM 为主，启发式为 fallback
        if heuristic_plan and _is_deterministic_task(heuristic_plan):
            # 确定性任务（纯计算/WebShop）：启发式规划最优
            # python/webshop 工具自身给出精确输出，无需 LLM 推理
            plan_data = heuristic_plan
            token_consumed = 0
            saved = estimate_tokens(prompt + PLANNER_SYSTEM_PROMPT)
            logs.append(f"[Planner] 确定性任务使用启发式规划（工具精确执行，零LLM调用，节省 ~{saved} tokens）")
            scheduler_decisions = append_scheduler_decision(
                {**state, "token_used": token_used},
                "planner",
                "heuristic_plan",
                "确定性任务命中启发式模式，跳过LLM规划调用",
                estimated_tokens_saved=saved,
            )
        else:
            # 搜索/知识类任务：LLM 规划
            from graph.token_budget import check_role_budget
            planner_quota = check_role_budget(state, "planner")
            if planner_quota["exceeded"]:
                token_consumed = 0
                if heuristic_plan:
                    plan_data = heuristic_plan
                    logs.append(f"[Planner] 角色配额超限({planner_quota['action']})，降级到启发式规划")
                    scheduler_decisions = append_scheduler_decision(
                        {**state, "token_used": token_used},
                        "planner",
                        "role_quota_exceeded",
                        f"Planner配额超限，降级动作: {planner_quota['action']}",
                    )
                else:
                    plan_data = {"steps": [], "complexity": "simple"}
                    logs.append("[Planner] 角色配额超限且启发式未命中，返回空计划")
            else:
                token_consumed = 0
                try:
                    plan_data, token_consumed = call_llm_json(prompt, PLANNER_SYSTEM_PROMPT, role="planner")
                except Exception as exc:
                    token_consumed = estimate_tokens(prompt + PLANNER_SYSTEM_PROMPT)
                    logs.append(f"[Planner] LLM计划解析失败: {type(exc).__name__}，尝试重试...")
                    # 重试一次，使用更强格式要求的 prompt
                    retry_prompt = prompt + "\n\n⚠️ 请务必只输出一个 JSON 对象，不要包含任何其他文字。"
                    try:
                        plan_data, token_consumed = call_llm_json(retry_prompt, PLANNER_SYSTEM_PROMPT, role="planner")
                        logs.append("[Planner] 重试成功")
                    except Exception as exc2:
                        plan_data = {}
                        logs.append(f"[Planner] 重试也失败: {type(exc2).__name__}")

            # LLM 返回空计划时尝试启发式兜底
            if not plan_data.get("steps") and use_heuristics and heuristic_plan:
                plan_data = heuristic_plan
                logs.append("[Planner] LLM规划返回空，回退到启发式规划")
    elif heuristic_plan:
        # 离线模式：启发式 only（无 API Key）
        plan_data = heuristic_plan
        token_consumed = 0
        saved = estimate_tokens(prompt + PLANNER_SYSTEM_PROMPT)
        logs.append(f"[Planner] 启发式规划命中，跳过LLM调用（节省 ~{saved} tokens）")
        scheduler_decisions = append_scheduler_decision(
            {**state, "token_used": token_used},
            "planner",
            "heuristic_plan",
            "命中启发式模式，跳过LLM规划调用",
            estimated_tokens_saved=saved,
        )
    else:
        plan_data = {"steps": [], "complexity": "simple"}
        token_consumed = 0
        logs.append("[Planner] 无API Key且启发式未命中，返回空计划")

    # 更新状态
    steps = plan_data.get("steps", [])
    complexity = plan_data.get("complexity", "medium")  # 默认 medium
    # 确保 steps 格式正确
    normalized_steps = []
    allowed_actions = {"search", "web_browse", "python", "file_read", "file_parse", "api_call", "webshop"}
    for idx, step in enumerate(steps, start=1):
        if step.get("action") not in allowed_actions:
            continue
        step.setdefault("id", idx)
        step.setdefault("status", "pending")
        step.setdefault("result", None)
        step.setdefault("retry_count", 0)
        step.setdefault("risk", "medium")
        step.setdefault("depends_on", [])
        step.setdefault("args", {})
        normalized_steps.append(step)
    steps = normalized_steps

    if not steps and use_heuristics:
        fallback = build_heuristic_plan(query, merge_steps=policy["merge_steps"])
        if fallback:
            steps = fallback["steps"]
            complexity = fallback.get("complexity", complexity)
            logs.append("[Planner] 空计划已由启发式计划兜底")

    token_used, role_token_used, budget_events = record_token_usage(state, "planner", token_consumed)
    if policy["merge_steps"]:
        saved = estimate_tokens(prompt + PLANNER_SYSTEM_PROMPT)
        scheduler_decisions = append_scheduler_decision(
            {**state, "token_used": token_used},
            "planner",
            "merge_steps",
            "预算超过85%，要求Planner压缩步骤数",
            usage_ratio=round(policy["usage_ratio"], 4),
            estimated_tokens_saved=saved,
        )

    logs.append(f"[Planner] 生成 {len(steps)} 个步骤的计划 (复杂度: {complexity}, 消耗 {token_consumed} tokens)")

    return {
        "plan": steps,
        "complexity": complexity,
        "current_step_idx": 0,
        "token_used": token_used,
        "role_token_used": role_token_used,
        "budget_events": budget_events,
        "scheduler_decisions": scheduler_decisions,
        "iteration": iteration,
        "logs": logs,
    }


def _is_deterministic_task(plan_data: dict) -> bool:
    """判断计划是否仅包含确定性工具步骤（python/webshop）。

    确定性工具指工具自身给出精确输出、无需 LLM 推理的工具：
    - python: AST 沙箱精确执行，结果确定
    - webshop: 内部做多维约束匹配，结果确定

    对这类任务，启发式规划生成工具调用参数即可，
    无需消耗 LLM tokens 进行规划。
    """
    steps = plan_data.get("steps", [])
    if not steps:
        return False
    deterministic_actions = {"python", "webshop"}
    return all(s.get("action") in deterministic_actions for s in steps)
