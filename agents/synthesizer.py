"""
Synthesizer Agent —— 综合者

职责：
  整合所有子任务的执行结果，生成最终答案。
  类比：就像项目经理汇总所有工程师的工作成果，写一份最终报告。

输入：AgentState 中的 results（所有步骤的执行结果）
输出：更新 AgentState 中的 final_answer

反思触发：
  如果综合结果不完整或存在矛盾，触发 Reflect 循环回到 Planner 重新规划。
  但不能无限循环，最多 MAX_ITERATIONS 轮。
"""
from agents.heuristics import synthesize_heuristic_answer
from agents.llm_utils import call_llm
from graph.token_budget import (
    append_scheduler_decision,
    estimate_tokens,
    get_budget_policy,
    record_token_usage,
)
from config import MAX_ITERATIONS, LLM_API_KEY

# Synthesizer 的系统提示词
SYNTHESIZER_SYSTEM_PROMPT = """你是一个结果综合专家（Synthesizer），负责整合多个子任务的执行结果，生成最终答案。

你的职责：
1. 阅读所有步骤的执行结果
2. 提取关键信息，去除冗余和矛盾
3. 按照逻辑顺序组织信息
4. 生成清晰、完整、准确的最终答案

要求：
- 答案要直接回应用户的原始问题
- 如果信息不足，明确指出缺少什么
- 如果不同步骤的结果有矛盾，指出矛盾并给出最可能正确的信息
- 根据问题类型选择合适的输出格式
- ⚠️ 如果问题要求判断（如"是否"、"有没有"、"对吗"），你必须在最终答案中明确给出"是"或"否"的结论
"""


def synthesizer_node(state: dict) -> dict:
    """
    Synthesizer 节点函数

    整合所有执行结果，生成最终答案：
    1. 收集所有步骤的结果
    2. 调用 LLM 综合分析
    3. 判断是否需要触发反思循环

    优化：simple 任务跳过 LLM 综合，直接提取关键结果
    """
    query = state.get("query", "")
    results = state.get("results", [])
    token_used = state.get("token_used", 0)
    token_budget = state.get("token_budget", 50000)
    iteration = state.get("iteration", 0)
    complexity = state.get("complexity", "medium")
    logs = state.get("logs", [])

    logs.append(f"[Synthesizer] 开始综合 {len(results)} 个步骤的结果")

    # 如果没有结果，直接返回
    if not results:
        return {
            "final_answer": "无法生成答案：没有执行任何步骤。",
            "reflection": "",
            "logs": logs,
        }

    # 构造结果摘要
    results_summary = ""
    for r in results:
        results_summary += f"\n步骤{r.get('step_id')}: {r.get('description', '')}\n"
        results_summary += f"结果: {r.get('result', '')}\n"

    # Token 预算感知：预算紧张时简化综合
    budget_ratio = token_used / token_budget if token_budget > 0 else 0
    use_heuristics = state.get("use_heuristics", True)
    has_api_key = bool(LLM_API_KEY)

    # 判断是否为确定性任务（所有结果均来自 python/webshop 工具）
    # 确定性工具自身给出精确输出，启发式抽取即可，无需 LLM 综合
    is_deterministic = False
    if results:
        deterministic_actions = {"python", "webshop"}
        is_deterministic = all(r.get("action") in deterministic_actions for r in results)

    heuristic_answer = synthesize_heuristic_answer(query, results) if use_heuristics else None
    direct_extractive_answer = False

    if has_api_key and not is_deterministic:
        # 真实 API 模式 + 搜索/知识类任务：LLM 综合为主
        if budget_ratio > 0.95:
            # 预算几乎耗尽，紧急模式
            final_answer = _emergency_synthesize(query, results)
            token_consumed = 0
            logs.append(f"[Synthesizer] 紧急模式（预算 {budget_ratio:.0%}），直接拼接结果")
            scheduler_decisions = append_scheduler_decision(
                state,
                "synthesizer",
                "emergency_synthesize",
                "预算超过95%，强制使用已有结果输出",
                estimated_tokens_saved=max(250, estimate_tokens(results_summary)),
            )
        else:
            # LLM 综合
            prompt = f"""
用户原始问题: {query}

各步骤执行结果:
{results_summary}

请综合以上结果，生成最终答案。答案要直接回应用户的问题。
"""
            final_answer, token_consumed = call_llm(prompt, SYNTHESIZER_SYSTEM_PROMPT, role="synthesizer")
            # LLM 失败时回退到启发式
            if not final_answer or final_answer.startswith("[LLM调用失败]"):
                if heuristic_answer:
                    final_answer = heuristic_answer
                    direct_extractive_answer = True
                    token_consumed = 0
                    logs.append("[Synthesizer] LLM综合失败，回退到启发式抽取")
                else:
                    final_answer = _emergency_synthesize(query, results)
                    token_consumed = 0
                    logs.append("[Synthesizer] LLM综合失败且启发式未命中，紧急拼接")
            else:
                logs.append(f"[Synthesizer] LLM综合完成 (消耗 {token_consumed} tokens)")
            scheduler_decisions = state.get("scheduler_decisions", [])
    elif heuristic_answer:
        # 确定性任务或离线模式：启发式综合（工具精确输出，无需 LLM）
        direct_extractive_answer = True
        final_answer = heuristic_answer
        token_consumed = 0
        logs.append("[Synthesizer] 抽取式综合完成（确定性/预算感知路径）")
        scheduler_decisions = append_scheduler_decision(
            state,
            "synthesizer",
            "extractive_synthesize",
            "工具结果已包含可直接抽取的答案，跳过LLM综合调用",
            estimated_tokens_saved=max(250, estimate_tokens(results_summary)),
        )
    elif budget_ratio > 0.95:
        # 预算几乎耗尽，直接拼接结果
        final_answer = _emergency_synthesize(query, results)
        token_consumed = 0
        logs.append(f"[Synthesizer] 紧急模式（预算 {budget_ratio:.0%}），直接拼接结果")
        scheduler_decisions = append_scheduler_decision(
            state,
            "synthesizer",
            "emergency_synthesize",
            "预算超过95%，强制使用已有结果输出",
            estimated_tokens_saved=max(250, estimate_tokens(results_summary)),
        )
    elif complexity == "simple" and len(results) == 1:
        # 简单任务快速路径：跳过 LLM 综合，直接用轻量提取
        final_answer = _fast_synthesize(query, results[0])
        token_consumed = 0
        logs.append(f"[Synthesizer] 快速模式（simple），跳过 LLM 综合")
        scheduler_decisions = append_scheduler_decision(
            state,
            "synthesizer",
            "fast_simple_synthesize",
            "simple单步任务直接提取工具结果",
            estimated_tokens_saved=max(150, estimate_tokens(results_summary)),
        )
    else:
        # 正常综合（离线模式下的 LLM mock 调用）
        prompt = f"""
用户原始问题: {query}

各步骤执行结果:
{results_summary}

请综合以上结果，生成最终答案。答案要直接回应用户的问题。
"""
        final_answer, token_consumed = call_llm(prompt, SYNTHESIZER_SYSTEM_PROMPT, role="synthesizer")
        logs.append(f"[Synthesizer] 综合完成 (消耗 {token_consumed} tokens)")
        scheduler_decisions = state.get("scheduler_decisions", [])

    # 判断是否需要触发反思循环
    reflection = ""
    _policy_state = dict(state) if not isinstance(state, dict) else state
    post_policy = get_budget_policy({**_policy_state, "token_used": token_used + token_consumed})
    need_reflect = (
        (not direct_extractive_answer)
        and (not post_policy["force_synthesize"])
        and _should_reflect(query, final_answer, results, iteration, complexity)
    )

    if need_reflect and iteration < MAX_ITERATIONS - 1:
        # 生成反思
        reflection = _generate_reflection(query, final_answer, results, token_used, token_budget)
        logs.append(f"[Synthesizer] 触发反思循环 (第 {iteration + 1} 轮): {reflection[:100]}...")

    token_used, role_token_used, budget_events = record_token_usage(state, "synthesizer", token_consumed)

    return {
        "final_answer": final_answer,
        "reflection": reflection,
        "token_used": token_used,
        "role_token_used": role_token_used,
        "budget_events": budget_events,
        "scheduler_decisions": scheduler_decisions,
        "iteration": iteration + 1,
        "logs": logs,
    }


def _should_reflect(query: str, answer: str, results: list, iteration: int, complexity: str = "medium") -> bool:
    """
    判断是否需要触发反思循环

    触发条件：
    1. 答案太短（可能不完整）
    2. 有步骤失败
    3. 答案缺少明确结论（问题要求判断但答案没有是/否）
    4. 还没达到最大迭代次数
    5. simple 任务不触发反思
    """
    if complexity == "simple":
        return False

    if iteration >= MAX_ITERATIONS - 1:
        return False

    # 答案太短
    if len(answer) < 30:
        return True

    # 有步骤失败
    has_failed = any(not r.get("success", False) for r in results)
    if has_failed:
        return True

    # 答案缺少明确结论
    query_lower = query.lower()
    answer_lower = answer.lower()
    if any(kw in query_lower for kw in ["是否", "判断", "是不是", "对吗"]):
        has_conclusion = any(kw in answer_lower for kw in [
            "是", "否", "不是", "true", "false", "整数", "偶数", "奇数",
            "大于", "小于", "等于", "能", "不能"
        ])
        if not has_conclusion:
            return True

    return False


def _generate_reflection(query: str, answer: str, results: list, token_used: int, token_budget: int) -> str:
    """生成反思总结，供下一轮 Planner 参考"""
    reflection_parts = []

    # 分析哪些步骤失败了
    failed_steps = [r for r in results if not r.get("success", False)]
    if failed_steps:
        reflection_parts.append(f"有 {len(failed_steps)} 个步骤失败，需要换一种方式执行。")

    # 分析答案完整性
    if len(answer) < 50:
        reflection_parts.append("上一次的答案信息不够完整，需要收集更多数据。")

    # 预算状况
    budget_remaining = token_budget - token_used
    if budget_remaining < token_budget * 0.15:
        reflection_parts.append(f"预算剩余较少（{budget_remaining} tokens），请用更少的步骤完成任务。")

    if not reflection_parts:
        reflection_parts.append("上一轮执行基本完成，但答案质量可以提升，请优化执行计划。")

    return " | ".join(reflection_parts)


def _emergency_synthesize(query: str, results: list) -> str:
    """紧急模式：预算耗尽时直接拼接结果"""
    parts = [f"根据已有信息回答问题「{query}」:\n"]
    for r in results:
        if r.get("success", False):
            parts.append(f"- {r.get('description', '')}: {r.get('result', '')[:200]}")
    parts.append("\n（注：因Token预算限制，未能进行深度综合分析）")
    return "\n".join(parts)


def _fast_synthesize(query: str, result: dict) -> str:
    """
    快速模式：simple 任务的轻量综合
    
    不调用 LLM，直接从工具执行结果中提取关键信息。
    适用于单步搜索或单步计算的任务。
    """
    raw_result = result.get("result", "")
    action = result.get("action", "")
    
    # 如果是 Python 执行结果，提取输出内容
    if action == "python":
        # Python 工具返回格式: "输出:\n<实际输出>" 或直接返回结果
        lines = raw_result.strip().split("\n")
        # 去掉 "输出:" 前缀行
        output_lines = [l for l in lines if not l.strip().startswith("输出:") and l.strip()]
        if output_lines:
            return "\n".join(output_lines)
        return raw_result.strip()
    
    # 如果结果是搜索结果，提取正文内容（优先找长度较长的实质性内容，而非标题）
    if "[搜索]" in raw_result or "[DuckDuckGo]" in raw_result or "[模拟搜索]" in raw_result:
        lines = raw_result.split("\n")
        candidates = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("来源:") or line.startswith("---"):
                continue
            cleaned = line.replace("[搜索] ", "").replace("[DuckDuckGo] ", "").replace("[模拟搜索] ", "")
            # 正文通常比标题长，优先选择长度>15的实质性内容
            if len(cleaned) > 15:
                candidates.append(cleaned)
        if candidates:
            return candidates[0]
        # 如果没有找到正文行，返回前300字符
        return raw_result[:300]
    
    # 其他情况：返回完整结果
    return raw_result
